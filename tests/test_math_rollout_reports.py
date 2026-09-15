import copy
import importlib
import json
from pathlib import Path

import pytest

from loopai.skills.Analyzer.math_rollout import (
    META_KEY, GRADES, normalize_rollouts, prepare_math_rollout_input,
    rollout_grade, write_enriched_rollouts,
)
from loopai.skills.Analyzer.math_rollout_report import (
    assess_training_readiness, build_rollout_evidence, classify_topics,
    generate_rollout_reports, render_training_report,
)
from loopai.skills.Analyzer.math_training_plan import build_training_plan, parse_training_review


def payload(counts=(12, 9, 6, 1, 0), n=12, repeats=2):
    results = []
    for index, correct in enumerate(counts):
        results.append({
            "problem_id": index, "problem": f"Solve x+{index}=12", "ground_truth": 0,
            "topic": "方程求解", "val_n": n, "num_correct": correct,
            "correct": True, "full_generation": "THIS IS ONLY A REPRESENTATIVE, NOT ALL ROLLOUTS",
            "generations": [{"full_generation": f"Step 1: x={g}; final answer {g}",
                             "predicted_answer": str(g), "correct": g < correct,
                             "formatted": True, "truncated": False, "output_token_count": 32}
                            for g in range(n)],
        })
    return {"data": [{"id": i, "answer": 0, "problem": r["problem"]} for i, r in enumerate(results)],
            "eval": [{"dataset": "fixture", "val_n": n, "results": copy.deepcopy(results)} for _ in range(repeats)]}


def labeled(rows):
    for row in rows:
        if row["correct"]:
            continue
        row["judge"] = {"stage": "math_labeled", "tags": ["计算错误"],
                        "overall_error_tag": "计算错误", "short_critique": f"{row['id']}：移项计算错误。",
                        "actionable": True, "construction_scope": "whole_case", "confidence": 0.9}
    return rows


@pytest.mark.parametrize("correct,total,expected", [
    (12, 12, "好"), (11, 12, "较好"), (9, 12, "较好"), (8, 12, "中等"),
    (6, 12, "中等"), (5, 12, "较差"), (1, 12, "较差"), (0, 12, "差"),
    (3, 4, "较好"), (2, 4, "中等"), (1, 4, "较差"), (1, 1, "好"), (0, 1, "差"),
])
def test_rollout_boundaries(correct, total, expected):
    assert rollout_grade(correct, total) == expected


def test_normalize_every_generation_and_preserve_zero_answer():
    source = payload()
    original = copy.deepcopy(source)
    rows, context = normalize_rollouts(source)
    assert source == original
    assert len(rows) == 120
    assert len({r["id"] for r in rows}) == 120
    assert context["unique_questions"] == 5
    assert context["num_groups"] == 10
    assert all(row["target"] == 0 for row in rows)
    assert all("REPRESENTATIVE" not in row["generated_ans"] for row in rows)
    assert context["runs"][0]["grade_counts"] == {g: 1 for g in GRADES}
    label_module = importlib.import_module("loopai.skills.Analyzer.nodes.math_llmaj_label_node")
    assert label_module._normalize_record_fields({"target": 0, "answer": 10})["target"] == 0


@pytest.mark.parametrize("mutation", ["missing_score", "string_score", "incomplete", "empty", "duplicate"])
def test_bad_input_is_not_silently_counted_as_failure(mutation):
    source = payload(repeats=1)
    result = source["eval"][0]["results"][0]
    if mutation == "missing_score":
        result["generations"][0].pop("correct")
    elif mutation == "string_score":
        result["generations"][0]["correct"] = "false"
    elif mutation == "incomplete":
        result["generations"].pop()
    elif mutation == "empty":
        result["generations"] = []
    else:
        source["eval"][0]["results"].append(copy.deepcopy(result))
    with pytest.raises(ValueError):
        normalize_rollouts(source)


def test_wrong_aggregate_is_audited_and_leaf_scores_win():
    source = payload(repeats=1)
    source["eval"][0]["results"][0]["num_correct"] = 1
    rows, context = normalize_rollouts(source)
    assert context["groups"][0]["correct"] == 12
    assert context["warnings"]


