import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopai.skills.Analyzer.oj_annotations import write_annotated_oj, read_oj_rows, source_digest
from loopai.skills.Analyzer.report_history import (
    prepare_report_history, commit_report_history, history_snapshot, compare_snapshots, render_report_history,
)
from loopai.skills.Analyzer.oj_report_bundle import publish_oj_report_bundles


def rows(correct=1, count=4):
    return [{"task_id": "q", "sample_index": i, "question": "Sort numbers", "completion": f"result {i}",
             "passed": i < correct, "topic": "数组排序", "judge": {"stage": "assert", "short_critique": "边界输入未正确处理。"}}
            for i in range(count)]


def state_for(tmp_path, version, task="task", **cfg):
    directory = tmp_path / task / "analyzer" / version
    directory.mkdir(parents=True, exist_ok=True)
    return {"task_id": task, "version_id": version, "analyzer": {"runtime_output_dir": str(directory),
            "analyze_task_type": "code", **cfg}}


def prepare(state, records, dataset="bench", metric="judger_passed"):
    root = Path(state["analyzer"]["runtime_output_dir"])
    return prepare_report_history(state, outdir=root, dataset=dataset, task_type=state["analyzer"]["analyze_task_type"],
                                  records=records, source_path=str(root / "oj.jsonl"), metric=metric)


@pytest.mark.parametrize("task_type", ["code", "text2sql"])
def test_export_preserves_every_original_field_and_all_rows(tmp_path, task_type):
    original = [{"task_id": "a", "passed": False, "completion": "bad", "custom": {"answer": 0}, "judge": {"old": 1}},
                {"task_id": "b", "passed": True, "completion": "ok", "list": [1, "中文", None]}]
    diagnosed = copy.deepcopy(original)
    diagnosed[0]["judge"] = {"stage": "syntax", "short_critique": "缺少冒号导致语法解析失败。"}
    diagnosed[0]["bench_name"] = "internal-only"
    before = copy.deepcopy(original)
    output = write_annotated_oj(original, diagnosed, tmp_path / "09_oj_enriched.jsonl", task_type)
    result = read_oj_rows(output)
    assert len(result) == len(original)
    assert result[1] == original[1]
    assert result[0].pop("short_critique") == "缺少冒号导致语法解析失败。"
    assert result[0].pop("overall_error_tag")
    assert result[0] == original[0]
    assert original == before
    assert "bench_name" not in result[0]


def test_export_rejects_misaligned_rows(tmp_path):
    with pytest.raises(ValueError, match="reordered"):
        write_annotated_oj(rows(), list(reversed(rows())), tmp_path / "out.jsonl", "code")


def test_auto_second_round_and_resume_and_first_baseline(tmp_path):
    first = prepare(state_for(tmp_path, "v1"), rows(1))
    assert first["public"]["has_baseline"] is False
    commit_report_history(first)
    state = state_for(tmp_path, "v2")
    second = prepare(state, rows(3))
    assert second["public"]["round_number"] == 2
    comparison = second["public"]["comparisons"][0]
    assert comparison["baseline_version_id"] == "v1"
    assert comparison["matched_question_count"] == 1
    assert comparison["improved_question_count"] == 1
    assert comparison["matched_mean_question_pass_rate_delta"] == .5
    commit_report_history(second)
    resumed = prepare(state, rows(3))
    assert resumed["public"] == second["public"]
    third = prepare(state_for(tmp_path, "v3"), rows(2))
    assert third["public"]["round_number"] == 3
    assert [c["baseline_version_id"] for c in third["public"]["comparisons"]] == ["v2", "v1"]
    assert "-25.00 个百分点" in render_report_history(third)
    assert "+25.00 个百分点" in render_report_history(third)
    commit_report_history(third)
    resumed_in_new_process = prepare(state_for(tmp_path, "v2"), rows(3))
    assert resumed_in_new_process["public"] == second["public"]


def test_incomplete_other_task_and_other_bench_not_selected(tmp_path):
    prepare(state_for(tmp_path, "unfinished"), rows())
    commit_report_history(prepare(state_for(tmp_path, "v1", task="other"), rows()))
    commit_report_history(prepare(state_for(tmp_path, "v1"), rows(), dataset="another-bench"))
    current = prepare(state_for(tmp_path, "v2"), rows())
    assert current["public"]["round_number"] == 1
    assert not current["public"]["has_baseline"]


