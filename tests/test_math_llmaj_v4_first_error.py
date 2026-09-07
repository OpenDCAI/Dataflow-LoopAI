# -*- coding: utf-8 -*-
"""v4.1 first-error + evidence + actionable gate regressions (mocked LLM JSON)."""
from __future__ import annotations

from loopai.skills.Analyzer.math_llmaj_quality import (
    assess_math_label_quality,
    correct_late_arithmetic_adsorption,
)
from loopai.skills.Analyzer.nodes import math_llmaj_label_node as m


def _judge(pred: str, label: dict) -> dict:
    rec = m._attach_judge({"id": 1, "generated_ans": pred}, label)
    return rec["judge"]


def test_answer_only_forces_incomplete_not_inconsistency():
    pred = "The answer is 199."
    label = {
        "tags": ["答案与过程不符"],
        "reason": "答案错",
        "confidence": 0.9,
        "domain": "geometry",
        "origin_source": "llm",
        "process_status": "substantive",
        "first_error_step": "最终答案",
        "evidence_quote": "199",
        "needs_review": False,
    }
    j = _judge(pred, label)
    assert j["process_status"] == "absent"
    assert j["tags"] == ["答题步骤不完整"]
    assert j["actionable"] is True
    assert j["needs_review"] is False
    action = m._build_obtainer_action(
        {"id": 1, "generated_ans": pred, "question": "q", "target": "200", "judge": j}
    )
    assert action is not None
    assert action["capability_bucket"] == "math_verification_completeness"


def test_superficial_formula_or_review():
    pred = "Use the quadratic formula. Answer: 3"
    label = {
        "tags": ["答案与过程不符"],
        "reason": "空泛",
        "confidence": 0.8,
        "domain": "algebra",
        "origin_source": "llm",
        "process_status": "superficial",
        "first_error_step": "套公式",
        "evidence_quote": "Use the quadratic formula",
    }
    j = _judge(pred, label)
    assert j["process_status"] in {"superficial", "absent", "incomplete"}
    assert "答案与过程不符" not in j["tags"]
    if j["tags"]:
        assert j["tags"][0] in {"公式使用错误或遗漏", "答题步骤不完整"}


def test_late_arith_adsorption_rewrites_to_simplify():
    pred = (
        "Let S be the series. After summing we get S = 10/81.\n"
        + ("more algebra steps and expansions. " * 40)
        + "\nThen 10^{101} \\equiv 0 \\pmod{1000} which is wrong.\n"
        "The answer is 42."
    )
    tags, ev, first, note = correct_late_arithmetic_adsorption(
        ["计算错误"],
        "10^{101} \\equiv 0 \\pmod{1000}",
        "末步同余",
        pred,
    )
    assert note == "late_arith_to_early_simplify"
    assert tags == ["化简错误"]
    assert "10/81" in ev or "S =" in ev

    label = {
        "tags": ["计算错误"],
        "reason": "末步同余错",
        "confidence": 0.9,
        "domain": "number_theory",
        "origin_source": "llm",
        "process_status": "substantive",
        "first_error_step": "末步同余",
        "evidence_quote": "10^{101} \\equiv 0 \\pmod{1000}",
        "needs_review": False,
    }
    j = _judge(pred, label)
    assert j["tags"] == ["化简错误"]
    assert j["actionable"] is True
    assert "10/81" in j["evidence_quote"] or "S =" in j["evidence_quote"]


def test_first_error_tag_normalization_via_post_map():
    pred = (
        "Step1: S = 1+2+... = 10/81.\n"
        "Step2: 10^101 \\equiv 0 \\pmod{1000}.\n"
        "Answer: 7"
    )
    label = {
        "tags": ["化简错误"],
        "reason": "S错化为10/81",
        "confidence": 0.88,
        "domain": "number_theory",
        "origin_source": "llm",
        "process_status": "substantive",
        "first_error_step": "S=10/81",
        "evidence_quote": "S = 1+2+... = 10/81",
        "needs_review": False,
    }
    j = _judge(pred, label)
    assert j["tags"] == ["化简错误"]
    assert j["actionable"] is True
    assert j["diagnosis_status"] == "diagnosed"
    action = m._build_obtainer_action(
        {"id": 24, "generated_ans": pred, "judge": j, "question": "q", "target": "1"}
    )
    assert action is not None
    assert action["capability_bucket"] == "math_algebra_symbolic"


