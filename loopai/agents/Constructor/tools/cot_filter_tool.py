"""
norma_filter_and_add_cot — 通用数据清洗过滤 + CoT 推理路径生成工具

流程：
  1. Alpagasus 质量过滤（低质量数据丢弃）
  2. CoT 生成（为每条记录补充 reasoning_steps 字段）
  3. CoT 质量过滤（推理步骤质量不达标的记录丢弃）

输入格式：Alpaca 格式（instruction / input / output）或包含 messages 的 SFT 格式。
  - instruction 与 input 字段会被拼接后作为 CoT 生成的 instruction 输入；
    原始 instruction / input 字段在输出中保持不变。

输出：在原始键值基础上新增 reasoning_steps 键（list[{"step": str}]）；
      当 constructor.append_cot_after_cleaning 为真时，另将 output（或 messages 内 assistant
      的 content）改写为 <think>…</think> 包裹的 CoT 文本后接原始 output；否则其它键均不做修改。
      最终结果覆盖写回原文件。

使用时机：仅当用户明确要求为数据集添加推理路径/Chain-of-Thought 时使用。
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import uuid
from typing import Any, Dict, List, Optional, Tuple

from loopai.agents.Constructor.tools.data_filter_tools import BaseCleanResult
from loopai.logger import get_logger
from loopai.schema.states import LoopAIState

logger = get_logger()

_COT_THINK_OPEN = "<think>"
_COT_THINK_CLOSE = "</think>"


def _format_reasoning_steps_text(steps: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        step_text = str(s.get("step") or "").strip()
        if step_text:
            lines.append(f"{i + 1}. {step_text}")
    return "\n".join(lines)


def _apply_cot_to_record_output(
    orig: Dict[str, Any],
    steps: List[Dict[str, Any]],
    append_cot_to_output: bool,
) -> Dict[str, Any]:
    """合并 reasoning_steps；若 append_cot_to_output 则将 CoT 写入标签内并保留原始输出在标签后。"""
    merged: Dict[str, Any] = {**orig, "reasoning_steps": steps}
    if not append_cot_to_output:
        return merged

    cot_body = _format_reasoning_steps_text(steps)
    prefix = f"{_COT_THINK_OPEN}{cot_body}{_COT_THINK_CLOSE}"

    messages = orig.get("messages")
    if isinstance(messages, list) and messages:
        new_msgs = copy.deepcopy(messages)
        for msg in new_msgs:
            if isinstance(msg, dict) and (msg.get("role") or "").lower() == "assistant":
                orig_content = str(msg.get("content") or "")
                msg["content"] = prefix + orig_content
                merged["messages"] = new_msgs
                return merged

    orig_output = str(orig.get("output") or "")
    merged["output"] = prefix + orig_output
    return merged

# ──────────────────────────────────────────────────────────────────────────────
# DataFlow 路径与 bootstrap
# ──────────────────────────────────────────────────────────────────────────────

_DATAFLOW_ROOT = "/mnt/paper2any/xbr/financial/DataFlow"
_SERVING_PKG = "dataflow.serving"
_SERVING_MODULE = "dataflow.serving.api_llm_serving_request"


def _bootstrap_dataflow(api_key: str = "") -> None:
    """向 sys.path 注入 DataFlow，并绕过重量级 serving/__init__.py。"""
    if _DATAFLOW_ROOT not in sys.path:
        sys.path.insert(0, _DATAFLOW_ROOT)

    if api_key:
        os.environ["DF_API_KEY"] = api_key

    if _SERVING_PKG not in sys.modules:
        pkg = types.ModuleType(_SERVING_PKG)
        pkg.__path__ = [os.path.join(_DATAFLOW_ROOT, "dataflow", "serving")]
        pkg.__package__ = _SERVING_PKG
        sys.modules[_SERVING_PKG] = pkg

    if _SERVING_MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _SERVING_MODULE,
            os.path.join(_DATAFLOW_ROOT, "dataflow", "serving", "api_llm_serving_request.py"),
            submodule_search_locations=[],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _SERVING_PKG
        sys.modules[_SERVING_MODULE] = mod
        spec.loader.exec_module(mod)


def _make_api_url(base_url: str) -> str:
    """将 state 中 base_url（结尾为 /v1）转为 DataFlow 需要的 chat/completions 端点。"""
    url = (base_url or "").rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    return url


# ──────────────────────────────────────────────────────────────────────────────
# 内部 pipeline 类（参数化版本的 FullPipelineAlpaca）
# ──────────────────────────────────────────────────────────────────────────────

_COT_SYSTEM = "你是一位专业的推理改写助手，擅长为回答提炼清晰的推理步骤。严格按照指定 JSON 格式输出。"

_COT_TEMPLATE = """\
你是一个推理改写助手。请根据以下问题和原始回答，提炼出若干条清晰的推理步骤，并将原始回答作为最终答案输出。

