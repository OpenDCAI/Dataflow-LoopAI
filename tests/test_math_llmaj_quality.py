# -*- coding: utf-8 -*-
from loopai.skills.Analyzer.math_llmaj_quality import (
    aggregate_obtainer_actions,
    assert_math_field_aliases,
    evaluate_labels,
    export_gold_template_from_labeled,
    validate_obtainer_seed,
    assess_math_label_quality,
)
from loopai.skills.Analyzer.nodes.math_llmaj_label_node import _attach_judge, _build_obtainer_action


def test_math_field_aliases_from_judger():
    raw = {
        "problem": "求 1+1",
        "answer": 2,
        "generated_ans": "答案是 3",
    }
    norm = assert_math_field_aliases(raw)
    assert norm["question"] == "求 1+1"
    assert norm["target"] == 2
    assert norm["prediction"] == "答案是 3"
    assert norm["generated_ans"] == "答案是 3"


def test_obtainer_seed_nonempty_contract():
    seed = {
        "problem": "q",
        "gold_answer": 1,
        "wrong_solution": "bad",
    }
    assert validate_obtainer_seed(seed) == []
    assert "problem_empty" in validate_obtainer_seed(
        {"problem": "", "gold_answer": 1, "wrong_solution": "x"}
    )


def test_needs_review_blocks_per_case_action():
    rec = {
        "id": 9,
        "question": "q",
        "target": 1,
        "generated_ans": "wrong long solution",
        "judge": {
            "tags": ["计算错误"],
            "confidence": 0.9,
            "domain": "algebra",
            "needs_review": True,
            "actionable": False,
            "evidence_quote": "2+2=5",
            "first_error_step": "2+2=5",
        },
    }
    assert _build_obtainer_action(rec) is None


def test_action_requires_evidence_and_fields():
    labeled = _attach_judge(
        {
            "id": 3,
            "question": "q",
            "target": 2,
            "generated_ans": ".... 所以 2a+b=13 ...",
        },
        {
            "tags": ["计算错误"],
            "short_critique": "解答在代入后的运算中出错，导致最终结果错误。",
            "overall_error_tag": "计算错误",
            "confidence": 0.9,
            "domain": "algebra",
            "origin_source": "llm",
            "evidence_quote": "所以 2a+b=13",
            "first_error_step": "2a+b",
            "repair_target": "分步验算",
            "needs_review": False,
            "context_truncated": False,
        },
    )
    act = _build_obtainer_action(labeled)
    assert act is not None
    assert act["seed_bad_case"]["problem"]
    assert act["seed_bad_case"]["gold_answer"] == 2
    assert act["seed_bad_case"]["wrong_solution"]
    assert act["evidence_quote"] == "所以 2a+b=13"
    assert act["overall_error_tag"] == "计算错误"
    assert act["short_critique"] == "解答在代入后的运算中出错，导致最终结果错误。"


def test_aggregate_actions_by_bucket_domain():
    actions = [
        {
            "mode": "construct",
            "capability_bucket": "math_arithmetic_calculation",
            "domain": "number_theory",
            "confidence": 0.9,
            "error_tags": ["计算错误"],
            "seed_bad_case": {"id": 1, "problem": "p1", "gold_answer": 1, "short_critique": "a"},
        },
        {
            "mode": "construct",
            "capability_bucket": "math_arithmetic_calculation",
            "domain": "number_theory",
            "confidence": 0.8,
            "error_tags": ["计算错误"],
            "seed_bad_case": {"id": 2, "problem": "p2", "gold_answer": 2, "short_critique": "b"},
        },
        {
            "mode": "construct",
            "capability_bucket": "math_modeling",
            "domain": "geometry",
            "confidence": 0.85,
            "error_tags": ["题意理解错误"],
            "seed_bad_case": {"id": 3, "problem": "p3", "gold_answer": 3, "short_critique": "c"},
        },
    ]
    agg = aggregate_obtainer_actions(actions, max_seeds_per_group=2, default_target_count=20)
    assert len(agg) == 2
    arith = next(x for x in agg if x["capability_bucket"] == "math_arithmetic_calculation")
    assert arith["sample_count"] == 2
    assert set(arith["seed_bad_case_ids"]) == {1, 2}
    assert arith["mode"] == "construct_budget"
    assert arith["target_count"] >= 10


def test_evaluate_labels_high_conf_precision():
    gold = [
        {"case_id": "1", "gold_tag": "计算错误", "is_diagnosable": True},
        {"case_id": "2", "gold_tag": "计算错误", "is_diagnosable": True},
        {"case_id": "3", "gold_tag": "化简错误", "is_diagnosable": True},
    ]
    pred = [
        {"id": "1", "judge": {"tags": ["计算错误"], "confidence": 0.9, "needs_review": False}},
        {"id": "2", "judge": {"tags": ["化简错误"], "confidence": 0.9, "needs_review": False}},
        {"id": "3", "judge": {"tags": ["化简错误"], "confidence": 0.5, "needs_review": False}},
    ]
    report = evaluate_labels(gold, pred, high_conf_threshold=0.8)
    assert report["n_gold_complete"] == 3
    assert report["high_conf_support"] == 2
    assert report["high_conf_precision"] == 0.5


