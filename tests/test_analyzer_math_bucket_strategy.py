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


def test_math_bucket_resolves_every_failure_to_training_or_metric_audit():
    actionable_a = _failed("计算错误")
    actionable_a["judge"] = {
        "stage": "math_labeled",
        "overall_error_tag": "计算错误",
        "tags": ["计算错误"],
        "actionable": True,
        "construction_scope": "step",
    }
    actionable_b = _failed("计算错误")
    actionable_b["judge"] = {
        "stage": "math_labeled",
        "overall_error_tag": "计算错误",
        "tags": ["计算错误"],
        "actionable": True,
        "construction_scope": "whole_case",
    }
    whole_case = _failed("公式使用错误或遗漏")
    whole_case["judge"] = {
        "stage": "math_labeled",
        "overall_error_tag": "公式使用错误或遗漏",
        "tags": ["公式使用错误或遗漏"],
        "actionable": True,
        "needs_review": False,
        "construction_scope": "whole_case",
    }
    metric_anomaly = _failed("评测异常")
    metric_anomaly["judge"] = {
        "stage": "math_metric_anomaly",
        "overall_error_tag": "评测异常",
        "tags": ["评测异常"],
        "actionable": False,
        "needs_review": False,
        "diagnosis_status": "metric_anomaly",
    }

    plan = build_training_bucket_strategy(
        [actionable_a, actionable_b, whole_case, metric_anomaly],
        task_type="math",
    )
    rows = {row["bucket"]: row for row in plan["buckets"]}

    assert rows["math_arithmetic_calculation"]["count"] == 2
    assert rows["math_arithmetic_calculation"]["actionable_count"] == 2
    assert rows["math_arithmetic_calculation"]["step_construction_count"] == 1
    assert rows["math_arithmetic_calculation"]["whole_case_construction_count"] == 1
    assert rows["math_strategy_theorem"]["count"] == 1
    assert rows["math_strategy_theorem"]["actionable_count"] == 1
    assert rows["math_strategy_theorem"]["whole_case_construction_count"] == 1
    assert plan["diagnostic_bucket"]["count"] == 0
    assert plan["metric_audit_bucket"]["count"] == 1
    assert plan["count_coverage"] == {
        "failed_total": 4,
        "model_failure_total": 3,
        "metric_anomaly_count": 1,
        "known_bucket_count": 3,
        "diagnostic_bucket_count": 0,
        "counted_total": 4,
        "all_failures_counted": True,
        "actionable_total": 3,
        "step_construction_total": 1,
        "whole_case_construction_total": 2,
        "non_actionable_total": 0,
        "count_definition": "all_failed_cases_observed_once",
        "actionable_count_definition": "all_resolved_model_failures_step_or_whole_case",
    }


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
    assert "【数学训练数据分桶建议】" in report_text
    summary_text = Path(analyzer["analyze_output_summary_text_path"]).read_text(encoding="utf-8")
    final_text = Path(analyzer["analyze_output_final_report_text_path"]).read_text(encoding="utf-8")
    assert "【数据集背景介绍】" in summary_text
    assert "【错误审计报告】" in report_text
    assert "待诊断" not in report_text
    assert "暂缓" not in report_text
    assert "【训练数据分桶建议】" in final_text
    assert "【错误审计报告】" not in final_text
    assert "generated data plan" not in final_text
    assert analyzer["analyze_output_summary_path"].endswith(".txt")
    assert analyzer["report_artifact_format"] == "text_only"
    assert "analysis_summary_json_path" not in analyzer
    assert "analyze_output_report_json_path" not in analyzer
    assert "analyze_output_final_report_json_path" not in analyzer
    bundle_dir = tmp_path / "output" / "数学评测最终报告"
    dataset_dir = bundle_dir / "MATH-500"
    assert Path(analyzer["math_report_bundle_dir"]) == bundle_dir
    assert Path(analyzer["math_report_dataset_dir"]) == dataset_dir
    assert Path(analyzer["math_report_overview_path"]).name == "总览.txt"
    assert {path.name for path in dataset_dir.iterdir()} == {
        "01_数据集背景与评测概览.txt",
        "02_完整分析与审计报告.txt",
        "03_最终报告.txt",
        "04_模型改进建议.txt",
        "05_数据爬取与构造建议.txt",
    }
    assert len(list(bundle_dir.rglob("*.txt"))) == 6
    assert not list(bundle_dir.rglob("*.json"))
    assert len(list((tmp_path / "output" / ".analyzer_report_history").glob("*.json"))) == 1


