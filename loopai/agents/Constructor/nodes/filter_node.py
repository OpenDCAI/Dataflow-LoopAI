"""
Data Cleaning Subgraph - 数据清洗子图

在 mapping_subgraph 之前对中间格式数据进行清洗。
流程：按逻辑数据集均分预算、组内再按分片均分采样（算法见 loopai.common.jsonl_dataset_sampling）→（SFT）ShareGPT 改写 → basic_data_flitter → planner（仅领域工具）→ process（仅领域工具）→ benchmark → cot_finalize（可选，append_cot_after_cleaning）。
"""
import asyncio
import copy
import os
import json
import re
import random
import shutil
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader
from loopai.common.jsonl_dataset_sampling import (
    cleaning_sampling_plan_by_dataset,
    logical_dataset_stem_from_jsonl_basename,
)
from loopai.agents.BaseAgent.base_agent import BaseAgent
from loopai.agents.Constructor.tools.data_filter_tools import (
    DEFAULT_LLM_TIMEOUT_SECONDS,
    _await_llm_with_timeout,
    basic_data_flitter,
    benchmark_data_cleaner,
    domain_code_gen_cleaner,
    domain_normal_data_cleaner,
    domain_text2sql_cleaner,
    rewrite_record_prompt_json_max_chars,
    truncate_json_for_llm_prompt,
    _strip_llm_json_text,
)
from loopai.agents.Constructor.tools.cot_filter_tool import norma_filter_and_add_cot
from loopai.agents.Constructor.utils.openai_compat_chat import (
    OpenAIChatParams,
    chat_completion_async,
    chat_completion_sync,
)

logger = get_logger()


def _load_prompt_pair(state: LoopAIState, prompt_name: str) -> tuple[str, str]:
    """从 *_prompt.json 加载 system/task，不经 BaseAgent 单例，避免 stream / agent 事件被连带触发。"""
    loader = PromptLoader(state.get("prompt_template_dir"))
    return loader("system", prompt_name), loader("task", prompt_name)


# 工具映射字典：将工具名称映射到对应的工具函数
TOOL_MAP = {
    "basic_data_flitter": basic_data_flitter,
    "text2sql": domain_text2sql_cleaner,
    "code_generate": domain_code_gen_cleaner,
    "normal_data": domain_normal_data_cleaner,
    "benchmark_cleaner": benchmark_data_cleaner,
    "norma_filter_and_add_cot": norma_filter_and_add_cot,
}