def test_repeated_rollouts_not_overwritten_and_example_limit_not_count_limit():
    baseline = [{**r, "task_id": str(q)} for q in range(30) for r in rows(1)]
    current = [{**r, "task_id": str(q)} for q in range(30) for r in rows(3)]
    before = history_snapshot(baseline, dataset="b", task_type="code", metric="passed")
    after = history_snapshot(current, dataset="b", task_type="code", metric="passed")
    before["version_id"] = "v1"
    comparison = compare_snapshots(after, before)
    assert before["metrics"]["total"] == 120
    assert comparison["matched_question_count"] == 30
    assert comparison["improved_question_count"] == 30
    assert len(comparison["improved_examples"]) == 20


def test_question_changes_and_metric_changes_are_not_claimed_as_progress():
    before = history_snapshot(rows(1), dataset="b", task_type="code", metric="passed")
    before["version_id"] = "v1"
    changed = [{**r, "question": "A different question"} for r in rows(4)]
    after = history_snapshot(changed, dataset="b", task_type="code", metric="passed")
    comparison = compare_snapshots(after, before)
    assert comparison["status"] == "insufficient_evidence"
    assert comparison["improved_question_count"] == 0
    assert comparison["new_question_count"] == 1
    after = history_snapshot(rows(4), dataset="b", task_type="code", metric="different-grader")
    assert compare_snapshots(after, before)["pass_rate_delta"] is None


def test_sampling_count_change_and_unscored_records_are_audited():
    before = history_snapshot(rows(1, 4), dataset="b", task_type="code", metric="passed")
    before["version_id"] = "v1"
    after = history_snapshot(rows(3, 6), dataset="b", task_type="code", metric="passed")
    comparison = compare_snapshots(after, before)
    assert comparison["matched_mean_question_pass_rate_delta"] == .25
    assert any("作答次数" in warning for warning in comparison["warnings"])
    unknown = history_snapshot([{"passed": "false"}], dataset="b", task_type="code", metric="passed")
    assert unknown["metrics"]["known"] == 0
    assert unknown["unidentified_records"] == 1


def test_nested_math_sampling_metadata_is_used_without_changing_input():
    from loopai.skills.Analyzer.report_history import with_rollout_sampling
    record = {"correct": False, "question": "q", "_math_rollout": {"run_id": "run", "val_n": 4}}
    context = {"runs": [{"run_id": "run", "metadata": {"temperature": .1}}]}
    before_rows = with_rollout_sampling([record], context)
    context["runs"][0]["metadata"]["temperature"] = .9
    after_rows = with_rollout_sampling([record], context)
    assert "temperature" not in record
    before = history_snapshot(before_rows, dataset="b", task_type="math", metric="judger_correctness")
    before["version_id"] = "old"
    after = history_snapshot(after_rows, dataset="b", task_type="math", metric="judger_correctness")
    assert any("采样参数不同" in warning for warning in compare_snapshots(after, before)["warnings"])


def test_explicit_baseline_is_respected_and_bad_path_not_silently_replaced(tmp_path):
    commit_report_history(prepare(state_for(tmp_path, "v1"), rows(3)))
    baseline = tmp_path / "explicit.jsonl"
    baseline.write_text("\n".join(json.dumps(row) for row in rows(0)))
    current = prepare(state_for(tmp_path, "v2", baseline_result_path=str(baseline)), rows(1))
    assert current["public"]["selection"] == "explicit"
    assert current["public"]["comparisons"][0]["baseline_metrics"]["correct"] == 0
    broken = prepare(state_for(tmp_path, "v3", baseline_result_path=str(tmp_path / "missing.jsonl")), rows())
    assert not broken["public"]["has_baseline"]
    assert broken["public"]["notices"]


