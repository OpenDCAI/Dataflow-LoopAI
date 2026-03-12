# -*- coding: utf-8 -*-
import os
import re
import json
import time
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, Any, List
from tqdm import tqdm
from langchain_core.language_models import BaseChatModel
from loopai.common.prompts.prompt_loader import PromptLoader
from langchain_openai import ChatOpenAI
from ..utils.llmaj import LLMJudge
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from loopai.agents.Analyzer.utils.openai_compat_llm import OpenAICompatChat
logger = get_logger()
from types import SimpleNamespace  
from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent
# ===== PromptLoader 单例 & 模板缓存 =====
_PROMPT_LOADER: PromptLoader | None = None
_TEMPLATE_CACHE: dict[tuple[str, str], str] = {}
import sys
from pathlib import Path
import importlib.util

def _force_real_general_text_package():
    base = (
        Path(__file__).resolve()
        .parent / "DataFlow" / "dataflow" / "operators" / "general_text"
    )

    if not base.exists():
        raise RuntimeError(f"general_text path not found: {base}")

    def _register(pkg_name: str, path: Path):
        spec = importlib.util.spec_from_file_location(
            pkg_name,
            path / "__init__.py",
            submodule_search_locations=[str(path)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name] = module
        spec.loader.exec_module(module)

    _register("dataflow.operators.general_text", base)

    eval_dir = base / "eval"
    if eval_dir.exists():
        _register("dataflow.operators.general_text.eval", eval_dir)
import pkgutil
import inspect
import importlib

METRIC_REGISTRY = {}

def auto_register_metrics():
    import dataflow.operators.general_text.eval as pkg
    for _, module_name, _ in pkgutil.iter_modules(pkg.__path__):
        module = importlib.import_module(f"{pkg.__name__}.{module_name}")
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if name.endswith("SampleEvaluator"):
                key = module_name.replace("_sample_evaluator", "")
                METRIC_REGISTRY[key] = obj
def run_metric(metric_name: str, reference: str, response: str):
    import pandas as pd

    MetricClass = METRIC_REGISTRY.get(metric_name)
    if not MetricClass:
        raise ValueError(f"Metric '{metric_name}' not found")

    metric = MetricClass()

    if "dataset" in metric_name:
        df = pd.DataFrame([{"text": response}])
        return metric.eval(df, input_key="text")[0]

    if metric_name in ("perspective", "presidio"):
        df = pd.DataFrame([{"text": response}])
        return metric.eval(df, input_key="text")[0]

    df = pd.DataFrame([{"ref": reference or "", "hyp": response}])
    return metric.eval(df, input_key="hyp", reference_key="ref")[0]
def get_prompt_loader() -> PromptLoader:
    global _PROMPT_LOADER
    if _PROMPT_LOADER is None:
        _PROMPT_LOADER = PromptLoader()
    return _PROMPT_LOADER

def get_template(group: str, name: str) -> str:
    key = (group, name)
    if key not in _TEMPLATE_CACHE:
        _TEMPLATE_CACHE[key] = get_prompt_loader()(group, name)
    return _TEMPLATE_CACHE[key]
class SafeDict(dict):  
    """
    安全字典，用于字符串 format_map 时，缺失 key 自动返回空字符串，避免 KeyError
    """
    def __missing__(self, key):
        return ""


def _trunc(s: str | None, n: int) -> str:  # NEW
    """
    将字符串截断到指定长度，并在末尾追加 truncated 标记
    Args:
        - s: 原始字符串
        - n: 最大保留长度
    Returns:
        截断后的字符串
    """
    s = s or ""
    return s if len(s) <= n else s[:n] + "\n...[truncated]"


def build_evidence_for_record_code(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据 code OJ 记录构造判因 evidence 结构。
    Args:
        - rec: 单条 code 评测记录
    Returns:
        判因 evidence 字典
    """
    return {
        "task_id": rec.get("task_id"),
        "sample_index": rec.get("sample_index"),
        "entry_point": rec.get("entry_point", ""),
        "prompt_head": (rec.get("problem_prompt") or "")[:800],
        "completion_head": (rec.get("completion") or "")[:800],
        "test_head": (rec.get("test_code") or "")[:800],
        "stdout_tail": (rec.get("stdout") or "")[-600:],
        "stderr_head": (rec.get("stderr") or "")[:600],
        "err_text": f"stdout:\n{rec.get('stdout', '')}\n\nstderr:\n{rec.get('stderr', '')}",
        "query": rec.get("query", "") or rec.get("completion", ""),
    }


def build_evidence_for_record_sql(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据 sql / text2sql OJ 记录构造判因 evidence 结构。
    这里按 dev_bird_for_oj_20_result.jsonl 常见字段做兼容：
        - question     : 自然语言问题
        - completion   : 模型生成 SQL
        - ground_truth : 标准 SQL
        - result       : 查询执行结果（字符串）
    Args:
        - rec: 单条 sql 评测记录
    Returns:
        判因 evidence 字典
    """
    question = rec.get("question") or rec.get("problem_prompt") or ""
    pred_sql = (
        rec.get("completion")
        or rec.get("pred_sql")
        or rec.get("sql")
        or ""
    )
    gold_sql = (
        rec.get("ground_truth")
        or rec.get("gold_sql")
        or rec.get("reference_sql")
        or rec.get("test_code")
        or ""
    )
    stdout = (
        rec.get("result")
        or rec.get("stdout")
        or rec.get("exec_stdout")
        or ""
    )
    stderr = rec.get("stderr") or rec.get("exec_stderr") or ""

    return {
        "task_id": rec.get("task_id"),
        "sample_index": rec.get("sample_index"),
        "entry_point": rec.get("entry_point", "") or rec.get("db_id", ""),
        "prompt_head": question[:800],
        "completion_head": pred_sql[:800],
        "test_head": gold_sql[:800],
        "stdout_tail": stdout[-600:],
        "stderr_head": stderr[:600],
        "err_text": f"stdout:\n{stdout}\n\nstderr:\n{stderr}",
        "query": pred_sql or rec.get("query", ""),
    }

def _analyzer(state: LoopAIState) -> dict:
    return state.get("analyzer") or {}

def init_model(state: LoopAIState) -> BaseChatModel:
    """
    初始化用于判因 / 短评的 LLM 模型（OpenAI 兼容接口）
    Args:
        - state: LoopAIState，包含模型路径、base_url、api_key 等配置
    Returns:
        已初始化好的 BaseChatModel 实例
    """
    cfg = _analyzer(state)  
    return OpenAICompatChat(
        model=cfg["analyze_model_path"],        
        base_url=cfg["analyze_base_url"],      
        api_key=cfg["analyze_api_key"],        
        max_tokens=cfg.get("analyze_max_tokens", 512),   
        temperature=cfg.get("analyze_temperature", 0.0), 
        top_p=cfg.get("analyze_top_p", 0.95),           
    )

def build_judge_prompt_generic(task: str, evidence: Dict[str, Any]) -> str:
    """
    判因 schema 的通用 prompt（code/sql 均可用）
    你可以随时替换这段为你自己的 prompt；要求模型返回严格 JSON。
    Args:
        - task: 任务类型（code/sql）
        - evidence: 判因 schema 字典
    Returns:
        完整 prompt
    """
    def trunc(s, n):
        s = s or ""
        return s if len(s) <= n else s[:n] + "\n...[truncated]"

    ev = {
        "prompt_head": trunc(evidence.get("prompt_head", ""), 256),
        "completion_head": trunc(evidence.get("completion_head", ""), 256),
        "test_head": trunc(evidence.get("test_head", ""), 256),
        "stdout_tail": trunc(evidence.get("stdout_tail", ""), 256),
        "stderr_head": trunc(evidence.get("stderr_head", ""), 256),
        "err_text": trunc(evidence.get("err_text", ""), 256),
        "query": trunc(evidence.get("query", ""), 256),
    }
    tpl = get_template("judge", "judge_user")
    return tpl.format(task=task, **ev)


def parse_assert_from_stdout(stdout: str) -> Dict[str, Any]:
    """
    失败断言解析
    Args:
        - stdout: 模型输出的标准输出
    Returns:
        解析后的断言信息字典，包含 input_expr, expected, actual 三键
    """
    actual = expected = input_expr = None
    s = stdout or ""
    m = re.search(r"^E\s+assert\s+(.+?)\s*==\s*(.+?)\s*$", s, flags=re.M)
    if m:
        actual = m.group(1).strip()
        expected = m.group(2).strip()
    if actual is None or expected is None:
        m = re.search(r"AssertionError[: ]\s*assert\s+(.+?)\s*==\s*(.+?)\s*$", s, flags=re.M)
        if m:
            actual = actual or m.group(1).strip()
            expected = expected or m.group(2).strip()
    m = re.search(r"candidate\((.*?)\)", s, flags=re.S)
    if m:
        input_expr = "candidate(" + " ".join(m.group(1).split()) + ")"
    return {"input_expr": input_expr, "expected": expected, "actual": actual}


def call_llm_with_control(
    llm: ChatOpenAI,
    prompts: List[str],
    max_concurrency: int = 4,
    chunk_size: int = 8,
):
    """
    带并发上限的批量调用封装，避免一次性开太多并发把引擎打挂。
    - max_concurrency: 每一小批内部允许的最大并发数
    - chunk_size: 每次送给 llm.batch 的样本数量
    """
    all_results = []
    n = len(prompts)
    for start in range(0, n, chunk_size):
        sub_prompts = prompts[start:start + chunk_size]
        # LangChain 的 ChatOpenAI.batch 支持 config，里面可以设置 max_concurrency
        sub_results = llm.batch(
            sub_prompts,
            config={"max_concurrency": max_concurrency}
        )
        all_results.extend(sub_results)
    return all_results
def summarize_brief(
    task_id: str,
    prompt_head: str,
    completion_head: str,
    test_head: str,
    oj: Dict[str, str],
    llm: ChatOpenAI,
    task_type: str = "code",  
) -> str:
    """
    总结一句话中文短评
    - code / sql 共用结构，但 **使用不同的 prompt 模板**
    Args:
        - task_id: 任务 ID
        - prompt_head: 题目片段 / 自然语言问题
        - completion_head: 被测生成代码片段 / 预测 SQL 片段
        - test_head: 测试代码片段 / 标准 SQL 片段
        - oj: OJ 输出字典，包含 stdout, stderr, input_expr, expected, actual 等键
        - llm: 初始化后的模型实例
        - task_type: "code" 或 "sql"，用于选择 prompt
    Returns:
        一句话中文短评
    """
    if task_type == "text2sql":
        tpl_name = "brief_sql_user"
    else:
        tpl_name = "brief_user"
    tpl = get_template("brief", tpl_name) 

    payload = SafeDict({
        "prompt_head": (prompt_head or "")[:600],
        "completion_head": (completion_head or "")[:800],
        "test_head": (test_head or "")[:600],
        "stdout": (oj.get("stdout") or "")[-600:],
        "stderr": (oj.get("stderr") or "")[:400],
        "stdout_tail": (oj.get("stdout") or "")[-600:],
        "meta_json": json.dumps({
            "task_id": task_id,
            "assert_parsed": {
                "input_expr": oj.get("input_expr"),
                "expected": oj.get("expected"),
                "actual": oj.get("actual"),
            }
        }, ensure_ascii=False),
    })

    user_block = tpl.format_map(payload)
    # ChatOpenAI.batch 返回 BaseMessage，取 content
    out = llm.batch([user_block])[0].content
    return (out or "").strip() or "（模型未返回内容）"


def build_brief_inputs_code(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    构造 code 任务的一句话短评输入（方便 eval_model_node 调用）。
    Args:
        - rec: 单条 code 评测记录
    Returns:
        包含 prompt_head / completion_head / test_head / oj 四个键的字典
    """
    apx = rec.get("assert_parsed") or parse_assert_from_stdout(rec.get("stdout") or "")
    return {
        "prompt_head": rec.get("problem_prompt") or "",
        "completion_head": rec.get("completion") or "",
        "test_head": rec.get("test_code") or "",
        "oj": {
            "stdout": rec.get("stdout") or "",
            "stderr": rec.get("stderr") or "",
            "input_expr": apx.get("input_expr"),
            "expected": apx.get("expected"),
            "actual": apx.get("actual"),
        },
    }


def build_brief_inputs_sql(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    构造 sql / text2sql 任务的一句话短评输入。
    这里按 dev_bird_for_oj_20_result.jsonl 的字段做兼容：
        - question     : 自然语言问题
        - completion   : 模型生成 SQL
        - ground_truth : 标准 SQL
        - result       : 查询执行结果（字符串）
    Args:
        - rec: 单条 sql 评测记录
    Returns:
        包含 prompt_head / completion_head / test_head / oj 四个键的字典
    """
    question = rec.get("question") or rec.get("problem_prompt") or ""
    pred_sql = (
        rec.get("completion")
        or rec.get("pred_sql")
        or rec.get("sql")
        or ""
    )
    gold_sql = (
        rec.get("ground_truth")
        or rec.get("gold_sql")
        or rec.get("reference_sql")
        or rec.get("test_code")
        or ""
    )
    stdout = (
        rec.get("result")
        or rec.get("stdout")
        or rec.get("exec_stdout")
        or ""
    )
    stderr = rec.get("stderr") or rec.get("exec_stderr") or ""

    return {
        "prompt_head": question,
        "completion_head": pred_sql,
        "test_head": gold_sql,
        "oj": {
            "stdout": stdout,
            "stderr": stderr,
            "input_expr": None,
            "expected": None,
            "actual": None,
        },
    }


def build_evidence_for_record(rec: Dict[str, Any], task_type: str) -> Dict[str, Any]:
    """
    根据任务类型构建判因 evidence 结构（code / text2sql 共用）。
    code 分支保持原有逻辑；sql 分支尽量兼容常见 Text2SQL OJ 字段。
    Args:
        - rec: 单条评测记录
        - task_type: 任务类型（"code" 或 "sql"）
    Returns:
        判因 evidence 字典
    """
    if task_type == "text2sql":
        return build_evidence_for_record_sql(rec)
    return build_evidence_for_record_code(rec)

def _build_and_write_summary(rows: List[Dict[str, Any]], outdir: Path, run_ts: str, task_type: str = "code"):
    """
    根据 rows 生成 summary 的函数
    Args:
        - rows: 评测结果行列表（通常为失败样本增强 OJ）
        - outdir: 输出目录
        - run_ts: 运行时间戳
        - task_type: 任务类型（"code" 或 "sql"），用于控制统计项
    Returns:
        生成的 summary JSON 路径和 TXT 路径
    """
    stage_counter = Counter()
    tag_counter = Counter()
    passed_samples = 0
    total_samples = 0

    loc_bins = Counter()
    kw_bins = Counter()

    io_parsed_ok = 0
    actual_top = Counter()
    expected_top = Counter()

    task_pass_map = defaultdict(bool)
    task_ids = set()

    def bin_loc(n: int) -> str:
        """
        将数值粗分到几个区间：
        - 对 code：代表 LOC
        - 对 sql：代表 SQL 语句长度（字符数）
        """
        if n <= 10: return "<=10"
        if n <= 30: return "11-30"
        if n <= 60: return "31-60"
        return ">60"

    def bin_kw(n: int) -> str:
        """
        将数值粗分到几个区间：
        - 对 code：代表控制语句数量
        - 对 sql：代表 SQL 词元数量
        """
        if n == 0: return "0"
        if n <= 3: return "1-3"
        if n <= 8: return "4-8"
        return ">8"

    for rec in rows:
        total_samples += 1
        if rec.get("passed"):
            passed_samples += 1
        else:
            stg = (rec.get("judge") or {}).get("stage", "other")
            stage_counter[stg] += 1
            tags = (rec.get("judge") or {}).get("tags") or []
            for t in tags:
                tag_counter[t] += 1

        if task_type == "text2sql":
            # NEW: 对于 sql，用 SQL 语句长度 / 词元数量做粗分布统计
            sql = (
                rec.get("completion")
                or rec.get("pred_sql")
                or rec.get("sql")
                or ""
            )
            sql_len = len(sql)
            token_cnt = len(sql.split()) if sql else 0
            loc_bins[bin_loc(sql_len)] += 1
            kw_bins[bin_kw(token_cnt)] += 1
        else:
            m = rec.get("code_metrics") or {}
            try:
                loc = int(m.get("loc", 0))
            except Exception:
                loc = 0
            try:
                kw = int(m.get("kw_total", 0))
            except Exception:
                kw = 0
            loc_bins[bin_loc(loc)] += 1
            kw_bins[bin_kw(kw)] += 1

        ap = rec.get("assert_parsed") or {}
        if ap.get("expected") is not None or ap.get("actual") is not None:
            io_parsed_ok += 1
            if ap.get("actual"):
                actual_top[ap["actual"][:60]] += 1
            if ap.get("expected"):
                expected_top[ap["expected"][:60]] += 1

        tid = rec.get("task_id")
        if tid is not None:
            task_ids.add(tid)
            if rec.get("passed"):
                task_pass_map[tid] = True

    total_tasks = len(task_ids)
    passed_tasks = sum(1 for v in task_pass_map.values() if v)
    pass_at_k_task = {}
    if total_tasks > 0:
        pass_at_k_task[1] = round(passed_tasks / total_tasks, 4)
        pass_at_k_task[10] = pass_at_k_task[1]
    else:
        pass_at_k_task = {}

    summary = {
        "run_ts": run_ts,
        "results_file": None,
        "total_samples": total_samples,
        "passed_samples": passed_samples,
        "pass_rate_samples": round(passed_samples / (total_samples or 1), 4),
        "pass_at_k_task": pass_at_k_task,
        "failure_stage_distribution": dict(stage_counter),
        "loc_distribution": dict(loc_bins),
        "kw_distribution": dict(kw_bins),
        "io_assert_parse": {
            "parsed_ok": io_parsed_ok,
            "parsed_rate": round(io_parsed_ok / (total_samples or 1), 4),
            "actual_top10": actual_top.most_common(10),
            "expected_top10": expected_top.most_common(10),
        },
        "tag_top10": tag_counter.most_common(10),
    }

    os.makedirs(outdir, exist_ok=True)
    summary_json = outdir / f"summary_{run_ts}.json"
    summary_txt = outdir / f"summary_{run_ts}.txt"
    with open(summary_json, "w", encoding="utf-8") as sf:
        json.dump(summary, sf, ensure_ascii=False, indent=2)

    lines = []
    lines.append(f"评测时间：{run_ts}")
    lines.append(f"样本正确率：{passed_samples}/{total_samples}（{summary['pass_rate_samples'] * 100:.2f}%）")
    if pass_at_k_task:
        lines.append("Pass@k(任务口径)： " + ", ".join([f"Pass@{k}={v * 100:.2f}%" for k, v in pass_at_k_task.items()]))
    lines.append("主要错因(stage)分布：")
    for k, v in stage_counter.most_common():
        lines.append(f"  - {k}: {v}")
    lines.append("标签 Top： " + ", ".join([f"{k}:{v}" for k, v in tag_counter.most_common(8)]))

    if task_type == "text2sql":
     
        if loc_bins:
            lines.append("SQL 语句长度分布（按字符数粗分）： " +
                         ", ".join([f"{k}:{v}" for k, v in loc_bins.items()]))
        else:
            lines.append("SQL 语句长度分布（按字符数粗分）： （无数据）")
        if kw_bins:
            lines.append("SQL 词元数量分布（粗粒度）： " +
                         ", ".join([f"{k}:{v}" for k, v in kw_bins.items()]))
        else:
            lines.append("SQL 词元数量分布（粗粒度）： （无数据）")
    else:
       
        if loc_bins:
            lines.append("代码行数分布（LOC）： " + ", ".join([f"{k}:{v}" for k, v in loc_bins.items()]))
        else:
            lines.append("代码行数分布（LOC）： （无数据）")
        if kw_bins:
            lines.append("控制语句分布（粗复杂度）： " + ", ".join([f"{k}:{v}" for k, v in kw_bins.items()]))
        else:
            lines.append("控制语句分布（粗复杂度）： （无数据）")

    lines.append(f"I/O断言解析成功：{io_parsed_ok}/{total_samples}")
    with open(summary_txt, "w", encoding="utf-8") as tf:
        tf.write("\n".join(lines))

    logger.info(f" 已生成 summary：{summary_json} ，文本简报：{summary_txt}")
    return str(summary_json), str(summary_txt)
def run_general_text_fallback_eval(state):
    _force_real_general_text_package()
    import pandas as pd
    import jieba
    import re
    import json, time
    from pathlib import Path
    from dataflow.operators.general_text.eval.bleu_sample_evaluator import BleuSampleEvaluator
    from dataflow.operators.general_text.eval.ngram_sample_evaluator import NgramSampleEvaluator

    def contains_chinese(s: str) -> bool:
        return bool(re.search(r'[\u4e00-\u9fff]', s or ""))

    def tokenize(s: str) -> list:
        if not s:
            return []
        if contains_chinese(s):
            return jieba.lcut(s)
        return s.split()

    def ttr(tokens: list) -> float:
        return len(set(tokens)) / len(tokens) if tokens else 0.0

    cfg = state["analyzer"]
    weights = cfg.get("metric_weights", {"bleu":0.4,"lexical_diversity":0.3,"ngram":0.3})
    eval_path = state["judger"]["eval_result_path"]

    with open(eval_path, "r", encoding="utf-8") as f:
        rows = [json.loads(x) for x in f]

    bleu_eval = BleuSampleEvaluator()
    ngram_eval = NgramSampleEvaluator()

    results = []

    for r in rows:
        ref_raw = r.get("reference","")
        hyp_raw = r.get("completion","")

        ref_tokens = tokenize(ref_raw)
        hyp_tokens = tokenize(hyp_raw)

        ref = " ".join(ref_tokens)
        hyp = " ".join(hyp_tokens)

        # BLEU
        if ref_tokens:
            df_bleu = pd.DataFrame([{"ref": ref, "hyp": hyp}])
            bleu = float(bleu_eval.eval(df_bleu, input_key="hyp", reference_key="ref")[0])
        else:
            bleu = 0.0

        # Lexical Diversity (统一TTR)
        lex = ttr(hyp_tokens)

        # Ngram
        df_text = pd.DataFrame([{"text": hyp}])
        ngram = float(ngram_eval.eval(df_text, input_key="text")[0])

        overall = (
            bleu * weights["bleu"] +
            lex * weights["lexical_diversity"] +
            ngram * weights["ngram"]
        )

        r["metrics"] = {
            "bleu": round(bleu, 4),
            "lexical_diversity": round(lex, 4),
            "ngram": round(ngram, 4),
            "overall": round(overall, 4)
        }

        results.append(r)

    outdir = Path(state["output_dir"])
    ts = time.strftime("%Y%m%d_%H%M%S")

    result_file = outdir / f"text_eval_scored_{ts}.jsonl"
    with open(result_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # summary
    n = len(results) or 1
    summary = {
        "num_samples": len(results),
        "average": {
            "bleu": round(sum(r["metrics"]["bleu"] for r in results) / n, 4),
            "lexical_diversity": round(sum(r["metrics"]["lexical_diversity"] for r in results) / n, 4),
            "ngram": round(sum(r["metrics"]["ngram"] for r in results) / n, 4),
            "overall": round(sum(r["metrics"]["overall"] for r in results) / n, 4),
        }
    }

    summary_file = outdir / f"text_eval_summary_{ts}.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    state["analyzer"]["analyze_output_result_path"] = str(result_file)
    state["analyzer"]["analyze_output_summary_path"] = str(summary_file)

    return state
def eval_model_node(state: LoopAIState):
    """
    模型评测分析节点函数
    该函数处理模型评测结果，分析失败案例原因，并生成增强版评测记录和总结报告

    Args:
        state: LoopAIState对象，包含以下关键参数：
            - analyze_task_type: 任务类型（"code" 或 "sql"）
            - eval_result_path: 评测结果文件路径
            - analyze_batch_size: 批处理大小，默认为20
            - analyze_model_path: 分析模型路径
            - analyze_base_url: 模型基础URL
            - analyze_api_key: 模型API密钥
            - analyze_temperature: 模型温度参数
            - analyze_top_p: 模型top_p参数
            - output_dir: 输出目录路径
            - quick_brief: 是否输出中文短评（失败<=20全量；失败>20抽样20条覆盖错误类型），默认False

    Returns:
        更新后的LoopAIState对象，包含以下新增字段：
            - analyze_output_result_path: 增强版评测记录文件路径
            - analyze_output_summary_path: 评测摘要JSON文件路径

    处理流程：
        1. 初始化LLMJudge和分析模型
        2. 读取评测结果并过滤失败案例
        3. 批量处理失败案例，收集证据并构建提示词
        4. 使用模型分析失败原因
        5. 可选地生成中文短评（quick_brief）
        6. 输出增强版评测记录
        7. 生成评测摘要报告
    """
    cfg = _analyzer(state)
    task_type = cfg.get("analyze_task_type", "code")  # "code" 或 "text2sql"
    if task_type not in ("code", "text2sql"):
        return run_general_text_fallback_eval(state)
    writer = get_stream_writer()
    if writer:
       writer(StreamEvent(
        current="AnalyzerAgent.eval_model_node",
        progress=0.0,
        message="常规任务评测样本开始",
        data={"task_type": task_type}
       ).json())
    
    judge = LLMJudge(task=task_type)

    # 读取评测结果（JSONL）
    eval_cfg = state.get("judger") or {}
    analyzer_cfg = state.get("analyzer") or {}
    eval_result_path = judger_cfg.get("out_result_path")
    if not eval_result_path:
       eval_result_path = analyzer_cfg.get("eval_result_path")

    if not eval_result_path:
        raise ValueError(
        "Missing analyzer.eval_result_path. "
        "Please provide analyzer.eval_result_path "
        "or run judger to generate out_result_path."
       )

    state.setdefault("analyzer", {})
    state["analyzer"]["eval_result_path"] = eval_result_path

    with open(eval_result_path, 'r', encoding='utf-8') as f:
        lines = [ln for ln in f if ln.strip()]
    result_content = [json.loads(ln) for ln in lines]

    # 仅失败样本做判因
    failed_results = [r for r in result_content if not r.get("passed")]
    total_failed = len(failed_results)
    # 初始化 LLM
    batch_size = int(cfg.get("analyze_batch_size", 20))
    llm = init_model(state)
    total_batches = (len(failed_results) + batch_size - 1) // batch_size
    start_p = 0.05   # 判因阶段起点
    end_p = 0.70     # 判因阶段终点
    span = end_p - start_p

    for i in tqdm(range(0, total_failed, batch_size)):
        processed = min(i + batch_size, total_failed)
        ratio = processed / max(total_failed, 1)

        # 🟢 动态真实进度
        progress = start_p + span * ratio

        if writer:
           writer(StreamEvent(
               current="AnalyzerAgent.eval_model_node",
               progress=round(progress, 3),
               message=f"判因分析中 ({processed}/{total_failed})",
               data={
                "current_batch": i // batch_size + 1,
                "total_batches": total_batches,
                "processed_samples": processed,
                "total_failed_samples": total_failed,
                }
            ).json())

        batch = failed_results[i:i + batch_size]

        evidences: List[Dict[str, Any]] = []
        prompts: List[str] = []
        for rec in batch:
            evidence = build_evidence_for_record(rec, task_type)
            evidences.append(evidence)
            prompts.append(build_judge_prompt_generic(task_type, evidence))
        overall_start = time.time()
        if writer:
           writer(StreamEvent(
        current="AnalyzerAgent.eval_model_node",
        progress=None,
        message="正在调用分析模型",
        data={"batch_size": len(batch)}
           ).json())

        # 模型批处理
                # 模型批处理（带并发上限 + 分块）
        batch_responses = call_llm_with_control(
            llm,
            prompts,
            max_concurrency=int(cfg.get("analyze_max_concurrency", 4)), 
            chunk_size=int(cfg.get("analyze_chunk_size", 8)),          
        )

        # 合并判因
        for j, rec in enumerate(batch):
            # ChatOpenAI.batch 返回 BaseMessage，取 content 作为 JSON 字符串
            model_json = batch_responses[j].content
            if not rec.get("judge"):
                try:
                    rec["judge"] = judge.analyze(evidences[j], model_result=model_json)
                except Exception as e:
                    rec["judge"] = {"stage": "other", "reason": f"LLMaJ 运行异常：{e}", "evidence": {}}

    # ===== quick_brief：仅对失败样本生成短评（失败<=20全量；失败>20抽样20条覆盖错误类型）=====
    if cfg.get("quick_brief", False) and len(failed_results) > 0:
        limit = int(cfg.get("quick_brief_limit", 20))
        if writer:
           writer(StreamEvent(
        current="AnalyzerAgent.eval_model_node",
        progress=0.70,
        message="开始生成失败样本中文短评",
        data={"limit": limit, "failed_samples": len(failed_results)}
           ).json())
        

        def _pick_quick_brief_indices() -> List[int]:
            # 失败<=limit：全量
            if len(failed_results) <= limit:
                return list(range(len(failed_results)))

            # 失败>limit：尽量覆盖不同错误类型（按 judge.stage 分组轮询抽样）
            groups: Dict[str, List[int]] = defaultdict(list)
            for idx, rec in enumerate(failed_results):
                stg = (rec.get("judge") or {}).get("stage") or "other"
                groups[str(stg)].append(idx)
            def safe_int(x, default=-1):
                try:
                    return int(x)
                except:
                    return default

            # 组内保持稳定顺序（task_id / sample_index / idx）
            def _stable_key(i: int):
                r = failed_results[i]
                return (str(r.get("task_id") or ""),safe_int(r.get("sample_index")), i)

            for k in list(groups.keys()):
                groups[k].sort(key=_stable_key)

            # 小类优先轮询，尽量覆盖更多类型
            keys = sorted(groups.keys(), key=lambda k: (len(groups[k]), k))
            ptr = {k: 0 for k in keys}

            picked: List[int] = []
            while len(picked) < limit:
                progressed = False
                for k in keys:
                    p = ptr[k]
                    if p < len(groups[k]):
                        picked.append(groups[k][p])
                        ptr[k] += 1
                        progressed = True
                        if len(picked) >= limit:
                            break
                if not progressed:
                    break

            # 去重保序
            seen = set()
            out = []
            for i in picked:
                if i not in seen:
                    out.append(i)
                    seen.add(i)
            return out

        picked_idx = _pick_quick_brief_indices()
        total_brief = len(picked_idx)

        for i, idx in enumerate(picked_idx, 1):
            rec = failed_results[idx]
            try:
                if task_type == "text2sql":
                    brief_inputs = build_brief_inputs_sql(rec)
                else:
                    brief_inputs = build_brief_inputs_code(rec)

                rec["brief_analysis"] = summarize_brief(
                    task_id=str(rec.get("task_id")),
                    prompt_head=brief_inputs["prompt_head"],
                    completion_head=brief_inputs["completion_head"],
                    test_head=brief_inputs["test_head"],
                    oj=brief_inputs["oj"],
                    llm=llm,
                    task_type=task_type
                )
            except Exception as e:
                rec["brief_analysis"] = f"（短评生成失败：{e}）"
            if writer and total_brief > 0:
               progress = 0.70 + 0.15 * (i / total_brief)
               writer(StreamEvent(
               current="AnalyzerAgent.eval_model_node",
               progress=round(progress, 3),
               message=f"生成短评中 ({i}/{total_brief})",
               data=None
               ).json())

    logger.info(f" 判因完成：共 {len(failed_results)} 条")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(cfg.get("output_dir") or state.get("output_dir"))
    out_dir.mkdir(parents=True, exist_ok=True)

    out_jsonl_path = out_dir / f"oj_records_enriched_{ts}.jsonl"
    with open(out_jsonl_path, "w", encoding="utf-8") as f:
        for r in failed_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    state.setdefault("analyzer", {})
    state["analyzer"]["analyze_output_result_path"] = str(out_jsonl_path.resolve())
    logger.info(f" 已写入增强版 OJ：{out_jsonl_path}")
    logger.info(" 完成：V2 评测 + 每条样本 LLMaJ（启发式/模型融合）" + (" + 中文短评" if cfg.get('quick_brief', False) else ""))
    if writer:
       writer(StreamEvent(
        current="AnalyzerAgent.eval_model_node",
        progress=0.85,
        message="已写入增强评测结果",
        data={"output_path": str(out_jsonl_path)}
       ).json())
    try:
        summary_json_path, summary_txt_path = _build_and_write_summary(
            failed_results, Path(state['output_dir']), ts, task_type=task_type
        )
        with open(summary_json_path, "r", encoding="utf-8") as f:
            sdata = json.load(f)
        sdata["results_file"] = str(out_jsonl_path.resolve())
        with open(summary_json_path, "w", encoding="utf-8") as f:
            json.dump(sdata, f, ensure_ascii=False, indent=2)
        state["analyzer"]["analyze_output_summary_path"] = summary_json_path
        logger.info(f" 已在 {state['output_dir']} 生成 summary，路径：{summary_json_path} / {summary_txt_path}")
        if writer:
           writer(StreamEvent(
        current="AnalyzerAgent.eval_model_node",
        progress=0.90,
        message="已生成评测摘要",
        data={ 
            "summary_json": summary_json_path,
            "summary_txt": summary_txt_path,
            "pass_rate": sdata.get("pass_rate_samples"),
            "total_samples": sdata.get("total_samples"),
            "passed_samples": sdata.get("passed_samples"),
            }
        ).json())
    except Exception as e:
        logger.warning(f"[WARN] 生成 summary 时发生错误：{e}")
    if writer:
       writer(StreamEvent(
        current="AnalyzerAgent.eval_model_node",
        progress=1.0,
        message="评测流程完成",
        data=None
       ).json())
    return state