def _sample_intermediate_data(data_path: str, max_samples: int) -> None:
    """
    对基础清洗后的中间数据执行采样。
    当记录数超过 max_samples 时，随机采样到 max_samples 条并覆写原文件。
    支持单个 JSONL 文件或包含多个 JSONL 文件的目录。
    """
    if not data_path or not os.path.exists(data_path):
        return

    jsonl_files: List[str] = []
    if os.path.isfile(data_path) and data_path.endswith(".jsonl"):
        jsonl_files = [data_path]
    elif os.path.isdir(data_path):
        jsonl_files = [
            os.path.join(data_path, f)
            for f in os.listdir(data_path)
            if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
        ]

    if not jsonl_files:
        return

    file_records: Dict[str, List[str]] = {}
    total = 0
    for fp in jsonl_files:
        lines: List[str] = []
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    lines.append(line.rstrip("\n"))
        file_records[fp] = lines
        total += len(lines)

    if total <= max_samples:
        logger.info(
            f"Post-basic-cleaning sampling: {total} records <= {max_samples}, no sampling needed"
        )
        return

    logger.info(
        f"Post-basic-cleaning sampling: {total} records > {max_samples}, "
        f"sampling down to {max_samples}"
    )

    for fp, lines in file_records.items():
        share = max(1, int(max_samples * len(lines) / total)) if lines else 0
        share = min(share, len(lines))
        if len(lines) > share:
            file_records[fp] = random.sample(lines, share)

    for fp, lines in file_records.items():
        with open(fp, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    sampled_total = sum(len(v) for v in file_records.values())
    logger.info(f"Post-basic-cleaning sampling complete: {total} -> {sampled_total}")


def _read_jsonl_file(filepath: str) -> List[Dict[str, Any]]:
    """读取JSONL文件"""
    records = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON line in {filepath}: {e}")
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {e}")
    return records


def _extract_user_query(state: LoopAIState) -> str:
    """从state中提取user_query"""
    # 优先从 automated_query 获取
    if state.get("automated_query"):
        return state.get("automated_query", "")
    
    # 从 messages 中提取最后一个 HumanMessage
    if state.get("messages") and len(state["messages"]) > 0:
        from langchain_core.messages import HumanMessage
        
        # 从后往前搜索最后一个 HumanMessage
        for message in reversed(state["messages"]):
            if isinstance(message, HumanMessage):
                if hasattr(message, "content"):
                    return message.content
            elif isinstance(message, dict):
                msg_type = message.get("type", "")
                msg_role = message.get("role", "")
                if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                    content = message.get("content", "")
                    if content:
                        return content
            elif hasattr(message, "type") and message.type == "human":
                if hasattr(message, "content"):
                    return message.content
    
    return ""


def _parse_json_list(response_text: str) -> List[str]:
    """解析LLM返回的JSON列表"""
    try:
        # 尝试直接解析JSON
        parsed = json.loads(response_text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    
    # 尝试提取代码块中的JSON
    pattern = r'```(?:json)?\s*(\[[\s\S]*?\])```'
    match = re.search(pattern, response_text)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    
    # 尝试查找方括号内的内容
    start_idx = response_text.find('[')
    end_idx = response_text.rfind(']')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            parsed = json.loads(response_text[start_idx:end_idx + 1])
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    
    logger.warning(f"Could not parse JSON list from LLM response: {response_text}")
    return []


def _default_sharegpt_pre_backup_root(data_path: str) -> str:
    """改写前备份目录：与 intermediate_data_path 同级的 pre_sharegpt_rewrite/。"""
    if not data_path or not os.path.exists(data_path):
        return ""
    if os.path.isfile(data_path) and data_path.endswith(".jsonl"):
        return os.path.join(os.path.dirname(os.path.abspath(data_path)), "pre_sharegpt_rewrite")
    if os.path.isdir(data_path):
        return os.path.join(os.path.abspath(data_path), "pre_sharegpt_rewrite")
    return ""


def _coerce_bool(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("0", "false", "no", "off", ""):
            return False
        if s in ("1", "true", "yes", "on"):
            return True
    return default


def _enumerate_intermediate_jsonl_files(data_path: str) -> List[str]:
    """列出待处理 jsonl（单文件或目录下全部 .jsonl），路径排序稳定。"""
    if not data_path or not os.path.exists(data_path):
        return []
    if os.path.isfile(data_path) and data_path.endswith(".jsonl"):
        return [data_path]
    if os.path.isdir(data_path):
        names = sorted(
            f for f in os.listdir(data_path)
            if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
        )
        return [os.path.join(data_path, f) for f in names]
    return []


def _parse_cleaning_random_seed(constructor: Dict[str, Any]) -> Optional[int]:
    """constructor.cleaning_random_seed：支持 int 或可解析为 int 的 str；无效则视为未设置。"""
    raw = constructor.get("cleaning_random_seed")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _reservoir_sample_non_empty_lines(
    filepath: str,
    k: int,
    rng: Optional[random.Random] = None,
) -> List[str]:
    """从文本文件流式均匀随机抽取 k 条非空行（k=0 返回空列表）。"""
    rnd = rng if rng is not None else random
    if k <= 0:
        return []
    pool: List[str] = []
    seen = 0
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            seen += 1
            s = line.rstrip("\n")
            if len(pool) < k:
                pool.append(s)
            else:
                j = rnd.randint(0, seen - 1)
                if j < k:
                    pool[j] = s
    return pool


def _user_query_for_rewrite(state: LoopAIState) -> str:
    c = state.get("constructor") or {}
    u = (c.get("user_query") or "").strip()
    if u:
        return u
    return (_extract_user_query(state) or "").strip()


def _collect_benchmark_example_strings(state: LoopAIState) -> List[str]:
    """供 ShareGPT 改写随机抽取的 benchmark JSON 文本列表。

    优先来源：constructor.benchmark_pool_path（新）
    兼容来源：constructor.benchmark_samples_path / state.banckmark_jsonl_path（旧）
    """
    out: List[str] = []
    constructor = state.get("constructor") or {}

    pool_path = (constructor.get("benchmark_pool_path") or "").strip()
    if pool_path and os.path.isfile(pool_path):
        try:
            with open(pool_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    sr = obj.get("sample_record")
                    if isinstance(sr, dict):
                        out.append(json.dumps(sr, ensure_ascii=False))
                    else:
                        out.append(json.dumps(obj, ensure_ascii=False))
        except OSError as e:
            logger.warning(f"_collect_benchmark_example_strings: cannot read {pool_path}: {e}")

    # 如果采样池已有可用样例，直接返回，避免与旧来源重复混合。
    if out:
        return out

    samples_path = (constructor.get("benchmark_samples_path") or "").strip()
    if samples_path and os.path.isfile(samples_path):
        try:
            with open(samples_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    sr = obj.get("sample_record")
                    if isinstance(sr, dict):
                        out.append(json.dumps(sr, ensure_ascii=False))
                    else:
                        out.append(json.dumps(obj, ensure_ascii=False))
        except OSError as e:
            logger.warning(f"_collect_benchmark_example_strings: cannot read {samples_path}: {e}")

    bm_path = (state.get("banckmark_jsonl_path") or "").strip()
    if bm_path and os.path.exists(bm_path):
        jsonl_files: List[str] = []
        if os.path.isfile(bm_path) and bm_path.endswith(".jsonl"):
            jsonl_files = [bm_path]
        elif os.path.isdir(bm_path):
            # 递归收集 .jsonl（benchmark 常为嵌套目录，如 openai_humaneval/.../*.jsonl）
            for root, _dirs, files in os.walk(bm_path):
                for name in sorted(files):
                    if not name.endswith(".jsonl"):
                        continue
                    fp_join = os.path.join(root, name)
                    if os.path.isfile(fp_join):
                        jsonl_files.append(fp_join)
            jsonl_files.sort()
        for fp in jsonl_files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(rec, dict):
                            out.append(line)
            except OSError as e:
                logger.warning(f"_collect_benchmark_example_strings: cannot read {fp}: {e}")
    return out


def _parse_llm_json_object(content: str) -> Optional[Dict[str, Any]]:
    raw = _strip_llm_json_text(content)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _fill_sharegpt_task_prompt(
    template: str,
    *,
    user_query: str,
    benchmark_example: str,
    target_schema: str,
    raw_record: str,
) -> str:
    """避免 str.format 将 benchmark / raw JSON 中的花括号误解析为占位符。"""
    return (
        template.replace("{user_query}", user_query)
        .replace("{benchmark_example}", benchmark_example)
        .replace("{target_schema}", target_schema)
        .replace("{raw_record}", raw_record)
    )


def _has_non_empty_msg_content(content: object) -> bool:
    if content is None:
        return False
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(item, str) and item.strip() for item in content)
    return False


# ShareGPT 改写：每批并发 LLM 请求数（每条样本仍单独一次请求）
SHAREGPT_REWRITE_CONCURRENCY_DEFAULT = 502


async def _sharegpt_rewrite_one_async(
    params: OpenAIChatParams,
    system_prompt: str,
    human: str,
    max_retries: int,
    timeout_seconds: float,
) -> Optional[Dict[str, Any]]:
    """单条样本异步改写，单次 LLM 调用受 timeout_seconds 限制；成功返回 dict，失败返回 None。"""
    msgs = [SystemMessage(content=system_prompt), HumanMessage(content=human)]
    for attempt in range(max(1, max_retries)):
        try:
            resp = await chat_completion_async(params, msgs, timeout_seconds=timeout_seconds)
            text = (resp.content or "").strip()
            out_obj = _parse_llm_json_object(text)
            if out_obj and _is_valid_messages_rewrite_output(out_obj):
                return out_obj
        except Exception as ex:
            logger.debug(f"sharegpt rewrite attempt {attempt + 1}: {ex}")
    return None


def _is_valid_messages_rewrite_output(rec: Dict[str, Any]) -> bool:
    """改写节点期望的 SFT 中间格式：messages[{role, content}]，且含 system / user / assistant。"""
    messages = rec.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    has_system = False
    has_user = False
    has_assistant = False
    for msg in messages:
        if not isinstance(msg, dict):
            return False
        role = msg.get("role")
        if not isinstance(role, str) or not role.strip():
            return False
        rl = role.lower()
        if rl not in ("system", "user", "assistant"):
            return False
        if not _has_non_empty_msg_content(msg.get("content")):
            return False
        if rl == "system":
            has_system = True
        elif rl == "user":
            has_user = True
        else:
            has_assistant = True
    return has_system and has_user and has_assistant


def sampling_plan_node(state: LoopAIState) -> LoopAIState:
    """第零步：按逻辑数据集均分 max_samples_before_cleaning，组内再按 jsonl 分片均分；不读文件计行。"""
    logger.info("=== Cleaning Subgraph: sampling_plan_node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 采样配额（按数据集均分）",
            progress=0.0,
            data={"phase": "data_cleaning", "node": "sampling_plan"},
        ).json())

    if "constructor" not in state:
        state["constructor"] = {}
    constructor = state["constructor"]
    constructor.pop("cleaning_presampled", None)
    constructor.pop("cleaning_sampling_plan", None)

    data_path = constructor.get("intermediate_data_path", "")
    budget = int(constructor.get("max_samples_before_cleaning", 20000) or 0)
    files = _enumerate_intermediate_jsonl_files(data_path)
    fcount = len(files)
    dataset_group_count = 0
    if fcount:
        dataset_group_count = len(
            {logical_dataset_stem_from_jsonl_basename(os.path.basename(fp)) for fp in files}
        )

    if fcount == 0:
        constructor["cleaning_sampling_plan"] = {}
        logger.warning("sampling_plan_node: no jsonl files under intermediate_data_path")
    elif budget <= 0:
        constructor["cleaning_sampling_plan"] = {fp: -1 for fp in files}
        logger.info("sampling_plan_node: budget=0, no per-file cap")
    else:
        constructor["cleaning_sampling_plan"] = cleaning_sampling_plan_by_dataset(files, budget)
        sample_vals = list(constructor["cleaning_sampling_plan"].values())
        logger.info(
            f"sampling_plan_node: {dataset_group_count} dataset(s), {fcount} files, budget={budget}, "
            f"quotas(sample)={sample_vals[:8]}..."
        )

    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 采样配额完成",
            progress=0.08,
            data={
                "phase": "data_cleaning",
                "node": "sampling_plan",
                "file_count": fcount,
                "dataset_group_count": dataset_group_count,
                "budget": budget,
            },
        ).json())
    return state


def apply_sampling_node(state: LoopAIState) -> LoopAIState:
    """第一步：按 cleaning_sampling_plan 对每个 jsonl 采样覆写；-1 表示不截断。"""
    logger.info("=== Cleaning Subgraph: apply_sampling_node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 按配额采样",
            progress=0.1,
            data={"phase": "data_cleaning", "node": "apply_sampling"},
        ).json())

    if "constructor" not in state:
        state["constructor"] = {}
    constructor = state["constructor"]
    plan = constructor.get("cleaning_sampling_plan") or {}
    plan_list = list(plan.items())
    total_plan = len(plan_list)
    presample_seed = _parse_cleaning_random_seed(constructor)
    presample_rng = random.Random(presample_seed) if presample_seed is not None else None
    if presample_seed is not None:
        logger.info(
            f"apply_sampling_node: 使用固定随机流蓄水池采样 cleaning_random_seed={presample_seed}"
        )
    logger.info(f"apply_sampling_node: 计划内 {total_plan} 个 jsonl 路径")

    for idx, (fp, quota) in enumerate(plan_list, start=1):
        if not fp or not os.path.isfile(fp):
            logger.warning(
                f"apply_sampling_node [{idx}/{total_plan}] skip missing file {fp!r}"
            )
            continue
        base = os.path.basename(fp)
        mode = "不截断(全量复制非空行)" if quota < 0 else f"蓄水池采样 quota={quota}"
        logger.info(f"apply_sampling_node [{idx}/{total_plan}] 开始 {base} | {mode}")
        tmp = fp + ".tmp.loopai_sample"
        try:
            if quota < 0:
                with open(fp, "r", encoding="utf-8") as fin, open(tmp, "w", encoding="utf-8") as fout:
                    kept = 0
                    for line in fin:
                        if line.strip():
                            fout.write(line.rstrip("\n") + "\n")
                            kept += 1
            else:
                sampled = _reservoir_sample_non_empty_lines(fp, quota, rng=presample_rng)
                with open(tmp, "w", encoding="utf-8") as fout:
                    for line in sampled:
                        fout.write(line + "\n")
                kept = len(sampled)
            os.replace(tmp, fp)
            logger.info(
                f"apply_sampling_node [{idx}/{total_plan}] 完成 {base} 写出非空行={kept}"
            )
        except Exception as e:
            logger.error(f"apply_sampling_node failed for {fp}: {e}", exc_info=True)
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    constructor["cleaning_presampled"] = True

    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 采样完成",
            progress=0.15,
            data={"phase": "data_cleaning", "node": "apply_sampling"},
        ).json())
    return state