【问题】
{instruction}

【原始回答】
{output}

要求：
- reasoning_steps：列出2-5条推理步骤，每条说明从问题信息到该建议的推导依据
- answer：直接复制原始回答，不做任何修改

以 JSON 格式输出，结构如下：
{{
  "reasoning_steps": [
    {{"step": "..."}},
    {{"step": "..."}}
  ],
  "answer": "..."
}}"""

_COT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"step": {"type": "string"}},
                "required": ["step"],
                "additionalProperties": False,
            },
        },
        "answer": {"type": "string"},
    },
    "required": ["reasoning_steps", "answer"],
    "additionalProperties": False,
}

_COT_FILTER_SYSTEM = """\
你是一位数据质量评估专家。请对以下 JSON 数据中的推理步骤质量进行评分。

评分标准（1-5分）：
1分：推理步骤与答案完全无关，或逻辑混乱
2分：推理步骤与答案有一定关联，但逻辑跳跃严重
3分：推理步骤基本合理，能部分支撑答案
4分：推理步骤清晰，逻辑连贯，能有效支撑答案
5分：推理步骤严密完整，逻辑链清晰，完全支撑答案

评估内容："""


class _CotPipeline:
    """参数化的 CoT 清洗流水线，基于 DataFlow FullPipelineAlpaca 模式。"""

    def __init__(
        self,
        input_file: str,
        cache_path: str,
        api_url: str,
        model_name: str,
        api_key: str,
        max_workers: int = 502,
        min_quality_score: int = 3,
    ) -> None:
        from dataflow.serving.api_llm_serving_request import APILLMServing_request
        from dataflow.operators.core_text import FormatStrPromptedGenerator, PromptedFilter
        from dataflow.operators.text_sft import AlpagasusFilter
        from dataflow.prompts.core_text import FormatStrPrompt
        from dataflow.utils.storage import FileStorage

        self.storage = FileStorage(
            first_entry_file_name=input_file,
            file_name_prefix="cot_pipeline",
            cache_path=cache_path,
            cache_type="jsonl",
        )

        if api_key:
            os.environ["DF_API_KEY"] = api_key
        llm = APILLMServing_request(
            api_url=api_url,
            model_name=model_name,
            max_workers=max_workers,
        )
        if getattr(llm, "session", None) is not None:
            llm.session.trust_env = False

        self.alpagasus_filter = AlpagasusFilter(
            llm_serving=llm,
            min_score=min_quality_score,
            max_score=5,
            dimension="quality",
        )
        self.cot_generator = FormatStrPromptedGenerator(
            llm_serving=llm,
            system_prompt=_COT_SYSTEM,
            prompt_template=FormatStrPrompt(f_str_template=_COT_TEMPLATE),
            json_schema=_COT_JSON_SCHEMA,
        )
        self.cot_filter = PromptedFilter(
            llm_serving=llm,
            system_prompt=_COT_FILTER_SYSTEM,
            min_score=min_quality_score,
            max_score=5,
        )

    def forward(self) -> None:
        logger.info("[CotPipeline] Stage 1: Alpagasus 质量过滤")
        self.alpagasus_filter.run(
            storage=self.storage.step(),
            input_instruction_key="instruction",
            input_input_key="input",
            input_output_key="output",
        )

        logger.info("[CotPipeline] Stage 2: CoT 生成")
        self.cot_generator.run(
            storage=self.storage.step(),
            output_key="cot_output",
            instruction="instruction",
            output="output",
        )

        logger.info("[CotPipeline] Stage 3: CoT 质量过滤")
        self.cot_filter.run(
            storage=self.storage.step(),
            input_key="cot_output",
            output_key="cot_score",
        )


def _find_pipeline_output(cache_path: str) -> str:
    """找到 pipeline 写出的最后一步输出文件。"""
    # 约定：3 步流程产出 cot_pipeline_step3.jsonl（step 从 1 起计）
    expected = os.path.join(cache_path, "cot_pipeline_step3.jsonl")
    if os.path.isfile(expected):
        return expected
    # 兜底：找 cache 目录下最新的 jsonl
    jsonl_files = [
        os.path.join(cache_path, f)
        for f in os.listdir(cache_path)
        if f.endswith(".jsonl") and os.path.isfile(os.path.join(cache_path, f))
    ]
    if not jsonl_files:
        return ""
    return max(jsonl_files, key=os.path.getmtime)


# ──────────────────────────────────────────────────────────────────────────────
# 格式检测与转换
# ──────────────────────────────────────────────────────────────────────────────

def _extract_alpaca_fields(rec: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    从任意格式的记录中提取 (instruction, input, output) 三元组。

    支持：
    - Alpaca 格式：直接读取 instruction / input / output 字段
    - ShareGPT messages 格式：user 消息 → instruction，assistant 消息 → output，input = ""
    """
    # Alpaca 格式
    if "instruction" in rec or "output" in rec:
        instr = str(rec.get("instruction") or "").strip()
        inp   = str(rec.get("input") or "").strip()
        out   = str(rec.get("output") or "").strip()
        return instr, inp, out

    # messages 格式
    messages = rec.get("messages") or []
    if isinstance(messages, list):
        system_content = ""
        user_content = ""
        assistant_content = ""
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or "").lower()
            content = str(msg.get("content") or "").strip()
            if role == "system" and not system_content:
                system_content = content
            elif role == "user" and not user_content:
                user_content = content
            elif role == "assistant" and not assistant_content:
                assistant_content = content
        # system 作为背景前缀拼入 instruction，让 CoT 生成时感知领域角色
        combined = (system_content + "\n" + user_content).strip() if system_content else user_content
        return combined, "", assistant_content

    return "", "", ""


