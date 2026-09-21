"""Read bench-scoped Judger/EvalPlus bundles without executing generated code."""
from __future__ import annotations

import ast
import hashlib
import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

META_KEY = "_code_bench"
SCHEMA = "evalplus_bench_v1"


def _read_rows(path: Path) -> list[dict]:
    from .oj_annotations import read_oj_rows
    return read_oj_rows(path)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expand_bench_sources(sources: list[dict]) -> list[dict]:
    """Accept a result file, bench directory, or a parent of bench directories."""
    expanded, seen = [], set()
    for source in sources:
        path = Path(source["path"]).expanduser()
        if path.is_dir():
            paths = sorted(path.glob("*_result.jsonl")) or sorted(path.glob("*/*_result.jsonl"))
            if not paths:
                raise ValueError(f"No <bench>_result.jsonl found in {path}")
        elif path.name.endswith("_summary.json"):
            paths = [path.with_name(path.name.removesuffix("_summary.json") + "_result.jsonl")]
        elif any(path.name.endswith(suffix) for suffix in (
            "_sample.jsonl", "_sanitized.jsonl", "_sample-sanitized.jsonl", "_eval_results.json")):
            raise ValueError("Use <bench>_result.jsonl or its bench directory, not generation/sanitization/raw evaluator files")
        else:
            paths = [path]
        for result in paths:
            identity = str(result.resolve())
            if identity not in seen:
                seen.add(identity)
                item = {**source, "path": identity}
                if path.is_dir() and len(paths) > 1:
                    item["bench_name_explicit"] = False
                expanded.append(item)
    return expanded