def cleaning_route_after_sample(state: LoopAIState) -> str:
    """SFT → sharegpt_rewrite_node；PT → basic_filter_node。"""
    cat = (state.get("constructor") or {}).get("category", "PT")
    cat_u = str(cat).upper()
    if cat_u == "SFT":
        return "sharegpt_rewrite_node"
    return "basic_filter_node"


def _run_sharegpt_rewrite_file(
    fp: str,
    tmp: str,
    stats: Dict[str, Any],
    *,
    params: OpenAIChatParams,
    bench_pool: List[str],
    bench_rng: Optional[random.Random],
    user_q: str,
    system_prompt: str,
    task_template: str,
    target_schema: str,
    max_retries: int,
    no_benchmark: bool,
    concurrency: int,
    max_raw_chars: int,
    llm_timeout_seconds: float,
) -> None:
    """
    单文件 ShareGPT 改写：先解析所有行，再对需 LLM 的行按批 asyncio 并发（每批 concurrency 条，每条仍一次请求）。
    单次 LLM 调用超时为 llm_timeout_seconds（与 constructor.llm_timeout 一致）。
    bench_rng：非 None 时用其抽取 benchmark 示例；否则用全局 random。
    """
    choice_rnd = bench_rng if bench_rng is not None else random
    rows: List[Dict[str, Any]] = []
    with open(fp, "r", encoding="utf-8") as fin:
        for line in fin:
            raw_line = line.strip()
            if not raw_line:
                continue
            stats["lines_in"] += 1
            if no_benchmark or not system_prompt or not task_template:
                rows.append({"t": "skip", "raw": raw_line})
                stats["lines_skip"] += 1
                continue
            try:
                orig = json.loads(raw_line)
            except json.JSONDecodeError:
                rows.append({"t": "bad"})
                stats["lines_fail"] += 1
                continue
            if not isinstance(orig, dict):
                rows.append({"t": "bad"})
                stats["lines_fail"] += 1
                continue
            bench_ex = choice_rnd.choice(bench_pool)
            human = _fill_sharegpt_task_prompt(
                task_template,
                user_query=user_q or "Not provided",
                benchmark_example=bench_ex,
                target_schema=target_schema,
                raw_record=truncate_json_for_llm_prompt(orig, max_raw_chars),
            )
            rows.append({"t": "llm", "human": human})

    llm_humans = [r["human"] for r in rows if r["t"] == "llm"]
    total_llm = len(llm_humans)
    base = os.path.basename(fp)

    async def _run_batches() -> List[Optional[Dict[str, Any]]]:
        out: List[Optional[Dict[str, Any]]] = []
        if not llm_humans:
            return out
        bs = max(1, concurrency)
        num_batches = (total_llm + bs - 1) // bs
        for bi in range(num_batches):
            start = bi * bs
            end = min(start + bs, total_llm)
            chunk = llm_humans[start:end]
            logger.info(
                f"sharegpt_rewrite [{base}] 进度 batch {bi + 1}/{num_batches} "
                f"并发={len(chunk)} 本条范围 {start + 1}-{end}/{total_llm}"
            )
            batch_results = await asyncio.gather(
                *[
                    _sharegpt_rewrite_one_async(
                        params,
                        system_prompt,
                        h,
                        max_retries,
                        llm_timeout_seconds,
                    )
                    for h in chunk
                ],
                return_exceptions=True,
            )
            for j, r in enumerate(batch_results):
                if isinstance(r, Exception):
                    logger.warning(
                        f"sharegpt_rewrite [{base}] batch {bi + 1} item {start + j + 1}/{total_llm} exc: {r}"
                    )
                    out.append(None)
                else:
                    out.append(r)
        return out

    llm_results: List[Optional[Dict[str, Any]]] = (
        asyncio.run(_run_batches()) if llm_humans else []
    )
    res_iter = iter(llm_results)

    with open(tmp, "w", encoding="utf-8") as fout:
        for r in rows:
            if r["t"] == "skip":
                fout.write(r["raw"] + "\n")
            elif r["t"] == "bad":
                continue
            else:
                out_obj = next(res_iter)
                if out_obj:
                    fout.write(json.dumps(out_obj, ensure_ascii=False) + "\n")
                    stats["lines_ok"] += 1
                else:
                    stats["lines_fail"] += 1


