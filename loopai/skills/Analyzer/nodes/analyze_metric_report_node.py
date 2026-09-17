# -*- coding: utf-8 -*-
import os
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Callable

from loopai.common.event_tool import StreamEvent
from loopai.skills.Analyzer.utils.stream import get_safe_stream_writer
from loopai.common.prompts.prompt_loader import PromptLoader
from loopai.skills.Analyzer.bucket_strategy import build_training_bucket_strategy
from loopai.skills.Analyzer.math_llmaj_quality import MATH_TAG_DESCRIPTIONS
from langchain_openai import ChatOpenAI
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

logger = get_logger()


CRITIQUE_PROFILE_SCHEMA = "short_critique_profile_v1"

MATH_REPORT_BUNDLE_DIRNAME = "数学评测最终报告"
from loopai.skills.Analyzer.report_bundle import REPORT_FILENAMES, register_report_bundle, write_report_text

MATH_REPORT_FILENAMES = {key: REPORT_FILENAMES[key] for key in ("summary", "report", "final_report", "suggestions", "obtainer")}
MATH_ROLLOUT_REPORT_FILENAMES = {key: REPORT_FILENAMES[key] for key in ("rollout", "training", "training_plan")}


def _write_math_report_text(path: str, text: str) -> None:
    write_report_text(path, text)

_CRITIQUE_CRAWL_HINTS = {
    "评测异常": {
        "source_types": ["Metric 等价性回归集", "答案提取边界样本"],
        "search_queries": ["数学表达式等价判定 测试集", "数学答案提取 normalization regression"],
        "sample_spec": "收集参考答案与模型答案的等价表达对，只用于测试和修复 Metric，不加入模型训练集。",
    },
    "输出格式错误": {
        "source_types": ["带严格答案协议的数学题库", "结构化输出纠错样本"],
        "search_queries": ["数学解题 完整推导 最终答案格式", "数学答案抽取 格式纠错 数据集"],
        "sample_spec": "保留题目、完整推导、单一可提取最终答案，并加入格式错误与正确格式的对照样本。",
    },
    "计算错误": {
        "source_types": ["基础运算题库", "带逐步验算的应用题"],
        "search_queries": ["数学逐步计算 验算 数据集", "算术应用题 完整解题步骤"],
        "sample_spec": "覆盖整数、分数、小数、比例和代入计算，每一步均给出可机械校验的中间结果。",
    },
    "化简错误": {
        "source_types": ["代数恒等变换题库", "符号化简过程数据"],
        "search_queries": ["代数化简 等价变形 完整步骤", "符号推理 因式分解 数据集"],
        "sample_spec": "采集展开、合并同类项、因式分解和分式化简，并标注每次变换的等价条件。",
    },
    "题意理解错误": {
        "source_types": ["数学应用题", "条件到方程的建模样本"],
        "search_queries": ["数学应用题 建模 列式 完整解析", "题意理解 条件抽取 数学数据集"],
        "sample_spec": "显式标注已知条件、未知量、约束关系和求解目标，包含相近题意的对比样本。",
    },
    "公式使用错误或遗漏": {
        "source_types": ["定理公式应用题库", "公式适用条件辨析样本"],
        "search_queries": ["数学公式 适用条件 例题", "定理选择 错误公式 对比样本"],
        "sample_spec": "同时提供公式、成立条件、正确代入过程和常见误用反例，强调何时不可使用。",
    },
    "答案与过程不符": {
        "source_types": ["过程答案一致性校验题", "解题结果自检样本"],
        "search_queries": ["数学推导 最终答案 一致性检查", "数学解题 自我验证 数据集"],
        "sample_spec": "要求从最后一个有效中间结果重新计算答案，并设置过程正确但抄写答案错误的对照样本。",
    },
    "答题步骤不完整": {
        "source_types": ["完整证明与推导题库", "缺步补全训练样本"],
        "search_queries": ["数学完整推导 步骤补全 数据集", "数学证明 关键步骤 标注"],
        "sample_spec": "标注必要步骤、关键依据和验证环节，构造缺少关键步骤与完整解答的成对样本。",
    },
}


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


def _safe_math_report_subject_name(summary: Dict[str, Any]) -> str:
    """Build a readable, portable directory name for one Math dataset."""
    dataset = summary.get("dataset") if isinstance(summary.get("dataset"), dict) else {}
    value = str(summary.get("bench_name") or "").strip()
    if not value or value == "unknown_bench":
        value = Path(str(dataset.get("source_name") or "数学数据集")).stem
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip(" ._")
    return (value or "数学数据集")[:120]


def _ensure_math_report_bundle(
    state: LoopAIState,
    analyzer_outdir: str,
    summary: Dict[str, Any],
) -> Tuple[Path, Path]:
    """Create the total-report directory and this dataset's report folder."""
    configured_root = str(
        _analyzer(state).get("math_report_bundle_root") or ""
    ).strip()
    if configured_root:
        bundle_root = Path(configured_root).expanduser()
        if not bundle_root.is_absolute():
            bundle_root = Path(analyzer_outdir) / bundle_root
    else:
        bundle_root = Path(analyzer_outdir) / MATH_REPORT_BUNDLE_DIRNAME
    dataset_dir = bundle_root / _safe_math_report_subject_name(summary)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    return bundle_root, dataset_dir


def _render_math_bundle_overview(bundle_root: Path) -> str:
    """Render a human-readable index for every dataset folder in the bundle."""
    dataset_names = sorted(
        path.name for path in bundle_root.iterdir() if path.is_dir()
    )
    lines = [
        "数学垂域 Analyzer 评测报告总览",
        "",
        "本目录汇总 Math Analyzer 生成的人类可读报告与增强 OJ；多 Rollout 输入附带训练需求 JSON，不包含 checkpoint 或运行事件。",
        "",
        "【数据集目录】",
        *([f"- {name}" for name in dataset_names] or ["- 暂无数据集报告"]),
        "",
        "【每个数据集的阅读顺序】",
        "1. 数据集背景与评测概览：数据用途、字段映射、样本分布和主要指标。",
        "2. 完整分析与审计报告：全量失败计数、五段式分析和分桶依据。",
        "3. 最终报告：适合直接阅读与汇报的精简结论。",
        "4. 模型改进建议：优先补强能力及 Metric 修复事项。",
        "5. 数据爬取与构造建议：补数来源、样本结构、质检与闭环方案。",
        "带多次 rollout 的输入另有：6. 五档能力分析；7. SFT 与 RL 训练阶段评估。",
        "8. 08_training_plan.json：SFT 转段二分结果、SFT/RL 布尔值、训练领域题型标签与题号依据。",
        "",
        "【统计口径】",
        "每条失败样本只计入一次；模型能力错误进入训练数据能力桶，评测异常只进入 Metric 回归修复。",
        "短评抽样仅用于形成错误画像，不改变全量分桶计数。",
    ]
    return "\n".join(lines) + "\n"


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
        from loopai.skills.Analyzer.math_rollout import is_rollout_payload, normalize_rollouts
        if is_rollout_payload(data):
            return normalize_rollouts(data)[0]
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
            "question": (
                rec.get("question")
                or rec.get("problem")
                or rec.get("prompt")
                or rec.get("input")
            ),
            "target": next((rec.get(key) for key in ("target", "answer", "ground_truth", "label", "reference")
                            if rec.get(key) is not None and rec.get(key) != ""), None),
            "generated_ans": (
                rec.get("generated_ans")
                or rec.get("completion")
                or rec.get("prediction")
                or rec.get("eval_pred")
            ),
        }
        judge = rec.get("judge") if isinstance(rec.get("judge"), dict) else {}
        if judge:
            sample["judge_tags"] = judge.get("tags")
            sample["judge_reason"] = judge.get("reason")
            sample["short_critique"] = judge.get("short_critique") or judge.get("reason")
            sample["overall_error_tag"] = judge.get("overall_error_tag") or (
                (judge.get("tags") or [None])[0]
            )

        if isinstance(detail, dict):
            sample["match_type"] = detail.get("match_type")
            sample["extracted"] = detail.get("extracted")
            sample["raw_pred"] = detail.get("raw_pred")

        quick_samples.append(sample)

        if len(quick_samples) >= top_k:
            break

    return quick_samples