def _status(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or value.lower() not in {"pass", "fail", "timeout"}:
        raise ValueError(f"Unknown EvalPlus verdict {value!r}; refusing to treat missing/pending scores as failures")
    return value.lower()


def _functions(code: str) -> set[str]:
    try:
        return {node.name for node in ast.walk(ast.parse(code)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    except (SyntaxError, ValueError, TypeError):
        return set()


def _match(candidates: list[dict], row: dict, *, single_sample: bool, unique_solution: bool):
    if any("completion_id" in r for r in candidates):
        indexed = [r for r in candidates if "completion_id" in r and r["completion_id"] == row.get("completion_id")]
        return indexed[0] if len(indexed) == 1 else None
    if single_sample and len(candidates) == 1:
        return candidates[0]
    if not unique_solution:
        return None
    same_solution = [r for r in candidates if r.get("solution") == row.get("solution")]
    return same_solution[0] if len(same_solution) == 1 else None


def load_bench_records(source: dict, task_type: str) -> list[dict]:
    """Normalize only an internal copy; retain provenance for original-OJ export."""
    path = Path(source["path"])
    originals = _read_rows(path)
    if not originals:
        raise ValueError(f"Empty Judger result: {path}")
    source["sha256"] = _hash(path)
    is_evalplus = task_type == "code" and any("base_status" in r or "plus_status" in r for r in originals)
    if not is_evalplus:
        rows = deepcopy(originals)
        for index, row in enumerate(rows):
            verdict = row.get("passed", row.get("correct"))
            if type(verdict) is not bool:
                raise ValueError(f"Judger row {index} requires a boolean passed/correct verdict")
            if type(row.get("correct")) is bool and row["correct"] != verdict:
                raise ValueError(f"Conflicting passed/correct verdicts at row {index}")
            row["passed"] = verdict
            row.setdefault("bench_name", source["bench_name"])
        source["record_bench_names"] = list(dict.fromkeys(r["bench_name"] for r in rows))
        return rows

    prefix = path.stem.removesuffix("_result")
    summary_path = path.with_name(prefix + "_summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig")) if summary_path.is_file() else {}
    if not isinstance(summary, dict):
        raise ValueError("Judger summary must be a JSON object")
    pass_source = summary.get("pass_source", "plus")
    if pass_source not in {"base", "plus"}:
        raise ValueError(f"Unsupported pass_source: {pass_source}")
    pass_at_k = summary.get("pass_at_k", {})
    if not isinstance(pass_at_k, dict) or any(not isinstance(scores, dict) for scores in pass_at_k.values()):
        raise ValueError("Judger pass_at_k must map test suites to metric objects")
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
           for scores in pass_at_k.values() for value in scores.values()):
        raise ValueError("Judger pass_at_k values must be finite probabilities")
    name = str((source.get("bench_name") if source.get("bench_name_explicit") else None) or summary.get("task") or prefix)
    if pass_source == "plus" and not name.endswith("+"):
        name += "+"
    source.update(bench_name=name, record_bench_names=[name], schema=SCHEMA, artifacts={})
    warnings = []
    if "pass_source" not in summary:
        warnings.append("未提供 pass_source，按增强测试 base 与 plus 同时通过统计；请保留原 summary 以核验口径。")
    if not summary.get("dataset_hash"):
        warnings.append("缺少 dataset_hash，无法核验跨轮测试集版本是否一致。")
    if summary_path.is_file():
        source["artifacts"]["summary"] = {"path": str(summary_path), "sha256": _hash(summary_path)}
    sidecars = {}
    for kind, suffix in (("generated", "_sample.jsonl"), ("extracted", "_sanitized.jsonl"), ("evaluated", "_sample-sanitized.jsonl")):
        side_path = path.with_name(prefix + suffix)
        if not side_path.is_file():
            continue
        source["artifacts"][kind] = {"path": str(side_path), "sha256": _hash(side_path)}
        grouped = defaultdict(list)
        for record in _read_rows(side_path):
            grouped[str(record.get("task_id"))].append(record)
        sidecars[kind] = grouped

    raw_path = path.with_name(prefix + "_sample-sanitized_eval_results.json")
    if raw_path.is_file():
        raw = json.loads(raw_path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict) or not isinstance(raw.get("eval"), dict):
            raise ValueError("Raw EvalPlus artifact must contain a task-keyed eval object")
        if summary.get("dataset_hash") and raw.get("hash") != summary["dataset_hash"]:
            raise ValueError("Judger summary and raw evaluator dataset hashes differ")
        # The raw evaluator lists are sorted by completion_id, independently of task order.
        fields = ("task_id", "solution", "base_status", "plus_status", "base_fail_tests", "plus_fail_tests")
        expected = Counter(json.dumps({k: r.get(k) for k in fields}, sort_keys=True) for r in originals)
        observed = Counter(json.dumps({k: r.get(k) for k in fields}, sort_keys=True)
                           for group in raw["eval"].values() for r in group)
        if expected != observed:
            raise ValueError("Flattened Judger result and raw EvalPlus records differ")
        source["artifacts"]["raw_evaluation"] = {"path": str(raw_path), "sha256": _hash(raw_path)}

    rows, seen = [], set()
    task_counts = Counter(str(r.get("task_id")) for r in originals)
    solution_counts = Counter((str(r.get("task_id")), str(r.get("solution"))) for r in originals)
    for index, original in enumerate(originals):
        row = deepcopy(original)
        task_id = row.get("task_id")
        if not isinstance(task_id, (str, int)) or isinstance(task_id, bool) or not isinstance(row.get("solution"), str):
            raise ValueError(f"EvalPlus row {index} requires task_id and evaluated solution")
        completion_id = row.get("completion_id")
        if completion_id is None and task_counts[str(task_id)] > 1:
            raise ValueError("Repeated task_id requires explicit completion_id; row order is not a rollout identity")
        identity = (str(task_id), str(completion_id))
        if identity in seen:
            raise ValueError(f"Duplicate EvalPlus sample: {identity}")
        seen.add(identity)
        base = _status(row.get("base_status"))
        plus = _status(row.get("plus_status"), optional=pass_source == "base")
        passed = base == "pass" and (pass_source == "base" or plus == "pass")
        if "passed" in row and (type(row["passed"]) is not bool or row["passed"] != passed):
            raise ValueError("Existing passed verdict conflicts with selected EvalPlus test suite")
        if "correct" in row and (type(row["correct"]) is not bool or row["correct"] != passed):
            raise ValueError("Existing correct verdict conflicts with selected EvalPlus test suite")
        for key in ("base_fail_tests", "plus_fail_tests"):
            if key in row and not isinstance(row[key], list):
                raise ValueError(f"{key} must be a list of failing inputs")
        meta = {"schema": SCHEMA, "pass_source": pass_source, "dataset_hash": summary.get("dataset_hash"),
                "image": summary.get("image"), "base_passed": base == "pass",
                "plus_passed": base == plus == "pass", "plus_only_failure": base == "pass" and plus in {"fail", "timeout"},
                "preprocessing_issue": False}
        for kind, grouped in sidecars.items():
            candidates = grouped.get(str(task_id), [])
            matched = _match(candidates, row, single_sample=task_counts[str(task_id)] == 1,
                             unique_solution=solution_counts[(str(task_id), row["solution"])] == 1)
            if matched is None:
                warnings.append(f"{kind} 留档存在缺失或多次作答配对不明确，仅采用 result 的实际送测代码。")
                continue
            if kind == "evaluated" and matched.get("solution") != row["solution"]:
                raise ValueError(f"Evaluated solution does not match sanitizer input: {identity}")
            if kind == "generated":
                meta["raw_completion"] = matched.get("completion", "")
            if kind == "extracted":
                own_solution = matched.get("solution", "")
                meta["extract_ok"] = matched.get("extract_ok")
                meta["extract_method"] = matched.get("extract_method")
                meta["extracted_solution_differs"] = own_solution != row["solution"]
                removed = _functions(own_solution) - _functions(row["solution"])
                meta["removed_functions"] = sorted(removed)
                meta["preprocessing_issue"] = bool(removed)
        row.update(passed=passed, completion=row["solution"], bench_name=name)
        row[META_KEY] = meta
        if completion_id is not None:
            row["sample_index"] = completion_id
        rows.append(row)

    base_count = sum(r[META_KEY]["base_passed"] for r in rows)
    plus_count = sum(r[META_KEY]["plus_passed"] for r in rows)
    failed_tasks = {r["task_id"] for r in rows if not r["passed"]}
    actual = {"samples": len(rows), "problems": len(task_counts), "base_pass_samples": base_count,
              "plus_pass_samples": plus_count, "failed_task_count": len(failed_tasks)}
    for key, count in actual.items():
        if key in summary and summary[key] != count:
            if key == "failed_task_count" and any(n > 1 for n in task_counts.values()):
                warnings.append("多次作答下 failed_task_count 与至少一次失败题数不一致，需确认该字段是否表示全错题数；本报告以逐条结果为准。")
                continue
            raise ValueError(f"Judger summary {key}={summary[key]} differs from result count {count}")
    # pass@1 is an equal-question mean, not a row-weighted pass rate when N differs.
    per_task = defaultdict(list)
    for row in rows:
        per_task[str(row["task_id"])].append(row["passed"])
    pass_one = sum(sum(values) / len(values) for values in per_task.values()) / len(per_task)
    if "pass@1" in summary and (type(summary["pass@1"]) not in (int, float)
                               or not math.isfinite(summary["pass@1"]) or abs(summary["pass@1"] - pass_one) > 1e-6):
        raise ValueError("Judger summary pass@1 does not agree with per-question outcomes")
    source["evaluation"] = {**actual, "schema": SCHEMA, "pass_source": pass_source,
        "dataset_hash": summary.get("dataset_hash"), "image": summary.get("image"),
        "judger_pass_at_k": pass_at_k, "judger_pass_at_1": summary.get("pass@1"),
        "plus_only_failures": sum(r[META_KEY]["plus_only_failure"] for r in rows),
        "extracted_solution_differences": sum(r[META_KEY].get("extracted_solution_differs", False) for r in rows),
        "preprocessing_issue_samples": sum(r[META_KEY]["preprocessing_issue"] for r in rows),
        "warnings": sorted(set(warnings))}
    return rows


def preprocessing_diagnosis(row: dict) -> dict | None:
    meta = row.get(META_KEY) or {}
    if not meta.get("preprocessing_issue"):
        return None
    critique = "自行提取代码中的函数在实际送测 solution 中缺失，应先核查清洗与送测链路，不能直接判为模型不会实现。"
    return {"stage": "other", "reason": critique, "short_critique": critique,
            "overall_error_tag": "评测预处理差异", "actionable": False,
            "evidence": {"removed_functions": meta["removed_functions"]}}


def evaluation_summary(records: list[dict]) -> dict:
    metas = [row[META_KEY] for row in records if META_KEY in row]
    if not metas:
        return {}
    return {"schema": SCHEMA, "pass_sources": sorted({m["pass_source"] for m in metas}),
            "dataset_hashes": sorted({m["dataset_hash"] for m in metas if m.get("dataset_hash")}),
            "base_pass_samples": sum(m["base_passed"] for m in metas),
            "plus_pass_samples": sum(m["plus_passed"] for m in metas),
            "plus_only_failures": sum(m["plus_only_failure"] for m in metas),
            "preprocessing_issue_samples": sum(m["preprocessing_issue"] for m in metas),
            "extracted_solution_differences": sum(m.get("extracted_solution_differs", False) for m in metas),
            "verdict_rule": "plus = base 与 plus 同时 pass；base = base pass",
            "evidence_note": "solution 为实际送测代码；fail_tests 是失败输入，不含期望输出或异常栈；空列表不等于无失败。"}