def sharegpt_rewrite_node(state: LoopAIState) -> LoopAIState:
    """第二步（SFT）：随机 benchmark + user_query，将每条样本改写为含 messages（system/user/assistant）的 JSONL；LLM 段按批并发（默认每批 64 条）。"""
    logger.info("=== Cleaning Subgraph: sharegpt_rewrite_node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - ShareGPT 改写",
            progress=0.18,
            data={"phase": "data_cleaning", "node": "sharegpt_rewrite"},
        ).json())

    if "constructor" not in state:
        state["constructor"] = {}
    constructor = state["constructor"]
    data_path = constructor.get("intermediate_data_path", "")
    files = _enumerate_intermediate_jsonl_files(data_path)
    bench_pool = _collect_benchmark_example_strings(state)
    user_q = _user_query_for_rewrite(state)
    max_retries = int(constructor.get("max_retries", 3) or 3)
    sharegpt_concurrency = int(
        constructor.get("sharegpt_rewrite_concurrency", SHAREGPT_REWRITE_CONCURRENCY_DEFAULT)
        or SHAREGPT_REWRITE_CONCURRENCY_DEFAULT
    )
    sharegpt_concurrency = max(1, sharegpt_concurrency)

    llm_timeout = float(
        constructor.get("llm_timeout", DEFAULT_LLM_TIMEOUT_SECONDS)
        or DEFAULT_LLM_TIMEOUT_SECONDS
    )
    if llm_timeout <= 0:
        llm_timeout = DEFAULT_LLM_TIMEOUT_SECONDS

    stats: Dict[str, Any] = {
        "files": 0,
        "lines_in": 0,
        "lines_ok": 0,
        "lines_skip": 0,
        "lines_fail": 0,
        "no_benchmark": False,
        "pre_backup_root": "",
        "pre_backup_files": [],
    }
    if not bench_pool:
        stats["no_benchmark"] = True
        logger.warning("sharegpt_rewrite_node: no benchmark examples, skipping rewrite")

    params = None
    if not stats["no_benchmark"]:
        model_name = constructor.get("model_path") or state.get("analyze_model_path")
        base_url = constructor.get("base_url") or state.get("analyze_base_url")
        api_key = constructor.get("api_key") or state.get("analyze_api_key")
        temperature = constructor.get("temperature", 0.7)
        top_p = constructor.get("top_p", 0.95)
        max_completion_tokens = constructor.get("max_completion_tokens", 4096)

        if model_name and base_url and api_key:
            params = OpenAIChatParams(
                model=model_name,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                top_p=top_p,
                max_completion_tokens=max_completion_tokens,
            )
        else:
            logger.warning("sharegpt_rewrite_node: no LLM config, skipping rewrite")
            stats["no_benchmark"] = True

    try:
        system_prompt, task_template = _load_prompt_pair(state, "sharegpt_rewrite_prompt")
    except Exception as e:
        logger.error(f"sharegpt_rewrite_node: missing prompt sharegpt_rewrite_prompt: {e}")
        system_prompt = ""
        task_template = ""

    target_schema = (
        "单条 JSON：顶层含非空 \"messages\" 数组；每项仅允许 "
        "{\"role\": \"system\"|\"user\"|\"assistant\", \"content\": string（非空）, "
        "可选 \"loss_mask\": boolean}。"
        "必须至少各含一条 role 为 system、user、assistant 的消息，且每条 content 非空。"
        "建议顺序：system → user → assistant。若 benchmark 为其它格式，应映射到此 schema。"
        "风格须贴近 benchmark 示例，并保持原始记录语义。"
    )

    save_pre_backup = _coerce_bool(constructor.get("sharegpt_rewrite_pre_backup"), True)
    backup_root_cfg = (constructor.get("sharegpt_rewrite_pre_backup_dir") or "").strip()
    backup_root = ""
    if save_pre_backup:
        backup_root = backup_root_cfg or _default_sharegpt_pre_backup_root(data_path)
    if save_pre_backup and backup_root:
        stats["pre_backup_root"] = backup_root
    elif save_pre_backup and not backup_root:
        logger.warning(
            "sharegpt_rewrite_node: sharegpt_rewrite_pre_backup 已开启但无法解析备份目录 "
            "(请设置 constructor.sharegpt_rewrite_pre_backup_dir 或有效的 intermediate_data_path)"
        )

    max_raw_chars = rewrite_record_prompt_json_max_chars(constructor)
    logger.info(
        f"sharegpt_rewrite_node: concurrency={sharegpt_concurrency} "
        f"(constructor.sharegpt_rewrite_concurrency 可覆盖), "
        f"llm_timeout_s={llm_timeout} (constructor.llm_timeout), "
        f"max_raw_json_chars={max_raw_chars} "
        f"(sharegpt_rewrite_max_raw_chars / CONSTRUCTOR_SHAREGPT_REWRITE_MAX_RAW_CHARS)"
    )

    cr_seed = _parse_cleaning_random_seed(constructor)
    bench_rng: Optional[random.Random] = None
    if cr_seed is not None:
        # 与蓄水池采样分流，避免同一随机序列
        bench_rng = random.Random(cr_seed ^ 0x9E3779B97F4A7C15)
        logger.info(
            f"sharegpt_rewrite_node: benchmark 抽取使用固定随机流（源自 cleaning_random_seed={cr_seed}）"
        )

    for fp in files:
        if not os.path.isfile(fp):
            continue
        stats["files"] += 1
        tmp = fp + ".tmp.loopai_sharegpt"
        prev_in = stats["lines_in"]
        prev_ok = stats["lines_ok"]
        prev_skip = stats["lines_skip"]
        prev_fail = stats["lines_fail"]
        try:
            if save_pre_backup and backup_root:
                os.makedirs(backup_root, exist_ok=True)
                dest = os.path.join(backup_root, os.path.basename(fp))
                ap_fp, ap_dest = os.path.abspath(fp), os.path.abspath(dest)
                if ap_fp != ap_dest:
                    shutil.copy2(fp, dest)
                    stats["pre_backup_files"].append(dest)
                    logger.info(
                        f"sharegpt_rewrite_node: pre-rewrite backup {os.path.basename(fp)} -> {dest}"
                    )
                else:
                    logger.warning(
                        f"sharegpt_rewrite_node: skip backup (destination equals source): {fp}"
                    )
            _run_sharegpt_rewrite_file(
                fp,
                tmp,
                stats,
                params=params,
                bench_pool=bench_pool,
                bench_rng=bench_rng,
                user_q=user_q,
                system_prompt=system_prompt,
                task_template=task_template,
                target_schema=target_schema,
                max_retries=max_retries,
                no_benchmark=bool(stats["no_benchmark"]),
                concurrency=sharegpt_concurrency,
                max_raw_chars=max_raw_chars,
                llm_timeout_seconds=llm_timeout,
            )
            os.replace(tmp, fp)
            logger.info(
                f"sharegpt_rewrite_node: done file={os.path.basename(fp)} "
                f"本文件 lines_in={stats['lines_in'] - prev_in} ok={stats['lines_ok'] - prev_ok} "
                f"skip={stats['lines_skip'] - prev_skip} fail={stats['lines_fail'] - prev_fail}"
            )
        except Exception as e:
            logger.error(f"sharegpt_rewrite_node failed for {fp}: {e}", exc_info=True)
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    constructor["cleaning_sharegpt_rewrite"] = stats
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - ShareGPT 改写完成",
            progress=0.22,
            data={"phase": "data_cleaning", "node": "sharegpt_rewrite", **stats},
        ).json())
    return state