def test_math_report_writes_complete_human_readable_bundle_by_default(tmp_path, monkeypatch):
    report_module = importlib.import_module(
        "loopai.skills.Analyzer.nodes.analyze_metric_report_node"
    )
    records_path = tmp_path / "math_predictions.jsonl"
    records = [
        _failed("计算错误", question="计算 17+28"),
        _failed("化简错误", question="化简多项式"),
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
                "details": [{"score": 0.0}, {"score": 0.0}],
            },
        },
    }
    state = {
        "task_id": "math-artifact-test",
        "bench": {"bench_name": "MATH-test", "bench_dataflow_eval_type": "qa"},
        "analyzer": {
            "analyze_task_type": "math",
            "runtime_output_dir": str(tmp_path / "output"),
            "metric_eval_results": metric_result,
            "metric_report_quick": True,
        },
    }
    monkeypatch.setattr(report_module, "_safe_get_writer", lambda: None)

    result = report_module.analyze_metric_report_node(state)
    analyzer = result["analyzer"]
    report_text = Path(analyzer["analyze_output_report_text_path"]).read_text(encoding="utf-8")
    final_text = Path(analyzer["analyze_output_final_report_text_path"]).read_text(encoding="utf-8")
    suggestion_text = Path(analyzer["analyze_output_suggestion_path"]).read_text(encoding="utf-8")
    obtainer_text = Path(analyzer["analyze_output_obtainer_txt_path"]).read_text(encoding="utf-8")

    for section in (
        "1) 失败模式画像",
        "2) 数据爬取与构造策略",
        "3) 训练数据配方",
        "4) 奖励/判因/评测改进建议",
        "5) 下一轮优先级路线图",
    ):
        assert section in report_text
    assert "改进建议：" in final_text
    assert "【模型改进建议】" in suggestion_text
    assert "【Obtainer 细粒度报告】" in obtainer_text
    assert analyzer["analyze_output_obtainer_text_path"] == analyzer["analyze_output_obtainer_txt_path"]
    assert Path(analyzer["analyze_output_summary_text_path"]).name == "01_数据集背景与评测概览.txt"
    assert Path(analyzer["analyze_output_report_text_path"]).name == "02_完整分析与审计报告.txt"
    assert Path(analyzer["analyze_output_final_report_text_path"]).name == "03_最终报告.txt"
    assert Path(analyzer["analyze_output_suggestion_path"]).name == "04_模型改进建议.txt"
    assert Path(analyzer["analyze_output_obtainer_txt_path"]).name == "05_数据爬取与构造建议.txt"
    dataset_dir = Path(analyzer["math_report_dataset_dir"])
    assert dataset_dir.name == "MATH-test"
    assert len(list(dataset_dir.glob("*.txt"))) == 5
    assert not list(dataset_dir.rglob("*.json"))
    assert len(list((tmp_path / "output" / ".analyzer_report_history").glob("*.json"))) == 1


def test_math_report_subject_name_is_portable():
    report_module = importlib.import_module(
        "loopai.skills.Analyzer.nodes.analyze_metric_report_node"
    )
    assert report_module._safe_math_report_subject_name({
        "bench_name": "Qwen3 8B / MATH:test?",
        "dataset": {},
    }) == "Qwen3_8B_MATH_test"


def test_direct_badcase_manifest_only_contains_actionable_seeds():
    report_module = importlib.import_module(
        "loopai.skills.Analyzer.nodes.analyze_metric_report_node"
    )
    actions = [
        {
            "action_id": "case-1",
            "actionable": True,
            "needs_review": False,
            "capability_bucket": "math_arithmetic_calculation",
            "domain": "arithmetic",
            "overall_error_tag": "计算错误",
            "short_critique": "第二步加法算错。",
            "confidence": 0.92,
            "seed_bad_case": {
                "problem": "计算 17+28",
                "gold_answer": "45",
                "wrong_solution": "17+28=44",
                "evidence_quote": "17+28=44",
                "first_error_step": "17+28=44",
            },
        },
        {
            "action_id": "case-review",
            "actionable": False,
            "needs_review": True,
            "seed_bad_case": {"problem": "p", "gold_answer": "a"},
        },
        {
            "action_id": "case-1",
            "actionable": True,
            "needs_review": False,
            "capability_bucket": "math_arithmetic_calculation",
            "domain": "arithmetic",
            "overall_error_tag": "计算错误",
            "seed_bad_case": {
                "problem": "计算 9+8",
                "gold_answer": "17",
                "wrong_solution": "9+8=18",
            },
        },
    ]

    rows = report_module._build_direct_badcase_rows(actions)

    assert len(rows) == 2
    assert len({row["construction_id"] for row in rows}) == 2
    assert rows[0]["source_mode"] == "direct_badcase"
    assert rows[0]["external_data_selection_required"] is False
    assert rows[0]["quality_gate"]["requires_benchmark_decontamination"] is True