def test_eval_node_generates_short_critique_in_same_request_and_export_uses_original(tmp_path, monkeypatch):
    eval_node = importlib.import_module("loopai.skills.Analyzer.nodes.eval_model")
    original = [{"task_id": "a", "passed": False, "completion": "def f()", "custom": {"ground_truth": 0}},
                {"task_id": "b", "passed": True, "completion": "return 1"}]
    source = tmp_path / "bench.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in original))
    before = source.read_bytes()
    state = state_for(tmp_path, "v1", eval_result_path=str(source), report_quick=True)
    state["output_dir"] = str(tmp_path)
    prompts = []
    monkeypatch.setattr(eval_node, "get_safe_stream_writer", lambda: None)
    monkeypatch.setattr(eval_node, "get_analyzer_resume_progress", lambda: 0)
    monkeypatch.setattr(eval_node, "init_model", lambda state: object())
    def llm_call(model, batch, **kwargs):
        prompts.extend(batch)
        return [SimpleNamespace(content=json.dumps({"stage": "syntax", "reason": "missing colon", "evidence": {},
                                                    "short_critique": "函数定义缺少冒号，无法解析。"})) for _ in batch]
    monkeypatch.setattr(eval_node, "call_llm_with_control", llm_call)
    eval_node.eval_model_node(state)
    assert len(prompts) == 1 and "short_critique" in prompts[0]
    publish_oj_report_bundles(state, emit=lambda *a, **kw: None)
    cfg = state["analyzer"]
    annotated = read_oj_rows(Path(cfg["enriched_oj_path"]))
    assert annotated[0].pop("short_critique") == "函数定义缺少冒号，无法解析。"
    assert annotated[0].pop("overall_error_tag")
    assert annotated == original
    assert source.read_bytes() == before
    assert cfg["report_artifacts"]["bench"]["files"]["enriched_oj"] == cfg["enriched_oj_path"]
    assert cfg["enriched_oj_sources"]["bench"]["original_source_verified"]
    source.write_text(json.dumps({**original[0], "completion": "changed"}))
    with pytest.raises(ValueError, match="source changed"):
        publish_oj_report_bundles(state, emit=lambda *a, **kw: None)


@pytest.mark.parametrize("task_type", ["code", "text2sql"])
def test_complete_publisher_delivers_history_in_text_and_json(tmp_path, task_type):
    for version, correct in (("v1", 1), ("v2", 3)):
        state = state_for(tmp_path, version, analyze_task_type=task_type, report_quick=True)
        cfg = state["analyzer"]
        directory = Path(cfg["runtime_output_dir"])
        source = directory / "source.jsonl"
        records = [{**row, "bench_name": "bench", "model": "fixture", "val_n": 4} for row in rows(correct)]
        source.write_text("\n".join(json.dumps(row) for row in records))
        summary = directory / "summary.json"
        summary.write_text(json.dumps({"results_file": str(source), "dataset_name": "bench"}))
        cfg.update(analyze_output_summary_path=str(summary), eval_result_sources=[
            {"path": str(source), "bench_name": "bench", "sha256": source_digest(source)}])
        publish_oj_report_bundles(state, emit=lambda *a, **kw: None)
        files = cfg["report_artifacts"]["bench"]["files"]
        assert len(files) == 9
        if version == "v2":
            plan = json.loads(Path(files["training_plan"]).read_text())
            assert plan["historical_comparison"]["round_number"] == 2
            comparison = plan["historical_comparison"]["comparisons"][0]
            assert comparison["baseline_version_id"] == "v1"
            assert comparison["improved_question_count"] == 1
            assert Path(comparison["baseline_result_path"]).name == "09_oj_enriched.jsonl"
            for key in ("report", "final_report"):
                assert "+50.00 个百分点" in Path(files[key]).read_text(encoding="utf-8-sig")


def test_legacy_complete_bundle_is_imported_but_partial_bundle_is_not(tmp_path):
    from loopai.skills.Analyzer.report_bundle import REPORT_FILENAMES, write_report_bundle
    for version in ("legacy", "partial"):
        state = state_for(tmp_path, version)
        root = Path(state["analyzer"]["runtime_output_dir"])
        source = root / "oj_records_enriched_old.jsonl"
        source.write_text("\n".join(json.dumps(row) for row in rows(1)))
        texts = {key: "Report" for key in REPORT_FILENAMES if key != "training_plan"}
        plan = {"task_type": "code", "sft_completed": False, "is_sft": True, "is_rl": False,
                "evaluation": {"rollouts": 4, "correct": 1}}
        directory = root / "reports" / "bench"
        files = write_report_bundle(directory, texts, plan)
        if version == "partial":
            Path(files["training"]).unlink()
    current = prepare(state_for(tmp_path, "v3"), rows(3))
    assert current["public"]["round_number"] == 2
    assert [r["baseline_version_id"] for r in current["public"]["comparisons"]] == ["legacy"]


def test_explicit_flat_math_without_metric_is_not_assumed_comparable(tmp_path):
    path = tmp_path / "math.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows(1)))
    state = state_for(tmp_path, "v2", analyze_task_type="math", baseline_result_path=str(path))
    history = prepare(state, rows(3), metric="math_verify")
    assert history["public"]["comparisons"][0]["pass_rate_delta"] is None
    state["analyzer"]["baseline_metric"] = "math_verify"
    history = prepare(state, rows(3), metric="math_verify")
    assert history["public"]["comparisons"][0]["pass_rate_delta"] == .5