def basic_filter_node(state: LoopAIState) -> LoopAIState:
    """仅执行 basic_data_flitter，结果写入 cleaning_results。"""
    logger.info("=== Cleaning Subgraph: basic_filter_node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 基础过滤",
            progress=0.25,
            data={"phase": "data_cleaning", "node": "basic_filter"},
        ).json())

    if "constructor" not in state:
        state["constructor"] = {}
    constructor = state["constructor"]
    current_path = constructor.get("intermediate_data_path", "")
    if not current_path or not os.path.exists(current_path):
        logger.warning("basic_filter_node: no intermediate_data_path")
        return state

    result = basic_data_flitter(current_path, state)
    new_path = result.cleaned_data_path or current_path
    constructor["intermediate_data_path"] = new_path

    if hasattr(result, "model_dump"):
        ser = result.model_dump()
    elif hasattr(result, "dict"):
        ser = result.dict()
    else:
        ser = {
            "cleaned_data_path": getattr(result, "cleaned_data_path", ""),
            "total_records": getattr(result, "total_records", 0),
            "valid_records": getattr(result, "valid_records", 0),
            "invalid_records": getattr(result, "invalid_records", 0),
            "success": getattr(result, "success", True),
            "error_message": getattr(result, "error_message", ""),
        }

    constructor["cleaning_results"] = {
        "tools_executed": [{"tool": "basic_data_flitter", "result": ser}],
        "tools_failed": [],
        "final_data_path": new_path,
        "sharegpt_preflight": constructor.get("cleaning_sharegpt_rewrite"),
    }

    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 基础过滤完成",
            progress=0.3,
            data={"phase": "data_cleaning", "node": "basic_filter", "valid": ser.get("valid_records", 0)},
        ).json())
    return state


def planner_node(state: LoopAIState) -> LoopAIState:
    """
    规划节点：在 basic_data_flitter 之后，仅规划领域工具（不含 basic_data_flitter）。
    """
    logger.info("=== Cleaning Subgraph: Planner Node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 规划领域清洗工具",
            progress=0.0,
            data={"phase": "data_cleaning", "node": "planner"},
        ).json())

    try:
        tool_plan: List[str] = []

        constructor_state = state.get("constructor", {})
        intermediate_data_path = constructor_state.get("intermediate_data_path", "")
        if not intermediate_data_path:
            logger.warning("No intermediate data path found, skipping domain tool planning")
            if "constructor" not in state:
                state["constructor"] = {}
            state["constructor"]["cleaning_tool_plan"] = ["normal_data"]
            return state

        if not os.path.exists(intermediate_data_path):
            logger.warning(f"Intermediate data path does not exist: {intermediate_data_path}")
            if "constructor" not in state:
                state["constructor"] = {}
            state["constructor"]["cleaning_tool_plan"] = ["normal_data"]
            return state
        
        # 获取 user_query 和 category（从 state.constructor 中获取）
        user_query = _extract_user_query(state)
        category = constructor_state.get("category", "PT").upper()
        
        logger.info(f"Planning tools for data path: {intermediate_data_path}")
        logger.info(f"User query: {user_query[:100] if user_query else 'N/A'}...")
        logger.info(f"Category: {category}")
        
        # 3. 基于 user_query 和 datasets_background 使用LLM判断领域工具
        # 从 state.constructor 中获取 user_query 和 datasets_background（constructor_state 已在上面获取）
        user_query_from_state = constructor_state.get("user_query", "")
        datasets_background = constructor_state.get("datasets_background", "")
        
        # 如果 user_query 为空，尝试从其他地方获取
        if not user_query_from_state:
            user_query_from_state = user_query
        
        logger.info(f"User query from state: {user_query_from_state[:100] if user_query_from_state else 'N/A'}...")
        logger.info(f"Datasets background: {datasets_background[:100] if datasets_background else 'N/A'}...")
        
        # 使用LLM判断应该使用哪个领域工具
        domain_tools = []
        
        try:
            # 从 state 创建 LLM 参数
            model_name = constructor_state.get("model_path") or state.get("analyze_model_path")
            base_url = constructor_state.get("base_url") or state.get("analyze_base_url")
            api_key = constructor_state.get("api_key") or state.get("analyze_api_key")
            temperature = constructor_state.get("temperature", 0.7)
            top_p = constructor_state.get("top_p", 0.95)
            max_completion_tokens = constructor_state.get("max_completion_tokens", 4096)

            if model_name and base_url and api_key:
                params = OpenAIChatParams(
                    model=model_name,
                    base_url=base_url,
                    api_key=api_key,
                    temperature=temperature,
                    top_p=top_p,
                    max_completion_tokens=max_completion_tokens,
                )

                system_prompt, task_prompt = _load_prompt_pair(state, "domain_tool_planner_prompt")

                # 构建用户提示词，包含用户需求和数据集背景
                user_prompt = task_prompt.format(
                    user_query=user_query_from_state if user_query_from_state else "Not provided",
                    datasets_background=datasets_background if datasets_background else "Not provided"
                )

                # 构建消息
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt)
                ]

                # 调用LLM（同步调用）
                response = chat_completion_sync(params, messages, timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS)
                response_text = response.content.strip()

                logger.debug(f"LLM response for domain tool planning: {response_text}")

                # 解析JSON响应
                domain_tools = _parse_json_list(response_text)

                # 验证工具名称是否有效
                valid_domain_tools = [
                    tool for tool in domain_tools
                    if tool in ["text2sql", "code_generate", "normal_data", "norma_filter_and_add_cot"]
                ]

                if constructor_state.get("append_cot_after_cleaning"):
                    valid_domain_tools = [
                        t for t in valid_domain_tools if t != "norma_filter_and_add_cot"
                    ]

                if valid_domain_tools:
                    tool_plan.extend(valid_domain_tools)
                    logger.info(f"LLM suggested domain tools based on user_query and datasets_background: {valid_domain_tools}")
                else:
                    tool_plan.append("normal_data")
                    logger.info("LLM did not suggest any valid domain tools, using default normal_data")
            else:
                logger.warning("Missing LLM configuration, using default normal_data tool")
                tool_plan.append("normal_data")

        except Exception as e:
            logger.error(f"Error calling LLM for domain tool planning: {e}", exc_info=True)
            tool_plan.append("normal_data")
            logger.info("Using default normal_data tool due to LLM error")
        
        # 4. 更新State（确保 tool_plan 被正确设置到 state.constructor 中）
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["cleaning_tool_plan"] = tool_plan
        logger.info(f"Final tool_plan: {tool_plan}")
        logger.debug(f"State after planner_node: cleaning_tool_plan = {state.get('constructor', {}).get('cleaning_tool_plan')}")
        
    except Exception as e:
        logger.error(f"Error in planner_node: {e}", exc_info=True)
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["cleaning_tool_plan"] = ["normal_data"]
        state["exception"] = f"Error in planner_node: {str(e)}"
    
    logger.info("=== Cleaning Subgraph: Planner Node Completed ===")
    if writer:
        tool_plan = state.get("constructor", {}).get("cleaning_tool_plan", [])
        writer(StreamEvent(
            current=current,
            message=f"Constructor: 数据清洗 - 规划完成，将执行 {len(tool_plan)} 个工具",
            progress=0.2,
            data={"phase": "data_cleaning", "node": "planner", "tool_plan": tool_plan},
        ).json())
    logger.debug(f"Returning state with cleaning_tool_plan: {state.get('constructor', {}).get('cleaning_tool_plan')}")
    return state


