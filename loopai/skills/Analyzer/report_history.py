"""Version-scoped, benchmark-scoped history for completed Analyzer reports."""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from .math_rollout import META_KEY, is_rollout_payload, normalize_rollouts
from .oj_annotations import read_oj_rows

HISTORY_DIR = ".analyzer_report_history"
SAMPLING_KEYS = ("temperature", "top_p", "val_n", "max_new_tokens", "max_tokens")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _first(row, keys):
    return next((row[key] for key in keys if row.get(key) is not None and row[key] != ""), None)


def _verdict(row):
    value = _first(row, ("passed", "correct", "success"))
    return value if type(value) is bool else None


def history_snapshot(records: list[dict], *, dataset: str, task_type: str, metric: str) -> dict:
    questions, tags = {}, Counter()
    correct = known = unidentified = 0
    for row in records:
        passed = _verdict(row)
        known += passed is not None
        correct += passed is True
        judge = row.get("judge") if isinstance(row.get("judge"), dict) else {}
        tag = judge.get("overall_error_tag") or row.get("overall_error_tag") or judge.get("stage") or "未提供错因"
        if passed is False:
            tags[str(tag)] += 1
        coords = row.get(META_KEY) or {}
        identity = coords.get("problem_id", _first(row, ("task_id", "problem_id", "question_id", "sample_id", "id")))
        question = _first(row, ("question", "problem", "problem_prompt", "prompt", "input"))
        reference = _first(row, ("target", "ground_truth", "answer", "reference", "gold_sql"))
        key = coords.get("question_key")
        if not key:
            if identity is None and question is None:
                unidentified += 1
                continue
            key = _digest([dataset, identity, question, reference, row.get("db_file"), row.get("test_code"), row.get("entry_point")])
        item = questions.setdefault(key, {"case_id": str(identity if identity is not None else key[:12]),
                                          "total": 0, "known": 0, "correct": 0, "error_tags": {}})
        item["total"] += 1
        item["known"] += passed is not None
        item["correct"] += passed is True
        if passed is False:
            item["error_tags"][str(tag)] = item["error_tags"].get(str(tag), 0) + 1
    sampling = {key: sorted({_digest(row[key]) for row in records if key in row}) for key in SAMPLING_KEYS}
    return {"dataset": dataset, "task_type": task_type, "metric": metric,
            "metrics": {"total": len(records), "known": known, "correct": correct, "failed": known - correct,
                        "pass_rate": correct / known if known else None},
            "questions": questions, "unidentified_records": unidentified,
            "error_distribution": dict(tags), "sampling": sampling,
            "evaluation_protocols": sorted({json.dumps({key: row["_code_bench"].get(key)
                for key in ("schema", "pass_source", "dataset_hash", "image")}, sort_keys=True)
                for row in records if row.get("_code_bench")})}