def _process_one_file(
    fp: str,
    api_url: str,
    model_name: str,
    api_key: str,
    *,
    append_cot_to_output: bool = False,
) -> Tuple[int, int, int]:
    """
    对单个 JSONL 文件执行清洗 + CoT 生成，结果覆盖写回原文件。

    支持 Alpaca 格式（instruction/input/output）和 messages 格式（ShareGPT）。

    Returns:
        (total_in, enriched_out, dropped)
    """
    # ── 读取原始记录 ──────────────────────────────────────────────────────
    orig_records: List[Dict[str, Any]] = []
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    orig_records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not orig_records:
        return 0, 0, 0

    total_in = len(orig_records)

    # ── 构造带临时 ID 的 adapted 记录（统一转为 Alpaca 字段供 pipeline 使用）──
    adapted: List[Dict[str, Any]] = []
    id_to_orig: Dict[str, Dict[str, Any]] = {}

    for rec in orig_records:
        tmp_id = str(uuid.uuid4())
        instr, inp, out = _extract_alpaca_fields(rec)
        if not instr or not out:
            # 无法提取有效 instruction/output，跳过
            continue
        # instruction + input 拼接（用户要求）
        combined_instr = (instr + "\n" + inp).strip() if inp else instr

        adapted_rec: Dict[str, Any] = {
            "instruction": combined_instr,
            "input": inp,
            "output": out,
            "_tmp_cot_id": tmp_id,
        }
        adapted.append(adapted_rec)
        id_to_orig[tmp_id] = rec

    # ── 在临时目录中跑 pipeline ───────────────────────────────────────────
    with tempfile.TemporaryDirectory(prefix="loopai_cot_") as tmpdir:
        input_tmp = os.path.join(tmpdir, "input.jsonl")
        with open(input_tmp, "w", encoding="utf-8") as f:
            for rec in adapted:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        cache_dir = os.path.join(tmpdir, "cache")
        os.makedirs(cache_dir, exist_ok=True)

        try:
            pipeline = _CotPipeline(
                input_file=input_tmp,
                cache_path=cache_dir,
                api_url=api_url,
                model_name=model_name,
                api_key=api_key,
            )
            pipeline.forward()
        except Exception as e:
            logger.error(f"[norma_filter_and_add_cot] pipeline failed for {fp}: {e}", exc_info=True)
            return total_in, 0, total_in

        output_file = _find_pipeline_output(cache_dir)
        if not output_file:
            logger.warning(f"[norma_filter_and_add_cot] no output file found after pipeline for {fp}")
            return total_in, 0, total_in

        # ── 读取 pipeline 输出，提取 _tmp_cot_id → reasoning_steps ────────
        id_to_steps: Dict[str, List[Dict[str, str]]] = {}
        with open(output_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    tmp_id = rec.get("_tmp_cot_id", "")
                    if not tmp_id:
                        continue
                    # cot_output 可能是 JSON 字符串（DataFlow 序列化为 str）或 dict
                    cot_raw = rec.get("cot_output")
                    if isinstance(cot_raw, str):
                        try:
                            cot_raw = json.loads(cot_raw)
                        except (json.JSONDecodeError, TypeError):
                            cot_raw = {}
                    steps = cot_raw.get("reasoning_steps") if isinstance(cot_raw, dict) else None
                    if isinstance(steps, list):
                        id_to_steps[tmp_id] = steps
                except json.JSONDecodeError:
                    pass

    # ── 将 reasoning_steps 合并回原始记录 ────────────────────────────────
    enriched: List[Dict[str, Any]] = []
    for a_rec in adapted:
        tmp_id = a_rec["_tmp_cot_id"]
        steps = id_to_steps.get(tmp_id)
        if steps is None:
            # 被过滤掉的记录不写入输出
            continue
        orig = id_to_orig[tmp_id]
        enriched.append(_apply_cot_to_record_output(orig, steps, append_cot_to_output))

    if not enriched:
        logger.warning(f"[norma_filter_and_add_cot] all records filtered out for {fp}")
        return total_in, 0, total_in

    # ── 覆盖写回原文件 ────────────────────────────────────────────────────
    tmp_out = fp + ".tmp.loopai_cot"
    try:
        with open(tmp_out, "w", encoding="utf-8") as f:
            for rec in enriched:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp_out, fp)
    except Exception as e:
        logger.error(f"[norma_filter_and_add_cot] write back failed for {fp}: {e}", exc_info=True)
        if os.path.isfile(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
        return total_in, 0, total_in

    dropped = total_in - len(enriched)
    logger.info(
        f"[norma_filter_and_add_cot] {os.path.basename(fp)}: "
        f"in={total_in} enriched={len(enriched)} dropped={dropped}"
    )
    return total_in, len(enriched), dropped


# ──────────────────────────────────────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────────────────────────────────────

def norma_filter_and_add_cot(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    通用数据清洗过滤 + CoT 推理路径生成。

    - 输入：Alpaca 格式（instruction / input / output）的 JSONL 文件或目录。
    - instruction 与 input 会拼接为 pipeline 的输入 instruction，
      原始字段在输出中保持不变。
    - 输出：保留原始所有键，新增 reasoning_steps 键。
    - 若 constructor.append_cot_after_cleaning 为真，另将 output / assistant content
      改写为 <think>…</think> + 原始输出。
    - 仅保留通过 Alpagasus 质量过滤与 CoT 质量过滤的记录。

    只在用户明确要求为数据集添加推理路径时使用此工具（或由清洗子图末尾节点在开关开启时调用）。
    """
    logger.info(f"[norma_filter_and_add_cot] start, data_path={data_path}")

    result = BaseCleanResult(
        cleaned_data_path=data_path,
        total_records=0,
        valid_records=0,
        invalid_records=0,
    )

    # ── 获取 LLM 配置 ─────────────────────────────────────────────────────
    constructor_cfg: Dict[str, Any] = state.get("constructor") or {}
    model_name = (constructor_cfg.get("model_path") or state.get("analyze_model_path") or "").strip()
    base_url = (constructor_cfg.get("base_url") or state.get("analyze_base_url") or "").strip()
    api_key = (constructor_cfg.get("api_key") or state.get("analyze_api_key") or "").strip()

    if not (model_name and base_url and api_key):
        logger.warning("[norma_filter_and_add_cot] missing LLM config, skipping")
        result.success = False
        result.error_message = "Missing LLM config (model_path / base_url / api_key)"
        return result

    api_url = _make_api_url(base_url)

    # ── Bootstrap DataFlow ────────────────────────────────────────────────
    try:
        _bootstrap_dataflow(api_key=api_key)
    except Exception as e:
        logger.error(f"[norma_filter_and_add_cot] DataFlow bootstrap failed: {e}", exc_info=True)
        result.success = False
        result.error_message = f"DataFlow bootstrap error: {e}"
        return result

    # ── 枚举 JSONL 文件 ───────────────────────────────────────────────────
    if os.path.isfile(data_path) and data_path.endswith(".jsonl"):
        jsonl_files = [data_path]
    elif os.path.isdir(data_path):
        jsonl_files = sorted(
            os.path.join(data_path, fn)
            for fn in os.listdir(data_path)
            if fn.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, fn))
        )
    else:
        logger.warning(f"[norma_filter_and_add_cot] invalid data_path: {data_path}")
        return result

    if not jsonl_files:
        logger.warning("[norma_filter_and_add_cot] no JSONL files found")
        return result

    append_cot_to_output = bool(constructor_cfg.get("append_cot_after_cleaning"))

    # ── 逐文件处理 ────────────────────────────────────────────────────────
    total = valid = invalid = 0
    for fp in jsonl_files:
        if not os.path.isfile(fp):
            continue
        t, v, d = _process_one_file(
            fp,
            api_url=api_url,
            model_name=model_name,
            api_key=api_key,
            append_cot_to_output=append_cot_to_output,
        )
        total += t
        valid += v
        invalid += d

    result.total_records = total
    result.valid_records = valid
    result.invalid_records = invalid
    result.cleaned_data_path = data_path

    logger.info(
        f"[norma_filter_and_add_cot] done: total={total} valid={valid} invalid={invalid}"
    )
    return result