def process_node(state: LoopAIState) -> LoopAIState:
    """
    执行节点：按顺序执行工具计划
    
    逻辑：
    1. 获取 tool_plan
    2. 遍历执行每个工具
    3. 更新数据路径（链式调用）
    4. 更新State
    """
    logger.info("=== Cleaning Subgraph: Process Node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()

    try:
        # 1. 获取 tool_plan（从 state.constructor 中获取）
        constructor_state = state.get("constructor", {})
        tool_plan = constructor_state.get("cleaning_tool_plan", [])
        logger.debug(f"Process node received state with cleaning_tool_plan: {tool_plan}")
        logger.debug(f"Full state keys: {list(state.keys())}")
        logger.debug(f"Constructor state keys: {list(constructor_state.keys())}")
        
        if not tool_plan:
            logger.warning("No tool_plan found, skipping process node")
            logger.warning(f"Available state keys: {list(state.keys())}")
            logger.warning(f"Constructor state: {constructor_state}")
            return state
        
        logger.info(f"Executing tool_plan: {tool_plan}")
        
        # 2. 获取数据路径（从 state.constructor 中获取）
        constructor_state = state.get("constructor", {})
        current_data_path = constructor_state.get("intermediate_data_path", "")
        if not current_data_path:
            logger.warning("No intermediate data path found, skipping tool execution")
            return state
        
        if not os.path.exists(current_data_path):
            logger.warning(f"Data path does not exist: {current_data_path}")
            return state
        
        # 3. 遍历执行工具（仅领域工具；basic 已在 basic_filter_node 执行）
        prior = constructor_state.get("cleaning_results") or {}
        cleaning_results = copy.deepcopy(prior) if prior else {
            "tools_executed": [],
            "tools_failed": [],
            "final_data_path": current_data_path,
        }
        cleaning_results.setdefault("tools_executed", [])
        cleaning_results.setdefault("tools_failed", [])
        cleaning_results["final_data_path"] = current_data_path

        total_tools = len(tool_plan)
        if writer:
            writer(StreamEvent(
                current=current,
                message=f"Constructor: 数据清洗 - 执行清洗工具（共 {total_tools} 步）",
                progress=0.2,
                progress_num=1,
                total=max(total_tools, 1),
                data={
                    "phase": "data_cleaning",
                    "node": "process",
                    "tool_plan": list(tool_plan),
                    "total_tools": total_tools,
                },
            ).json())

        for tool_idx, tool_name in enumerate(tool_plan):
            try:
                logger.info(f"Executing tool: {tool_name}")
                
                # 从TOOL_MAP获取工具函数
                if tool_name not in TOOL_MAP:
                    logger.warning(f"Unknown tool: {tool_name}, skipping")
                    cleaning_results["tools_failed"].append({
                        "tool": tool_name,
                        "error": "Tool not found in TOOL_MAP"
                    })
                    continue

                if tool_name == "basic_data_flitter":
                    logger.warning(
                        "process_node: basic_data_flitter in plan is ignored (already ran in basic_filter_node)"
                    )
                    continue

                tool_func = TOOL_MAP[tool_name]

                # 调用工具函数（同步调用）
                result = tool_func(current_data_path, state)

                # 检查工具是否执行成功（通过 success 字段或检查是否有错误）
                tool_success = getattr(result, 'success', True)  # 向后兼容：默认为 True
                error_message = getattr(result, 'error_message', '')

                if not tool_success:
                    logger.warning(f"Tool {tool_name} failed: {error_message}")
                    cleaning_results["tools_failed"].append({
                        "tool": tool_name,
                        "error": error_message or "Tool returned success=False"
                    })
                    # 工具失败时不更新路径，继续执行下一个工具
                    continue

                # 更新数据路径（链式调用）
                new_data_path = result.cleaned_data_path or current_data_path
                if new_data_path != current_data_path:
                    logger.info(f"Tool {tool_name} updated data path: {current_data_path} -> {new_data_path}")
                    current_data_path = new_data_path

                # 记录执行结果
                if hasattr(result, "model_dump"):
                    serializable_result = result.model_dump()
                elif hasattr(result, "dict"):
                    serializable_result = result.dict()
                else:
                    serializable_result = {
                        "cleaned_data_path": getattr(result, "cleaned_data_path", ""),
                        "total_records": getattr(result, "total_records", 0),
                        "valid_records": getattr(result, "valid_records", 0),
                        "invalid_records": getattr(result, "invalid_records", 0),
                        "success": getattr(result, "success", True),
                        "error_message": getattr(result, "error_message", ""),
                    }
                cleaning_results["tools_executed"].append({
                    "tool": tool_name,
                    "result": serializable_result
                })
                
                logger.info(f"Tool {tool_name} completed successfully")
                
            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                cleaning_results["tools_failed"].append({
                    "tool": tool_name,
                    "error": str(e)
                })
                # 继续执行下一个工具，不中断流程
        
        # 4. 更新State（更新 state.constructor 中的数据路径和清洗结果）
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["intermediate_data_path"] = current_data_path
        state["constructor"]["cleaning_results"] = cleaning_results
        
        logger.info(f"Process node completed. Final data path: {current_data_path}")
        logger.info(f"Tools executed: {len(cleaning_results['tools_executed'])}, "
                   f"Tools failed: {len(cleaning_results['tools_failed'])}")
        if writer:
            writer(StreamEvent(
                current=current,
                message=f"Constructor: 数据清洗 - 执行完成，共 {len(cleaning_results['tools_executed'])} 个工具",
                progress=0.75,
                data={
                    "phase": "data_cleaning",
                    "node": "process",
                    "tools_executed": len(cleaning_results["tools_executed"]),
                    "tools_failed": len(cleaning_results["tools_failed"]),
                    "tool_plan": list(tool_plan),
                },
            ).json())

    except Exception as e:
        logger.error(f"Error in process_node: {e}", exc_info=True)
        state["exception"] = f"Error in process_node: {str(e)}"

    logger.info("=== Cleaning Subgraph: Process Node Completed ===")
    return state


def benchmark_cleaner_node(state: LoopAIState) -> LoopAIState:
    """
    Benchmark 清洗节点：移除与 benchmark 数据集相似/重复的记录
    
    此节点在领域工具清洗之后执行，根据 state.banckmark_jsonl_path 指定的
    benchmark 数据集，从清洗后的数据中移除相似的记录，防止测试数据泄露。
    
    逻辑：
    1. 检查 banckmark_jsonl_path 是否存在
    2. 如果存在，调用 benchmark_data_cleaner 工具执行清洗
    3. 更新数据路径和清洗结果
    """
    logger.info("=== Cleaning Subgraph: Benchmark Cleaner Node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - Benchmark 去重",
            progress=0.8,
            data={"phase": "data_cleaning", "node": "benchmark_cleaner"},
        ).json())

    try:
        # 1. 检查是否配置了 benchmark 路径
        benchmark_path = state.get("banckmark_jsonl_path", "")
        if not benchmark_path:
            logger.info("No benchmark_jsonl_path configured, skipping benchmark cleaning")
            if writer:
                writer(StreamEvent(
                    current=current,
                    message="Constructor: 数据清洗 - 未配置 Benchmark，跳过",
                    progress=1.0,
                    data={"phase": "data_cleaning", "node": "benchmark_cleaner", "skipped": True},
                ).json())
            logger.info("=== Cleaning Subgraph: Benchmark Cleaner Node Completed (skipped, no path) ===")
            return state

        import os
        if not os.path.exists(benchmark_path):
            logger.warning(f"Benchmark path does not exist: {benchmark_path}, skipping benchmark cleaning")
            return state
        
        # 2. 获取当前数据路径（从 state.constructor 中获取）
        constructor_state = state.get("constructor", {})
        current_data_path = constructor_state.get("intermediate_data_path", "")
        if not current_data_path:
            logger.warning("No intermediate data path found, skipping benchmark cleaning")
            return state
        
        if not os.path.exists(current_data_path):
            logger.warning(f"Data path does not exist: {current_data_path}, skipping benchmark cleaning")
            return state
        
        logger.info(f"Executing benchmark cleaner on: {current_data_path}")
        logger.info(f"Using benchmark file: {benchmark_path}")
        benchmark_samples_path = constructor_state.get("benchmark_samples_path", "")
        if benchmark_samples_path:
            logger.info(f"Using benchmark sampled reference: {benchmark_samples_path}")
        
        # 3. 调用 benchmark_data_cleaner 工具
        result = benchmark_data_cleaner(current_data_path, state)
        
        # 4. 检查工具执行结果
        tool_success = getattr(result, 'success', True)
        error_message = getattr(result, 'error_message', '')
        
        if not tool_success:
            logger.warning(f"Benchmark cleaner failed: {error_message}")
            # 工具失败时，记录错误但不中断流程
            if "constructor" not in state:
                state["constructor"] = {}
            cleaning_results = state["constructor"].get("cleaning_results", {})
            if "tools_failed" not in cleaning_results:
                cleaning_results["tools_failed"] = []
            cleaning_results["tools_failed"].append({
                "tool": "benchmark_cleaner",
                "error": error_message or "Tool returned success=False"
            })
            state["constructor"]["cleaning_results"] = cleaning_results
            return state
        
        # 5. 更新数据路径（链式调用）
        new_data_path = result.cleaned_data_path or current_data_path
        if new_data_path != current_data_path:
            logger.info(f"Benchmark cleaner updated data path: {current_data_path} -> {new_data_path}")
        
        # 6. 更新 State
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["intermediate_data_path"] = new_data_path
        
        # 更新清洗结果统计
        cleaning_results = state["constructor"].get("cleaning_results", {})
        if "tools_executed" not in cleaning_results:
            cleaning_results["tools_executed"] = []
        cleaning_results["tools_executed"].append({
            "tool": "benchmark_cleaner",
            "result": {
                "cleaned_data_path": result.cleaned_data_path,
                "total_records": result.total_records,
                "valid_records": result.valid_records,
                "invalid_records": result.invalid_records
            }
        })
        cleaning_results["final_data_path"] = new_data_path
        
        # 记录 benchmark 清洗结果
        cleaning_results["benchmark_cleaning"] = {
            "benchmark_path": benchmark_path,
            "benchmark_samples_path": benchmark_samples_path,
            "records_removed": result.invalid_records,
            "records_kept": result.valid_records
        }
        
        state["constructor"]["cleaning_results"] = cleaning_results
        
        logger.info(f"Benchmark cleaner completed - "
                   f"total: {result.total_records}, "
                   f"kept: {result.valid_records}, "
                   f"removed: {result.invalid_records}")
        if writer:
            writer(StreamEvent(
                current=current,
                message=f"Constructor: 数据清洗 - Benchmark 完成，保留 {result.valid_records} 条",
                progress=1.0,
                data={
                    "phase": "data_cleaning",
                    "node": "benchmark_cleaner",
                    "valid_records": result.valid_records,
                    "invalid_records": result.invalid_records,
                },
            ).json())

    except Exception as e:
        logger.error(f"Error in benchmark_cleaner_node: {e}", exc_info=True)
        # 发生错误时，记录异常但不中断流程
        if "constructor" not in state:
            state["constructor"] = {}
        cleaning_results = state["constructor"].get("cleaning_results", {})
        if "tools_failed" not in cleaning_results:
            cleaning_results["tools_failed"] = []
        cleaning_results["tools_failed"].append({
            "tool": "benchmark_cleaner",
            "error": str(e)
        })
        state["constructor"]["cleaning_results"] = cleaning_results
        if writer:
            writer(StreamEvent(
                current=current,
                message="Constructor: 数据清洗 - Benchmark 节点结束",
                progress=1.0,
                data={"phase": "data_cleaning", "node": "benchmark_cleaner", "error": str(e)},
            ).json())

    logger.info("=== Cleaning Subgraph: Benchmark Cleaner Node Completed ===")
    return state