def test_readiness_uses_groups_not_rollouts_and_requires_diagnoses():
    rows, context = normalize_rollouts(payload())
    evidence = build_rollout_evidence(context, labeled(rows))
    assert evidence["stats"]["mixed_groups"] == 6
    assert evidence["stats"]["mixed_group_fraction"] == 0.6
    assert sum(b["failed_rollouts"] for b in evidence["bands"].values()) == evidence["stats"]["failed"]
    assert len(evidence["pooled_questions"]) == 5
    assessment = assess_training_readiness(evidence)
    assert assessment["decision"] == "先修复薄弱项，再评估 RL 小规模试验"  # 28/60 < .5
    assert "不能" in assessment["sft_completion"]
    assert "5 道独立题" in assessment["limitations"][0]
    for row in rows:
        row.pop("judge", None)
    unknown = assess_training_readiness(build_rollout_evidence(context, rows), {"min_rollout_accuracy": .4})
    assert unknown["unknown_evidence"] is True
    assert "证据不足" in unknown["decision"]


def test_missing_format_is_unknown_and_all_correct_not_automatically_rl_ready():
    rows, context = normalize_rollouts(payload((12,), repeats=1))
    for r in rows:
        r.pop("formatted")
    evidence = build_rollout_evidence(context, rows)
    checks = {c["metric"]: c for c in assess_training_readiness(evidence)["checks"]}
    assert checks["format_rate"]["passed"] is None
    assert checks["mixed_group_fraction"]["passed"] is False


def test_partial_success_supports_conditional_pilot_not_sft_completion():
    rows, context = normalize_rollouts(payload((12, 9, 6), repeats=2))
    evidence = build_rollout_evidence(context, labeled(rows))
    a = assess_training_readiness(evidence)
    assert a["decision"] == "具备开展有条件 RL 小规模试验的信号"
    text = render_training_report(evidence, a)
    assert "不能仅凭" in text
    assert "未执行模型综合评审" in text
    with pytest.raises(ValueError):
        assess_training_readiness(evidence, {"max_truncation_rate": float("nan")})


def test_nested_enriched_oj_changes_only_failure_tag_and_critique(tmp_path):
    source = payload(repeats=1)
    path = tmp_path / "input.json"
    path.write_text(json.dumps(source))
    state = {"analyzer": {"analyze_task_type": "math", "eval_result_path": str(path), "runtime_output_dir": str(tmp_path)}}
    assert prepare_math_rollout_input(state)
    cfg = state["analyzer"]
    rows = [json.loads(line) for line in Path(cfg["math_rollout_input"]["normalized_path"]).read_text().splitlines()]
    for row in rows:
        if not row["correct"]:
            row.update(overall_error_tag="计算错误", short_critique="移项计算错误")
    output = write_enriched_rollouts(cfg["math_rollout_input"], rows, tmp_path / "enriched.json")
    enriched = json.loads(output.read_text())
    tagged = 0
    for run in enriched["eval"]:
        for result in run["results"]:
            for generation in result["generations"]:
                if not generation["correct"]:
                    assert generation.pop("overall_error_tag") == "计算错误"
                    assert generation.pop("short_critique") == "移项计算错误"
                    tagged += 1
    assert tagged == sum(not row["correct"] for row in rows)
    assert enriched == source
    assert json.loads(path.read_text()) == source
    rows[0]["correct"] = False
    with pytest.raises(ValueError):
        write_enriched_rollouts(cfg["math_rollout_input"], rows, tmp_path / "bad.json")


def test_old_math_inputs_do_not_enter_rollout_adapter(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"data": [{"problem": "q", "answer": 0, "prediction": "1"}]}))
    assert not prepare_math_rollout_input({"analyzer": {"analyze_task_type": "math", "eval_result_path": str(path)}})


def test_topic_model_uses_all_questions_including_all_correct_and_deduplicates_repeats(tmp_path):
    source = payload(repeats=3)
    for run in source["eval"]:
        for r in run["results"]:
            r.pop("topic")
    _, context = normalize_rollouts(source)
    seen = []
    def invoke(prompt):
        batch = json.loads(prompt.split("\n", 1)[1])
        seen.extend(batch)
        return json.dumps({"topics": [{"question_key": r["question_key"], "topic": "方程求解", "reason": "题干含方程"} for r in batch]})
    topics = classify_topics(context, invoke, tmp_path, {})
    assert len(seen) == 5
    assert len(topics) == 5
    classify_topics(context, lambda p: pytest.fail("cache should be reused"), tmp_path, {})