def compare_snapshots(current: dict, baseline: dict) -> dict:
    current_q, base_q = current["questions"], baseline["questions"]
    shared = set(current_q) & set(base_q)
    warnings = []
    metric_compatible = current["metric"] == baseline["metric"]
    if not metric_compatible:
        warnings.append("主评测指标不同，不能把分数差解读为模型进步或退步。")
    if current.get("evaluation_protocols", []) != baseline.get("evaluation_protocols", []):
        metric_compatible = False
        warnings.append("基础/增强测试口径、测试集哈希或评测器标识不同或缺失，不能作为可比提升。")
    elif any(not json.loads(protocol).get("dataset_hash") for protocol in current.get("evaluation_protocols", [])):
        metric_compatible = False
        warnings.append("缺少测试集哈希，无法确认两轮使用同一版本的测试集，不计算可比提升。")
    if set(current_q) != set(base_q):
        warnings.append("题目集合发生变化；整体通过率仅供描述，优先查看共同题目的等权平均通过率。")
    if current["sampling"] != baseline["sampling"]:
        warnings.append("采样参数不同，变化不能单独归因于训练。")
    if current["unidentified_records"] or baseline["unidentified_records"]:
        warnings.append("部分记录缺少稳定题号与题干，未强行按行号匹配。")
    changed_n = any(current_q[key]["total"] != base_q[key]["total"] for key in shared)
    if changed_n:
        warnings.append("同题作答次数发生变化，比较按题平均的通过比例，不配对随机生成序号。")
    improved, regressed, unchanged, deltas = [], [], 0, []
    for key in sorted(shared) if metric_compatible else []:
        cur, old = current_q[key], base_q[key]
        if cur["known"] != cur["total"] or old["known"] != old["total"]:
            continue
        delta = cur["correct"] / cur["total"] - old["correct"] / old["total"]
        deltas.append(delta)
        item = {"case_id": cur["case_id"], "delta": delta,
                "current_correct": cur["correct"], "current_total": cur["total"],
                "baseline_correct": old["correct"], "baseline_total": old["total"],
                "current_error_tags": cur["error_tags"], "baseline_error_tags": old["error_tags"]}
        if delta > 0:
            improved.append(item)
        elif delta < 0:
            regressed.append(item)
        else:
            unchanged += 1
    if not deltas:
        warnings.append("没有可可靠配对的同指标题目，不能确认模型效果变化。")
    current_rate, baseline_rate = current["metrics"]["pass_rate"], baseline["metrics"]["pass_rate"]
    errors = set(current["error_distribution"]) | set(baseline["error_distribution"])
    return {"baseline_version_id": baseline["version_id"], "baseline_round": baseline.get("round_number"),
            "baseline_result_path": baseline.get("source_path"), "current_result_path": current.get("source_path"),
            "status": "comparable" if deltas else "insufficient_evidence",
            "current_metrics": current["metrics"], "baseline_metrics": baseline["metrics"],
            "pass_rate_delta": current_rate - baseline_rate if metric_compatible and current_rate is not None and baseline_rate is not None else None,
            "matched_question_count": len(deltas), "new_question_count": len(set(current_q) - set(base_q)),
            "removed_question_count": len(set(base_q) - set(current_q)),
            "matched_mean_question_pass_rate_delta": sum(deltas) / len(deltas) if deltas else None,
            "improved_question_count": len(improved), "regressed_question_count": len(regressed), "unchanged_question_count": unchanged,
            "improved_examples": sorted(improved, key=lambda item: -item["delta"])[:20],
            "regressed_examples": sorted(regressed, key=lambda item: item["delta"])[:20],
            "error_distribution_diff": {tag: current["error_distribution"].get(tag, 0) - baseline["error_distribution"].get(tag, 0) for tag in sorted(errors)},
            "warnings": warnings,
            "interpretation": "描述性对比，不是训练收益的因果证明；错因标签变化还可能受判因模型或规则变化影响。"}


def with_rollout_sampling(records: list[dict], context: dict) -> list[dict]:
    metadata = {run["run_id"]: run.get("metadata") or {} for run in context.get("runs", [])}
    result = []
    for row in records:
        coords = row.get(META_KEY) or {}
        sampling = {key: value for key, value in metadata.get(coords.get("run_id"), {}).items() if key in SAMPLING_KEYS}
        if "val_n" in coords:
            sampling["val_n"] = coords["val_n"]
        result.append({**sampling, **row})
    return result


def _read_baseline(path: Path, dataset: str) -> list[dict]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if is_rollout_payload(payload):
            rows, context = normalize_rollouts(payload)
            allowed = {group["group_id"] for group in context["groups"] if group["dataset"] == dataset}
            if dataset == "+".join(dict.fromkeys(run["dataset"] for run in context["runs"])):
                allowed = {group["group_id"] for group in context["groups"]}
            return with_rollout_sampling([row for row in rows if row[META_KEY]["group_id"] in allowed], context)
    rows = read_oj_rows(path)
    if any("base_status" in row or "plus_status" in row for row in rows):
        from .code_bench_inputs import load_bench_records
        return load_bench_records({"path": str(path), "bench_name": dataset, "bench_name_explicit": True}, "code")
    named = any(row.get("bench_name") for row in rows)
    return [row for row in rows if row.get("bench_name") == dataset] if named else rows