def _record_case_id(record: Dict[str, Any], index: int) -> str:
    for key in ("case_id", "id", "unique_id", "q_id", "idx"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return str(index)


def _collect_short_critiques(
    records: List[Dict[str, Any]],
    primary_metric_item: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Collect v4.2 short critiques before per-tag sampling is applied."""
    details = primary_metric_item.get("details", []) or []
    critiques: List[Dict[str, Any]] = []

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
        short_critique = str(
            judge.get("short_critique") or record.get("short_critique") or ""
        ).strip()
        overall_tag = str(
            judge.get("overall_error_tag") or record.get("overall_error_tag") or ""
        ).strip()
        tags = judge.get("tags")
        if not overall_tag and isinstance(tags, list) and tags:
            overall_tag = str(tags[0] or "").strip()
        if not short_critique and overall_tag:
            # v4.2 mirrors the critique into reason; this keeps early v4.2 outputs readable.
            short_critique = str(judge.get("reason") or "").strip()
        if not short_critique:
            continue

        detail = details[index] if index < len(details) else None
        score = _normalize_detail_score(detail) if detail is not None else None
        critiques.append({
            "item_id": f"critique-{index}",
            "source_index": index,
            "case_id": _record_case_id(record, index),
            "metric_score": score,
            "domain": judge.get("domain") or record.get("domain") or record.get("subset") or "unknown",
            "overall_error_tag": overall_tag or "判因未完成",
            "tag_description": MATH_TAG_DESCRIPTIONS.get(overall_tag, ""),
            "short_critique": short_critique,
            "first_error_step": judge.get("first_error_step") or "",
            "repair_target": judge.get("repair_target") or "",
            "needs_review": bool(judge.get("needs_review", not overall_tag)),
            "actionable": bool(judge.get("actionable", False)),
        })

    return critiques


def _parse_critique_sample_limit(value: Any) -> Tuple[str, Optional[int]]:
    text = str(value if value is not None else 5).strip().lower()
    if text == "full":
        return "full", None
    try:
        limit = int(text)
    except (TypeError, ValueError):
        limit = 5
    return "per_tag_limit", max(1, limit)


def _evenly_spaced_sample(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[0]]
    indices = [round(index * (len(rows) - 1) / (limit - 1)) for index in range(limit)]
    return [rows[index] for index in indices]


def _select_critiques_per_tag(
    critiques: List[Dict[str, Any]],
    sample_value: Any = 5,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Select an evenly spread sample for every label, or all rows for ``full``."""
    mode, limit = _parse_critique_sample_limit(sample_value)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in critiques:
        tag = str(item.get("overall_error_tag") or "判因未完成")
        grouped.setdefault(tag, []).append(item)

    selected: List[Dict[str, Any]] = []
    per_tag: Dict[str, Dict[str, int]] = {}
    for tag, rows in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        picked = list(rows) if mode == "full" else _evenly_spaced_sample(rows, limit or 5)
        selected.extend(picked)
        per_tag[tag] = {
            "available": len(rows),
            "selected": len(picked),
        }

    return selected, {
        "mode": mode,
        "configured_value": "full" if mode == "full" else limit,
        "available_short_critiques": len(critiques),
        "selected_short_critiques": len(selected),
        "per_tag": per_tag,
    }


def _pack_critique_batches(
    critiques: List[Dict[str, Any]],
    *,
    batch_size: int = 40,
    max_chars: int = 12000,
) -> List[List[Dict[str, Any]]]:
    """Pack critiques without dropping or duplicating an item."""
    batch_size = max(1, int(batch_size))
    max_chars = max(1000, int(max_chars))
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_chars = 0

    for item in critiques:
        item_chars = len(json.dumps(item, ensure_ascii=False))
        exceeds_budget = current and current_chars + item_chars > max_chars
        if current and (len(current) >= batch_size or exceeds_budget):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars

    if current:
        batches.append(current)
    return batches


def _safe_json_object(text: Any) -> Optional[Dict[str, Any]]:
    if isinstance(text, dict):
        return text
    value = str(text or "").strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, flags=re.I | re.S)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(value[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _string_list(value: Any, *, limit: int = 12) -> List[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _normalize_profile_payload(payload: Any) -> Optional[Dict[str, Any]]:
    obj = _safe_json_object(payload)
    if not obj:
        return None
    raw_patterns = obj.get("error_profile") or obj.get("error_patterns") or obj.get("patterns") or []
    raw_recommendations = obj.get("crawl_recommendations") or obj.get("recommendations") or []

    patterns: List[Dict[str, Any]] = []
    if isinstance(raw_patterns, list):
        for row in raw_patterns[:20]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("title") or "").strip()
            description = str(row.get("description") or row.get("portrait") or "").strip()
            if not name and not description:
                continue
            try:
                affected_count = int(
                    row.get("sampled_evidence_count")
                    or row.get("affected_count")
                    or row.get("count")
                    or 0
                )
            except Exception:
                affected_count = 0
            patterns.append({
                "name": name or "未命名错误模式",
                "description": description,
                "sampled_evidence_count": max(0, affected_count),
                # Compatibility alias. This is a count inside the selected
                # critique sample, never the full bad-case population.
                "affected_count": max(0, affected_count),
                "overall_error_tags": _string_list(
                    row.get("overall_error_tags") or row.get("tags")
                ),
                "domains": _string_list(row.get("domains")),
                "representative_critiques": _string_list(
                    row.get("representative_critiques") or row.get("examples"), limit=5
                ),
                "learning_need": str(row.get("learning_need") or row.get("training_need") or "").strip(),
            })

    recommendations: List[Dict[str, Any]] = []
    if isinstance(raw_recommendations, list):
        for row in raw_recommendations[:20]:
            if not isinstance(row, dict):
                continue
            target_gap = str(row.get("target_gap") or row.get("name") or row.get("title") or "").strip()
            if not target_gap:
                continue
            try:
                priority = int(row.get("priority") or len(recommendations) + 1)
            except Exception:
                priority = len(recommendations) + 1
            recommendations.append({
                "priority": max(1, priority),
                "target_gap": target_gap,
                "source_types": _string_list(row.get("source_types") or row.get("sources"), limit=8),
                "search_queries": _string_list(row.get("search_queries") or row.get("queries"), limit=10),
                "sample_spec": str(row.get("sample_spec") or row.get("sample_shape") or "").strip(),
                "quality_checks": _string_list(
                    row.get("quality_checks") or row.get("acceptance_criteria"), limit=8
                ),
                "target_metric": str(row.get("target_metric") or "").strip(),
            })

    if not patterns and not recommendations:
        return None
    return {
        "error_profile": patterns,
        "crawl_recommendations": recommendations,
    }


def _fallback_profile_for_critiques(critiques: List[Dict[str, Any]], task_type: str = "math") -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in critiques:
        grouped.setdefault(str(item.get("overall_error_tag") or "判因未完成"), []).append(item)

    patterns: List[Dict[str, Any]] = []
    recommendations: List[Dict[str, Any]] = []
    ordered = sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    for priority, (tag, rows) in enumerate(ordered, start=1):
        examples = _string_list([row.get("short_critique") for row in rows], limit=3)
        domains = _string_list([row.get("domain") for row in rows], limit=8)
        description = "；".join(examples)
        patterns.append({
            "name": tag,
            "description": description or MATH_TAG_DESCRIPTIONS.get(tag, "需进一步归纳该类失败。"),
            "sampled_evidence_count": len(rows),
            "affected_count": len(rows),
            "overall_error_tags": [tag],
            "domains": domains,
            "representative_critiques": examples,
            "learning_need": MATH_TAG_DESCRIPTIONS.get(tag, "补充可验证的同类纠错样本。"),
        })

        hint = _CRITIQUE_CRAWL_HINTS.get(tag, {
            "source_types": ["带完整过程和人工错因标注的数学题库"],
            "search_queries": [f"{tag} 数学错题 完整解析 数据集"],
            "sample_spec": "保留题目、标准推导、错误作答、短评、总错因标签和可验证最终答案。",
        })
        if task_type != "math":
            hint = {
                "source_types": ["带独立测试用例的编程题库" if task_type == "code" else "包含数据库 schema 与可执行参考查询的 SQL 数据集"],
                "search_queries": [f"{task_type} {tag} 可验证训练数据"],
                "sample_spec": ("采集独立题目、函数接口、正确补全、边界测试与纠错对照。" if task_type == "code" else
                                "采集独立业务问题、schema、参考 SQL、数据库快照与查询结果校验。"),
            }
        recommendations.append({
            "priority": priority,
            "target_gap": tag,
            "source_types": list(hint["source_types"]),
            "search_queries": list(hint["search_queries"]),
            "sample_spec": hint["sample_spec"],
            "quality_checks": (
                ["等价答案应判为通过", "提取结果与最终答案一致", "加入 Metric 回归测试"]
                if tag == "评测异常"
                else ["题目与答案可验证", "推导步骤完整", "错因标签与短评一致", "去重并隔离评测集"]
            ),
            "target_metric": (
                "Metric 误判率与答案提取成功率"
                if tag == "评测异常"
                else "对应能力桶的步骤正确率与最终答案正确率"
            ),
        })

    return {
        "error_profile": patterns,
        "crawl_recommendations": recommendations,
    }


def _build_critique_batch_prompt(
    batch: List[Dict[str, Any]],
    *,
    batch_index: int,
    batch_total: int,
) -> str:
    labels = _string_list([item.get("overall_error_tag") for item in batch], limit=20)
    return f"""
你是 Analyzer 的错误画像归纳器。下面是按总错因标签抽取的代表短评，位于全部输入的第 {batch_index}/{batch_total} 批。
本批标签为：{json.dumps(labels, ensure_ascii=False)}。
必须逐条阅读本批中的每个 short_critique，分别分析各标签内部的具体表现，不得只看标签名称，也不得遗漏尾部记录。
只基于给定短评、总错因标签、领域、首错和修复目标归纳，不得猜测未提供的解题内容。

请只输出一个 JSON 对象：
{{
  "error_profile": [
    {{
      "name": "可辨识的错误模式",
      "description": "该模式的具体表现与共同特征",
      "sampled_evidence_count": 该模式覆盖的本批代表短评数,
      "overall_error_tags": ["相关总错因标签"],
      "domains": ["相关领域"],
      "representative_critiques": ["本批原始短评，最多3条"],
      "learning_need": "模型需要补强的能力"
    }}
  ],
  "crawl_recommendations": [
    {{
      "priority": 1,
      "target_gap": "要补强的具体缺陷",
      "source_types": ["可执行的数据来源类型"],
      "search_queries": ["可直接搜索的查询词"],
      "sample_spec": "应采集或合成的样本结构",
      "quality_checks": ["验收条件"],
      "target_metric": "用于闭环验证的指标"
    }}
  ]
}}

要求：
1. 错误画像应合并语义相近的短评，但不能用笼统的 other/unknown 代替已有具体信息。
2. 爬取建议必须能直接执行，并与画像中的缺陷逐项对应。
3. 不得把错误出现比例直接等同于训练数据比例；这里只描述需求和优先级。
4. 「评测异常」只能生成 Metric 回归与修复建议，禁止生成模型训练或爬取数据建议。
5. 不输出 Markdown，不输出 JSON 之外的文字。

本批短评：
{json.dumps(batch, ensure_ascii=False)}
""".strip()


def _merge_profile_payloads_deterministically(
    payloads: List[Dict[str, Any]],
) -> Dict[str, Any]:
    patterns_by_name: Dict[str, Dict[str, Any]] = {}
    recommendations_by_gap: Dict[str, Dict[str, Any]] = {}

    for payload in payloads:
        for row in payload.get("error_profile") or []:
            name = str(row.get("name") or "未命名错误模式")
            target = patterns_by_name.setdefault(name, {
                "name": name,
                "description": row.get("description") or "",
                "sampled_evidence_count": 0,
                "affected_count": 0,
                "overall_error_tags": [],
                "domains": [],
                "representative_critiques": [],
                "learning_need": row.get("learning_need") or "",
            })
            sampled_count = int(
                row.get("sampled_evidence_count")
                or row.get("affected_count")
                or 0
            )
            target["sampled_evidence_count"] += sampled_count
            target["affected_count"] += sampled_count
            for key, limit in (("overall_error_tags", 12), ("domains", 12), ("representative_critiques", 5)):
                target[key] = _string_list(target[key] + _string_list(row.get(key), limit=limit), limit=limit)
            if not target["description"]:
                target["description"] = row.get("description") or ""
            if not target["learning_need"]:
                target["learning_need"] = row.get("learning_need") or ""

        for row in payload.get("crawl_recommendations") or []:
            gap = str(row.get("target_gap") or "").strip()
            if not gap:
                continue
            target = recommendations_by_gap.setdefault(gap, dict(row))
            for key, limit in (("source_types", 8), ("search_queries", 10), ("quality_checks", 8)):
                target[key] = _string_list(
                    _string_list(target.get(key), limit=limit) + _string_list(row.get(key), limit=limit),
                    limit=limit,
                )

    patterns = sorted(
        patterns_by_name.values(),
        key=lambda row: (
            -int(row.get("sampled_evidence_count") or row.get("affected_count") or 0),
            row.get("name") or "",
        ),
    )
    recommendations = list(recommendations_by_gap.values())
    for index, row in enumerate(recommendations, start=1):
        row["priority"] = index
    return {
        "error_profile": patterns,
        "crawl_recommendations": recommendations,
    }


def _build_critique_reduce_prompt(
    payloads: List[Dict[str, Any]],
    *,
    reduce_round: int,
) -> str:
    return f"""
你是 Analyzer 的全局错误画像整合器。以下是各标签短评经过抽样或全量阅读后的中间归纳，当前为第 {reduce_round} 轮归并。
请合并重复模式、保留有区别的具体缺陷，并形成全局错误画像和统一爬取建议。

只输出与下列结构一致的 JSON：
{{
  "error_profile": [{{"name":"", "description":"", "sampled_evidence_count":0,
    "overall_error_tags":[], "domains":[], "representative_critiques":[], "learning_need":""}}],
  "crawl_recommendations": [{{"priority":1, "target_gap":"", "source_types":[],
    "search_queries":[], "sample_spec":"", "quality_checks":[], "target_metric":""}}]
}}

约束：
1. 不得用 other/unknown 覆盖已有具体短评；相近模式可合并，但要保留具体表现。
2. sampled_evidence_count 只表示抽中并阅读的代表短评数，不是全量错误数，
   不得直接当作训练数据预算。
3. 爬取建议需给出来源、检索词、样本结构、质检条件和闭环指标。
4. 「评测异常」必须与模型能力错误分开，只能进入 Metric 修复计划。
5. 不输出 Markdown，不输出 JSON 之外的文字。

待归并结果：
{json.dumps(payloads, ensure_ascii=False)}
""".strip()


def _build_critique_profile(
    llm: Any,
    critiques: List[Dict[str, Any]],
    *,
    samples_per_tag: Any = 5,
    batch_size: int = 40,
    max_chars: int = 12000,
    reduce_group_size: int = 8,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    task_type: str = "math",
    invoke_prompt: Optional[Callable[[Any, str], str]] = None,
) -> Dict[str, Any]:
    call = invoke_prompt or _invoke_prompt
    selected_critiques, selection = _select_critiques_per_tag(
        critiques,
        samples_per_tag,
    )
    batches = _pack_critique_batches(
        selected_critiques,
        batch_size=batch_size,
        max_chars=max_chars,
    )
    if not batches:
        return {
            "schema_version": CRITIQUE_PROFILE_SCHEMA,
            "status": "not_applicable",
            "selection": selection,
            "coverage": {
                "available_short_critiques": len(critiques),
                "selected_short_critiques": 0,
                "processed_short_critiques": 0,
                "batch_count": 0,
                "all_selected_critiques_processed": True,
                "all_available_critiques_processed": len(critiques) == 0,
            },
            "tag_distribution": {},
            "domain_distribution": {},
            "error_profile": [],
            "crawl_recommendations": [],
        }

    partials: List[Dict[str, Any]] = []
    fallback_batches = 0
    for batch_index, batch in enumerate(batches, start=1):
        if progress_callback:
            progress_callback("map", batch_index, len(batches))
        prompt = _build_critique_batch_prompt(
            batch,
            batch_index=batch_index,
            batch_total=len(batches),
        )
        try:
            normalized = (
                _normalize_profile_payload(call(llm, prompt))
                if llm is not None else None
            )
        except Exception as exc:
            logger.warning(f"[analyze_metric_report] 短评批次 {batch_index} 归纳失败，使用规则兜底: {exc}")
            normalized = None
        if normalized is None:
            fallback_batches += 1
            normalized = _fallback_profile_for_critiques(batch, task_type)
        partials.append(normalized)

    reduce_group_size = max(2, int(reduce_group_size))
    reduce_rounds = 0
    reduce_fallbacks = 0
    while len(partials) > 1:
        reduce_rounds += 1
        groups = [
            partials[index:index + reduce_group_size]
            for index in range(0, len(partials), reduce_group_size)
        ]
        reduced: List[Dict[str, Any]] = []
        for group_index, group in enumerate(groups, start=1):
            if len(group) == 1:
                reduced.append(group[0])
                continue
            if progress_callback:
                progress_callback("reduce", group_index, len(groups))
            prompt = _build_critique_reduce_prompt(group, reduce_round=reduce_rounds)
            try:
                normalized = (
                    _normalize_profile_payload(call(llm, prompt))
                    if llm is not None else None
                )
            except Exception as exc:
                logger.warning(f"[analyze_metric_report] 短评归并第 {reduce_rounds} 轮失败，使用规则兜底: {exc}")
                normalized = None
            if normalized is None:
                reduce_fallbacks += 1
                normalized = _merge_profile_payloads_deterministically(group)
            reduced.append(normalized)
        partials = reduced

    final_sections = partials[0] if partials else _fallback_profile_for_critiques(selected_critiques, task_type)
    deterministic_sections = _fallback_profile_for_critiques(selected_critiques, task_type)
    if not final_sections.get("error_profile"):
        final_sections["error_profile"] = deterministic_sections["error_profile"]
    if not final_sections.get("crawl_recommendations"):
        final_sections["crawl_recommendations"] = deterministic_sections["crawl_recommendations"]
    tag_distribution = dict(Counter(
        str(item.get("overall_error_tag") or "判因未完成") for item in critiques
    ).most_common())
    domain_distribution = dict(Counter(
        str(item.get("domain") or "unknown") for item in critiques
    ).most_common())
    return {
        "schema_version": CRITIQUE_PROFILE_SCHEMA,
        "status": "completed",
        "analysis_mode": (
            "deterministic_fallback"
            if llm is None else (
                "llm_map_reduce"
                if fallback_batches == 0 and reduce_fallbacks == 0
                else "llm_map_reduce_with_deterministic_fallback"
            )
        ),
        "selection": selection,
        "coverage": {
            "available_short_critiques": len(critiques),
            "selected_short_critiques": len(selected_critiques),
            "processed_short_critiques": len(selected_critiques),
            "batch_count": len(batches),
            "batch_sizes": [len(batch) for batch in batches],
            "fallback_batch_count": fallback_batches,
            "reduce_rounds": reduce_rounds,
            "reduce_fallback_count": reduce_fallbacks,
            "all_selected_critiques_processed": (
                sum(len(batch) for batch in batches) == len(selected_critiques)
            ),
            "all_available_critiques_processed": len(selected_critiques) == len(critiques),
        },
        "tag_distribution": tag_distribution,
        "domain_distribution": domain_distribution,
        "error_profile": final_sections.get("error_profile") or [],
        "crawl_recommendations": final_sections.get("crawl_recommendations") or [],
    }


def _render_critique_profile_sections(profile: Dict[str, Any]) -> Tuple[str, str]:
    coverage = profile.get("coverage") or {}
    available = int(coverage.get("available_short_critiques") or 0)
    selected = int(coverage.get("selected_short_critiques") or 0)
    if selected <= 0:
        return "", ""

    selection = profile.get("selection") or {}
    mode = selection.get("mode") or "per_tag_limit"
    scope = "全部短评" if mode == "full" else f"每标签最多 {selection.get('configured_value', 5)} 条"
    sample_distribution = "、".join(
        f"{tag}（共 {counts.get('available', 0)} 条，读取 {counts.get('selected', 0)} 条）"
        for tag, counts in (selection.get("per_tag") or {}).items()
        if isinstance(counts, dict)
    ) or "无"
    tag_distribution = "、".join(
        f"{tag} {count} 条"
        for tag, count in (profile.get("tag_distribution") or {}).items()
    ) or "无"
    bucket_distribution = "、".join(
        f"{bucket} {count} 条"
        for bucket, count in (profile.get("population_bucket_distribution") or {}).items()
    ) or "无"
    portrait_lines = [
        "【按总错因标签生成的错误画像】",
        f"抽样规则：{scope}；共发现 {available} 条短评，本轮实际读取并处理 {selected} 条；"
        f"共 {coverage.get('batch_count', 0)} 批。",
        f"各标签抽样：{sample_distribution}",
        f"总错因分布（所有可用短评）：{tag_distribution}",
        f"能力桶分布（全部 bad case）：{bucket_distribution}",
    ]
    for index, row in enumerate(profile.get("error_profile") or [], start=1):
        count = int(row.get("sampled_evidence_count") or row.get("affected_count") or 0)
        portrait_lines.append(
            f"{index}. {row.get('name')}（代表短评 {count} 条）：{row.get('description') or ''}"
        )
        if row.get("learning_need"):
            portrait_lines.append(f"   能力缺口：{row.get('learning_need')}")
        examples = _string_list(row.get("representative_critiques"), limit=3)
        if examples:
            portrait_lines.append(f"   代表短评：{'；'.join(examples)}")

    crawl_lines = [
        "【基于标签短评的总爬取建议】",
        "以下优先级综合各标签代表短评的语义，不把错误数量直接等同于训练数据配额。",
    ]
    for index, row in enumerate(profile.get("crawl_recommendations") or [], start=1):
        crawl_lines.append(f"{index}. {row.get('target_gap')}")
        sources = _string_list(row.get("source_types"), limit=8)
        queries = _string_list(row.get("search_queries"), limit=10)
        checks = _string_list(row.get("quality_checks"), limit=8)
        if sources:
            crawl_lines.append(f"   数据来源：{'、'.join(sources)}")
        if queries:
            crawl_lines.append(f"   检索词：{'；'.join(queries)}")
        if row.get("sample_spec"):
            crawl_lines.append(f"   样本要求：{row.get('sample_spec')}")
        if checks:
            crawl_lines.append(f"   质检：{'；'.join(checks)}")
        if row.get("target_metric"):
            crawl_lines.append(f"   闭环指标：{row.get('target_metric')}")

    return "\n".join(portrait_lines), "\n".join(crawl_lines)


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


def _infer_bucket_task_type(state: LoopAIState, summary: Dict[str, Any]) -> str:
    """Select the capability taxonomy without changing the metric pipeline."""
    analyzer = _analyzer(state)
    configured = str(analyzer.get("analyze_task_type") or "").strip().lower().replace("-", "_")
    if configured in {"code", "coding", "programming", "python"}:
        return "code"
    if configured in {"text2sql", "text_to_sql", "sql"}:
        return "text2sql"
    if configured in {
        "math", "mathematics", "mathematical", "math_reasoning", "math_qa", "数学", "数学推理",
    }:
        return "math"
    if configured in {"general", "general_text", "text", "qa"}:
        return "general"

    primary_metric = str(summary.get("primary_metric") or "").strip().lower()
    if primary_metric in {"math_verify", "numerical_match"}:
        return "math"

    hints = " ".join(
        str(value or "").lower().replace("-", "_")
        for value in (
            summary.get("bench_name"), summary.get("task_domain"), analyzer.get("task_domain"),
        )
    )
    math_hints = (
        "gsm8k", "svamp", "ape210k", "mawps", "asdiv", "competition_math", "math_500",
        "hendrycks_math", "aqua_rat", "gaokao_mathqa", "math_qa", "omni_math", "数学",
    )
    if any(token in hints for token in math_hints) or re.search(r"(?:^|\s)math(?:$|\s|_)", hints):
        return "math"
    return "general"


def _dataset_profile(
    metric_result: Dict[str, Any],
    records: List[Dict[str, Any]],
    *,
    bench_name: str,
    eval_type: str,
    task_domain: str,
    total: int,
) -> Dict[str, Any]:
    """Describe the evaluated dataset without asking the LLM to infer schema."""
    aliases = {
        "question": ("question", "problem", "prompt", "input", "query", "instruction"),
        "reference": ("target", "answer", "ground_truth", "label", "reference", "solution"),
        "prediction": ("generated_ans", "completion", "prediction", "eval_pred", "response", "output"),
    }
    field_counts: Counter = Counter()
    attribute_counters: Dict[str, Counter] = {
        key: Counter()
        for key in (
            "subject", "topic", "subtopic", "level", "course",
            "question_type", "category", "domain",
        )
    }
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            if value not in (None, "", [], {}):
                field_counts[str(key)] += 1
        for key, counter in attribute_counters.items():
            value = record.get(key)
            if value in (None, "", [], {}):
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, (dict, list)):
                    item = json.dumps(item, ensure_ascii=False, sort_keys=True)
                counter[str(item)] += 1

    resolved_fields = {
        role: [key for key in keys if field_counts.get(key, 0) > 0]
        for role, keys in aliases.items()
    }
    example: Dict[str, Any] = {}
    if records and isinstance(records[0], dict):
        for key, value in list(records[0].items())[:16]:
            if isinstance(value, str) and len(value) > 240:
                example[key] = value[:240] + "..."
            elif isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False)
                example[key] = rendered[:240] + ("..." if len(rendered) > 240 else "")
            else:
                example[key] = value

    alignment = metric_result.get("alignment") or {}
    source_path = alignment.get("source_path") or alignment.get("path")
    return {
        "name": bench_name,
        "source_name": Path(source_path).name if source_path else None,
        "task_domain": task_domain,
        "eval_type": eval_type,
        "source_path": source_path,
        "declared_samples": total,
        "loaded_records": len(records),
        "record_count_matches": len(records) == total if records else False,
        "field_schema": [key for key, _ in field_counts.most_common()],
        "field_presence": dict(field_counts.most_common()),
        "attribute_distributions": {
            key: [
                {"value": value, "count": count}
                for value, count in counter.most_common(10)
            ]
            for key, counter in attribute_counters.items()
            if counter
        },
        "resolved_fields": resolved_fields,
        "example": example,
    }


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
    prompt_metric_result = metric_result
    rollout_context = (state.get("analyzer") or {}).get("math_rollout_input")
    if rollout_context:
        # Scores stay intact on disk/in state. The LLM needs aggregates and diagnosis,
        # not thousands of already-consumed metric rows or ten full long trajectories.
        prompt_metric_result = {
            "source_schema": metric_result.get("source_schema"),
            "num_samples": total,
            "unique_questions": rollout_context["unique_questions"],
            "question_run_groups": rollout_context["num_groups"],
            "metrics": {name: {key: value for key, value in item.items() if key != "details"}
                        for name, item in (metric_result.get("metrics") or {}).items()},
            "detail_policy": "完整逐次评分保留在原始指标文件，报告输入仅使用精确汇总。",
        }
        for sample in quick_samples:
            for key in ("generated_ans", "raw_pred"):
                value = sample.get(key)
                if isinstance(value, str) and len(value) > 1600:
                    sample[key] = value[:800] + "\n[报告输入仅展示首尾片段，完整作答保留在 OJ]\n" + value[-800:]
                    sample["prediction_excerpted"] = True
    failure_patterns = _build_failure_patterns(primary_metric_name, primary_metric_item)
    top_err = failure_patterns[0]["name"] if failure_patterns else "none"

    summary = {
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
        "summary_json": prompt_metric_result,
    }
    summary["bucket_task_type"] = _infer_bucket_task_type(state, summary)
    summary["dataset"] = _dataset_profile(
        metric_result,
        records,
        bench_name=bench_name,
        eval_type=eval_type,
        task_domain=task_domain,
        total=total,
    )
    return summary


def _render_summary_report(summary: Dict[str, Any]) -> str:
    dataset = summary.get("dataset") or {}
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    failed = max(0, total - passed)
    resolved = dataset.get("resolved_fields") or {}
    attribute_distributions = dataset.get("attribute_distributions") or {}
    attribute_lines = []
    attribute_labels = {
        "subject": "学科",
        "topic": "主题",
        "subtopic": "子主题",
        "level": "难度级别",
        "course": "课程",
        "question_type": "题型",
        "category": "类别",
        "domain": "领域",
    }
    for key, label in attribute_labels.items():
        rows = attribute_distributions.get(key) or []
        if not rows:
            continue
        rendered = "、".join(
            f"{row.get('value')}（{row.get('count')} 条）" for row in rows[:6]
        )
        attribute_lines.append(f"- {label}分布：{rendered}")
    metric_lines = [
        f"- {name}: {item.get('score')}（{item.get('priority') or '未标优先级'}）"
        for name, item in (summary.get("metric_overview") or {}).items()
        if isinstance(item, dict)
    ]
    return "\n".join([
        "【数据集背景介绍】",
        (
            f"本轮评测任务标识为 {summary.get('bench_name') or '未命名任务'}，"
            f"实际读取 {dataset.get('source_name') or '未命名数据源'}；"
            f"数据用于评估 {summary.get('task_domain') or 'general'} 领域的 "
            f"{summary.get('eval_type') or '通用'} 能力。"
        ),
        (
            f"本轮载入 {dataset.get('loaded_records', 0)} 条记录，评测声明 {total} 条；"
            f"主指标为 {summary.get('primary_metric')}。"
        ),
        f"输入题目字段：{', '.join(resolved.get('question') or []) or '未识别'}。",
        f"参考答案字段：{', '.join(resolved.get('reference') or []) or '未识别'}。",
        f"模型输出字段：{', '.join(resolved.get('prediction') or []) or '未识别'}。",
        *(attribute_lines or ["- 数据未提供可统计的学科、主题或难度字段。"]),
        "",
        "【评测概览】",
        f"- 总样本：{total}",
        f"- 通过：{passed}",
        f"- 失败：{failed}",
        f"- 主指标得分：{summary.get('primary_score')}",
        *(metric_lines or ["- 其他指标：无"]),
    ])


def _render_audit_report(
    summary: Dict[str, Any],
    obtainer_stats: Dict[str, Any],
    critique_profile: Dict[str, Any],
) -> str:
    allocation = obtainer_stats.get("allocation_plan") or {}
    coverage = allocation.get("count_coverage") or {}
    dataset = summary.get("dataset") or {}
    llmaj_stats = obtainer_stats.get("math_llmaj_stats") or {}
    selection = critique_profile.get("selection") or {}
    critique_coverage = critique_profile.get("coverage") or {}

    lines = [
        "【错误审计报告】",
        "【输入与对齐审计】",
        f"- 声明样本数：{dataset.get('declared_samples', summary.get('total', 0))}",
        f"- 实际载入记录数：{dataset.get('loaded_records', 0)}",
        f"- 记录数一致：{'是' if dataset.get('record_count_matches', False) else '否'}",
        f"- 数据路径：{dataset.get('source_path') or '未提供'}",
        "",
        "【全量 bad case 分桶审计】",
        (
            f"- Metric 判定失败 {coverage.get('failed_total', 0)} 条："
            f"确认模型错误 {coverage.get('model_failure_total', 0)} 条，"
            f"评测异常 {coverage.get('metric_anomaly_count', 0)} 条。"
        ),
        (
            f"- 已逐条完成处理 {coverage.get('counted_total', 0)} 条；"
            f"覆盖完整：{'是' if coverage.get('all_failures_counted', False) else '否'}"
        ),
        (
            f"- 确认的模型错误已全部归入能力桶："
            f"{coverage.get('known_bucket_count', 0)} 条。"
        ),
        (
            f"- 构造路由：步骤级修复 {coverage.get('step_construction_total', 0)} 条，"
            f"整题级对比构造 {coverage.get('whole_case_construction_total', 0)} 条。"
        ),
    ]
    for row in allocation.get("buckets") or []:
        lines.append(
            f"- {row.get('label')}：全量 {row.get('observed_count', row.get('count', 0))} 条，"
            f"步骤级修复 {row.get('step_construction_count', 0)} 条，"
            f"整题级构造 {row.get('whole_case_construction_count', 0)} 条。"
        )
    metric_audit = allocation.get("metric_audit_bucket") or {}
    if metric_audit.get("count"):
        lines.append(
            f"- 评测异常：{metric_audit.get('count')} 条，转入 Metric 回归与修复，不进入训练配比。"
        )

    lines.extend([
        "",
        "【判因与短评抽样审计】",
        f"- 有总错因标签：{llmaj_stats.get('tagged_total', 0)} 条",
        f"- 有一句话短评：{llmaj_stats.get('short_critique_count', 0)} 条",
        f"- 已进入训练构造路由：{llmaj_stats.get('actionable_total', 0)} 条",
        (
            f"- 短评仅用于语义归纳：发现 {critique_coverage.get('available_short_critiques', 0)} 条，"
            f"实际读取 {critique_coverage.get('selected_short_critiques', 0)} 条；"
            f"抽样方式={'读取全部' if selection.get('mode') == 'full' else '按标签限额'}，"
            f"每标签最多读取 {selection.get('configured_value', 5)} 条。"
        ),
        "- 上述短评抽样不会改变任何能力桶的全量 count。",
    ])
    return "\n".join(lines)


def _build_direct_badcase_rows(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapt actionable Analyzer actions into pipeline-ready repair seeds."""
    rows: List[Dict[str, Any]] = []
    id_occurrences: Counter = Counter()
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict) or not action.get("actionable") or action.get("needs_review"):
            continue
        seed = action.get("seed_bad_case") if isinstance(action.get("seed_bad_case"), dict) else {}
        if not seed.get("problem") or seed.get("gold_answer") in (None, ""):
            continue
        action_id = str(action.get("action_id") or f"badcase-{action_index}")
        occurrence = id_occurrences[action_id]
        id_occurrences[action_id] += 1
        construction_id = action_id if occurrence == 0 else f"{action_id}-{occurrence}"
        rows.append({
            "schema_version": "analyzer_direct_badcase_repair_v1",
            "construction_id": construction_id,
            "source_action_id": action_id,
            "source_mode": "direct_badcase",
            "external_data_selection_required": False,
            "capability_bucket": action.get("capability_bucket"),
            "domain": action.get("domain"),
            "instruction": seed.get("problem"),
            "reference_answer": seed.get("gold_answer"),
            "negative_response": seed.get("wrong_solution"),
            "diagnosis": {
                "overall_error_tag": action.get("overall_error_tag"),
                "short_critique": action.get("short_critique") or seed.get("short_critique"),
                "first_error_step": action.get("first_error_step") or seed.get("first_error_step"),
                "evidence_quote": action.get("evidence_quote") or seed.get("evidence_quote"),
                "repair_target": action.get("repair_target") or seed.get("repair_target"),
                "confidence": action.get("confidence"),
            },
            "construction": {
                "mode": "contrastive_repair",
                "required_output": "完整、正确、可验证的修复解答",
                "preserve_negative_for_contrast": True,
            },
            "quality_gate": {
                "source_actionable": True,
                "requires_reference_verification": True,
                "requires_benchmark_decontamination": True,
                "requires_deduplication": True,
            },
        })
    return rows


def _render_final_report(
    summary_text: str,
    audit_text: str,
    data_plan_text: str,
    *,
    direct_badcase_count: int,
) -> str:
    direct_section = "\n".join([
        "【Bad case 直构入口】",
        f"- 已识别 {direct_badcase_count} 条可用于直接修复的高置信 bad case。",
        "- 下游可直接读取增强 OJ，跳过外部数据检索/选择，进入纠错样本生成。",
        "- 仍需执行参考答案校验、去重和 benchmark 防泄漏检查。",
    ])
    return "\n\n".join([
        summary_text.strip(),
        audit_text.strip(),
        "【数据构造与训练建议】\n" + data_plan_text.strip(),
        direct_section,
    ]) + "\n"


def _render_math_quick_analysis(
    summary: Dict[str, Any],
    obtainer_stats: Dict[str, Any],
    critique_profile: Dict[str, Any],
) -> str:
    """Render the same five-section report contract without extra LLM calls."""
    allocation = obtainer_stats.get("allocation_plan") or {}
    buckets = sorted(
        allocation.get("buckets") or [],
        key=lambda item: -float(item.get("recommended_share") or 0.0),
    )
    profiles = critique_profile.get("error_profile") or []
    crawl_rows = critique_profile.get("crawl_recommendations") or []
    metric_audit = allocation.get("metric_audit_bucket") or {}

    lines = ["1) 失败模式画像（Failure Taxonomy）"]
    if profiles:
        for row in profiles[:8]:
            tags = "、".join(_string_list(row.get("overall_error_tags"), limit=4))
            lines.append(
                f"- {row.get('name') or tags or '数学能力缺口'}："
                f"{row.get('description') or row.get('learning_need') or '需结合代表短评修复'}"
            )
            if tags:
                lines.append(f"  关联错因：{tags}")
    else:
        for row in buckets[:8]:
            lines.append(
                f"- {row.get('label')}：观察 {row.get('observed_count', 0)} 条；"
                f"推荐样本形态为 {row.get('sample_direction') or '可验证的纠错样本'}。"
            )

    lines.extend(["", "2) 数据爬取与构造策略（Data Acquisition Plan）"])
    if crawl_rows:
        for index, row in enumerate(crawl_rows[:12], start=1):
            sources = "、".join(_string_list(row.get("source_types"), limit=4)) or "数学题库"
            queries = "；".join(_string_list(row.get("search_queries"), limit=4)) or "按错因检索"
            checks = "；".join(_string_list(row.get("quality_checks"), limit=4)) or "答案与步骤可验证"
            lines.append(f"- {index}. {row.get('target_gap') or '能力缺口'}")
            lines.append(f"  来源：{sources}；检索：{queries}")
            lines.append(f"  样本：{row.get('sample_spec') or '保留题目、标准过程、错误作答与正确答案'}")
            lines.append(f"  质检：{checks}")
    else:
        for row in buckets[:8]:
            lines.append(f"- {row.get('label')}：{row.get('sample_direction')}")

    lines.extend(["", "3) 训练数据配方（Training Recipe）"])
    for row in buckets:
        lines.append(
            f"- {row.get('label')}：{float(row.get('recommended_percent') or 0.0):.2f}%；"
            f"步骤级 {row.get('step_construction_count', 0)} 条，"
            f"整题级 {row.get('whole_case_construction_count', 0)} 条。"
        )
    lines.extend([
        "- 步骤级和整题级修复均可用于 SFT；同题 bad/better/gold 可组成 DPO 偏好对。",
        "- 仅在答案验证器稳定后，才将可机械验分样本用于 GRPO/RL。",
        "",
        "4) 奖励/判因/评测改进建议（for Loop）",
        "- 保留 overall_error_tag、short_critique、construction_scope 和证据字段，便于闭环追踪。",
        "- 同时检查最终答案正确率、步骤正确率、答案提取率和数学等价判定准确率。",
        (
            f"- 将 {metric_audit.get('count', 0)} 条评测异常加入 Metric 回归集，"
            "不得混入模型训练数据。"
        ),
        "- 对格式、单位、符号等可验证约束增加确定性校验，避免奖励模型把格式问题当推理问题。",
        "",
        "5) 下一轮优先级路线图（Next Iteration Checklist）",
    ])
    if buckets:
        lines.append(
            f"- P0：先补 {buckets[0].get('label')}，验收标准为对应能力桶通过率提升且无回退。"
        )
    if len(buckets) > 1:
        lines.append(
            f"- P1：补 {buckets[1].get('label')}，验收标准为步骤级错误率下降。"
        )
    lines.append("- P2：进行小规模试训，按单位样本指标收益更新下一轮分桶比例。")
    return "\n".join(lines)


def _render_math_final_summary(
    summary: Dict[str, Any],
    allocation_plan: Dict[str, Any],
    critique_profile: Dict[str, Any],
) -> str:
    """Match the concise conclusion role of Code/Text2SQL final_report.txt."""
    dataset = summary.get("dataset") or {}
    resolved = dataset.get("resolved_fields") or {}
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    failed = max(0, total - passed)
    accuracy = passed / max(total, 1)
    coverage = allocation_plan.get("count_coverage") or {}
    buckets = sorted(
        allocation_plan.get("buckets") or [],
        key=lambda item: -float(item.get("recommended_share") or 0.0),
    )
    metric_audit = allocation_plan.get("metric_audit_bucket") or {}
    profiles = critique_profile.get("error_profile") or []

    lines = [
        "【背景介绍】",
        (
            f"{summary.get('bench_name') or '本数据集'} 是一组用于评估"
            f"{summary.get('task_domain') or '数学'}能力的数学任务，本轮实际读取 "
            f"{dataset.get('source_name') or '评测结果文件'}。"
        ),
        (
            f"样本以 {', '.join(resolved.get('question') or []) or '题目字段'} 作为输入，"
            f"以 {', '.join(resolved.get('prediction') or []) or '模型答案字段'} 作为预测，"
            f"并与 {', '.join(resolved.get('reference') or []) or '参考答案字段'} 对齐评测。"
        ),
        "",
        "【评测结果】",
        f"本次评测共 {total} 个样本，其中通过 {passed} 个、失败 {failed} 个，正确率 {accuracy * 100:.2f}%。",
        f"主指标为 {summary.get('primary_metric')}，得分为 {summary.get('primary_score')}。",
        (
            f"失败样本中确认模型错误 {coverage.get('model_failure_total', 0)} 条，"
            f"评测异常 {coverage.get('metric_anomaly_count', 0)} 条。"
        ),
        "",
        "【主要失败模式】",
    ]
    if profiles:
        for row in profiles[:5]:
            detail = row.get("description") or "需针对性修复"
            learning_need = str(row.get("learning_need") or "").strip()
            if learning_need and learning_need not in detail:
                detail = f"{detail}；训练需求为{learning_need}"
            lines.append(f"- {row.get('name') or '数学能力缺口'}：{detail}")
    else:
        for row in sorted(
            buckets,
            key=lambda item: -int(item.get("observed_count") or 0),
        )[:5]:
            lines.append(f"- {row.get('label')}：观察到 {row.get('observed_count', 0)} 条。")

    lines.extend(["", "【训练数据分桶建议】"])
    for row in buckets:
        lines.append(
            f"- {row.get('label')}：{float(row.get('recommended_percent') or 0.0):.2f}% "
            f"（观察 {row.get('observed_count', 0)} 条）"
        )
    if metric_audit.get("count"):
        lines.append(
            f"- 评测异常：{metric_audit.get('count')} 条，模型训练预算 0.00%，转入 Metric 修复。"
        )
    lines.extend([
        f"- 更新规则：{allocation_plan.get('pilot_update_rule') or '小规模试训后按单位样本收益更新。'}",
        "",
        "整体来看，应先修复高收益能力桶，并将评测异常独立回归验证，再依据下一轮真实指标增益调整数据配比。",
    ])
    return "\n".join(lines)


def _render_math_suggestions(allocation_plan: Dict[str, Any]) -> str:
    """Produce the compact optional suggestions artifact used by Code/Text2SQL."""
    buckets = sorted(
        allocation_plan.get("buckets") or [],
        key=lambda item: -float(item.get("recommended_share") or 0.0),
    )
    metric_audit = allocation_plan.get("metric_audit_bucket") or {}
    lines = ["【模型改进建议】"]
    for index, row in enumerate(buckets[:2], start=1):
        lines.append(
            f"{index}. 优先补强{row.get('label')}（{float(row.get('recommended_percent') or 0.0):.2f}%）："
            f"{row.get('sample_direction') or '构造可验证的针对性样本'}。"
        )
    third_index = len(lines)
    if metric_audit.get("count"):
        lines.append(
            f"{third_index}. 修复 {metric_audit.get('count')} 条评测异常对应的答案提取、归一化与等价判定。"
        )
    else:
        lines.append(f"{third_index}. 小规模试训后按单位样本收益更新各桶比例，避免照搬错误占比。")
    return "\n".join(lines)


def _render_math_obtainer_report(
    obtainer_stats: Dict[str, Any],
    critique_profile: Dict[str, Any],
) -> str:
    """Match the optional final_report_*.obtainer.txt artifact contract."""
    allocation = obtainer_stats.get("allocation_plan") or {}
    coverage = allocation.get("count_coverage") or {}
    portrait_text, crawl_text = _render_critique_profile_sections(critique_profile)
    lines = [
        "【Obtainer 细粒度报告】",
        "",
        f"失败样本数：{obtainer_stats.get('failed_total', 0)}",
        f"通过样本数：{obtainer_stats.get('passed_total', 0)}",
        f"可进入模型数据构造：{coverage.get('model_failure_total', 0)}",
        f"仅进入 Metric 修复：{coverage.get('metric_anomaly_count', 0)}",
        "",
        "【分桶补数建议】",
        _render_allocation_plan(allocation),
    ]
    if portrait_text:
        lines.extend(["", portrait_text])
    if crawl_text:
        lines.extend(["", crawl_text])
    lines.extend([
        "",
        "【低风险合成策略】",
        "- 步骤证据充分时构造首错定位与逐步修复样本。",
        "- 步骤证据较弱时保留整题上下文，构造 bad/better/gold 对照样本。",
        "- 每条样本均需校验参考答案、去重，并与 benchmark 做污染隔离。",
        "",
        "【不建议的补数方向】",
        "- 不按原始错误占比机械扩充数据，不把评测异常混入模型训练。",
        "- 不采集只有最终答案、缺少可验证过程或标签边界不清的复杂样本。",
    ])
    return "\n".join(part for part in lines if part is not None)


def _derive_math_llmaj_stats(
    records: List[Dict[str, Any]],
    primary_metric_item: Dict[str, Any],
) -> Dict[str, Any]:
    """Recount report-facing Math label stats from the current records."""
    details = primary_metric_item.get("details", []) or []
    diagnosis_distribution: Counter = Counter()
    overall_tag_distribution: Counter = Counter()
    process_distribution: Counter = Counter()
    failed_total = 0
    tagged_total = 0
    short_critique_count = 0
    actionable_total = 0
    metric_anomaly_count = 0
    needs_review_count = 0
    evidence_valid_count = 0
    step_construction_count = 0
    whole_case_construction_count = 0

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        if index < len(details):
            failed = _normalize_detail_score(details[index]) == 0.0
        else:
            failed = record.get("passed") is False
        if not failed:
            continue

        failed_total += 1
        judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
        tags = judge.get("tags") if isinstance(judge.get("tags"), list) else []
        overall_tag = str(
            judge.get("overall_error_tag")
            or record.get("overall_error_tag")
            or (tags[0] if tags else "")
            or ""
        ).strip()
        if overall_tag:
            tagged_total += 1
            overall_tag_distribution[overall_tag] += 1

        short_critique = str(
            judge.get("short_critique")
            or record.get("short_critique")
            or judge.get("reason")
            or ""
        ).strip()
        if short_critique:
            short_critique_count += 1

        diagnosis_status = str(judge.get("diagnosis_status") or "unknown")
        diagnosis_distribution[diagnosis_status] += 1
        if diagnosis_status == "metric_anomaly" or overall_tag == "评测异常":
            metric_anomaly_count += 1
        if judge.get("actionable"):
            actionable_total += 1
        if judge.get("needs_review"):
            needs_review_count += 1
        if judge.get("evidence_valid"):
            evidence_valid_count += 1

        process_status = str(judge.get("process_status") or "unknown")
        process_distribution[process_status] += 1
        construction_scope = str(judge.get("construction_scope") or "none")
        if construction_scope == "step":
            step_construction_count += 1
        elif construction_scope == "whole_case":
            whole_case_construction_count += 1

    return {
        "failed_total": failed_total,
        "tagged_total": tagged_total,
        "short_critique_count": short_critique_count,
        "actionable_total": actionable_total,
        "actionable_count": actionable_total,
        "metric_anomaly_count": metric_anomaly_count,
        "needs_review_count": needs_review_count,
        "needs_review_or_unknown": needs_review_count
        + diagnosis_distribution.get("unknown", 0),
        "evidence_valid_count": evidence_valid_count,
        "step_construction_count": step_construction_count,
        "whole_case_construction_count": whole_case_construction_count,
        "diagnosis_distribution": dict(diagnosis_distribution),
        "overall_tag_distribution": dict(overall_tag_distribution),
        "process_absent_count": process_distribution.get("absent", 0),
        "process_incomplete_count": process_distribution.get("incomplete", 0),
        "process_superficial_count": process_distribution.get("superficial", 0),
        "process_substantive_count": process_distribution.get("substantive", 0),
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
    all_metrics = metric_result.get("metrics", {}) or {}

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
            "question": (
                rec.get("question")
                or rec.get("problem")
                or rec.get("prompt")
                or rec.get("input")
            ),
            "target": (
                rec.get("target")
                or rec.get("answer")
                or rec.get("ground_truth")
                or rec.get("label")
                or rec.get("reference")
            ),
            "generated_ans": (
                rec.get("generated_ans")
                or rec.get("completion")
                or rec.get("prediction")
                or rec.get("eval_pred")
            ),
        }

        bucket_record = dict(rec)
        bucket_record["passed"] = score != 0.0
        bucket_record["metric_detail"] = detail
        bucket_record["metric_details"] = {
            metric_name: metric_item.get("details", [])[idx]
            for metric_name, metric_item in all_metrics.items()
            if isinstance(metric_item, dict)
            and isinstance(metric_item.get("details"), list)
            and idx < len(metric_item.get("details", []))
        }
        bucket_record["primary_metric"] = primary_metric_name
        bucket_record.setdefault("question", sample["question"])
        bucket_record.setdefault("target", sample["target"])
        bucket_record.setdefault("generated_ans", sample["generated_ans"])
        bucket_record.setdefault("domain", domain)
        # Preserve Math LLMaJ labels when present.
        if isinstance(rec.get("judge"), dict):
            bucket_record["judge"] = rec["judge"]
        if isinstance(rec.get("pred_steps"), list):
            bucket_record["pred_steps"] = rec["pred_steps"]
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
        task_type=summary.get("bucket_task_type") or "general",
        alpha=float(analyzer_cfg.get("bucket_power_alpha", 1.0)),
        min_share=float(analyzer_cfg.get("bucket_min_share", 0.05)),
        max_share=float(analyzer_cfg.get("bucket_max_share", 0.50)),
    )
    math_llmaj_stats = dict(analyzer_cfg.get("math_llmaj_stats") or {})
    if summary.get("bucket_task_type") == "math":
        # Counts shown in the report must describe the records being reported,
        # even when an older checkpoint omitted its cached stats payload.
        math_llmaj_stats.update(
            _derive_math_llmaj_stats(records, primary_metric_item)
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
            [row["label"], row.get("actionable_count", row["count"])]
            for row in allocation_plan.get("buckets", [])
        ],
        "allocation_plan": allocation_plan,
        "llmaj_actions": analyzer_cfg.get("obtainer_actions") or [],
        "llmaj_actions_path": analyzer_cfg.get("obtainer_actions_path"),
        "math_llmaj_stats": math_llmaj_stats,
    }


def _render_quick_metric_reports(
    summary: Dict[str, Any],
    obtainer_stats: Dict[str, Any],
) -> Tuple[str, str, str]:
    """Skip extra LLM report calls; build brief text from structured stats."""
    buckets = (obtainer_stats.get("allocation_plan") or {}).get("buckets") or []
    bucket_lines = []
    for row in buckets[:12]:
        if not isinstance(row, dict):
            continue
        bucket_lines.append(
            f"- {row.get('label')}：全部 {row.get('count', 0)} 条，"
            f"步骤级修复 {row.get('step_construction_count', 0)} 条，"
            f"整题级构造 {row.get('whole_case_construction_count', 0)} 条，"
            f"建议占首轮训练数据的 {row.get('recommended_percent', 0)}%"
        )
    actions = obtainer_stats.get("llmaj_actions") or []
    bucket_labels = {
        str(row.get("bucket")): str(row.get("label") or row.get("bucket"))
        for row in buckets
        if isinstance(row, dict)
    }
    action_distribution = Counter(
        str(action.get("capability_bucket") or "未标明能力桶")
        for action in actions
        if isinstance(action, dict)
    )
    action_lines = [
        f"- {bucket_labels.get(bucket, bucket)}：{count} 条可直接构造样本"
        for bucket, count in action_distribution.most_common()
    ]
    stats = obtainer_stats.get("math_llmaj_stats") or {}
    diagnosis = stats.get("diagnosis_distribution") or {}

    report = "\n".join(
        [
            "【评测摘要】",
            f"- 数据集：{summary.get('bench_name')}",
            f"- 总样本：{summary.get('total')}，通过：{summary.get('passed')}，"
            f"失败：{max(0, int(summary.get('total') or 0) - int(summary.get('passed') or 0))}",
            f"- 主指标：{summary.get('primary_metric')}，得分：{summary.get('primary_score')}",
            "",
            "【判因概况】",
            f"- 已可靠判因：{diagnosis.get('diagnosed', 0)} 条",
            f"- 规则确认：{diagnosis.get('rule_confirmed', 0)} 条",
            f"- 评测异常：{diagnosis.get('metric_anomaly', 0)} 条",
            "",
            "【训练能力桶】",
            *(bucket_lines or ["- （空）"]),
        ]
    )
    data_plan = "\n".join(
        [
            "【数据构造计划】",
            *(bucket_lines or ["- 暂无 bucket"]),
            "",
            "【已路由构造样本】",
            *(action_lines or ["- 暂无 obtainer_actions"]),
        ]
    )
    obtainer = "\n".join(
        [
            "【数据构造执行摘要】",
            f"- 失败样本：{obtainer_stats.get('failed_total', 0)} 条",
            f"- 已路由训练构造：{len(actions)} 条",
            "",
            *(action_lines or ["- 暂无动作"]),
        ]
    )
    return report, data_plan, obtainer


def build_prompt_for_report(summary: Dict[str, Any]) -> str:
    """
    构造自然语言评测报告用的 prompt。
    prompt 模板从 PromptLoader 中读取。
    """
    loader = PromptLoader()
    template = loader("analyze_metric_report", "report_user")

    prompt = template.format(
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
    if summary.get("bucket_task_type") == "math":
        allocation_json = json.dumps(summary.get("allocation_plan") or {}, ensure_ascii=False)
        prompt += f"""

【Math 报告约束】
1. 最终答案指标只说明是否匹配，不得单凭失败结果猜测具体数学错因。
2. 优先引用步骤级结构化错因；能力分桶与代数、几何、概率统计等题目领域必须分开描述。
3. 明确区分答案提取、基础计算、符号变换、数学建模、策略定理、过程一致性和验证完整性。
4. “评测异常”必须单列为 Metric 修复项，不得计入模型训练预算；其余模型错误均应进入明确能力桶。
5. 只输出人类可读的中文段落、标题和列表，不得粘贴 JSON、Python 对象或代码块。
allocation_plan={allocation_json}
"""
    return prompt + "\n只输出人类可读的中文报告，不得粘贴 JSON 或代码对象。\n"


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
    if summary.get("bucket_task_type") == "math":
        critique_profile_json = json.dumps(
            summary.get("critique_profile") or {}, ensure_ascii=False
        )
        return prompt + f"""

【Math 分桶约束】
1. 必须优先使用 allocation_plan，不得把最终答案失败率直接当作训练数据比例。
2. 主预算按数学能力桶分配；代数、几何、概率统计等 domain_breakdown 只决定桶内题目来源。
3. 每项建议必须对应 sample_direction，并说明可验证的目标指标或步骤级正确率。
4. “评测异常”为 0 训练预算并转入 Metric 修复；recommended_percent 是首轮先验，试训后按单位样本收益更新。
5. 必须结合 critique_profile 中按标签抽取的代表短评形成错误画像和数据获取建议，不得把抽样数误写成全量错误数。
6. 输出章节名称、顺序和职责必须与 Code/Text2SQL 报告一致：失败模式画像、数据爬取与构造策略、训练数据配方、奖励/判因/评测改进建议、下一轮优先级路线图。
7. 只输出人类可读的中文段落、标题和列表，不得粘贴 JSON、Python 对象或代码块。
allocation_plan={allocation_json}
critique_profile={critique_profile_json}
"""
    return prompt + f"""

【General Text 分桶约束】
1. 必须优先使用 summary 中的 allocation_plan，不得直接按 primary metric 失败比例分配训练数据。
2. 指令遵循、相关性、事实性、推理、完整性、语言质量和安全拒答是相互独立的能力桶。
3. other/待诊断样本不进入训练预算，只能建议补充评测证据或人工复核。
4. recommended_percent 仅表示第一轮先验预算；小规模试训后应按单位样本指标收益更新。
5. 只输出人类可读的中文段落、标题和列表，不得粘贴 JSON、Python 对象或代码块。
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
    if summary.get("bucket_task_type") == "math":
        return prompt + """

【Math 数据获取约束】
1. 使用 allocation_plan.recommended_percent 生成能力级预算，再用 domain_breakdown 选择代数、几何、概率统计等题目来源。
2. 数据必须保留题目、标准过程、最终答案和可定位的关键步骤，不能只采集答案对。
3. 不得把 primary_metric_failure 或 other 直接当作可采集的数据类型；“评测异常”只用于修复 Metric。
4. 每个能力桶必须对应 sample_direction，并给出该能力的验证指标。
5. 只输出人类可读的中文段落、标题和列表，不得粘贴 JSON、Python 对象或代码块。
"""
    return prompt + """

【General Text 数据获取约束】
1. 使用 allocation_plan.recommended_percent 生成能力级数据预算，再参考 domain_breakdown 选择内容领域。
2. 不得把 primary_metric_failure、unknown 或 other 直接当作可采集的数据类型。
3. 每个能力桶的数据建议必须对应 sample_direction，并说明验证该能力提升的指标。
4. 只输出人类可读的中文段落、标题和列表，不得粘贴 JSON、Python 对象或代码块。
"""


def _render_allocation_plan(allocation_plan: Dict[str, Any]) -> str:
    rows = allocation_plan.get("buckets") or []
    if not rows:
        return ""
    task_type = allocation_plan.get("task_type") or "general"
    title = "数学训练数据分桶建议" if task_type == "math" else "通用文本训练数据分桶建议"
    lines = [
        f"【{title}】",
        (
            "每条确认的模型错误都进入能力桶；精确证据用于步骤级修复，证据较弱时使用整题级对比构造。"
            if task_type == "math"
            else "“全部数量”统计每一条错误样本；“可直接构造数量”只统计通过证据门控的样本。"
        ),
        "错误出现频率只作为需求信号；以下比例同时考虑归因置信度、严重性、迁移价值、学习效率和数据成本。",
    ]
    coverage = allocation_plan.get("count_coverage") or {}
    lines.append(
        f"全量覆盖：{coverage.get('counted_total', 0)}/{coverage.get('failed_total', 0)}，"
        f"逐条计数完成：{'是' if coverage.get('all_failures_counted', False) else '否'}。"
    )
    for row in sorted(rows, key=lambda item: -item.get("recommended_share", 0.0)):
        if task_type == "math":
            lines.append(
                f"- {row.get('label')}：{row.get('recommended_percent', 0):.2f}% "
                f"（全量 {row.get('observed_count', row.get('count', 0))} 条，"
                f"步骤级 {row.get('step_construction_count', 0)} 条，"
                f"整题级 {row.get('whole_case_construction_count', 0)} 条，"
                f"置信度 {row.get('classification_confidence', 0):.2f}）"
            )
        else:
            lines.append(
                f"- {row.get('label')}：{row.get('recommended_percent', 0):.2f}% "
                f"（全量 {row.get('observed_count', row.get('count', 0))} 条，"
                f"可直接构造 {row.get('actionable_count', 0)} 条，"
                f"置信度 {row.get('classification_confidence', 0):.2f}）"
            )
        lines.append(f"  样本方向：{row.get('sample_direction', '')}")
        domains = row.get("domain_breakdown") or []
        if domains:
            domain_text = "、".join(
                f"{item.get('domain')} {item.get('count')} 条" for item in domains[:5]
            )
            lines.append(f"  领域分布：{domain_text}")
    if task_type == "math":
        metric_audit = allocation_plan.get("metric_audit_bucket") or {}
        if metric_audit.get("count"):
            lines.append(
                f"- 评测异常：{metric_audit.get('count')} 条，训练预算 0.00%，"
                "转入答案提取与数学等价判定回归测试。"
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
    t_node = time.perf_counter()
    stage_timing: Dict[str, float] = {}

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

    t_load = time.perf_counter()
    metric_result = _load_metric_result(state)
    analyzer_cfg = _analyzer(state)
    records = analyzer_cfg.get("labeled_records")
    if not isinstance(records, list) or not records:
        labeled_path = analyzer_cfg.get("labeled_records_path")
        if labeled_path and os.path.exists(labeled_path):
            records = _load_records_from_alignment({
                "alignment": {"path": labeled_path}
            })
    if not isinstance(records, list) or not records:
        records = _load_records_from_alignment(metric_result)
    summary = _build_summary(state, metric_result, records)
    report_history = None
    if summary.get("bucket_task_type") == "math":
        from loopai.skills.Analyzer.report_history import prepare_report_history, with_rollout_sampling
        _, primary = _select_primary_metric(metric_result)
        details = primary.get("details") or []
        history_records = []
        for index, row in enumerate(records):
            item = dict(row)
            if type(item.get("passed", item.get("correct"))) is not bool and index < len(details):
                detail = details[index]
                value = detail.get("score") if isinstance(detail, dict) else detail
                if isinstance(value, (int, float)) and value in (0, 1):
                    item["passed"] = value == 1
            history_records.append(item)
        history_source = analyzer_cfg.get("enriched_oj_path") or analyzer_cfg.get("eval_result_path") or (metric_result.get("alignment") or {}).get("path")
        report_history = prepare_report_history(
            state, outdir=Path(_ensure_analyzer_outdir(state)), dataset=summary["bench_name"], task_type="math",
            records=with_rollout_sampling(history_records, analyzer_cfg.get("math_rollout_input") or {}),
            source_path=str(history_source or _ensure_analyzer_outdir(state)), metric=summary["primary_metric"])
        summary["historical_comparison"] = report_history["public"]
        if analyzer_cfg.get("math_rollout_input"):
            analyzer_cfg["math_rollout_input"]["historical_comparison"] = report_history["public"]
    obtainer_stats = _build_obtainer_stats(state, metric_result, records, summary)
    allocation_plan = obtainer_stats.get("allocation_plan") or {}
    if (
        summary.get("bucket_task_type") == "math"
        and int((allocation_plan.get("diagnostic_bucket") or {}).get("count") or 0) > 0
    ):
        raise RuntimeError(
            "Math 判因仍有未完成样本，已拒绝生成残缺报告；"
            "请使用同一 version_id 续跑 math_llmaj_label 节点。"
        )
    summary["allocation_plan"] = allocation_plan
    _analyzer(state)["allocation_plan"] = allocation_plan
    stage_timing["load_bucket_ms"] = round((time.perf_counter() - t_load) * 1000.0, 1)

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

    llm = None
    _, primary_metric_item = _select_primary_metric(metric_result)
    short_critiques = _collect_short_critiques(records, primary_metric_item)
    t_profile = time.perf_counter()

    def _profile_progress(phase: str, current: int, total: int):
        if phase == "map":
            progress = 0.22 + 0.14 * (current / max(1, total))
            message = f"归纳标签短评 {current}/{total} 批"
        else:
            progress = 0.38 + 0.04 * (current / max(1, total))
            message = f"合并错误画像 {current}/{total} 组"
        _emit(
            message,
            progress=min(progress, 0.42),
            data={
                "phase": phase,
                "current": current,
                "total": total,
            },
        )

    if short_critiques:
        try:
            llm = init_model(state)
        except Exception as exc:
            logger.warning(f"[analyze_metric_report] 无法初始化短评归纳模型，使用规则兜底: {exc}")
        critique_profile = _build_critique_profile(
            llm,
            short_critiques,
            samples_per_tag=analyzer_cfg.get("critique_samples_per_tag", 5),
            batch_size=int(analyzer_cfg.get("critique_profile_batch_size", 40) or 40),
            max_chars=int(analyzer_cfg.get("critique_profile_batch_max_chars", 12000) or 12000),
            reduce_group_size=int(analyzer_cfg.get("critique_profile_reduce_group_size", 8) or 8),
            progress_callback=_profile_progress,
        )
    else:
        critique_profile = _build_critique_profile(
            None,
            [],
            samples_per_tag=analyzer_cfg.get("critique_samples_per_tag", 5),
        )

    summary["critique_profile"] = critique_profile
    obtainer_stats["critique_profile"] = critique_profile
    analyzer_cfg["critique_profile"] = critique_profile
    population_buckets = {
        str(row.get("label") or row.get("bucket")): int(
            row.get("observed_count", row.get("count", 0)) or 0
        )
        for row in allocation_plan.get("buckets") or []
        if isinstance(row, dict)
    }
    metric_audit_bucket = allocation_plan.get("metric_audit_bucket") or {}
    if metric_audit_bucket.get("count"):
        population_buckets["评测异常"] = int(metric_audit_bucket.get("count") or 0)
    critique_profile["population_bucket_distribution"] = population_buckets
    critique_profile["count_coverage"] = allocation_plan.get("count_coverage") or {}
    direct_badcase_rows = _build_direct_badcase_rows(
        analyzer_cfg.get("obtainer_actions") or []
    )
    stage_timing["critique_profile_ms"] = round(
        (time.perf_counter() - t_profile) * 1000.0, 1
    )

    quick_mode = bool(
        analyzer_cfg.get("metric_report_quick")
        if analyzer_cfg.get("metric_report_quick") is not None
        else analyzer_cfg.get("quick_brief", False)
    )
    is_math = summary.get("bucket_task_type") == "math"
    t_report = time.perf_counter()
    if quick_mode:
        if is_math:
            _emit(
                "quick 模式：按 Code/Text2SQL 五段式结构生成 Math 报告",
                progress=0.45,
            )
            report_text = _render_math_quick_analysis(
                summary, obtainer_stats, critique_profile
            )
            data_plan_text = ""
            obtainer_text = ""
        else:
            _emit(
                "quick 模式：跳过三份常规报告 LLM，保留标签短评画像",
                progress=0.45,
            )
            report_text, data_plan_text, obtainer_text = _render_quick_metric_reports(
                summary, obtainer_stats
            )
    else:
        if llm is None:
            llm = init_model(state)
        if is_math:
            report_prompt = build_prompt_for_data_plan(summary)
            _emit(
                "调用模型生成五段式 Math 分析报告",
                progress=0.55,
                data={"prompt_chars": len(report_prompt or "")},
            )
            report_text = _invoke_prompt(llm, report_prompt)
            data_plan_text = ""
            obtainer_text = ""
        else:
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
    stage_timing["report_ms"] = round((time.perf_counter() - t_report) * 1000.0, 1)

    allocation_text = _render_allocation_plan(allocation_plan)
    portrait_text, crawl_text = _render_critique_profile_sections(critique_profile)
    summary_text = _render_summary_report(summary)
    structured_audit_text = _render_audit_report(summary, obtainer_stats, critique_profile)
    suggestion_text = ""
    if is_math:
        report_parts = [
            structured_audit_text.strip(),
            "【分析与闭环建议】\n" + report_text.strip(),
        ]
        if portrait_text:
            report_parts.extend([portrait_text.strip(), crawl_text.strip()])
        if allocation_text:
            report_parts.append(allocation_text.strip())
        report_text = "\n\n".join(part for part in report_parts if part).strip() + "\n"
        suggestion_text = _render_math_suggestions(allocation_plan)
        obtainer_text = _render_math_obtainer_report(
            obtainer_stats, critique_profile
        )
        final_report_text = _render_math_final_summary(
            summary, allocation_plan, critique_profile
        )
        if suggestion_text:
            final_report_text = (
                f"{final_report_text.rstrip()}\n\n"
                "---------------------\n改进建议：\n"
                f"{suggestion_text.strip()}\n"
            )
        else:
            final_report_text = final_report_text.rstrip() + "\n"
    else:
        if allocation_text:
            report_text = f"{report_text.rstrip()}\n\n{allocation_text}\n"
            data_plan_text = f"{data_plan_text.rstrip()}\n\n{allocation_text}\n"
            obtainer_text = f"{obtainer_text.rstrip()}\n\n{allocation_text}\n"
        if portrait_text:
            report_text = f"{report_text.rstrip()}\n\n{portrait_text}\n\n{crawl_text}\n"
            data_plan_text = f"{data_plan_text.rstrip()}\n\n{crawl_text}\n"
            obtainer_text = f"{obtainer_text.rstrip()}\n\n{portrait_text}\n\n{crawl_text}\n"
        report_text = (
            f"{structured_audit_text.rstrip()}\n\n"
            f"【模型生成的评测分析】\n{report_text.strip()}\n"
        )
        final_report_text = _render_final_report(
            summary_text,
            report_text,
            data_plan_text,
            direct_badcase_count=len(direct_badcase_rows),
        )

    rollout_text = training_text = ""
    if is_math and analyzer_cfg.get("math_rollout_input"):
        from loopai.skills.Analyzer.math_rollout_report import generate_rollout_reports
        from loopai.skills.Analyzer.math_training_plan import render_training_decision
        rollout_text, training_text, rollout_summary = generate_rollout_reports(
            state, records, llm,
            invoke=_invoke_prompt, build_profile=_build_critique_profile,
            render_profile=_render_critique_profile_sections,
            progress=lambda message: _emit(message, progress=0.86),
        )
        analyzer_cfg["math_rollout_summary"] = rollout_summary
        report_text += "\n" + rollout_text + "\n" + training_text
        final_report_text += (
            "\n【Rollout 与训练阶段】\n"
            + "、".join(f"{grade} {count} 组" for grade, count in rollout_summary["grade_counts"].items())
            + "。\n" + render_training_decision(rollout_summary["training_plan"])
            + "判定限于本次评测范围，不认证训练历史或全部能力；理由见 07，领域需求见 08_training_plan.json。\n"
        )

    t_write = time.perf_counter()
    if report_history:
        from loopai.skills.Analyzer.report_history import render_report_history
        history_text = render_report_history(report_history)
        report_text += "\n" + history_text
        final_report_text += "\n" + history_text
    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = _ensure_analyzer_outdir(state)

    analyzer = _analyzer(state)
    if summary.get("bucket_task_type") == "math":
        bundle_root, dataset_dir = _ensure_math_report_bundle(
            state, outdir, summary
        )
        analyzer["math_report_bundle_dir"] = str(bundle_root)
        analyzer["math_report_dataset_dir"] = str(dataset_dir)
        analyzer["math_report_overview_path"] = str(bundle_root / "总览.txt")
        analyzer["analyze_output_summary_text_path"] = str(
            dataset_dir / MATH_REPORT_FILENAMES["summary"]
        )
        analyzer["analyze_output_summary_txt_path"] = analyzer[
            "analyze_output_summary_text_path"
        ]
        analyzer["analyze_output_summary_path"] = analyzer[
            "analyze_output_summary_text_path"
        ]
        analyzer["analyze_output_report_text_path"] = str(
            dataset_dir / MATH_REPORT_FILENAMES["report"]
        )
        analyzer["analyze_output_final_report_text_path"] = str(
            dataset_dir / MATH_REPORT_FILENAMES["final_report"]
        )
        analyzer["direct_badcase_construction_count"] = len(direct_badcase_rows)
        analyzer["report_artifact_format"] = "text_only"
        for legacy_key in (
            "analysis_summary_json_path",
            "analyze_output_report_json_path",
            "analyze_output_final_report_json_path",
            "analyze_output_data_plan_text_path",
            "analyze_output_obtainer_json_path",
            "analyze_output_obtainer_text_path",
            "analyze_output_obtainer_txt_path",
            "analyze_output_suggestion_path",
            "analyze_output_critique_profile_json_path",
            "analyze_output_direct_badcase_path",
            "metric_summary_compat_path",
            "metric_report_json_compat_path",
            "metric_report_text_compat_path",
        ):
            analyzer.pop(legacy_key, None)

        analyzer["analyze_output_suggestion_path"] = str(
            dataset_dir / MATH_REPORT_FILENAMES["suggestions"]
        )
        analyzer["analyze_output_obtainer_txt_path"] = str(
            dataset_dir / MATH_REPORT_FILENAMES["obtainer"]
        )
        analyzer["analyze_output_obtainer_text_path"] = analyzer[
            "analyze_output_obtainer_txt_path"
        ]

        artifact_paths = {
            "bundle_overview_txt": analyzer["math_report_overview_path"],
            "summary_txt": analyzer["analyze_output_summary_text_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "final_report_txt": analyzer["analyze_output_final_report_text_path"],
            "enriched_oj": analyzer.get("enriched_oj_path"),
            "suggestion_txt": analyzer["analyze_output_suggestion_path"],
            "obtainer_txt": analyzer["analyze_output_obtainer_txt_path"],
        }
        if rollout_text:
            analyzer["math_rollout_report_path"] = str(dataset_dir / MATH_ROLLOUT_REPORT_FILENAMES["rollout"])
            analyzer["math_training_stage_report_path"] = str(dataset_dir / MATH_ROLLOUT_REPORT_FILENAMES["training"])
            _write_math_report_text(analyzer["math_rollout_report_path"], rollout_text)
            _write_math_report_text(analyzer["math_training_stage_report_path"], training_text)
            analyzer["math_training_plan_path"] = str(dataset_dir / MATH_ROLLOUT_REPORT_FILENAMES["training_plan"])
            plan_path = Path(analyzer["math_training_plan_path"])
            temp_path = plan_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(rollout_summary["training_plan"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(plan_path)
            analyzer["report_artifact_format"] = "text_with_training_plan"
            artifact_paths.update(rollout_txt=analyzer["math_rollout_report_path"],
                                  training_stage_txt=analyzer["math_training_stage_report_path"],
                                  training_plan_json=analyzer["math_training_plan_path"])

        _emit(
            "写入人类可读的 Math 分析报告",
            progress=0.9,
            data=artifact_paths,
        )
        _write_math_report_text(analyzer["math_report_overview_path"],
            _render_math_bundle_overview(bundle_root)
        )
        _write_math_report_text(analyzer["analyze_output_summary_text_path"],
            summary_text
        )
        _write_math_report_text(analyzer["analyze_output_report_text_path"],
            report_text
        )
        _write_math_report_text(analyzer["analyze_output_final_report_text_path"],
            final_report_text
        )
        _write_math_report_text(analyzer["analyze_output_suggestion_path"],
            suggestion_text.rstrip() + "\n"
        )
        _write_math_report_text(analyzer["analyze_output_obtainer_txt_path"],
            obtainer_text.rstrip() + "\n"
        )
        if rollout_text:
            files = {key: str(dataset_dir / filename) for key, filename in REPORT_FILENAMES.items()}
        else:
            files = {key: str(dataset_dir / filename) for key, filename in MATH_REPORT_FILENAMES.items()}
        enriched_source = analyzer.get("enriched_oj_path")
        if enriched_source and Path(enriched_source).is_file():
            import shutil
            source_path = Path(enriched_source)
            enriched_target = dataset_dir / ("09_oj_enriched" + source_path.suffix)
            if source_path.resolve() != enriched_target.resolve():
                temp = enriched_target.with_suffix(enriched_target.suffix + ".tmp")
                shutil.copyfile(source_path, temp)
                temp.replace(enriched_target)
            files["enriched_oj"] = str(enriched_target.resolve())
            analyzer["enriched_oj_path"] = files["enriched_oj"]
            analyzer["enriched_oj_paths"] = {summary["bench_name"]: files["enriched_oj"]}
            if report_history:
                report_history["current"]["source_path"] = files["enriched_oj"]
        if rollout_text:
            register_report_bundle(analyzer, summary["bench_name"], dataset_dir, files)
        if report_history:
            from loopai.skills.Analyzer.report_history import commit_report_history
            commit_report_history(report_history)
        analyzer["analysis_summary"] = summary
        stage_timing["write_ms"] = round(
            (time.perf_counter() - t_write) * 1000.0, 1
        )
        stage_timing["total_ms"] = round(
            (time.perf_counter() - t_node) * 1000.0, 1
        )
        analyzer.setdefault("stage_timing_ms", {})["report"] = stage_timing
        _emit(
            "Math 报告分析完成",
            progress=1.0,
            data={**artifact_paths, "stage_timing_ms": stage_timing},
        )
        logger.info("\n".join(
            f"已写入：{path}" for path in artifact_paths.values() if path
        ))
        return state

    analyzer["analysis_summary_json_path"] = os.path.join(outdir, f"summary_{ts}.json")
    analyzer["analyze_output_summary_path"] = analyzer["analysis_summary_json_path"]
    analyzer["analyze_output_summary_text_path"] = os.path.join(outdir, f"summary_{ts}.txt")
    analyzer["analyze_output_report_json_path"] = os.path.join(outdir, f"report_{ts}.json")
    analyzer["analyze_output_report_text_path"] = os.path.join(outdir, f"report_{ts}.txt")
    analyzer["analyze_output_final_report_json_path"] = os.path.join(outdir, f"final_report_{ts}.json")
    analyzer["analyze_output_final_report_text_path"] = os.path.join(outdir, f"final_report_{ts}.txt")
    analyzer["analyze_output_data_plan_text_path"] = os.path.join(outdir, f"metric_data_plan_{ts}.txt")
    analyzer["analyze_output_obtainer_json_path"] = os.path.join(outdir, f"metric_obtainer_{ts}.json")
    analyzer["analyze_output_obtainer_text_path"] = os.path.join(outdir, f"metric_obtainer_{ts}.txt")
    analyzer["analyze_output_critique_profile_json_path"] = os.path.join(
        outdir, f"metric_critique_profile_{ts}.json"
    )
    analyzer["analyze_output_direct_badcase_path"] = os.path.join(
        outdir, f"direct_badcase_construction_{ts}.jsonl"
    )
    analyzer["direct_badcase_construction_count"] = len(direct_badcase_rows)
    # Keep the previous metric-prefixed filenames as compatibility artifacts.
    analyzer["metric_summary_compat_path"] = os.path.join(outdir, f"metric_summary_{ts}.json")
    analyzer["metric_report_json_compat_path"] = os.path.join(outdir, f"metric_report_{ts}.json")
    analyzer["metric_report_text_compat_path"] = os.path.join(outdir, f"metric_report_{ts}.txt")

    _emit(
        "写入 metric 分析报告",
        progress=0.9,
        data={
            "summary_json": analyzer["analysis_summary_json_path"],
            "summary_txt": analyzer["analyze_output_summary_text_path"],
            "report_json": analyzer["analyze_output_report_json_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "final_report_json": analyzer["analyze_output_final_report_json_path"],
            "final_report_txt": analyzer["analyze_output_final_report_text_path"],
            "data_plan_txt": analyzer["analyze_output_data_plan_text_path"],
            "obtainer_json": analyzer["analyze_output_obtainer_json_path"],
            "obtainer_txt": analyzer["analyze_output_obtainer_text_path"],
            "critique_profile_json": analyzer["analyze_output_critique_profile_json_path"],
            "direct_badcase_jsonl": analyzer["analyze_output_direct_badcase_path"],
        },
    )

    report_json = {
        "summary": summary,
        "analysis_report": report_text,
        "data_plan_report": data_plan_text,
        "obtainer_stats": obtainer_stats,
        "obtainer_report": obtainer_text,
        "quick_mode": quick_mode,
        "obtainer_actions": analyzer.get("obtainer_actions") or [],
        "obtainer_actions_path": analyzer.get("obtainer_actions_path"),
        "math_llmaj_stats": analyzer.get("math_llmaj_stats") or {},
        "critique_profile": critique_profile,
        "direct_badcase_construction": {
            "path": analyzer["analyze_output_direct_badcase_path"],
            "count": len(direct_badcase_rows),
            "external_data_selection_required": False,
            "quality_gate_required": True,
        },
        "stage_timing_ms": stage_timing,
    }

    final_report_json = {
        "schema_version": "analyzer_metric_final_report_v1",
        "dataset": summary.get("dataset") or {},
        "summary": summary,
        "audit_report": report_text,
        "data_plan_report": data_plan_text,
        "obtainer_report": obtainer_text,
        "allocation_plan": allocation_plan,
        "critique_profile": critique_profile,
        "direct_badcase_construction": report_json["direct_badcase_construction"],
        "artifacts": {
            "summary_json": analyzer["analysis_summary_json_path"],
            "summary_txt": analyzer["analyze_output_summary_text_path"],
            "report_json": analyzer["analyze_output_report_json_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "final_report_json": analyzer["analyze_output_final_report_json_path"],
            "final_report_txt": analyzer["analyze_output_final_report_text_path"],
            "data_plan_txt": analyzer["analyze_output_data_plan_text_path"],
            "obtainer_json": analyzer["analyze_output_obtainer_json_path"],
            "obtainer_txt": analyzer["analyze_output_obtainer_text_path"],
            "direct_badcase_jsonl": analyzer["analyze_output_direct_badcase_path"],
        },
        "stage_timing_ms": stage_timing,
    }

    Path(analyzer["analysis_summary_json_path"]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    Path(analyzer["analyze_output_summary_text_path"]).write_text(summary_text, encoding="utf-8")
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
    Path(analyzer["analyze_output_final_report_json_path"]).write_text(
        json.dumps(final_report_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(analyzer["analyze_output_final_report_text_path"]).write_text(
        final_report_text,
        encoding="utf-8",
    )
    with Path(analyzer["analyze_output_direct_badcase_path"]).open("w", encoding="utf-8") as file_obj:
        for row in direct_badcase_rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")
    Path(analyzer["metric_summary_compat_path"]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(analyzer["metric_report_json_compat_path"]).write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(analyzer["metric_report_text_compat_path"]).write_text(report_text, encoding="utf-8")
    Path(analyzer["analyze_output_critique_profile_json_path"]).write_text(
        json.dumps({
            "profile": critique_profile,
            "selected_short_critiques": _select_critiques_per_tag(
                short_critiques,
                analyzer_cfg.get("critique_samples_per_tag", 5),
            )[0],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    analyzer["analysis_summary"] = summary
    stage_timing["write_ms"] = round((time.perf_counter() - t_write) * 1000.0, 1)
    stage_timing["total_ms"] = round((time.perf_counter() - t_node) * 1000.0, 1)
    analyzer.setdefault("stage_timing_ms", {})["report"] = stage_timing
    report_json["stage_timing_ms"] = stage_timing
    final_report_json["stage_timing_ms"] = stage_timing
    Path(analyzer["analyze_output_report_json_path"]).write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    Path(analyzer["metric_report_json_compat_path"]).write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(analyzer["analyze_output_final_report_json_path"]).write_text(
        json.dumps(final_report_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _emit(
        "metric 报告分析完成",
        progress=1.0,
        data={
            "summary_json": analyzer["analysis_summary_json_path"],
            "summary_txt": analyzer["analyze_output_summary_text_path"],
            "report_txt": analyzer["analyze_output_report_text_path"],
            "final_report_txt": analyzer["analyze_output_final_report_text_path"],
            "data_plan_txt": analyzer["analyze_output_data_plan_text_path"],
            "obtainer_txt": analyzer["analyze_output_obtainer_text_path"],
            "critique_profile_json": analyzer["analyze_output_critique_profile_json_path"],
            "direct_badcase_jsonl": analyzer["analyze_output_direct_badcase_path"],
            "stage_timing_ms": stage_timing,
        },
    )

    logger.info(
        f"已写入：{analyzer['analysis_summary_json_path']}\n"
        f"已写入：{analyzer['analyze_output_summary_text_path']}\n"
        f"已写入：{analyzer['analyze_output_report_json_path']}\n"
        f"已写入：{analyzer['analyze_output_report_text_path']}\n"
        f"已写入：{analyzer['analyze_output_final_report_json_path']}\n"
        f"已写入：{analyzer['analyze_output_final_report_text_path']}\n"
        f"已写入：{analyzer['analyze_output_data_plan_text_path']}\n"
        f"已写入：{analyzer['analyze_output_obtainer_json_path']}\n"
        f"已写入：{analyzer['analyze_output_obtainer_text_path']}\n"
        f"已写入：{analyzer['analyze_output_critique_profile_json_path']}\n"
        f"已写入：{analyzer['analyze_output_direct_badcase_path']}"
    )

    return state