def test_extra_reports_use_every_failed_critique_and_keep_zero_band_visible(tmp_path):
    rows, context = normalize_rollouts(payload((12, 9, 6, 0), repeats=2))
    context["normalized_path"] = str(tmp_path / "records.jsonl")
    state = {"analyzer": {"math_rollout_input": context}}
    seen = []
    def profile(llm, critiques, **kwargs):
        assert kwargs["samples_per_tag"] == "full"
        seen.extend(c["item_id"] for c in critiques)
        return {"coverage": {"processed_short_critiques": len(critiques)}, "analysis_mode": "llm_map_reduce", "error_profile": [], "crawl_recommendations": []}
    prompts = []
    def invoke(llm, prompt):
        prompts.append(prompt)
        plan = json.loads(prompt.split("\n", 1)[1])["training_plan"]
        return json.dumps({"assessment": "支持：部分题目有稳定成功轨迹。反对：独立题量小，需 reward 审计后试验。",
                           "domains": [{"domain_id": d["domain_id"], "reason": "移项计算需对照验证",
                                        "data_requirements": ["独立方程题及移项检查"]} for d in plan["domains"]]})
    rollout, training, summary = generate_rollout_reports(state, labeled(rows), object(), invoke=invoke,
        build_profile=profile, render_profile=lambda p: ("错误画像", "训练数据建议"), progress=lambda msg: None)
    assert len(seen) == sum(not row["correct"] for row in rows)
    assert len(set(seen)) == len(seen)
    assert "【较差】" in rollout and "本档无题目" in rollout
    assert "支持：" in training
    assert summary["model_review_completed"]
    assert "不能用单个小 benchmark" in prompts[-1]
    assert summary["training_plan"]["sft_completed"] is False
    assert summary["readiness"]["sft_completion"] == "否"
    assert "SFT 是否达到转段条件：否" in training
    assert all(d["analysis_source"] == "llm_with_scored_evidence" for d in summary["training_plan"]["domains"])


def test_metric_and_report_nodes_accept_rollouts_without_oneeval_regrading(tmp_path, monkeypatch):
    metric = importlib.import_module("loopai.skills.Analyzer.nodes.metric_score_node")
    recommend = importlib.import_module("loopai.skills.Analyzer.nodes.metric_recommend_node")
    report = importlib.import_module("loopai.skills.Analyzer.nodes.analyze_metric_report_node")
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(payload(repeats=1)))
    state = {"task_id": "rollout-test", "analyzer": {"analyze_task_type": "math", "eval_result_path": str(path), "runtime_output_dir": str(tmp_path / "output"), "metric_report_quick": True}}
    monkeypatch.setattr(metric, "MetricRunner", lambda *a, **kw: pytest.fail("must reuse Judger scores"))
    monkeypatch.setattr(recommend, "get_safe_stream_writer", lambda: None)
    monkeypatch.setattr(metric, "get_safe_stream_writer", lambda: None)
    recommend.metric_recommend_node(state)
    metric.metric_score_node(state)
    cfg = state["analyzer"]
    assert cfg["metric_eval_results"]["num_samples"] == 60
    assert "judger_correctness" in cfg["metric_eval_results"]["metrics"]
    rows = [json.loads(line) for line in Path(cfg["math_rollout_input"]["normalized_path"]).read_text().splitlines()]
    cfg["labeled_records"] = labeled(rows)
    monkeypatch.setattr(report, "init_model", lambda state: None)
    monkeypatch.setattr(report, "_safe_get_writer", lambda: None)
    report.analyze_metric_report_node(state)
    directory = Path(cfg["math_report_dataset_dir"])
    assert len(list(directory.glob("*.txt"))) == 7
    for output in directory.glob("*.txt"):
        raw = output.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw
    assert "【Rollout 五档能力分析】" in Path(cfg["analyze_output_report_text_path"]).read_text(encoding="utf-8-sig")
    assert "【SFT / RL 训练阶段评估】" in Path(cfg["math_training_stage_report_path"]).read_text(encoding="utf-8-sig")
    plan = json.loads(Path(cfg["math_training_plan_path"]).read_text(encoding="utf-8"))
    assert plan == cfg["math_rollout_summary"]["training_plan"]
    assert all(type(plan[k]) is bool for k in ("is_sft", "is_rl", "sft_completed"))
    assert cfg["report_artifact_format"] == "text_with_training_plan"
    assert "08_training_plan.json" in Path(cfg["analyze_output_final_report_text_path"]).read_text(encoding="utf-8-sig")