def _baseline_metric(path: Path, task_type: str, declared: str | None = None) -> str:
    if declared:
        return declared
    if task_type in {"code", "text2sql"}:
        return "judger_passed"
    if path.suffix.lower() == ".json" and is_rollout_payload(json.loads(path.read_text(encoding="utf-8-sig"))):
        return "judger_correctness"
    return "unknown_baseline_metric"


def _legacy_snapshots(root: Path, state: dict, dataset: str, task_type: str, before: int, excluded: set[str]):
    """Import only old bundles with all seven texts and a validated training plan."""
    from .report_bundle import REPORT_FILENAMES, safe_dataset_names
    names = {dataset, safe_dataset_names([dataset])[dataset]}
    for version in root.iterdir() if root.is_dir() else []:
        if not version.is_dir() or version.name in excluded:
            continue
        for plan_path in version.rglob("08_training_plan.json"):
            if plan_path.parent.name not in names:
                continue
            files = [plan_path.parent / name for name in REPORT_FILENAMES.values()]
            if not all(path.is_file() and path.stat().st_size for path in files):
                continue
            completed = max(path.stat().st_mtime_ns for path in files)
            if completed >= before:
                continue
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                if plan.get("task_type") != task_type:
                    continue
                sources = list(plan_path.parent.glob("09_oj_enriched.*")) or sorted(version.glob("oj_records_enriched_*.json*"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
                for source in sources:
                    if source.stat().st_mtime_ns > completed:
                        continue
                    rows = _read_baseline(source, dataset)
                    baseline_metric = _baseline_metric(source, task_type, plan.get("primary_metric"))
                    snapshot = history_snapshot(rows, dataset=dataset, task_type=task_type, metric=baseline_metric)
                    expected = plan.get("evaluation") or {}
                    if snapshot["metrics"]["total"] != expected.get("rollouts") or snapshot["metrics"]["correct"] != expected.get("correct"):
                        continue
                    snapshot.update(task_id=str(state.get("task_id") or ""), version_id=version.name,
                                    source_path=str(source.resolve()), completed_at_ns=completed, round_number=None, imported_legacy=True)
                    yield snapshot
                    break
            except (OSError, ValueError, TypeError, KeyError):
                continue


def prepare_report_history(state: dict, *, outdir: Path, dataset: str, task_type: str,
                           records: list[dict], source_path: str, metric: str = "judger_passed") -> dict:
    cfg = state["analyzer"]
    outdir = outdir.resolve()
    task_id = str(state.get("task_id") or "")
    version_id = str(state.get("version_id") or cfg.get("version_id") or outdir.name)
    key = _digest([task_type, dataset])
    record_path = outdir / HISTORY_DIR / f"{key}.json"
    previous, notices, saved = {}, [], {}
    if record_path.is_file():
        try:
            saved = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            notices.append("本版本历史索引无法读取，将重新生成；原报告未删除。")
    identity = [task_id, version_id, str(outdir)]
    started = cfg.get("report_history_run") or {}
    if started.get("identity") != identity:
        started = {"identity": identity, "started_at_ns": saved.get("started_at_ns") or time.time_ns()}
        cfg["report_history_run"] = started
    before = saved.get("started_at_ns") or started["started_at_ns"]
    # Only sibling versions inside this task's analyzer directory are searched.
    root = outdir.parent if outdir.parent.name == "analyzer" else outdir
    for path in root.glob(f"*/{HISTORY_DIR}/{key}.json"):
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if (old.get("task_id") == task_id and old.get("task_type") == task_type and old.get("dataset") == dataset
                    and old.get("version_id") != version_id and old.get("completed_at_ns", before) < before
                    and old.get("status") == "completed"):
                previous[old["version_id"]] = old
        except (OSError, ValueError):
            notices.append("有历史索引无法读取，已跳过；未删除任何历史报告。")
    if root != outdir:
        for old in _legacy_snapshots(root, state, dataset, task_type, before, set(previous) | {version_id}):
            if old["version_id"] != version_id:
                previous.setdefault(old["version_id"], old)
    history = sorted(previous.values(), key=lambda item: item["completed_at_ns"])
    explicit = (cfg.get("baseline_result_paths") or {}).get(dataset) or cfg.get("baseline_result_path")
    if explicit:
        try:
            path = Path(explicit).expanduser().resolve()
            if path == Path(source_path).resolve():
                raise ValueError("基准与当前结果是同一文件")
            rows = _read_baseline(path, dataset)
            old = history_snapshot(rows, dataset=dataset, task_type=task_type,
                                   metric=_baseline_metric(path, task_type, cfg.get("baseline_metric")))
            old.update(version_id="explicit", source_path=str(path), round_number=None)
            baselines = [old]
        except (OSError, ValueError, TypeError) as exc:
            baselines = []
            notices.append(f"指定基准读取失败：{exc}；未静默替换为其他基准。")
    else:
        baselines = ([history[-1]] + ([history[0]] if len(history) > 1 else [])) if history else []
    round_number = saved.get("round_number", len(history) + 1)
    current = history_snapshot(records, dataset=dataset, task_type=task_type, metric=metric)
    current.update(schema_version="1.0", task_id=task_id, version_id=version_id,
                   source_path=str(Path(source_path).resolve()), round_number=round_number, started_at_ns=before)
    comparisons = [compare_snapshots(current, baseline) for baseline in baselines]
    public = {"round_number": round_number, "has_baseline": bool(comparisons),
              "selection": "explicit" if explicit else "previous_completed_and_first",
              "comparisons": comparisons, "notices": notices}
    cfg.setdefault("historical_comparisons", {})[dataset] = public
    return {"current": current, "public": public, "record_path": str(record_path)}


def commit_report_history(history: dict) -> None:
    path = Path(history["record_path"])
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        existing = {}
    payload = {**history["current"], "status": "completed",
               "completed_at_ns": existing.get("completed_at_ns") or time.time_ns()}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def render_report_history(history: dict) -> str:
    public = history["public"] if "public" in history else history
    lines = ["【跨轮历史对比】", f"当前为本任务该 Bench 的第 {public['round_number']} 个已报告版本；中断续跑不计新轮。"]
    if not public["has_baseline"]:
        lines.append("未发现可用的此前已完成版本，不生成虚构的提升/退步结论；也可指定 baseline_result_path。")
    for comparison in public["comparisons"]:
        delta = comparison["matched_mean_question_pass_rate_delta"]
        lines.extend([f"基准版本：{comparison['baseline_version_id']}；文件：{comparison['baseline_result_path']}",
                      f"基准正确/总作答：{comparison['baseline_metrics']['correct']}/{comparison['baseline_metrics']['total']}；"
                      f"当前：{comparison['current_metrics']['correct']}/{comparison['current_metrics']['total']}。",
                      f"共同可比较题目：{comparison['matched_question_count']}；新增题目：{comparison['new_question_count']}；移除题目：{comparison['removed_question_count']}。",
                      f"共同题目等权平均通过率变化：{delta * 100:+.2f} 个百分点。" if delta is not None else "共同题目通过率变化：证据不足。",
                      f"改善 {comparison['improved_question_count']} 题，退步 {comparison['regressed_question_count']} 题，持平 {comparison['unchanged_question_count']} 题。",
                      "错因次数变化（全量）：" + "、".join(f"{tag} {count:+d}" for tag, count in comparison["error_distribution_diff"].items())])
        for label, key in (("改善题例", "improved_examples"), ("退步题例", "regressed_examples")):
            lines.append(label + "：" + ("；".join(f"{r['case_id']}：{r['baseline_correct']}/{r['baseline_total']} → {r['current_correct']}/{r['current_total']}" for r in comparison[key]) or "无"))
        lines.extend(comparison["warnings"])
        lines.append(comparison["interpretation"])
    lines.extend(public["notices"])
    return "\n".join(lines) + "\n"