def test_short_critique_sampling_is_per_tag_and_supports_full():
    report_module = importlib.import_module(
        "loopai.skills.Analyzer.nodes.analyze_metric_report_node"
    )
    records = []
    details = []
    for index in range(8):
        records.append({
            "id": f"calc-{index}",
            "judge": {
                "short_critique": f"计算短评 {index}",
                "overall_error_tag": "计算错误",
                "domain": "algebra",
            },
        })
        details.append({"score": 0.0})
    for index in range(3):
        records.append({
            "id": f"formula-{index}",
            "judge": {
                "short_critique": f"公式短评 {index}",
                "overall_error_tag": "公式使用错误或遗漏",
                "domain": "geometry",
            },
        })
        details.append({"score": 0.0})

    critiques = report_module._collect_short_critiques(records, {"details": details})
    selected, selection = report_module._select_critiques_per_tag(critiques, 5)
    selected_full, selection_full = report_module._select_critiques_per_tag(critiques, "full")

    assert len(critiques) == 11
    assert len(selected) == 8
    assert selection["per_tag"]["计算错误"] == {"available": 8, "selected": 5}
    assert selection["per_tag"]["公式使用错误或遗漏"] == {"available": 3, "selected": 3}
    assert selection["mode"] == "per_tag_limit"
    assert len({row["item_id"] for row in selected}) == len(selected)
    assert len(selected_full) == 11
    assert selection_full["mode"] == "full"

    full_profile = report_module._build_critique_profile(
        None,
        critiques,
        samples_per_tag="full",
        batch_size=4,
        max_chars=100000,
    )
    assert full_profile["coverage"]["available_short_critiques"] == 11
    assert full_profile["coverage"]["processed_short_critiques"] == 11
    assert full_profile["coverage"]["all_available_critiques_processed"] is True


