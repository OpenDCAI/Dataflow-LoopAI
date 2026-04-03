# -*- coding: utf-8 -*-
import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent
from loopai.common.prompts.prompt_loader import PromptLoader
from langchain_openai import ChatOpenAI
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

logger = get_logger()


def _analyzer(state: LoopAIState) -> dict:
    """
    读取 analyzer 配置。
    如果 state 中没有 analyzer，则直接报错。
    """
    if "analyzer" not in state:
        raise KeyError("state 中缺少 analyzer 配置，请在 graph.invoke 中传入 analyzer")
    return state["analyzer"]


def _ensure_analyzer_outdir(state: LoopAIState) -> str:
    """
    创建并返回 analyzer 输出目录。
    目录结构保持与现有 Analyzer 节点一致：
        output_dir / task_id / analyzer
    """
    cfg = _analyzer(state)
    base_outdir = Path(cfg.get("output_dir") or state.get("output_dir") or "./outputs")
    task_id = state.get("task_id") or "default_task"
    outdir = base_outdir / task_id / "analyzer"
    outdir.mkdir(parents=True, exist_ok=True)
    return str(outdir)


def _safe_get_writer():
    """
    安全获取 langgraph 的 stream writer。
    当节点在 graph 外被单独测试时，避免因缺少 runnable context 报错。
    """
    try:
        return get_stream_writer()
    except RuntimeError:
        return None


def init_model(state: LoopAIState) -> ChatOpenAI:
    """
    初始化分析用模型。
    使用 OpenAI-compatible / vLLM 风格接口。
    """
    cfg = _analyzer(state)
    model = ChatOpenAI(
        model=cfg["analyze_model_path"],
        api_key=cfg.get("analyze_api_key", "EMPTY"),
        base_url=cfg.get("analyze_base_url"),
        temperature=cfg.get("analyze_temperature", 0.0),
        top_p=cfg.get("analyze_top_p", 0.95),
    )
    return model