def test_training_plan_routes_same_topic_by_question_evidence_without_duplicates():
    rows, context = normalize_rollouts(payload((12, 11, 10, 6, 1, 0)))
    evidence = build_rollout_evidence(context, labeled(rows))
    plan = build_training_plan(evidence, rows, assess_training_readiness(evidence))
    assert plan["sft_completed"] is False
    assert plan["is_sft"] is True and plan["is_rl"] is True
    assert len(plan["domains"]) == 2
    sft, rl = plan["domains"]
    assert sft["tag"] == rl["tag"] == "方程求解"
    assert sft["training_stage"] == "sft" and sft["is_sft"] is True and sft["is_rl"] is False
    assert rl["training_stage"] == "rl" and rl["is_rl"] is True and rl["is_sft"] is False
    assert {q["problem_id"] for q in sft["question_refs"]} == {4, 5}
    assert {q["problem_id"] for q in rl["question_refs"]} == {1, 2, 3}
    assert sum(d["evidence"]["total"] - d["evidence"]["correct"] for d in plan["domains"]) == evidence["stats"]["failed"]
    assert len(plan["excluded_questions"]) == 1
    assert sum(d["evidence"]["error_tags"]["计算错误"] for d in plan["domains"]) == evidence["stats"]["failed"]


@pytest.mark.parametrize("correct,expected_sft,expected_rl", [(12, True, False), (11, True, True), (10, False, True), (0, False, False)])
def test_binary_completion_both_sides_and_rl_independent(correct, expected_sft, expected_rl):
    rows, context = normalize_rollouts(payload((correct,), repeats=1))
    evidence = build_rollout_evidence(context, labeled(rows))
    plan = build_training_plan(evidence, rows, assess_training_readiness(evidence))
    assert plan["sft_completed"] is expected_sft
    assert plan["is_sft"] is (not expected_sft)
    assert plan["is_rl"] is expected_rl


def test_completion_thresholds_are_configurable_and_missing_evidence_is_false():
    rows, context = normalize_rollouts(payload((10,), repeats=1))
    evidence = build_rollout_evidence(context, labeled(rows))
    readiness = assess_training_readiness(evidence)
    assert build_training_plan(evidence, rows, readiness, {"min_rollout_accuracy": .8})["sft_completed"] is True
    for bad in ({"typo": .5}, {"min_rollout_accuracy": float("nan")}, {"min_format_rate": True}):
        with pytest.raises(ValueError):
            build_training_plan(evidence, rows, readiness, bad)
    for r in rows:
        r.pop("formatted")
    evidence = build_rollout_evidence(context, rows)
    plan = build_training_plan(evidence, rows, assess_training_readiness(evidence), {"min_rollout_accuracy": .8})
    assert plan["sft_completed"] is False and plan["is_rl"] is False
    assert any("缺少" in reason for reason in plan["sft_reasons"])


def test_training_review_cannot_invent_ids_or_override_flags():
    plan = {"domains": [{"domain_id": "math-001"}]}
    row = {"domain_id": "math-001", "reason": "原因", "data_requirements": ["独立题"]}
    parsed = parse_training_review(json.dumps({"assessment": "结论", "is_sft": "false", "domains": [row]}), plan)
    assert "is_sft" not in parsed
    for invalid in ([], [row, row], [{**row, "domain_id": "invented"}], [{**row, "data_requirements": []}]):
        with pytest.raises(ValueError):
            parse_training_review(json.dumps({"assessment": "结论", "domains": invalid}), plan)


