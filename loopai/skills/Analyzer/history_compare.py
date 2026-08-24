from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple


_ID_KEYS = ("sample_id", "task_id", "id")
_SCORE_KEYS = ("pred_score", "score", "manual_label")
_ERROR_KEYS = ("errors", "error", "error_type", "error_types")


def _read_jsonl(path: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not path:
        return [], "empty path"
    if not os.path.exists(path):
        return [], f"file not found: {path}"

    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    rows.append({"_line_no": line_no, "_raw": line})
                    continue
                if isinstance(obj, dict):
                    obj.setdefault("_line_no", line_no)
                    rows.append(obj)
                else:
                    rows.append({"_line_no": line_no, "value": obj})
    except Exception as exc:
        return [], f"failed to read {path}: {exc}"
    return rows, None


def _case_id(row: Dict[str, Any], fallback_index: int) -> str:
    for key in _ID_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return f"line:{row.get('_line_no', fallback_index)}"


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _passed(row: Dict[str, Any]) -> Optional[bool]:
    if "passed" in row:
        return bool(row.get("passed"))
    if "success" in row:
        return bool(row.get("success"))
    result = str(row.get("result", "")).lower()
    if result in {"passed", "pass", "success", "true"}:
        return True
    if result in {"failed", "fail", "error", "false"}:
        return False
    return None


def _score(row: Dict[str, Any]) -> Optional[float]:
    for key in _SCORE_KEYS:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _score_gap(row: Dict[str, Any]) -> Optional[float]:
    pred_score = _as_float(row.get("pred_score"))
    manual_label = _as_float(row.get("manual_label"))
    if pred_score is None or manual_label is None:
        return None
    return pred_score - manual_label


def _iter_errors(row: Dict[str, Any]) -> Iterable[str]:
    values: List[Any] = []
    for key in _ERROR_KEYS:
        if key in row:
            values.append(row.get(key))
    judge = row.get("judge")
    if isinstance(judge, dict):
        for key in _ERROR_KEYS:
            if key in judge:
                values.append(judge.get(key))
        if judge.get("stage"):
            values.append(judge.get("stage"))

    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, list):
            for item in value:
                if item not in (None, ""):
                    yield str(item)
        elif isinstance(value, dict):
            for item in value.values():
                if item not in (None, ""):
                    yield str(item)
        else:
            yield str(value)


def _distribution(values: Iterable[Any]) -> Dict[str, int]:
    counter = Counter(str(v) for v in values if v not in (None, ""))
    return dict(counter.most_common())


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    passed_values = [_passed(row) for row in rows]
    passed_count = sum(1 for value in passed_values if value is True)
    failed_count = sum(1 for value in passed_values if value is False)
    known_total = passed_count + failed_count

    scores = [value for value in (_score(row) for row in rows) if value is not None]
    gaps = [value for value in (_score_gap(row) for row in rows) if value is not None]

    return {
        "total": len(rows),
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate": (passed_count / known_total) if known_total else None,
        "avg_score": (sum(scores) / len(scores)) if scores else None,
        "avg_score_gap": (sum(gaps) / len(gaps)) if gaps else None,
        "score_distribution": _distribution(round(score, 4) for score in scores),
        "error_distribution": _distribution(error for row in rows for error in _iter_errors(row)),
    }