def _load_metric_result(state: LoopAIState) -> Dict[str, Any]:
    """
    加载 metric_score_node 产出的 metric 结果。
    只从 analyzer 读取（metric 是 analyzer 产物）。
    """
    analyzer = _analyzer(state)

    metric_eval_results = analyzer.get("metric_eval_results")
    if metric_eval_results:
        return metric_eval_results

    metric_eval_result_path = analyzer.get("metric_eval_result_path")
    if not metric_eval_result_path:
        raise ValueError("缺少 analyzer.metric_eval_results 或 metric_eval_result_path")

    with open(metric_eval_result_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_records_from_alignment(metric_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    根据 metric_result 中的 alignment.path 回读原始 records。
    当前支持 JSONL / JSON 两种格式。
    """
    alignment = metric_result.get("alignment") or {}
    path = alignment.get("path")
    if not path:
        return []

    if not os.path.exists(path):
        logger.warning(f"[analyze_metric_report] alignment.path 不存在: {path}")
        return []

    if path.endswith(".jsonl"):
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["rows", "records", "data", "examples", "items"]:
            if key in data and isinstance(data[key], list):
                return data[key]

    return []


def _select_primary_metric(metric_result: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    从 metric_result 中选择主指标。
    优先取 priority=primary 的指标；如果没有，则退化为第一个指标。
    """
    metrics = metric_result.get("metrics", {}) or {}

    for name, item in metrics.items():
        if item.get("priority") == "primary":
            return name, item

    if metrics:
        first_name = next(iter(metrics.keys()))
        return first_name, metrics[first_name]

    return "unknown", {}


def _normalize_detail_score(detail_item: Any) -> float:
    """
    统一从 detail 中抽取 score。
    兼容两种格式：
    1. 纯数值：1.0 / 0.0
    2. 对象：{"score": 1.0, ...}
    """
    if isinstance(detail_item, (int, float)):
        return float(detail_item)

    if isinstance(detail_item, dict):
        return float(detail_item.get("score", 0.0))

    return 0.0


def _build_metric_overview(metric_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    构造各指标概览，只保留报告需要的简要字段。
    避免把大段 details 原样塞给 LLM。
    """
    metrics = metric_result.get("metrics", {}) or {}
    overview = {}

    for name, item in metrics.items():
        overview[name] = {
            "score": item.get("score"),
            "priority": item.get("priority"),
            "desc": item.get("desc", ""),
        }

        artifacts = item.get("artifacts")
        if isinstance(artifacts, dict):
            if "extractor_used" in artifacts:
                overview[name]["extractor_used"] = artifacts.get("extractor_used")

    return overview


def _build_quick_samples(
    records: List[Dict[str, Any]],
    primary_metric_item: Dict[str, Any],
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    抽取少量失败样本给 LLM 作为证据。
    兼容：
    - 主指标 details 为纯分数列表
    - 主指标 details 为对象列表（含 raw_pred / extracted / match_type）
    """
    details = primary_metric_item.get("details", []) or []
    quick_samples = []

    for idx, detail in enumerate(details):
        score = _normalize_detail_score(detail)
        if score != 0.0:
            continue

        rec = records[idx] if idx < len(records) else {}

        sample = {
            "idx": idx,
            "question": rec.get("question") or rec.get("prompt") or rec.get("input"),
            "target": rec.get("target") or rec.get("ground_truth") or rec.get("label"),
            "generated_ans": rec.get("generated_ans") or rec.get("completion") or rec.get("prediction"),
        }

        if isinstance(detail, dict):
            sample["match_type"] = detail.get("match_type")
            sample["extracted"] = detail.get("extracted")
            sample["raw_pred"] = detail.get("raw_pred")

        quick_samples.append(sample)

        if len(quick_samples) >= top_k:
            break

    return quick_samples


def _build_failure_patterns(primary_metric_name: str, primary_metric_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    基于主指标 details 构造粗粒度失败模式。
    当前采用轻量统计：
    - 若 detail 是对象且有 match_type，则统计各 match_type
    - 否则退化为 primary_metric_failure
    """
    details = primary_metric_item.get("details", []) or []
    score_zero_details = [d for d in details if _normalize_detail_score(d) == 0.0]

    if not score_zero_details:
        return []

    # 如果 detail 是 dict 并且存在 match_type，则做一层更细统计
    match_type_counter = {}
    for d in score_zero_details:
        if isinstance(d, dict):
            mt = d.get("match_type") or "unknown"
            match_type_counter[mt] = match_type_counter.get(mt, 0) + 1

    if match_type_counter:
        patterns = []
        for mt, cnt in sorted(match_type_counter.items(), key=lambda x: x[1], reverse=True):
            patterns.append({
                "name": mt,
                "count": cnt,
                "metric": primary_metric_name,
            })
        return patterns

    return [{
        "name": "primary_metric_failure",
        "count": len(score_zero_details),
        "metric": primary_metric_name,
    }]


def _infer_task_domain(state: LoopAIState) -> str:
    """
    推断任务领域。
    优先顺序：
    1. state.analyzer.task_domain
    2. bench.meta.domain
    3. analyzer.analyze_task_type
    4. 默认 general
    """
    analyzer = _analyzer(state)
    judger = state.get("judger", {}) or {}
    bench = state.get("bench") or judger.get("bench")

    if analyzer.get("task_domain"):
        return analyzer["task_domain"]

    meta = getattr(bench, "meta", {}) or {}
    if meta.get("domain"):
        return meta["domain"]

    if analyzer.get("analyze_task_type"):
        return analyzer["analyze_task_type"]

    return "general"


def _build_summary(
    state: LoopAIState,
    metric_result: Dict[str, Any],
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    将 metric_result + records 统一整理为结构化 summary。
    这个 summary 是两个 LLM prompt 的共同输入层。
    """
    judger = state.get("judger", {}) or {}
    bench = state.get("bench") or judger.get("bench")
    bench_name = getattr(bench, "bench_name", "unknown_bench")
    eval_type = getattr(bench, "bench_dataflow_eval_type", "unknown_eval_type")
    task_domain = _infer_task_domain(state)

    total = int(metric_result.get("num_samples", len(records)))

    primary_metric_name, primary_metric_item = _select_primary_metric(metric_result)
    primary_score = float(primary_metric_item.get("score", 0.0) or 0.0)

    primary_details = primary_metric_item.get("details", []) or []
    passed = sum(1 for d in primary_details if _normalize_detail_score(d) == 1.0)
    accuracy = primary_score

    metric_overview = _build_metric_overview(metric_result)
    quick_samples = _build_quick_samples(records, primary_metric_item, top_k=10)
    failure_patterns = _build_failure_patterns(primary_metric_name, primary_metric_item)
    top_err = failure_patterns[0]["name"] if failure_patterns else "none"

    return {
        "bench_name": bench_name,
        "eval_type": eval_type,
        "task_domain": task_domain,
        "total": total,
        "passed": passed,
        "accuracy": accuracy,
        "primary_metric": primary_metric_name,
        "primary_score": primary_score,
        "metric_overview": metric_overview,
        "top_err": top_err,
        "failure_patterns": failure_patterns,
        "quick_samples": quick_samples,
        "by_stage": {},
        "summary_json": metric_result,
    }


def build_prompt_for_report(summary: Dict[str, Any]) -> str:
    """
    构造自然语言评测报告用的 prompt。
    prompt 模板从 PromptLoader 中读取。
    """
    loader = PromptLoader()
    template = loader("analyze_metric_report", "report_user")

    return template.format(
        bench_name=summary["bench_name"],
        eval_type=summary["eval_type"],
        task_domain=summary["task_domain"],
        total=summary["total"],
        passed=summary["passed"],
        accuracy=summary["accuracy"],
        primary_metric=summary["primary_metric"],
        primary_score=summary["primary_score"],
        metric_overview_json=json.dumps(summary["metric_overview"], ensure_ascii=False),
        top_err=summary["top_err"],
        failure_patterns_json=json.dumps(summary["failure_patterns"], ensure_ascii=False),
        quick_samples_json=json.dumps(summary["quick_samples"], ensure_ascii=False),
        summary_json=json.dumps(summary["summary_json"], ensure_ascii=False),
    )


def build_prompt_for_data_plan(summary: Dict[str, Any]) -> str:
    """
    构造数据爬取 / 数据构造 / 训练闭环建议用的 prompt。
    prompt 模板从 PromptLoader 中读取。
    """
    loader = PromptLoader()
    template = loader("analyze_metric_report", "data_plan_user")

    return template.format(
        bench_name=summary["bench_name"],
        eval_type=summary["eval_type"],
        task_domain=summary["task_domain"],
        total=summary["total"],
        passed=summary["passed"],
        primary_metric=summary["primary_metric"],
        primary_score=summary["primary_score"],
        top_err=summary["top_err"],
        failure_patterns_json=json.dumps(summary["failure_patterns"], ensure_ascii=False),
        by_stage_json=json.dumps(summary["by_stage"], ensure_ascii=False),
        quick_samples_json=json.dumps(summary["quick_samples"], ensure_ascii=False),
        summary_json=json.dumps(summary["summary_json"], ensure_ascii=False),
    )

def _invoke_prompt(llm, prompt):
    """
    调用 DeepSeek API（兼容 OpenAI SDK写法）
    """
    try:
        # DeepSeek 推荐用 invoke
        resp = llm.invoke(prompt)
        return resp.content
    except Exception as e:
        # fallback（极少数情况）
        try:
            resp = llm.client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return resp.choices[0].message.content
        except Exception as e2:
            raise RuntimeError(f"LLM调用失败: {e} | fallback失败: {e2}")


def analyze_metric_report_node(state: LoopAIState):
    """
    读取 metric_score_node 产出的 metric 结果，生成两类报告：
    1. 自然语言评测报告
    2. 数据爬取 / 数据构造 / 训练建议报告

    同时输出一份结构化 summary JSON，便于后续节点或外部系统复用。
    """
    writer = _safe_get_writer()

    def _emit(message, *, progress=None, data=None):
        if writer:
            writer(StreamEvent(
                current="AnalyzerAgent.analyze_metric_report_node",
                message=message,
                progress=progress,
                data=data
            ).json())

    _emit(
        "开始分析 metric 评测结果",
        progress=0.0,
        data={
            "metric_eval_result_path": _analyzer(state).get("metric_eval_result_path"),
        },
    )

    metric_result = _load_metric_result(state)
    records = _load_records_from_alignment(metric_result)
    summary = _build_summary(state, metric_result, records)

    _emit(
        "已构建 metric 摘要",
        progress=0.2,
        data={
            "bench_name": summary["bench_name"],
            "total": summary["total"],
            "passed": summary["passed"],
            "primary_metric": summary["primary_metric"],
            "primary_score": summary["primary_score"],
            "top_err": summary["top_err"],
        },
    )

    llm = init_model(state)

    report_prompt = build_prompt_for_report(summary)
    _emit(
        "调用模型生成自然语言评测报告",
        progress=0.45,
        data={"prompt_chars": len(report_prompt or "")},
    )
    report_text = _invoke_prompt(llm, report_prompt)

    data_plan_prompt = build_prompt_for_data_plan(summary)
    _emit(
        "调用模型生成数据构造与训练建议",
        progress=0.7,
        data={"prompt_chars": len(data_plan_prompt or "")},
    )
    data_plan_text = _invoke_prompt(llm, data_plan_prompt)

    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = _ensure_analyzer_outdir(state)

    analyzer = _analyzer(state)
    analyzer["analysis_summary_json_path"] = os.path.join(outdir, f"metric_summary_{ts}.json")
    analyzer["analyze_output_report_json_path"] = os.path.join(outdir, f"metric_report_{ts}.json")
    analyzer["analyze_output_report_text_path"] = os.path.join(outdir, f"metric_report_{ts}.txt")
    analyzer["analyze_output_data_plan_text_path"] = os.path.join(outdir, f"metric_data_plan_{ts}.txt")

    _emit(
        "写入 metric 分析报告",
        progress=0.9,
        data={
            "summary_json": analyzer["analysis_summary_json_path"],
            "report_json": analyzer["analyze_output_report_json_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "data_plan_txt": analyzer["analyze_output_data_plan_text_path"],
        },
    )

    report_json = {
        "summary": summary,
        "analysis_report": report_text,
        "data_plan_report": data_plan_text,
    }

    Path(analyzer["analysis_summary_json_path"]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    Path(analyzer["analyze_output_report_json_path"]).write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    Path(analyzer["analyze_output_report_text_path"]).write_text(report_text, encoding="utf-8")
    Path(analyzer["analyze_output_data_plan_text_path"]).write_text(data_plan_text, encoding="utf-8")

    analyzer["analysis_summary"] = summary

    _emit(
        "metric 报告分析完成",
        progress=1.0,
        data={
            "summary_json": analyzer["analysis_summary_json_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "data_plan_txt": analyzer["analyze_output_data_plan_text_path"],
        },
    )

    logger.info(
        f"已写入：{analyzer['analysis_summary_json_path']}\n"
        f"已写入：{analyzer['analyze_output_report_json_path']}\n"
        f"已写入：{analyzer['analyze_output_report_text_path']}\n"
        f"已写入：{analyzer['analyze_output_data_plan_text_path']}"
    )

    return state