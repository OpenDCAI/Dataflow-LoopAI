import json
import importlib
from pathlib import Path

from loopai.skills.Analyzer.bucket_strategy import (
    build_training_bucket_strategy,
    classify_failure_bucket,
)


def _failed(error: str, *, question: str = "求方程的解") -> dict:
    return {
        "id": error,
        "passed": False,
        "question": question,
        "reference": "给出完整推导并验证最终答案。",
        "prediction": "这里是模型给出的完整解题过程和最终答案。",
        "pred_steps": [{"step_score": 0, "errors": [error]}],
    }


def test_math_structured_labels_map_to_capability_buckets():
    cases = {
        "计算错误": "math_arithmetic_calculation",
        "化简错误": "math_algebra_symbolic",
        "题意理解错误": "math_modeling",
        "公式使用错误或遗漏": "math_strategy_theorem",
        "答案与过程不符": "math_reasoning_consistency",
        "答题步骤不完整": "math_verification_completeness",
        "输出格式错误": "math_output_contract",
    }

    for error, expected in cases.items():
        result = classify_failure_bucket(_failed(error), task_type="math")
        assert result["bucket"] == expected
        assert result["confidence"] >= 0.86


def test_math_extraction_failure_is_separate_from_wrong_answer():
    extraction_failure = {
        "passed": False,
        "question": "计算 1+1",
        "reference": "2",
        "prediction": "没有按要求给出可提取的最终答案",
        "metric_detail": {"score": 0.0, "match_type": "none"},
        "metric_details": {"extraction_rate": 0.0},
    }
    wrong_answer_without_diagnosis = {
        "passed": False,
        "question": "计算 1+1 并说明理由",
        "reference": "先计算一加一，最终答案为二。",
        "prediction": "我完成了计算和推导，但最终得到三。",
        "metric_detail": {"score": 0.0, "match_type": "none"},
        "metric_details": {"extraction_rate": 1.0},
    }

    assert classify_failure_bucket(extraction_failure, "math")["bucket"] == "math_output_contract"
    assert classify_failure_bucket(wrong_answer_without_diagnosis, "math")["bucket"] == "diagnostic_unknown"


def test_math_strategy_allocates_by_capability_and_reports_domain():
    records = [
        _failed("计算错误", question="计算分数与百分比"),
        _failed("化简错误", question="化简多项式并解方程"),
        _failed("题意理解错误", question="已知椭圆焦点和弦长，求参数"),
        _failed("公式使用错误或遗漏", question="使用导数求函数最大值"),
        _failed("答案与过程不符", question="证明数列不等式"),
        _failed("答题步骤不完整", question="求概率并验证所有情况"),
    ]

    plan = build_training_bucket_strategy(records, task_type="mathematics")
    rows = {row["bucket"]: row for row in plan["buckets"]}

    assert plan["task_type"] == "math"
    assert plan["other_impact"]["unresolved_count"] == 0
    assert abs(sum(row["recommended_share"] for row in rows.values()) - 1.0) < 2e-6
    assert rows["math_modeling"]["domain_breakdown"][0]["domain"] == "geometry"
    assert plan["methodology_references"]


def test_existing_general_route_is_unchanged():
    record = {
        "passed": False,
        "prediction": "A fluent but unsupported answer.",
        "judge": {"reason": "factual error and unsupported claim"},
    }
    assert classify_failure_bucket(record, "general")["bucket"] == "general_factuality_grounding"

    code_record = {"passed": False, "prediction": "def broken(:", "stderr": "SyntaxError: invalid syntax"}
    sql_record = {"passed": False, "prediction": "SELECT missing FROM t", "stderr": "no such column: missing"}
    assert classify_failure_bucket(code_record, "code")["bucket"] == "code_syntax_completion"
    assert classify_failure_bucket(sql_record, "text2sql")["bucket"] == "sql_schema_linking"


def test_math_metric_report_persists_math_allocation_plan(tmp_path, monkeypatch):
    report_module = importlib.import_module(
        "loopai.skills.Analyzer.nodes.analyze_metric_report_node"
    )
    assert report_module._infer_bucket_task_type(
        {"analyzer": {"analyze_task_type": "general"}},
        {"primary_metric": "numerical_match", "bench_name": "custom", "task_domain": "general"},
    ) == "general"

    records_path = tmp_path / "math_predictions.jsonl"
    records = [
        _failed("计算错误", question="计算分数与百分比"),
        _failed("题意理解错误", question="已知椭圆焦点和弦长，求参数"),
    ]
    records_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )
    metric_result = {
        "num_samples": 2,
        "alignment": {"path": str(records_path)},
        "metrics": {
            "math_verify": {
                "priority": "primary",
                "score": 0.0,
                "details": [
                    {"score": 0.0, "match_type": "none", "extracted": "3"},
                    {"score": 0.0, "match_type": "none", "extracted": "1/2"},
                ],
            },
            "extraction_rate": {
                "priority": "diagnostic",
                "score": 1.0,
                "details": [1.0, 1.0],
            },
        },
    }
    state = {
        "task_id": "math-report-test",
        "bench": {"bench_name": "MATH-500", "bench_dataflow_eval_type": "qa"},
        "analyzer": {
            "analyze_task_type": "math",
            "runtime_output_dir": str(tmp_path / "output"),
            "metric_eval_results": metric_result,
        },
    }

    monkeypatch.setattr(report_module, "_safe_get_writer", lambda: None)
    monkeypatch.setattr(report_module, "init_model", lambda state: object())
    monkeypatch.setattr(report_module, "build_prompt_for_report", lambda summary: "report")
    monkeypatch.setattr(report_module, "build_prompt_for_data_plan", lambda summary: "data plan")
    monkeypatch.setattr(report_module, "build_prompt_for_obtainer", lambda summary, stats: "obtainer")
    monkeypatch.setattr(report_module, "_invoke_prompt", lambda llm, prompt: f"generated {prompt}")

    result = report_module.analyze_metric_report_node(state)
    analyzer = result["analyzer"]
    plan = analyzer["allocation_plan"]

    assert plan["task_type"] == "math"
    assert analyzer["analysis_summary"]["bucket_task_type"] == "math"
    report_text = Path(analyzer["analyze_output_report_text_path"]).read_text(encoding="utf-8")
    assert "【Math 训练数据分桶建议】" in report_text
    report_json = json.loads(
        Path(analyzer["analyze_output_report_json_path"]).read_text(encoding="utf-8")
    )
    assert report_json["summary"]["allocation_plan"]["task_type"] == "math"