def _diff_dict(current: Dict[str, Any], baseline: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    diff = {}
    for key in keys:
        cur = current.get(key)
        base = baseline.get(key)
        if isinstance(cur, (int, float)) and isinstance(base, (int, float)):
            diff[key] = cur - base
        else:
            diff[key] = {"current": cur, "baseline": base}
    return diff


def _counter_diff(current: Dict[str, int], baseline: Dict[str, int]) -> Dict[str, int]:
    keys = set(current) | set(baseline)
    return {key: int(current.get(key, 0)) - int(baseline.get(key, 0)) for key in sorted(keys)}


def _case_delta(current: Dict[str, Any], baseline: Dict[str, Any]) -> Optional[float]:
    cur_score = _score(current)
    base_score = _score(baseline)
    if cur_score is not None and base_score is not None:
        return cur_score - base_score

    cur_passed = _passed(current)
    base_passed = _passed(baseline)
    if cur_passed is not None and base_passed is not None:
        return float(cur_passed) - float(base_passed)
    return None


def _case_brief(case_id: str, current: Dict[str, Any], baseline: Dict[str, Any], delta: float) -> Dict[str, Any]:
    return {
        "case_id": case_id,
        "delta": delta,
        "current_passed": _passed(current),
        "baseline_passed": _passed(baseline),
        "current_score": _score(current),
        "baseline_score": _score(baseline),
    }


def _matched_cases(current_rows: List[Dict[str, Any]], baseline_rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    current_by_id = {_case_id(row, idx): row for idx, row in enumerate(current_rows)}
    baseline_by_id = {_case_id(row, idx): row for idx, row in enumerate(baseline_rows)}

    improved = []
    regressed = []
    for case_id in sorted(set(current_by_id) & set(baseline_by_id)):
        current = current_by_id[case_id]
        baseline = baseline_by_id[case_id]
        delta = _case_delta(current, baseline)
        if delta is None:
            continue
        brief = _case_brief(case_id, current, baseline, delta)
        if delta > 0:
            improved.append(brief)
        elif delta < 0:
            regressed.append(brief)
    improved.sort(key=lambda item: item["delta"], reverse=True)
    regressed.sort(key=lambda item: item["delta"])
    return improved[:20], regressed[:20]


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def _summary(comparison: Dict[str, Any]) -> str:
    metric_diff = comparison.get("metric_diff", {})
    pass_delta = metric_diff.get("pass_rate")
    score_gap_delta = metric_diff.get("avg_score_gap")
    improved = len(comparison.get("improved_cases") or [])
    regressed = len(comparison.get("regressed_cases") or [])

    parts = [
        f"通过率变化：{_fmt_pct(pass_delta) if isinstance(pass_delta, (int, float)) else 'N/A'}。",
        f"平均分数差距变化：{score_gap_delta:.4f}。" if isinstance(score_gap_delta, (int, float)) else "平均分数差距变化：N/A。",
        f"进步样本数：{improved}；退步样本数：{regressed}。",
    ]
    if isinstance(pass_delta, (int, float)) and pass_delta < 0:
        parts.append("建议：优先检查退步样本和新增的高频错误类型。")
    elif isinstance(pass_delta, (int, float)) and pass_delta > 0:
        parts.append("建议：保留带来进步的改动，并继续关注剩余高频错误。")
    else:
        parts.append("建议：结合分数差距和错误分布做后续定向分析。")
    return " ".join(parts)


def build_historical_comparison(
    current_result_path: Optional[str],
    baseline_result_path: Optional[str],
) -> Dict[str, Any]:
    comparison: Dict[str, Any] = {
        "has_baseline": bool(baseline_result_path),
        "baseline_result_path": baseline_result_path,
        "current_result_path": current_result_path,
        "metric_diff": {},
        "score_distribution_diff": {},
        "error_distribution_diff": {},
        "improved_cases": [],
        "regressed_cases": [],
        "comparison_summary": "",
    }

    if not baseline_result_path:
        comparison["has_baseline"] = False
        comparison["warning"] = "未配置 baseline_result_path"
        return comparison

    current_rows, current_warning = _read_jsonl(current_result_path or "")
    baseline_rows, baseline_warning = _read_jsonl(baseline_result_path)
    warnings = [warning for warning in (current_warning, baseline_warning) if warning]
    if warnings:
        comparison["has_baseline"] = False
        comparison["warning"] = "; ".join(warnings)
        comparison["comparison_summary"] = "历史对比警告：" + comparison["warning"]
        return comparison

    current_stats = _stats(current_rows)
    baseline_stats = _stats(baseline_rows)
    improved, regressed = _matched_cases(current_rows, baseline_rows)

    comparison.update({
        "has_baseline": True,
        "current_metrics": current_stats,
        "baseline_metrics": baseline_stats,
        "metric_diff": _diff_dict(
            current_stats,
            baseline_stats,
            ("total", "passed", "failed", "pass_rate", "avg_score", "avg_score_gap"),
        ),
        "score_distribution_diff": _counter_diff(
            current_stats.get("score_distribution") or {},
            baseline_stats.get("score_distribution") or {},
        ),
        "error_distribution_diff": _counter_diff(
            current_stats.get("error_distribution") or {},
            baseline_stats.get("error_distribution") or {},
        ),
        "improved_cases": improved,
        "regressed_cases": regressed,
    })
    comparison["comparison_summary"] = _summary(comparison)
    return comparison


def render_historical_comparison_text(comparison: Dict[str, Any]) -> str:
    lines = ["", "历史对比", "---------------------"]
    lines.append(f"基准结果文件：{comparison.get('baseline_result_path') or 'N/A'}")
    lines.append(f"当前结果文件：{comparison.get('current_result_path') or 'N/A'}")

    if comparison.get("warning"):
        lines.append(f"警告：{comparison['warning']}")
        return "\n".join(lines)

    metric_diff = comparison.get("metric_diff") or {}
    lines.append(f"通过率变化：{_fmt_pct(metric_diff.get('pass_rate')) if isinstance(metric_diff.get('pass_rate'), (int, float)) else 'N/A'}")
    score_gap = metric_diff.get("avg_score_gap")
    lines.append(f"平均分数差距变化：{score_gap:.4f}" if isinstance(score_gap, (int, float)) else "平均分数差距变化：N/A")

    error_diff = comparison.get("error_distribution_diff") or {}
    if error_diff:
        top_errors = sorted(error_diff.items(), key=lambda item: abs(item[1]), reverse=True)[:8]
        lines.append("错误分布变化：" + ", ".join(f"{k}:{v:+d}" for k, v in top_errors))
    else:
        lines.append("错误分布变化：N/A")

    improved = comparison.get("improved_cases") or []
    regressed = comparison.get("regressed_cases") or []
    lines.append("进步样本：" + (", ".join(str(item.get("case_id")) for item in improved[:10]) or "N/A"))
    lines.append("退步样本：" + (", ".join(str(item.get("case_id")) for item in regressed[:10]) or "N/A"))
    lines.append("建议：" + (comparison.get("comparison_summary") or "检查变化样本和错误分布。"))
    return "\n".join(lines)