def test_export_gold_template_keeps_pred_for_reference():
    rows = [
        {
            "id": 7,
            "idx": 0,
            "question": "q",
            "target": 1,
            "generated_ans": "ans",
            "judge": {"tags": ["计算错误"], "domain": "algebra", "confidence": 0.88},
        }
    ]
    tpl = export_gold_template_from_labeled(rows)
    assert tpl[0]["case_id"] == "7"
    assert tpl[0]["gold_tag"] == ""
    assert tpl[0]["pred_tag"] == "计算错误"
    assert tpl[0]["problem_preview"] == "q"


def test_missing_short_critique_is_filled_and_routed():
    result = assess_math_label_quality(
        {
            "tags": ["计算错误"],
            "confidence": 0.9,
            "origin_source": "llm",
            "process_status": "substantive",
            "first_error_step": "2+2=5",
            "evidence_quote": "2+2=5",
        },
        "Step 1: 2+2=5. Therefore the answer is 5.",
    )
    assert result["overall_error_tag"] == "计算错误"
    assert result["short_critique"] == "运算错误。"
    assert result["needs_review"] is False
    assert result["actionable"] is True
    assert result["construction_scope"] == "step"


def test_correct_equivalent_failure_routes_to_metric_audit():
    result = assess_math_label_quality(
        {
            "tags": [],
            "short_critique": "模型推导正确，最终结果与标准答案等价。",
            "confidence": 0.95,
            "origin_source": "llm",
            "process_status": "substantive",
        },
        "The final answer is (x+1)(x-1).",
    )
    assert result["overall_error_tag"] == "评测异常"
    assert result["diagnosis_status"] == "metric_anomaly"
    assert result["resolution_route"] == "metric_audit"
    assert result["needs_review"] is False
    assert result["actionable"] is False


def test_correct_failure_with_reference_match_wording_routes_to_metric_audit():
    critiques = (
        "算式转换与加减均正确，算式与模型过程一致。",
        "该题虽然最终结果与标准答案一致，但模型额外给出了代码验证。",
    )
    for critique in critiques:
        result = assess_math_label_quality(
            {
                "tags": [],
                "short_critique": critique,
                "confidence": 0.9,
                "origin_source": "llm",
                "process_status": "substantive",
            },
            "The derivation is complete and the final answer matches the reference.",
        )
        assert result["overall_error_tag"] == "评测异常"
        assert result["diagnosis_status"] == "metric_anomaly"
        assert result["actionable"] is False


def test_correct_answer_with_format_metric_failure_routes_to_metric_audit():
    result = assess_math_label_quality(
        {
            "tags": [],
            "short_critique": "题目虽作对，但因与标准答案格式不完全一致（缺少星号），可能导致判定失败。",
            "confidence": 0.92,
            "origin_source": "llm",
            "process_status": "substantive",
        },
        "The computed answer is correct but uses an equivalent format.",
    )

    assert result["overall_error_tag"] == "评测异常"
    assert result["diagnosis_status"] == "metric_anomaly"
    assert result["resolution_route"] == "metric_audit"
    assert result["construction_scope"] == "none"


def test_local_correct_step_does_not_mask_real_output_error():
    result = assess_math_label_quality(
        {
            "tags": ["输出格式错误"],
            "short_critique": "第一步计算正确，但最终答案格式错误，无法按题目要求提取。",
            "confidence": 0.9,
            "origin_source": "llm",
            "process_status": "substantive",
        },
        "Step 1 is correct. The final response omits the required answer field.",
    )

    assert result["overall_error_tag"] == "输出格式错误"
    assert result["diagnosis_status"] == "diagnosed"
    assert result["resolution_route"] == "training_data"


def test_legacy_inconsistency_demote_is_restored_as_whole_case():
    result = assess_math_label_quality(
        {
            "tags": [],
            "overall_error_tag": "",
            "short_critique": "现有作答不足以证明最终答案与推导过程存在明确冲突。",
            "quality_reason": "inconsistency_demote",
            "process_status": "substantive",
            "origin_source": "llm",
        },
        "先得到 x=2，最后写成 x=3。",
    )

    assert result["overall_error_tag"] == "答案与过程不符"
    assert result["diagnosis_status"] == "diagnosed"
    assert result["actionable"] is True
    assert result["construction_scope"] == "whole_case"
    assert result["resolution_route"] == "training_data"