def cot_finalize_node(state: LoopAIState) -> LoopAIState:
    """
    Benchmark 之后的末尾 CoT 步骤：当 constructor.append_cot_after_cleaning 为真时，
    调用 norma_filter_and_add_cot（与规划中的领域工具互斥，由 planner 剔除同名工具）。
    """
    logger.info("=== Cleaning Subgraph: CoT finalize Node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()

    constructor_state = state.get("constructor", {}) or {}
    if not constructor_state.get("append_cot_after_cleaning"):
        logger.debug("cot_finalize_node: append_cot_after_cleaning disabled, skipping")
        return state

    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 末尾 CoT 清洗",
            progress=0.9,
            data={"phase": "data_cleaning", "node": "cot_finalize"},
        ).json())

    try:
        current_data_path = constructor_state.get("intermediate_data_path", "")
        if not current_data_path:
            logger.warning("cot_finalize_node: no intermediate_data_path, skipping")
            return state
        if not os.path.exists(current_data_path):
            logger.warning(
                f"cot_finalize_node: intermediate_data_path missing on disk, skipping: {current_data_path}"
            )
            return state

        logger.info(f"cot_finalize_node: running norma_filter_and_add_cot on {current_data_path}")
        result = norma_filter_and_add_cot(current_data_path, state)

        tool_success = getattr(result, "success", True)
        error_message = getattr(result, "error_message", "")

        if "constructor" not in state:
            state["constructor"] = {}
        cleaning_results = state["constructor"].get("cleaning_results") or {}
        cleaning_results = copy.deepcopy(cleaning_results) if cleaning_results else {
            "tools_executed": [],
            "tools_failed": [],
            "final_data_path": current_data_path,
        }
        cleaning_results.setdefault("tools_executed", [])
        cleaning_results.setdefault("tools_failed", [])

        if not tool_success:
            logger.warning(f"cot_finalize_node: norma_filter_and_add_cot failed: {error_message}")
            cleaning_results["tools_failed"].append({
                "tool": "norma_filter_and_add_cot",
                "error": error_message or "Tool returned success=False",
            })
            state["constructor"]["cleaning_results"] = cleaning_results
            return state

        new_data_path = result.cleaned_data_path or current_data_path
        state["constructor"]["intermediate_data_path"] = new_data_path

        if hasattr(result, "model_dump"):
            serializable_result = result.model_dump()
        elif hasattr(result, "dict"):
            serializable_result = result.dict()
        else:
            serializable_result = {
                "cleaned_data_path": getattr(result, "cleaned_data_path", ""),
                "total_records": getattr(result, "total_records", 0),
                "valid_records": getattr(result, "valid_records", 0),
                "invalid_records": getattr(result, "invalid_records", 0),
                "success": getattr(result, "success", True),
                "error_message": getattr(result, "error_message", ""),
            }
        cleaning_results["tools_executed"].append({
            "tool": "norma_filter_and_add_cot",
            "result": serializable_result,
        })
        cleaning_results["final_data_path"] = new_data_path
        state["constructor"]["cleaning_results"] = cleaning_results

        logger.info(
            f"cot_finalize_node: done valid={getattr(result, 'valid_records', 0)} "
            f"invalid={getattr(result, 'invalid_records', 0)}"
        )
        if writer:
            writer(StreamEvent(
                current=current,
                message="Constructor: 数据清洗 - 末尾 CoT 完成",
                progress=1.0,
                data={
                    "phase": "data_cleaning",
                    "node": "cot_finalize",
                    "valid_records": getattr(result, "valid_records", 0),
                },
            ).json())

    except Exception as e:
        logger.error(f"Error in cot_finalize_node: {e}", exc_info=True)
        if "constructor" not in state:
            state["constructor"] = {}
        cleaning_results = state["constructor"].get("cleaning_results") or {}
        if "tools_failed" not in cleaning_results:
            cleaning_results["tools_failed"] = []
        cleaning_results["tools_failed"].append({"tool": "norma_filter_and_add_cot", "error": str(e)})
        state["constructor"]["cleaning_results"] = cleaning_results

    logger.info("=== Cleaning Subgraph: CoT finalize Node Completed ===")
    return state


