"""Adapt evaluated Math rollouts without replacing the Judger's verdicts."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "math_judger_rollouts_v1"
META_KEY = "_math_rollout"
GRADES = ("好", "较好", "中等", "较差", "差")
GRADE_RULES = {
    "好": "正确率 = 100%（12 次中 12 次正确）",
    "较好": "75% <= 正确率 < 100%（12 次中 9–11 次正确）",
    "中等": "50% <= 正确率 < 75%（12 次中 6–8 次正确）",
    "较差": "0% < 正确率 < 50%（12 次中 1–5 次正确）",
    "差": "正确率 = 0%（12 次中 0 次正确）",
}


def rollout_grade(correct: int, total: int) -> str:
    if total <= 0 or not 0 <= correct <= total:
        raise ValueError("Rollout counts require 0 <= correct <= total and total > 0")
    if correct == total:
        return "好"
    if correct == 0:
        return "差"
    if 4 * correct >= 3 * total:
        return "较好"
    return "中等" if 2 * correct >= total else "较差"


def is_rollout_payload(payload: Any) -> bool:
    # An explicitly present eval list must be validated, even when it is empty.
    return isinstance(payload, dict) and isinstance(payload.get("eval"), list)


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None and v != ""), None)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize_rollouts(payload: dict) -> tuple[list[dict], dict]:
    """Flatten generations; retain stable run/problem/generation coordinates."""
    if not is_rollout_payload(payload) or not payload["eval"]:
        raise ValueError("Math rollout input requires a nonempty eval list")
    source_questions = {}
    for row in payload.get("data") or []:
        if isinstance(row, dict) and row.get("id") is not None:
            key = str(row["id"])
            if key in source_questions:
                raise ValueError(f"Duplicate question id in data: {key}")
            source_questions[key] = row
    records, groups, runs, warnings = [], [], [], []
    for run_index, evaluation in enumerate(payload["eval"]):
        if not isinstance(evaluation, dict) or not evaluation.get("results"):
            raise ValueError(f"eval[{run_index}] has no results")
        run_id = f"eval_{run_index + 1:03d}"
        dataset = str(evaluation.get("dataset") or payload.get("dataset") or "math")
        metadata = {k: v for k, v in evaluation.items() if k != "results"}
        seen_ids, run_groups = set(), []
        for problem_index, result in enumerate(evaluation["results"]):
            if not isinstance(result, dict):
                raise ValueError(f"{run_id}: invalid problem record")
            problem_id = _first(result.get("problem_id"), result.get("id"))
            if problem_id is None or str(problem_id) in seen_ids:
                raise ValueError(f"{run_id}: missing or duplicate problem_id {problem_id}")
            seen_ids.add(str(problem_id))
            original = source_questions.get(str(problem_id), {})
            question = _first(result.get("problem"), result.get("question"), original.get("problem"))
            target = _first(result.get("ground_truth"), result.get("answer"), original.get("answer"))
            if question is None or target is None:
                raise ValueError(f"{run_id}/{problem_id}: missing problem or ground_truth")
            generations = result.get("generations")
            if not isinstance(generations, list) or not generations:
                raise ValueError(f"{run_id}/{problem_id}: empty generations")
            expected = _first(result.get("val_n"), evaluation.get("val_n"), len(generations))
            if type(expected) is not int or expected <= 0 or expected != len(generations):
                raise ValueError(f"{run_id}/{problem_id}: incomplete rollout group, val_n={expected}, received={len(generations)}")
            question_key = _digest([dataset, str(problem_id), str(question), str(target)])
            indices = []
            for generation_index, generation in enumerate(generations):
                if not isinstance(generation, dict) or type(generation.get("correct")) is not bool:
                    raise ValueError(f"{run_id}/{problem_id}/{generation_index}: correct must be an explicit boolean")
                for name in ("formatted", "truncated"):
                    if name in generation and type(generation[name]) is not bool:
                        raise ValueError(f"{run_id}/{problem_id}: {name} must be boolean when supplied")
                prediction = _first(generation.get("full_generation"), generation.get("prediction"), generation.get("completion"))
                if prediction is None:
                    prediction = generation.get("predicted_answer", "")
                coords = {
                    "run_index": run_index, "run_id": run_id,
                    "problem_index": problem_index, "problem_id": problem_id,
                    "generation_index": generation_index, "question_key": question_key,
                    "group_id": f"{run_id}/problem_{problem_index + 1:03d}",
                    "val_n": expected,
                }
                record = {
                    "id": f"{coords['group_id']}/rollout_{generation_index + 1:03d}",
                    "question": str(question), "target": target,
                    "generated_ans": str(prediction), "passed": generation["correct"],
                    "correct": generation["correct"], META_KEY: coords,
                }
                for name in ("predicted_answer", "formatted", "truncated", "finish_reason", "stop_reason", "output_token_count", "overall_error_tag", "short_critique"):
                    if name in generation:
                        record[name] = generation[name]
                for name in ("subject", "topic", "subtopic", "question_type", "level"):
                    value = _first(result.get(name), original.get(name))
                    if value is not None:
                        record[name] = value
                indices.append(len(records))
                records.append(record)
            correct = sum(records[i]["passed"] for i in indices)
            group = {
                "group_id": coords["group_id"], "run_id": run_id, "dataset": dataset,
                "question_key": question_key, "problem_id": problem_id,
                "question": str(question), "target": target, "indices": indices,
                "total": len(indices), "correct": correct,
                "grade": rollout_grade(correct, len(indices)),
            }
            for name in ("subject", "topic", "question_type"):
                if name in records[indices[0]]:
                    group[name] = records[indices[0]][name]
            if result.get("num_correct") is not None and result["num_correct"] != correct:
                warnings.append(f"{run_id}/题 {problem_id}: num_correct={result['num_correct']} 与逐次正确数 {correct} 不一致，以逐次 correct 为准。")
            groups.append(group)
            run_groups.append(group)
        run_total = sum(g["total"] for g in run_groups)
        run_correct = sum(g["correct"] for g in run_groups)
        for key, actual in (("num_problems", len(run_groups)), ("total_solutions", run_total), ("average_at_n", run_correct), ("pass_at_n", sum(g["correct"] > 0 for g in run_groups))):
            if key in metadata and metadata[key] != actual:
                warnings.append(f"{run_id}: {key}={metadata[key]} 与明细统计 {actual} 不一致。")
        runs.append({"run_id": run_id, "dataset": dataset, "metadata": metadata,
                     "problems": len(run_groups), "total": run_total, "correct": run_correct,
                     "at_least_one_correct": sum(g["correct"] > 0 for g in run_groups),
                     "grade_counts": dict(Counter(g["grade"] for g in run_groups))})
    return records, {"schema": SCHEMA, "runs": runs, "groups": groups, "warnings": warnings,
                     "unique_questions": len({g["question_key"] for g in groups}),
                     "num_groups": len(groups), "num_rollouts": len(records)}


def prepare_math_rollout_input(state: dict) -> bool:
    """Reuse existing scores for nested Math inputs in both CLI and graph nodes."""
    cfg = state.setdefault("analyzer", {})
    if str(cfg.get("analyze_task_type", "")).lower() not in {"math", "mathematics", "math_reasoning", "数学"}:
        return False
    judger = state.get("judger") or {}
    bench = state.get("bench") or judger.get("bench") or {}
    bench_meta = bench.get("meta", {}) if isinstance(bench, dict) else getattr(bench, "meta", {})
    bench_meta = bench_meta or {}
    path = _first(cfg.get("eval_result_path"), judger.get("output_result_path"), judger.get("out_result_path"), judger.get("eval_result_path"),
                  bench_meta.get("eval_detail_path"), (bench_meta.get("artifact_paths") or {}).get("records_path"))
    if not path or Path(path).suffix.lower() != ".json":
        return False
    source = Path(path).expanduser().resolve()
    stat = source.stat()
    source_stamp = [str(source), stat.st_size, stat.st_mtime_ns]
    existing = cfg.get("math_rollout_input") or {}
    if (existing.get("source_stamp") == source_stamp
            and Path(existing.get("normalized_path") or "").is_file()
            and (cfg.get("metric_eval_results") or {}).get("source_schema") == SCHEMA):
        return True
    with source.open(encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not is_rollout_payload(payload):
        return False
    records, context = normalize_rollouts(payload)
    outdir = Path(cfg.get("runtime_output_dir") or Path(cfg.get("output_dir") or state.get("output_dir") or "outputs") / str(state.get("task_id") or "default_task") / "analyzer")
    outdir.mkdir(parents=True, exist_ok=True)
    normalized = outdir / "math_rollout_records.jsonl"
    with normalized.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    context.update(source_stamp=source_stamp, source_path=str(source), normalized_path=str(normalized.resolve()))
    primary = {"priority": "primary", "score": sum(r["passed"] for r in records) / len(records),
               "details": [{"score": float(r["passed"]), "extracted": r.get("predicted_answer"), "match_type": "judger_verdict"} for r in records]}
    metrics = {"judger_correctness": primary}
    # Missing format/truncation fields remain unknown, not presumed successes.
    for field, metric_name in (("formatted", "extraction_rate"), ("truncated", "truncation_rate")):
        if all(field in r for r in records):
            metrics[metric_name] = {"priority": "diagnostic", "score": sum(r[field] for r in records) / len(records), "details": [float(r[field]) for r in records]}
    result = {"source_schema": SCHEMA, "num_samples": len(records), "metrics": metrics,
              "alignment": {"path": str(normalized.resolve()), "source_path": str(source), "mode": "records"}}
    bench_name = "+".join(dict.fromkeys(r["dataset"] for r in context["runs"]))
    state["bench"] = {"bench_name": bench_name, "bench_dataflow_eval_type": "qa"}
    cfg["math_rollout_input"] = context
    cfg["metric_eval_results"] = result
    cfg["metric_plan"] = {bench_name: [{"name": "judger_correctness", "priority": "primary"}]}
    state["metric_plan"] = cfg["metric_plan"]
    state["eval_results"] = result
    return True


def write_enriched_rollouts(context: dict, records: list[dict], output: Path) -> Path:
    """Add public labels to original generations; preserve every original field."""
    source = Path(context["source_path"])
    stat = source.stat()
    if context.get("source_stamp") != [str(source), stat.st_size, stat.st_mtime_ns]:
        raise ValueError("Judger source changed during diagnosis; restart with the updated input")
    with source.open(encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    expected = sum(len(p["generations"]) for e in payload["eval"] for p in e["results"])
    if expected != len(records):
        raise ValueError("Enriched rollout count does not match original input")
    seen = set()
    for record in records:
        loc = record[META_KEY]
        coord = (loc["run_index"], loc["problem_index"], loc["generation_index"])
        if coord in seen:
            raise ValueError(f"Duplicate enriched rollout coordinate: {coord}")
        seen.add(coord)
        generation = payload["eval"][coord[0]]["results"][coord[1]]["generations"][coord[2]]
        if record["correct"] != generation["correct"]:
            raise ValueError("Analyzer must not change Judger correctness")
        if not generation["correct"]:
            for field in ("overall_error_tag", "short_critique"):
                if not record.get(field):
                    raise ValueError(f"Missing {field} for rollout {coord}")
                generation[field] = record[field]
    with output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return output
