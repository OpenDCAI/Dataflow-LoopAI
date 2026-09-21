"""Adapt flat Code/SQL Judger records without regrading or mutating the OJ."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from copy import deepcopy

from .oj_annotations import diagnosis_annotation
from .math_rollout import GRADES, META_KEY, rollout_grade


def _first(row: dict, keys: tuple, default=None):
    return next((row[k] for k in keys if row.get(k) is not None and row[k] != ""), default)


def _identity(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def adapt_oj_report_evidence(records: list[dict], dataset: str, task_type: str,
                             cache_path: str) -> tuple[dict, list[dict]]:
    if not records:
        raise ValueError("Cannot publish a report for an empty Judger result")
    rows, groups, runs = [], {}, {}
    warnings, seen_samples = set(), set()
    run_fields = ("model", "model_name", "model_path", "checkpoint", "version_id", "temperature", "top_p", "val_n")
    for index, original in enumerate(records):
        verdict = _first(original, ("passed", "correct"))
        if type(verdict) is not bool:
            raise ValueError(f"Judger record {index}: passed/correct must be an explicit boolean")
        if type(original.get("passed")) is bool and type(original.get("correct")) is bool and original["passed"] != original["correct"]:
            raise ValueError(f"Judger record {index}: conflicting passed/correct verdicts")
        row = deepcopy(original)
        code_meta = row.get("_code_bench") or {}
        if code_meta:
            if code_meta.get("extracted_solution_differs"):
                warnings.add("自行提取代码与实际送测 solution 存在差异；以 result 中送测代码为准，不直接归咎于模型能力。")
            if code_meta.get("preprocessing_issue"):
                warnings.add("部分函数在清洗/送测过程中缺失，需先修复评测链路；相关题目暂不产生模型训练需求。")
            if not code_meta.get("dataset_hash"):
                warnings.add("缺少测试集哈希，无法核验测试版本可比性。")
        row["correct"] = verdict
        metadata = {key: row[key] for key in run_fields if key in row}
        metadata["dataset"] = dataset
        if code_meta:
            metadata["evaluation_protocol"] = {key: code_meta.get(key) for key in ("schema", "pass_source", "dataset_hash", "image")}
        run_id = _identity([dataset, row.get("run_id"), metadata])
        question = _first(row, ("question", "problem_prompt", "problem", "prompt", "input"), "")
        question = question if isinstance(question, str) else ""
        problem_id = _first(row, ("task_id", "problem_id", "question_id", "id"))
        if problem_id is None:
            problem_id = _identity([question, row.get("db_file")]) if question else f"record-{index}"
            if not question:
                warnings.add("部分记录缺少题号与题干，按独立记录统计，不能确定同题采样关系。")
        question_key = _identity([dataset, problem_id, question, row.get("db_file")])
        group_id = f"{run_id}/{question_key}"
        sample_id = _first(row, ("sample_index", "completion_id", "generation_index"))
        if sample_id is not None:
            identity = (group_id, str(sample_id))
            if identity in seen_samples:
                raise ValueError(f"Duplicate Judger sample identity: {problem_id}/{sample_id}")
            seen_samples.add(identity)
        group = groups.setdefault(group_id, {
            "group_id": group_id, "question_key": question_key, "run_id": run_id,
            "problem_id": str(problem_id), "dataset": dataset, "question": question,
            "topic": str(_first(row, ("topic", "question_tag", "category", "question_type"), "")),
            "total": 0, "correct": 0, "expected": set(),
        })
        topic = str(_first(row, ("topic", "question_tag", "category", "question_type"), ""))
        if topic and group["topic"] and topic != group["topic"]:
            warnings.add("同题存在不一致题型标签，需核对元数据后再下发训练任务。")
        if not group["topic"]:
            group["topic"] = topic
        expected = _first(row, ("val_n", "num_rollouts"))
        if expected is not None:
            if type(expected) is not int or expected < 1:
                raise ValueError("Declared rollout count must be a positive integer")
            group["expected"].add(expected)
        row[META_KEY] = {"group_id": group_id, "generation_index": group["total"]}
        row["id"] = f"{group_id}/{group['total']}"
        group["total"] += 1
        group["correct"] += verdict
        if not verdict:
            judge = row.get("judge") if isinstance(row.get("judge"), dict) else {}
            row["judge"] = {**judge, **diagnosis_annotation(row, task_type)}
        # Runtime timeout and syntax validity are not generation metadata.
        for field in ("formatted", "truncated"):
            if type(row.get(field)) is not bool:
                row.pop(field, None)
        if "truncated" not in row and row.get("finish_reason") in {"length", "stop", "eos_token"}:
            row["truncated"] = row["finish_reason"] == "length"
        rows.append(row)
        runs.setdefault(run_id, {"run_id": run_id, "dataset": dataset, "metadata": metadata})
    for group in groups.values():
        if group["topic"].lower().replace("_", "") in {"qa", "shortans", "shortanswer", "multiplechoice", "mcq"}:
            group["topic"] = ""
        expected = group.pop("expected")
        if len(expected) > 1 or (expected and expected != {group["total"]}):
            warnings.add("声明的采样数与实际作答数不一致；以下分档只描述已收到记录，不代表完整 rollout。")
        elif not expected:
            warnings.add("未声明每题预期采样数，无法验证是否收齐全部 rollout；分档仅按已收到记录计算。")
    group_tags = defaultdict(Counter)
    for row in rows:
        if not row["correct"]:
            tag = row["judge"]["overall_error_tag"]
            if tag:
                group_tags[row[META_KEY]["group_id"]][tag] += 1
    for group in groups.values():
        if not group["topic"] and not group["question"] and group_tags[group["group_id"]]:
            group["topic"] = "能力缺陷：" + group_tags[group["group_id"]].most_common(1)[0][0]
            group["topic_source"] = "capability_evidence"
    if all(g["total"] == 1 for g in groups.values()):
        warnings.add("每题仅观察到一次作答，只会出现好/差两档；不能由此判断多次采样稳定性或组内奖励信号。")
    if any(not any(run["metadata"].get(k) for k in ("model", "model_name", "model_path", "checkpoint")) for run in runs.values()):
        warnings.add("缺少可核实的模型或 checkpoint 标识，训练转段需要补充实验配置。")
    by_run = defaultdict(list)
    for group in groups.values():
        by_run[group["run_id"]].append(group)
    for key, run in runs.items():
        current = by_run[key]
        counts = Counter(rollout_grade(g["correct"], g["total"]) for g in current)
        run.update(correct=sum(g["correct"] for g in current), total=sum(g["total"] for g in current),
                   problems=len(current), at_least_one_correct=sum(g["correct"] > 0 for g in current),
                   grade_counts={grade: counts[grade] for grade in GRADES})
    return {"task_type": task_type, "normalized_path": cache_path, "num_rollouts": len(rows),
            "num_groups": len(groups), "unique_questions": len({g["question_key"] for g in groups.values()}),
            "groups": list(groups.values()), "runs": list(runs.values()), "warnings": sorted(warnings),
            "denominator_scope": "observed"}, rows