class CleaningSubgraph(BaseAgent):
    """
    数据清洗子图类
    
    继承 BaseAgent 以使用统一的 LLM 创建和 prompt 加载机制
    管理数据清洗的完整子图流程
    """
    
    @property
    def role_name(self) -> str:
        """Role name"""
        return "CleaningSubgraph"
    
    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"
    
    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "domain_tool_planner_prompt"
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = 'empty',
        temperature: float = 0.7,
        top_p: float = 0.95,
        max_completion_tokens: int = 4096,
        prompt_template_dir: Optional[str] = None,
        checkpointer: Optional[BaseCheckpointSaver] = None,
        store: Optional[BaseStore] = None
    ):
        """
        初始化 CleaningSubgraph
        
        Args:
            model_name: 模型名称
            base_url: LLM 服务器地址
            api_key: API 密钥
            temperature: 温度参数
            top_p: top_p 参数
            max_completion_tokens: 最大生成 token 数
            prompt_template_dir: prompt 模板目录
            checkpointer: 检查点保存器
            store: 状态存储
        """
        # 初始化 BaseAgent（不自动创建 LLM，因为我们需要从 state 动态获取配置）
        super().__init__(
            tools=[],
            model_name=None,  # 不在这里创建 LLM，从 state 动态获取
            base_url=None,
            api_key='empty',
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion_tokens,
            prompt_template_dir=prompt_template_dir,
            checkpointer=checkpointer,
            store=store
        )
        self.checkpointer = checkpointer
        self.store = store
        self.graph = None
    
    def create_llm_from_state(self, state: LoopAIState) -> Optional[OpenAIChatParams]:
        """
        从 state 中获取配置并创建 LLM 参数

        Args:
            state: 当前状态

        Returns:
            OpenAIChatParams 实例，如果配置不完整则返回 None
        """
        # 从 state.constructor 中获取配置
        constructor_state = state.get("constructor", {})
        model_name = constructor_state.get("model_path") or state.get("analyze_model_path")
        base_url = constructor_state.get("base_url") or state.get("analyze_base_url")
        api_key = constructor_state.get("api_key") or state.get("analyze_api_key")
        temperature = constructor_state.get("temperature", self.temperature)
        top_p = constructor_state.get("top_p", self.top_p)
        max_completion_tokens = constructor_state.get("max_completion_tokens", self.max_completion_tokens)

        if not (model_name and base_url and api_key):
            return None

        if base_url is None:
            logger.error(f'Undefined base_url in {self.role_name}')
            raise AssertionError(f'Undefined base_url in {self.role_name}')

        params = OpenAIChatParams(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion_tokens,
        )

        return params
    
    def get_prompt(self, prompt_type: str, prompt_name: str) -> str:
        """
        获取 prompt（使用 BaseAgent 的 prompt_loader）
        
        Args:
            prompt_type: prompt 类型
            prompt_name: prompt 名称
            
        Returns:
            prompt 字符串
        """
        return self.prompt_loader(prompt_type, prompt_name)
    
    def init_graph(self):
        """实现 BaseAgent 的抽象方法（不使用）"""
        pass
    
    def __call__(self):
        """实现 BaseAgent 的抽象方法（不使用）"""
        pass
    
    def build(self, **kwargs) -> StateGraph:
        """
        构建并编译清洗子图

        流程：均分配额采样 →（SFT）ShareGPT 改写 → basic → planner（领域）→ process → benchmark →（可选）cot_finalize

        Returns:
            编译后的 StateGraph
        """
        builder = StateGraph(LoopAIState)

        builder.add_node("sampling_plan_node", sampling_plan_node)
        builder.add_node("apply_sampling_node", apply_sampling_node)
        builder.add_node("sharegpt_rewrite_node", sharegpt_rewrite_node)
        builder.add_node("basic_filter_node", basic_filter_node)
        builder.add_node("planner_node", planner_node)
        builder.add_node("process_node", process_node)
        builder.add_node("benchmark_cleaner_node", benchmark_cleaner_node)
        builder.add_node("cot_finalize_node", cot_finalize_node)

        builder.set_entry_point("sampling_plan_node")
        builder.add_edge("sampling_plan_node", "apply_sampling_node")
        builder.add_conditional_edges(
            "apply_sampling_node",
            cleaning_route_after_sample,
            {
                "sharegpt_rewrite_node": "sharegpt_rewrite_node",
                "basic_filter_node": "basic_filter_node",
            },
        )
        builder.add_edge("sharegpt_rewrite_node", "basic_filter_node")
        builder.add_edge("basic_filter_node", "planner_node")
        builder.add_edge("planner_node", "process_node")
        builder.add_edge("process_node", "benchmark_cleaner_node")
        builder.add_edge("benchmark_cleaner_node", "cot_finalize_node")
        builder.set_finish_point("cot_finalize_node")
        
        # 编译图
        compile_kwargs = {}
        if self.checkpointer:
            compile_kwargs["checkpointer"] = self.checkpointer
        if self.store:
            compile_kwargs["store"] = self.store
        compile_kwargs.update(kwargs)
        
        self.graph = builder.compile(**compile_kwargs)
        return self.graph


def create_cleaning_subgraph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    store: Optional[BaseStore] = None,
    **kwargs
) -> StateGraph:
    """
    创建清洗子图的便捷函数
    
    Args:
        checkpointer: 检查点保存器
        store: 状态存储
        **kwargs: 传递给 compile 的其他参数
    
    Returns:
        编译后的 StateGraph
    """
    subgraph = CleaningSubgraph(checkpointer=checkpointer, store=store)
    return subgraph.build(**kwargs)


def filter_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    映射后的过滤节点（用于 mapping_subgraph）
    
    这是一个占位符函数，用于 mapping_subgraph 中的 filter_node。
    实际的清洗逻辑在 CleaningSubgraph 中实现。
    
    Args:
        state: 当前状态
        store: 状态存储（可选）
    
    Returns:
        更新后的状态
    """
    logger.info("=== Mapping Filter Node: Starting ===")
    # 占位符实现：直接返回状态，不做任何处理
    # 如果需要映射后的过滤逻辑，可以在这里实现
    logger.info("=== Mapping Filter Node: Completed ===")
    return state
