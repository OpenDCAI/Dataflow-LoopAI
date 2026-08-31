# -*- coding: utf-8 -*-
import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple

from loopai.common.event_tool import StreamEvent
from loopai.skills.Analyzer.utils.stream import get_safe_stream_writer
from loopai.common.prompts.prompt_loader import PromptLoader
from loopai.skills.Analyzer.bucket_strategy import build_training_bucket_strategy
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
    runtime_outdir = cfg.get("runtime_output_dir")
    if runtime_outdir:
        outdir = Path(runtime_outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        return str(outdir)
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
    return get_safe_stream_writer()

def _runtime_api_key(cfg: dict) -> str:
    return (
        cfg.get("analyze_api_key")
        or os.getenv("_LOOPAI_ANALYZER_RUNTIME_API_KEY")
        or os.getenv("ANALYZER_API_KEY")
        or os.getenv("analyzer_api_key")
        or os.getenv("DEEPSEEK_API_KEY")
        or "EMPTY"
    )


def init_model(state: LoopAIState) -> ChatOpenAI:
    """
    初始化分析用模型。
    使用 OpenAI-compatible / vLLM 风格接口。
    """
    cfg = _analyzer(state)
    model = ChatOpenAI(
        model=cfg["analyze_model_path"],
        api_key=_runtime_api_key(cfg),
        base_url=cfg.get("analyze_base_url"),
        temperature=cfg.get("analyze_temperature", 0.0),
        top_p=cfg.get("analyze_top_p", 0.95),
        timeout=float(cfg.get("analyze_request_timeout_seconds", 300)),
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

    if isinstance(bench, dict):
        meta = bench.get("meta", {}) or {}
    else:
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
    if isinstance(bench, dict):
        bench_name = bench.get("bench_name", "unknown_bench")
        eval_type = bench.get("bench_dataflow_eval_type", "unknown_eval_type")
    else:
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


def _build_obtainer_stats(
    state: LoopAIState,
    metric_result: Dict[str, Any],
    records: List[Dict[str, Any]],
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    """
    为 obtainer 提供更细粒度的数据缺口统计
    """
    primary_metric_name, primary_metric_item = _select_primary_metric(metric_result)
    details = primary_metric_item.get("details", []) or []

    failed_samples = []
    passed_samples = []

    match_type_fail_counter = {}
    match_type_pass_counter = {}
    domain_counter = {}
    field_presence_counter = {}
    bucket_records = []

    for idx, detail in enumerate(details):
        score = _normalize_detail_score(detail)
        rec = records[idx] if idx < len(records) else {}
        domain = (
            rec.get("domain")
            or rec.get("subset")
            or rec.get("source")
            or summary.get("task_domain")
        )

        sample = {
            "idx": idx,
            "domain": domain,
            "question": rec.get("question") or rec.get("prompt") or rec.get("input"),
            "target": rec.get("target") or rec.get("ground_truth") or rec.get("label"),
            "generated_ans": rec.get("generated_ans") or rec.get("completion") or rec.get("prediction"),
        }

        bucket_record = dict(rec)
        bucket_record["passed"] = score != 0.0
        bucket_record["metric_detail"] = detail
        bucket_record["primary_metric"] = primary_metric_name
        bucket_record.setdefault("generated_ans", sample["generated_ans"])
        bucket_record.setdefault("domain", domain)
        bucket_records.append(bucket_record)

        if isinstance(detail, dict):
            sample["match_type"] = detail.get("match_type")
            sample["extracted"] = detail.get("extracted")
            sample["raw_pred"] = detail.get("raw_pred")

        if domain:
            domain_counter[domain] = domain_counter.get(domain, 0) + 1

        for k, v in rec.items():
            if v not in [None, "", [], {}]:
                field_presence_counter[k] = field_presence_counter.get(k, 0) + 1

        if score == 0.0:
            failed_samples.append(sample)
            mt = sample.get("match_type") or "primary_metric_failure"
            match_type_fail_counter[mt] = match_type_fail_counter.get(mt, 0) + 1
        else:
            passed_samples.append(sample)
            mt = sample.get("match_type") or "matched"
            match_type_pass_counter[mt] = match_type_pass_counter.get(mt, 0) + 1

    fail_bias_match_type = []
    all_match_types = set(match_type_fail_counter.keys()) | set(match_type_pass_counter.keys())
    for mt in all_match_types:
        f = match_type_fail_counter.get(mt, 0)
        p = match_type_pass_counter.get(mt, 0)
        fail_bias_match_type.append({
            "match_type": mt,
            "fail_count": f,
            "pass_count": p,
            "bias": f - p,
        })
    fail_bias_match_type.sort(key=lambda x: (-x["bias"], -x["fail_count"], x["match_type"]))

    representative_failure_samples = []
    for s in failed_samples[:20]:
        representative_failure_samples.append({
            "idx": s.get("idx"),
            "domain": s.get("domain"),
            "match_type": s.get("match_type"),
            "question": s.get("question"),
            "target": s.get("target"),
            "generated_ans": s.get("generated_ans"),
            "extracted": s.get("extracted"),
            "raw_pred": s.get("raw_pred"),
        })

    top_fields = sorted(field_presence_counter.items(), key=lambda x: x[1], reverse=True)[:20]
    top_domains = sorted(domain_counter.items(), key=lambda x: x[1], reverse=True)[:20]
    analyzer_cfg = _analyzer(state)
    allocation_plan = build_training_bucket_strategy(
        bucket_records,
        task_type="general",
        alpha=float(analyzer_cfg.get("bucket_power_alpha", 1.0)),
        min_share=float(analyzer_cfg.get("bucket_min_share", 0.05)),
        max_share=float(analyzer_cfg.get("bucket_max_share", 0.50)),
    )

    return {
        "primary_metric": primary_metric_name,
        "failed_total": len(failed_samples),
        "passed_total": len(passed_samples),
        "failure_match_type_top": sorted(match_type_fail_counter.items(), key=lambda x: x[1], reverse=True)[:10],
        "domain_top": top_domains,
        "field_presence_top": top_fields,
        "fail_bias_match_type": fail_bias_match_type[:15],
        "representative_failure_samples": representative_failure_samples,
        "actionable_bucket_top": [
            [row["label"], row["count"]]
            for row in allocation_plan.get("buckets", [])
        ],
        "allocation_plan": allocation_plan,
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

    prompt = template.format(
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
    allocation_json = json.dumps(summary.get("allocation_plan") or {}, ensure_ascii=False)
    return prompt + f"""

【General Text 分桶约束】
1. 必须优先使用 summary 中的 allocation_plan，不得直接按 primary metric 失败比例分配训练数据。
2. 指令遵循、相关性、事实性、推理、完整性、语言质量和安全拒答是相互独立的能力桶。
3. other/待诊断样本不进入训练预算，只能建议补充评测证据或人工复核。
4. recommended_percent 仅表示第一轮先验预算；小规模试训后应按单位样本指标收益更新。
allocation_plan={allocation_json}
"""

def build_prompt_for_obtainer(summary: Dict[str, Any], obtainer_stats: Dict[str, Any]) -> str:
    """
    构造细粒度 obtainer 侧报告用的 prompt。
    prompt 模板从 PromptLoader 中读取。
    """
    loader = PromptLoader()
    template = loader("data_obtainer", "suggest_obtainer")

    prompt = template.format(
        dataset_json=json.dumps({
            "bench_name": summary["bench_name"],
            "eval_type": summary["eval_type"],
            "task_domain": summary["task_domain"],
            "primary_metric": summary["primary_metric"],
            "primary_score": summary["primary_score"],
            "total": summary["total"],
        }, ensure_ascii=False),
        summary_json=json.dumps(summary["summary_json"], ensure_ascii=False),
        obtainer_stats_json=json.dumps(obtainer_stats, ensure_ascii=False),
    )
    return prompt + """

【General Text 数据获取约束】
1. 使用 allocation_plan.recommended_percent 生成能力级数据预算，再参考 domain_breakdown 选择内容领域。
2. 不得把 primary_metric_failure、unknown 或 other 直接当作可采集的数据类型。
3. 每个能力桶的数据建议必须对应 sample_direction，并说明验证该能力提升的指标。
"""


def _render_allocation_plan(allocation_plan: Dict[str, Any]) -> str:
    rows = allocation_plan.get("buckets") or []
    if not rows:
        return ""
    lines = [
        "【General Text 训练数据分桶建议】",
        "错误出现频率只作为需求信号；以下比例同时考虑归因置信度、严重性、迁移价值、学习效率和数据成本。",
    ]
    for row in sorted(rows, key=lambda item: -item.get("recommended_share", 0.0)):
        lines.append(
            f"- {row.get('label')}：{row.get('recommended_percent', 0):.2f}% "
            f"（观察 {row.get('count', 0)} 条，置信度 {row.get('classification_confidence', 0):.2f}）"
        )
        lines.append(f"  样本方向：{row.get('sample_direction', '')}")
        domains = row.get("domain_breakdown") or []
        if domains:
            domain_text = "、".join(
                f"{item.get('domain')} {item.get('count')} 条" for item in domains[:5]
            )
            lines.append(f"  领域分布：{domain_text}")
    other_impact = allocation_plan.get("other_impact") or {}
    lines.append(
        "- 待诊断样本：0.00% 训练预算"
        f"（重分类后仍未解决 {other_impact.get('unresolved_count', 0)} 条）"
    )
    lines.append(f"- 动态更新：{allocation_plan.get('pilot_update_rule', '')}")
    return "\n".join(lines)

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
                current="analyzer.analyze_metric_report",
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
    obtainer_stats = _build_obtainer_stats(state, metric_result, records, summary)
    allocation_plan = obtainer_stats.get("allocation_plan") or {}
    summary["allocation_plan"] = allocation_plan
    _analyzer(state)["allocation_plan"] = allocation_plan

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

    obtainer_prompt = build_prompt_for_obtainer(summary, obtainer_stats)
    _emit(
        "调用模型生成细粒度 obtainer 侧报告",
        progress=0.82,
        data={"prompt_chars": len(obtainer_prompt or "")},
    )
    obtainer_text = _invoke_prompt(llm, obtainer_prompt)
    allocation_text = _render_allocation_plan(allocation_plan)
    if allocation_text:
        report_text = f"{report_text.rstrip()}\n\n{allocation_text}\n"
        data_plan_text = f"{data_plan_text.rstrip()}\n\n{allocation_text}\n"
        obtainer_text = f"{obtainer_text.rstrip()}\n\n{allocation_text}\n"

    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = _ensure_analyzer_outdir(state)

    analyzer = _analyzer(state)
    analyzer["analysis_summary_json_path"] = os.path.join(outdir, f"metric_summary_{ts}.json")
    analyzer["analyze_output_report_json_path"] = os.path.join(outdir, f"metric_report_{ts}.json")
    analyzer["analyze_output_report_text_path"] = os.path.join(outdir, f"metric_report_{ts}.txt")
    analyzer["analyze_output_data_plan_text_path"] = os.path.join(outdir, f"metric_data_plan_{ts}.txt")
    analyzer["analyze_output_obtainer_json_path"] = os.path.join(outdir, f"metric_obtainer_{ts}.json")
    analyzer["analyze_output_obtainer_text_path"] = os.path.join(outdir, f"metric_obtainer_{ts}.txt")

    _emit(
        "写入 metric 分析报告",
        progress=0.9,
        data={
            "summary_json": analyzer["analysis_summary_json_path"],
            "report_json": analyzer["analyze_output_report_json_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "data_plan_txt": analyzer["analyze_output_data_plan_text_path"],
            "obtainer_json": analyzer["analyze_output_obtainer_json_path"],
            "obtainer_txt": analyzer["analyze_output_obtainer_text_path"],
        },
    )

    report_json = {
        "summary": summary,
        "analysis_report": report_text,
        "data_plan_report": data_plan_text,
        "obtainer_stats": obtainer_stats,
        "obtainer_report": obtainer_text,
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
    Path(analyzer["analyze_output_obtainer_json_path"]).write_text(
        json.dumps(obtainer_stats, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    Path(analyzer["analyze_output_obtainer_text_path"]).write_text(obtainer_text, encoding="utf-8")

    analyzer["analysis_summary"] = summary

    _emit(
        "metric 报告分析完成",
        progress=1.0,
        data={
            "summary_json": analyzer["analysis_summary_json_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "data_plan_txt": analyzer["analyze_output_data_plan_text_path"],
            "obtainer_txt": analyzer["analyze_output_obtainer_text_path"],
        },
    )

    logger.info(
        f"已写入：{analyzer['analysis_summary_json_path']}\n"
        f"已写入：{analyzer['analyze_output_report_json_path']}\n"
        f"已写入：{analyzer['analyze_output_report_text_path']}\n"
        f"已写入：{analyzer['analyze_output_data_plan_text_path']}\n"
        f"已写入：{analyzer['analyze_output_obtainer_json_path']}\n"
        f"已写入：{analyzer['analyze_output_obtainer_text_path']}"
    )

    return state
