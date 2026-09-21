"""Binary, evidence-scoped training decisions and traceable collection tags."""
from __future__ import annotations

import json
import math
import re
from collections import Counter

from .math_rollout import META_KEY


DEFAULT_SFT_COMPLETION_THRESHOLDS = {
    "min_rollout_accuracy": 0.90,
    "min_format_rate": 0.95,
    "max_truncation_rate": 0.05,
    "max_all_wrong_group_fraction": 0.05,
}


def build_training_plan(evidence: dict, records: list[dict], readiness: dict,
                        overrides: dict | None = None) -> dict:
    thresholds = dict(DEFAULT_SFT_COMPLETION_THRESHOLDS)
    for key, value in (overrides or {}).items():
        if key not in thresholds or isinstance(value, bool):
            raise ValueError(f"Invalid math_sft_completion_thresholds key: {key}")
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"SFT threshold {key} must be between 0 and 1")
        thresholds[key] = value
    stats = evidence["stats"]
    task_type = evidence.get("task_type", "math")
    sft_data = {
        "math": "收集独立同类题的完整解题示范、关键步骤和答案一致性检查。",
        "code": "收集独立同类编程题、明确函数接口、可执行参考实现及边界测试，验证代码补全协议。",
        "text2sql": "收集独立业务问题、数据库 schema、参考 SQL 和执行结果，覆盖关联、过滤与聚合边界。",
    }.get(task_type, "收集独立同类题及可验证的完整示范。")
    values = {**stats, "all_wrong_group_fraction": stats["all_wrong_groups"] / stats["question_run_groups"]}
    checks = []
    for metric, key, higher, label in (
        ("rollout_accuracy", "min_rollout_accuracy", True, "逐次正确率"),
        ("format_rate", "min_format_rate", True, "输出格式通过率"),
        ("truncation_rate", "max_truncation_rate", False, "生成截断率"),
        ("all_wrong_group_fraction", "max_all_wrong_group_fraction", False, "全错组占比"),
    ):
        value = values[metric]
        passed = value is not None and (value >= thresholds[key] if higher else value <= thresholds[key])
        checks.append({"metric": metric, "label": label, "value": value,
                       "operator": ">=" if higher else "<=", "threshold": thresholds[key],
                       "passed": bool(passed), "evidence_available": value is not None})
    # Missing evidence is a conservative "no", not a fabricated model failure.
    evidence_ok = not readiness["unknown_evidence"] and not evidence["warnings"]
    sft_completed = bool(evidence_ok and all(c["passed"] for c in checks))
    rl_candidate = bool(evidence_ok and all(c["passed"] is True for c in readiness["checks"]))
    reasons = [f"{c['label']}未达到转段条件" if c["evidence_available"] else f"缺少{c['label']}证据"
               for c in checks if not c["passed"]]
    if not evidence_ok:
        reasons.append("判因、可比性或输入一致性证据未通过检查")
    if sft_completed:
        reasons.append("本次评测范围内所有预设 SFT 转段条件均通过")

    questions, group_questions = {}, {}
    datasets = {r["run_id"]: r["dataset"] for r in evidence["runs"]}
    for band in evidence["bands"].values():
        for group in band["groups"]:
            key = group["question_key"]
            group_questions[group["group_id"]] = key
            question = questions.setdefault(key, {
                "question_key": key, "problem_id": group["problem_id"],
                "dataset": datasets[group["run_id"]], "tag": group["topic"],
                "tag_source": group["topic_source"], "correct": 0, "total": 0,
                "groups": 0, "mixed_groups": 0, "error_tags": Counter(),
            })
            question["correct"] += group["correct"]
            question["total"] += group["total"]
            question["groups"] += 1
            question["mixed_groups"] += int(0 < group["correct"] < group["total"])
    for record in records:
        if record["correct"]:
            continue
        judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
        tag = str(judge.get("overall_error_tag") or record.get("overall_error_tag") or "未提供错因")
        questions[group_questions[record[META_KEY]["group_id"]]]["error_tags"][tag] += 1
        if (record.get("_code_bench") or {}).get("preprocessing_issue"):
            questions[group_questions[record[META_KEY]["group_id"]]]["preprocessing_issue"] = True

    domains, excluded = {}, []
    for key, q in sorted(questions.items()):
        if q["correct"] == q["total"]:
            excluded.append({"question_key": key, "tag": q["tag"], "reason": "全部通过，保留为回归评测，不据此自动增加训练配额"})
            continue
        if q.get("preprocessing_issue"):
            excluded.append({"question_key": key, "tag": q["tag"], "reason": "清洗/送测差异需先复核，不自动转为模型训练数据需求"})
            continue
        if q["tag_source"] == "unavailable" or q["error_tags"].get("评测异常", 0):
            excluded.append({"question_key": key, "tag": q["tag"], "reason": "题型或评分证据需复核，不自动转为训练数据需求"})
            continue
        rate = q["correct"] / q["total"]
        # Route questions before merging topics so easy siblings cannot hide a hard case.
        stage = "rl" if rl_candidate and rate >= 0.5 and q["mixed_groups"] else "sft"
        capability = q["tag_source"] == "capability_evidence"
        domain = domains.setdefault((q["tag"], stage, capability), {
            "tag": q["tag"], "question_tags": [] if capability else [q["tag"]],
            "tag_type": "capability" if capability else "question_topic", "training_stage": stage,
            "is_sft": stage == "sft", "is_rl": stage == "rl", "question_refs": [],
            "evidence": {"correct": 0, "total": 0, "mixed_groups": 0, "error_tags": Counter()},
            "reason": ("存在同题对错对照且成功率不低于 50%，推荐独立同类题的可验证 RL 小试。" if stage == "rl"
                       else "存在失败且未满足本次 RL 候选规则，优先用独立同类题的完整示范验证 SFT 补强效果。"),
            "data_requirements": (["收集独立同类题与可验证答案，在当前模型上重新采样并审核奖励函数。"] if stage == "rl"
                                  else [sft_data]),
            "analysis_source": "engineering_rule",
        })
        domain["question_refs"].append({k: q[k] for k in (
            "question_key", "dataset", "problem_id", "tag_source", "correct", "total", "groups", "mixed_groups")})
        for field in ("correct", "total", "mixed_groups"):
            domain["evidence"][field] += q[field]
        domain["evidence"]["error_tags"].update(q["error_tags"])
    rows = sorted(domains.values(), key=lambda d: (d["training_stage"] != "sft", d["tag"]))
    for index, domain in enumerate(rows, 1):
        domain["domain_id"] = f"{task_type}-{index:03d}"
        domain["evidence"]["error_tags"] = dict(domain["evidence"]["error_tags"])
        domain["evidence"]["accuracy"] = domain["evidence"]["correct"] / domain["evidence"]["total"]
    return {
        "schema_version": "1.0", "task_type": task_type,
        "evaluation": dict(stats),
        "sft_completed": sft_completed,
        "is_sft": not sft_completed or any(d["is_sft"] for d in rows),
        "is_rl": any(d["is_rl"] for d in rows),
        "rl_scope": "pilot_only", "automatic_training_authorized": False,
        "decision_scope": f"仅判断本次 {task_type} 输入评测范围是否达到预设转段门槛，不认证模型训练历史或全部领域能力",
        "boolean_definitions": {
            "sft_completed": "是否达到本次评测范围的 SFT 转段条件；缺少必要证据时为 false",
            "is_sft": "是否建议继续 SFT 补强或收集 SFT 候选数据，不表示 SFT 已完成",
            "is_rl": "是否建议收集 RL 候选数据用于小规模试验，不表示立即启动或扩大 RL",
        },
        "sft_reasons": reasons, "sft_checks": checks, "sft_thresholds": thresholds,
        "rl_checks": readiness["checks"],
        "routing_policy": "先按同题跨轮描述性正确率分流：低于50%优先SFT；不低于50%且存在轮内混合组、全局RL初筛通过时推荐RL；全对题不自动补数。同一题型可含不同训练用途，但题目引用不重复。该规则是待实验校准的起点，不是训练收益预测。",
        "domains": rows, "excluded_questions": excluded,
        "prerequisites": ["在与本 benchmark 隔离的候选数据上复核判分、推理正确性与格式约定。",
                          "RL 候选题需重新采样并审计 reward；比较等预算 SFT/RL 小试后再扩大。"],
        "data_boundary": "question_refs 仅为诊断溯源，禁止直接将测试题、答案或复刻题用于训练。",
        "limitations": readiness["limitations"], "input_warnings": list(evidence["warnings"]),
    }