def test_true_inconsistency_without_dual_evidence_uses_whole_case():
    pred = (
        "After solving the system we obtain x=3, y=4, so the product is 12.\n"
        "Therefore the final answer is 15."
    )
    bad = {
        "tags": ["答案与过程不符"],
        "reason": "过程推12答案写15",
        "confidence": 0.9,
        "domain": "algebra",
        "origin_source": "llm",
        "process_status": "substantive",
        "first_error_step": "最终答案与过程结论冲突",
        "evidence_quote": "product is 12",
        "needs_review": False,
    }
    j_bad = _judge(pred, bad)
    assert j_bad["tags"] == ["答案与过程不符"]
    assert j_bad["actionable"] is True
    assert j_bad["construction_scope"] == "whole_case"

    good = dict(bad)
    good["evidence_quote"] = "product is 12||final answer is 15"
    j_good = _judge(pred, good)
    assert j_good["tags"] == ["答案与过程不符"]
    assert j_good["actionable"] is True
    assert j_good["construction_scope"] == "step"
    assert m._build_obtainer_action(
        {"id": 2, "generated_ans": pred, "judge": j_good, "question": "q", "target": "12"}
    )


def test_missing_evidence_routes_to_whole_case_construction():
    pred = (
        "Step 1: expand (a+b)^2 = a^2+2ab+b^2.\n"
        "Step 2: plug numbers and get 17.\n"
        "The answer is 17."
    )
    label = {
        "tags": ["计算错误"],
        "reason": "算错",
        "confidence": 0.9,
        "domain": "algebra",
        "origin_source": "llm",
        "process_status": "substantive",
        "first_error_step": "中间算术",
        "evidence_quote": "",
        "needs_review": False,
    }
    j = _judge(pred, label)
    assert j["needs_review"] is False
    assert j["actionable"] is True
    assert j["construction_scope"] == "whole_case"
    action = m._build_obtainer_action(
        {"id": 3, "question": "q", "target": "16", "generated_ans": pred, "judge": j}
    )
    assert action is not None
    assert action["construction_scope"] == "whole_case"


def test_rule_format_pollution_stays_high_conf_actionable():
    pred = "user\nAnswer: 1\nuser\nAnswer: 1\nuser\nAnswer: 1\nuser\nAnswer: 1"
    rule = m._rule_label({"generated_ans": pred, "subject": "algebra"}, detail=None, extraction_detail=1.0)
    assert rule is not None
    j = _judge(pred, rule)
    assert j["tags"] == ["输出格式错误"]
    assert j["diagnosis_status"] == "rule_confirmed"
    assert j["needs_review"] is False
    assert j["actionable"] is True
    assert j["confidence"] >= 0.85


def test_ungrounded_evidence_cannot_claim_step_scope():
    pred = "Step1: a=1. Step2: b=2. Answer: 3"
    label = {
        "tags": ["化简错误"],
        "reason": "瞎编证据",
        "confidence": 0.9,
        "domain": "algebra",
        "origin_source": "llm",
        "process_status": "substantive",
        "first_error_step": "S=10/81",
        "evidence_quote": "S = 10/81",
        "needs_review": False,
    }
    j = _judge(pred, label)
    assert j["needs_review"] is False
    assert j["actionable"] is True
    assert j["evidence_valid"] is False
    assert j["construction_scope"] == "whole_case"
    action = m._build_obtainer_action(
        {"id": 5, "question": "q", "target": "2", "generated_ans": pred, "judge": j}
    )
    assert action is not None
    assert action["construction_scope"] == "whole_case"


def test_assess_quality_is_single_source():
    pred = "The answer is 7."
    q = assess_math_label_quality(
        {
            "tags": ["答案与过程不符"],
            "confidence": 0.91,
            "origin_source": "llm",
            "evidence_quote": "7",
            "first_error_step": "ans",
            "process_status": "substantive",
        },
        pred,
    )
    assert q["tags"] == ["答题步骤不完整"]
    assert q["actionable"] is True
    assert q["diagnosis_status"] == "diagnosed"