def test_label_node_preserves_nested_input_and_report_reads_disk_checkpoint(tmp_path, monkeypatch):
    label_node = importlib.import_module("loopai.skills.Analyzer.nodes.math_llmaj_label_node")
    report = importlib.import_module("loopai.skills.Analyzer.nodes.analyze_metric_report_node")
    source = payload((2,), n=4, repeats=2)
    input_path = tmp_path / "nested.json"
    input_path.write_text(json.dumps(source))
    state = {"analyzer": {"analyze_task_type": "math", "eval_result_path": str(input_path),
                          "runtime_output_dir": str(tmp_path / "version"), "metric_report_quick": True}}
    prepare_math_rollout_input(state)
    monkeypatch.setattr(label_node, "get_safe_stream_writer", lambda: None)
    monkeypatch.setattr(label_node, "_rule_label", lambda *args: {
        "tags": ["计算错误"], "overall_error_tag": "计算错误", "short_critique": "移项计算错误。",
        "reason": "移项计算错误。", "confidence": 0.95, "origin_source": "llm",
        "process_status": "substantive", "evidence_quote": "Step 1", "first_error_step": "Step 1"})
    label_node.math_llmaj_label_node(state)
    cfg = state["analyzer"]
    assert cfg["labeled_records"] == []
    assert Path(cfg["labeled_records_path"]).suffix == ".jsonl"
    enhanced = json.loads(Path(cfg["enriched_oj_path"]).read_text())
    for run in enhanced["eval"]:
        for problem in run["results"]:
            for generation in problem["generations"]:
                if not generation["correct"]:
                    assert generation.pop("overall_error_tag")
                    assert generation.pop("short_critique")
    assert enhanced == source
    assert cfg["metric_eval_results"]["alignment"]["source_path"] == str(input_path)
    assert len(report._load_records_from_alignment(cfg["metric_eval_results"])) == 8
    monkeypatch.setattr(report, "_safe_get_writer", lambda: None)
    monkeypatch.setattr(report, "init_model", lambda s: None)
    report.analyze_metric_report_node(state)
    assert cfg["math_rollout_summary"]["stats"]["missing_critiques"] == 0


def test_different_sampling_configs_prevent_pooled_readiness_claim():
    source = payload((12, 9, 6), repeats=2)
    source["eval"][0]["temperature"] = .1
    source["eval"][1]["temperature"] = 1.0
    rows, context = normalize_rollouts(source)
    evidence = build_rollout_evidence(context, labeled(rows))
    assert evidence["stats"]["comparison_cohorts"] == 2
    assert "证据不足" in assess_training_readiness(evidence)["decision"]


def test_profile_outage_is_not_cached_as_successful_model_review(tmp_path):
    rows, context = normalize_rollouts(payload((6,), repeats=1))
    context["normalized_path"] = str(tmp_path / "records.jsonl")
    state = {"analyzer": {"math_rollout_input": context}}
    def fallback(*args, **kwargs):
        return {"coverage": {"fallback_batch_count": 1}, "error_profile": []}
    with pytest.raises(RuntimeError, match="归纳有模型请求失败"):
        generate_rollout_reports(state, labeled(rows), object(), invoke=lambda *a: "评审",
                                 build_profile=fallback, render_profile=lambda p: ("", ""), progress=lambda msg: None)
    assert not list((tmp_path / "rollout_report_cache").glob("profile_*.json"))


def test_report_prompt_does_not_include_full_rollout_trajectories(tmp_path):
    report = importlib.import_module("loopai.skills.Analyzer.nodes.analyze_metric_report_node")
    source = payload((0,), n=12, repeats=1)
    for generation in source["eval"][0]["results"][0]["generations"]:
        generation["full_generation"] = "Reasoning. " * 10000
    path = tmp_path / "long.json"
    path.write_text(json.dumps(source))
    state = {"analyzer": {"analyze_task_type": "math", "eval_result_path": str(path), "runtime_output_dir": str(tmp_path)}}
    prepare_math_rollout_input(state)
    result = state["analyzer"]["metric_eval_results"]
    records, _ = normalize_rollouts(source)
    summary = report._build_summary(state, result, records)
    assert "details" not in summary["summary_json"]["metrics"]["judger_correctness"]
    assert len(result["metrics"]["judger_correctness"]["details"]) == 12
    assert all(s["prediction_excerpted"] for s in summary["quick_samples"])
    assert all(len(s["generated_ans"]) < 1700 for s in summary["quick_samples"])
    assert all(s["target"] == 0 for s in summary["quick_samples"])
    assert len(records[0]["generated_ans"]) > 100000