def parse_training_review(text: str, plan: dict) -> dict:
    """LLM supplies prose, never overrides the scored routing or binary policy."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    value = json.loads(cleaned)
    if not isinstance(value, dict) or not isinstance(value.get("assessment"), str) or not value["assessment"].strip():
        raise ValueError("Training review must contain nonempty assessment text")
    rows = value.get("domains")
    if not isinstance(rows, list):
        raise ValueError("Training review must contain domains")
    expected = {row["domain_id"] for row in plan["domains"]}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("domain_id") not in expected or row["domain_id"] in seen:
            raise ValueError("Training review has unknown/duplicate domain ids")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("Training review missing domain reason")
        requirements = row.get("data_requirements")
        if not isinstance(requirements, list) or not requirements or not all(isinstance(x, str) and x.strip() for x in requirements):
            raise ValueError("Training review missing concrete data requirements")
        seen.add(row["domain_id"])
    if seen != expected:
        raise ValueError("Training review omitted collection domains")
    return {"assessment": value["assessment"].strip(), "domains": [
        {k: row[k] for k in ("domain_id", "reason", "data_requirements")} for row in rows]}


def render_training_decision(plan: dict) -> str:
    yn = lambda value: "是" if value else "否"
    return (f"SFT 是否达到转段条件：{yn(plan['sft_completed'])}。\n"
            f"是否继续 SFT 补强：{yn(plan['is_sft'])}。\n"
            f"是否建议 RL 小规模试验：{yn(plan['is_rl'])}。\n"
            "SFT 和 RL 分别判断，可针对不同能力同时准备数据；不自动启动训练。\n")