def test_quick_report_keeps_sampled_critique_profile_and_crawl_plan(tmp_path, monkeypatch):
    report_module = importlib.import_module(
        "loopai.skills.Analyzer.nodes.analyze_metric_report_node"
    )
    records = []
    for index in range(6):
        records.append({
            "id": f"case-{index}",
            "question": f"问题 {index}",
            "answer": str(index),
            "prediction": "错误答案",
            "judge": {
                "tags": ["计算错误"],
                "overall_error_tag": "计算错误",
                "short_critique": f"第 {index} 条计算短评。",
                "reason": f"第 {index} 条计算短评。",
                "domain": "arithmetic",
                "first_error_step": "第一步",
                "repair_target": "逐步验算",
                "confidence": 0.9,
            },
            "pred_steps": [{"step_score": 0, "errors": ["计算错误"]}],
        })

    metric_result = {
        "num_samples": 6,
        "metrics": {
            "math_verify": {
                "priority": "primary",
                "score": 0.0,
                "details": [{"score": 0.0, "match_type": "none"} for _ in records],
            },
        },
    }
    state = {
        "task_id": "critique-report-test",
        "bench": {"bench_name": "MATH-test", "bench_dataflow_eval_type": "qa"},
        "analyzer": {
            "analyze_task_type": "math",
            "runtime_output_dir": str(tmp_path / "output"),
            "metric_eval_results": metric_result,
            "labeled_records": records,
            "metric_report_quick": True,
            "critique_samples_per_tag": 5,
            "math_llmaj_stats": {
                "diagnosis_distribution": {"diagnosed": 6},
                "actions": [{"raw_payload": "DO_NOT_RENDER" * 1000}],
            },
        },
    }
    prompts = []

    def fake_invoke(_llm, prompt):
        prompts.append(prompt)
        return json.dumps({
            "error_profile": [{
                "name": "连续运算缺少校验",
                "description": "中间计算失误会传递到最终答案。",
                "affected_count": 5,
                "overall_error_tags": ["计算错误"],
                "domains": ["arithmetic"],
                "representative_critiques": ["第 0 条计算短评。"],
                "learning_need": "逐步计算与验算",
            }],
            "crawl_recommendations": [{
                "priority": 1,
                "target_gap": "逐步计算与验算",
                "source_types": ["基础运算题库"],
                "search_queries": ["数学逐步计算 验算 数据集"],
                "sample_spec": "保留可校验的中间步骤。",
                "quality_checks": ["每步结果可验证"],
                "target_metric": "步骤正确率",
            }],
        }, ensure_ascii=False)

    monkeypatch.setattr(report_module, "_safe_get_writer", lambda: None)
    monkeypatch.setattr(report_module, "init_model", lambda state: object())
    monkeypatch.setattr(report_module, "_invoke_prompt", fake_invoke)

    result = report_module.analyze_metric_report_node(state)
    analyzer = result["analyzer"]
    profile = analyzer["critique_profile"]

    assert profile["selection"]["configured_value"] == 5
    assert profile["coverage"]["available_short_critiques"] == 6
    assert profile["coverage"]["selected_short_critiques"] == 5
    assert profile["coverage"]["all_selected_critiques_processed"] is True
    assert len(prompts) == 1
    assert prompts[0].count('"short_critique"') == 5

    report_text = Path(analyzer["analyze_output_report_text_path"]).read_text(encoding="utf-8")
    final_text = Path(analyzer["analyze_output_final_report_text_path"]).read_text(encoding="utf-8")
    assert "【按总错因标签生成的错误画像】" in report_text
    assert "- 有总错因标签：6 条" in report_text
    assert "- 有一句话短评：6 条" in report_text
    assert "每标签最多 5 条" in report_text
    assert "【基于标签短评的总爬取建议】" in report_text
    assert "逐步计算与验算" in final_text
    assert "DO_NOT_RENDER" not in report_text
    assert '{"' not in report_text
    assert '{"' not in final_text
    assert "analyze_output_data_plan_text_path" not in analyzer
    assert "analyze_output_critique_profile_json_path" not in analyzer


def test_public_enriched_oj_only_appends_tag_and_short_critique(tmp_path):
    label_module = importlib.import_module(
        "loopai.skills.Analyzer.nodes.math_llmaj_label_node"
    )
    source_records = [
        {"id": "passed", "problem": "1+1", "answer": "2", "correctness": True},
        {
            "id": "failed",
            "problem": "2+3",
            "answer": "5",
            "completion": "2+3=6",
            "metadata": {"source": "judger"},
        },
    ]
    labeled_records = [
        {**source_records[0], "passed": True},
        {
            **source_records[1],
            "passed": False,
            "metric_detail": {"score": 0},
            "pred_steps": [{"errors": ["计算错误"]}],
            "judge": {
                "overall_error_tag": "计算错误",
                "short_critique": "模型把二加三算成了六。",
                "confidence": 0.95,
                "actionable": True,
            },
        },
    ]

    enriched = label_module._build_enriched_oj_records(
        source_records, labeled_records, [1]
    )

    assert enriched[0] == source_records[0]
    assert enriched[1]["overall_error_tag"] == "计算错误"
    assert enriched[1]["short_critique"] == "模型把二加三算成了六。"
    assert set(enriched[1]) - set(source_records[1]) == {
        "overall_error_tag", "short_critique"
    }
    assert enriched[1]["metadata"] == source_records[1]["metadata"]
    assert "judge" not in enriched[1]
    assert "passed" not in enriched[1]
    assert "metric_detail" not in enriched[1]
    assert "pred_steps" not in enriched[1]

    source_path = tmp_path / "judger_output.json"
    source_payload = {"dataset": "math", "records": source_records}
    source_path.write_text(
        json.dumps(source_payload, ensure_ascii=False), encoding="utf-8"
    )
    output_path = label_module._write_enriched_oj(
        str(source_path), tmp_path, "20260907_010101", enriched
    )
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["dataset"] == "math"
    assert written["records"] == enriched
