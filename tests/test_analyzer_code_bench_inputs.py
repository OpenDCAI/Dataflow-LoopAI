import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopai.skills.Analyzer.bench_inputs import resolve_eval_result_sources
from loopai.skills.Analyzer.bucket_strategy import classify_failure_bucket
from loopai.skills.Analyzer.code_bench_inputs import load_bench_records, preprocessing_diagnosis
from loopai.skills.Analyzer.oj_annotations import export_bench_oj, read_oj_rows
from loopai.skills.Analyzer.oj_report_bundle import publish_oj_report_bundles
from loopai.skills.Analyzer.report_history import history_snapshot, compare_snapshots


def write_rows(path, records):
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")


def make_bench(root, name="future-python-bench", pass_source="plus", count=3):
    root.mkdir(parents=True, exist_ok=True)
    rows = [{"task_id": f"{name}/{index}", "completion_id": 0,
             "solution": f"def f{index}():\n    return {index}",
             "base_status": "pass" if index < 2 else "fail",
             "plus_status": "pass" if index == 0 else "fail",
             "base_fail_tests": [] if index < 2 else [[2]],
             "plus_fail_tests": [] if index == 0 else [[index]]} for index in range(count)]
    path = root / f"{name}_result.jsonl"
    write_rows(path, rows)
    write_rows(root / f"{name}_sample-sanitized.jsonl", [{"task_id": r["task_id"], "solution": r["solution"]} for r in reversed(rows)])
    write_rows(root / f"{name}_sanitized.jsonl", [{"task_id": r["task_id"], "solution": r["solution"], "extract_ok": True} for r in rows])
    write_rows(root / f"{name}_sample.jsonl", [{"task_id": r["task_id"], "completion": "Here is code:\n```python\n" + r["solution"] + "\n```"} for r in rows])
    passed = 1 if pass_source == "plus" else min(count, 2)
    summary = {"task": name, "pass_source": pass_source, "dataset_hash": "fixture-hash", "image": "evalplus:fixture",
               "samples": count, "problems": count, "base_pass_samples": min(count, 2), "plus_pass_samples": 1,
               "failed_task_count": count - passed, "pass@1": passed / count,
               "pass_at_k": {"base": {"pass@1": min(count, 2) / count}, "plus": {"pass@1": 1 / count}}}
    (root / f"{name}_summary.json").write_text(json.dumps(summary))
    (root / f"{name}_sample-sanitized_eval_results.json").write_text(json.dumps({"hash": "fixture-hash", "eval": {
        r["task_id"]: [{k: v for k, v in r.items() if k != "completion_id"}] for r in reversed(rows)}}))
    return path, rows


def sources(path):
    return resolve_eval_result_sources({"analyzer": {"eval_result_path": str(path)}}, "code")


def load(path):
    source = sources(path)[0]
    return source, load_bench_records(source, "code")


@pytest.mark.parametrize("selection,expected", [("plus", [True, False, False]), ("base", [True, True, False])])
def test_generic_bench_uses_selected_suite_and_executed_solution(tmp_path, selection, expected):
    path, originals = make_bench(tmp_path, pass_source=selection)
    source, rows = load(path)
    assert [r["passed"] for r in rows] == expected
    assert source["bench_name"] == "future-python-bench" + ("+" if selection == "plus" else "")
    assert rows[1]["_code_bench"]["plus_only_failure"]
    assert "```" not in rows[1]["completion"]
    assert "```" in rows[1]["_code_bench"]["raw_completion"]
    assert source["evaluation"]["base_pass_samples"] == 2
    assert source["evaluation"]["plus_pass_samples"] == 1
    assert read_oj_rows(path) == originals


def test_directory_parent_and_summary_resolve_only_flattened_results(tmp_path):
    first, _ = make_bench(tmp_path / "a", name="A")
    second, _ = make_bench(tmp_path / "b", name="B")
    assert {s["path"] for s in sources(tmp_path)} == {str(first), str(second)}
    assert sources(first.parent)[0]["path"] == str(first)
    assert sources(first.with_name("A_summary.json"))[0]["path"] == str(first)
    assert len(resolve_eval_result_sources({"analyzer": {"eval_result_path": [str(first), str(first.parent)]}}, "code")) == 1
    mapped = resolve_eval_result_sources({"judger": {"bench_result": {"Custom A": {
        "result_path": str(first), "task_type": "code"}}}}, "code")
    load_bench_records(mapped[0], "code")
    assert mapped[0]["bench_name"] == "Custom A+"


@pytest.mark.parametrize("suffix", ["_sample.jsonl", "_sanitized.jsonl", "_sample-sanitized.jsonl", "_sample-sanitized_eval_results.json"])
def test_intermediate_files_are_not_mistaken_for_judger_results(tmp_path, suffix):
    with pytest.raises(ValueError, match="result.jsonl"):
        sources(tmp_path / ("bench" + suffix))


@pytest.mark.parametrize("mutation", ["status", "missing_plus", "duplicate", "hash", "raw", "counts", "solution", "score"])
def test_invalid_or_inconsistent_artifacts_fail_loudly(tmp_path, mutation):
    path, rows = make_bench(tmp_path)
    summary_path = tmp_path / "future-python-bench_summary.json"
    if mutation in {"status", "missing_plus", "duplicate"}:
        (tmp_path / "future-python-bench_sample-sanitized_eval_results.json").unlink()
        if mutation == "status":
            rows[0]["plus_status"] = "pending"
        elif mutation == "missing_plus":
            rows[0].pop("plus_status")
        else:
            rows.append(copy.deepcopy(rows[0]))
        write_rows(path, rows)
    elif mutation in {"hash", "counts", "score"}:
        summary = json.loads(summary_path.read_text())
        summary[{"hash": "dataset_hash", "counts": "samples", "score": "pass@1"}[mutation]] = {
            "hash": "other", "counts": 100, "score": .99}[mutation]
        summary_path.write_text(json.dumps(summary))
    elif mutation == "raw":
        raw_path = tmp_path / "future-python-bench_sample-sanitized_eval_results.json"
        raw = json.loads(raw_path.read_text())
        raw["eval"][rows[0]["task_id"]][0]["solution"] = "different"
        raw_path.write_text(json.dumps(raw))
    else:
        write_rows(tmp_path / "future-python-bench_sample-sanitized.jsonl", [{"task_id": r["task_id"], "solution": "different"} for r in rows])
    with pytest.raises(ValueError):
        load(path)


def test_plus_requires_both_suites_and_base_only_accepts_absent_plus(tmp_path):
    path = tmp_path / "custom_result.jsonl"
    write_rows(path, [{"task_id": "q", "solution": "pass", "base_status": "fail", "plus_status": "pass"}])
    assert load(path)[1][0]["passed"] is False
    write_rows(path, [{"task_id": "q", "solution": "pass", "base_status": "pass", "plus_status": None}])
    (tmp_path / "custom_summary.json").write_text(json.dumps({"pass_source": "base"}))
    assert load(path)[1][0]["passed"] is True


def test_ambiguous_multisample_sidecars_are_not_joined_by_row_order(tmp_path):
    path = tmp_path / "custom_result.jsonl"
    rows = [{"task_id": "q", "completion_id": index, "solution": str(index), "base_status": "fail", "plus_status": "fail"} for index in range(2)]
    write_rows(path, rows)
    write_rows(tmp_path / "custom_sample.jsonl", [{"task_id": "q", "completion": "A"}, {"task_id": "q", "completion": "B"}])
    source, normalized = load(path)
    assert all("raw_completion" not in r["_code_bench"] for r in normalized)
    assert any("配对不明确" in w for w in source["evaluation"]["warnings"])


@pytest.mark.parametrize("explicit_id", [False, True])
def test_partial_sidecar_cannot_be_reused_for_every_rollout(tmp_path, explicit_id):
    path = tmp_path / "custom_result.jsonl"
    write_rows(path, [{"task_id": "q", "completion_id": index, "solution": "pass",
                      "base_status": "fail", "plus_status": "fail"} for index in range(2)])
    extra = {"completion_id": 0} if explicit_id else {}
    write_rows(tmp_path / "custom_sample.jsonl", [{"task_id": "q", "completion": "one answer", **extra}])
    write_rows(tmp_path / "custom_sanitized.jsonl", [{"task_id": "q", "solution": "def lost(): pass", **extra}])
    _, normalized = load(path)
    assert normalized[0]["_code_bench"]["preprocessing_issue"] == explicit_id
    assert not normalized[1]["_code_bench"]["preprocessing_issue"]
    assert "raw_completion" not in normalized[1]["_code_bench"]


def test_preprocessing_differences_are_audited_and_not_model_training_demand(tmp_path):
    path, original = make_bench(tmp_path)
    extracted = [{"task_id": r["task_id"], "solution": r["solution"] + "\ndef lost():\n    return 1"} for r in original]
    write_rows(tmp_path / "future-python-bench_sanitized.jsonl", extracted)
    source, normalized = load(path)
    assert source["evaluation"]["preprocessing_issue_samples"] == 3
    failed = normalized[1]
    failed["judge"] = preprocessing_diagnosis(failed)
    assert failed["judge"]["overall_error_tag"] == "评测预处理差异"
    assert classify_failure_bucket(failed, "code")["bucket"] == "diagnostic_unknown"
    from loopai.skills.Analyzer.oj_report_evidence import adapt_oj_report_evidence
    from loopai.skills.Analyzer.math_rollout_report import build_rollout_evidence, assess_training_readiness
    from loopai.skills.Analyzer.math_training_plan import build_training_plan
    for row in normalized:
        row["topic"] = "fixture topic"
        if not row["passed"]:
            row["judge"] = preprocessing_diagnosis(row)
    context, evidence_rows = adapt_oj_report_evidence(normalized, "future-python-bench+", "code", str(tmp_path / "input.json"))
    evidence = build_rollout_evidence(context, evidence_rows)
    plan = build_training_plan(evidence, evidence_rows, assess_training_readiness(evidence))
    assert plan["domains"] == []
    assert sum("清洗/送测" in row["reason"] for row in plan["excluded_questions"]) == 2


def test_export_preserves_new_original_schema_without_internal_fields(tmp_path):
    path, original = make_bench(tmp_path)
    source, normalized = load(path)
    for row in normalized:
        if not row["passed"]:
            row["judge"] = {"stage": "assert", "short_critique": "失败输入暴露结果不一致，需核查代码。"}
    cfg = {"analyze_task_type": "code", "eval_result_sources": [source]}
    output = export_bench_oj(cfg, source["bench_name"], normalized, tmp_path / "reports", str(path))
    exported = read_oj_rows(output)
    for row in exported[1:]:
        assert row.pop("short_critique")
        assert row.pop("overall_error_tag")
    assert exported == original
    assert read_oj_rows(path) == original
    assert all("passed" not in row and "_code_bench" not in row for row in exported)


def test_history_rejects_changes_to_test_hash_or_suite(tmp_path):
    path, _ = make_bench(tmp_path)
    _, normalized = load(path)
    before = history_snapshot(normalized, dataset="b", task_type="code", metric="judger_passed")
    before["version_id"] = "v1"
    for changed in ("dataset_hash", "pass_source"):
        rows = copy.deepcopy(normalized)
        for row in rows:
            row["_code_bench"][changed] = "different"
        after = history_snapshot(rows, dataset="b", task_type="code", metric="judger_passed")
        comparison = compare_snapshots(after, before)
        assert comparison["status"] == "insufficient_evidence"
        assert comparison["pass_rate_delta"] is None
    for row in normalized:
        row["_code_bench"]["dataset_hash"] = None
    unknown = history_snapshot(normalized, dataset="b", task_type="code", metric="judger_passed")
    comparison = compare_snapshots(unknown, {**unknown, "version_id": "unknown-v1"})
    assert comparison["status"] == "insufficient_evidence"
    assert comparison["pass_rate_delta"] is None


def test_eval_and_nine_file_report_pipeline_with_two_new_benches(tmp_path, monkeypatch):
    eval_node = importlib.import_module("loopai.skills.Analyzer.nodes.eval_model")
    root = tmp_path / "input"
    make_bench(root / "a", name="A")
    make_bench(root / "b", name="B")
    state = {"task_id": "new-code", "version_id": "v1", "output_dir": str(tmp_path), "analyzer": {
        "analyze_task_type": "code", "eval_result_path": str(root), "report_quick": True,
        "runtime_output_dir": str(tmp_path / "new-code" / "analyzer" / "v1"), "output_dir": str(tmp_path)}}
    prompts = []
    monkeypatch.setattr(eval_node, "get_safe_stream_writer", lambda: None)
    monkeypatch.setattr(eval_node, "get_analyzer_resume_progress", lambda: 0)
    monkeypatch.setattr(eval_node, "init_model", lambda s: object())
    def call(model, batch, **kwargs):
        prompts.extend(batch)
        return [SimpleNamespace(content=json.dumps({"stage": "other", "reason": "需结合题干进一步复核。",
                "evidence": {}, "short_critique": "送测代码未通过测试，缺少题干和期望值，需补充证据。"})) for _ in batch]
    monkeypatch.setattr(eval_node, "call_llm_with_control", call)
    eval_node.eval_model_node(state)
    assert len(prompts) == 4
    assert all("evaluated_solution" in p and "plus_fail_inputs" in p for p in prompts)
    publish_oj_report_bundles(state, emit=lambda *a, **kw: None)
    artifacts = state["analyzer"]["report_artifacts"]
    assert set(artifacts) == {"A+", "B+"}
    for artifact in artifacts.values():
        assert len(artifact["files"]) == 9
        overview = Path(artifact["files"]["summary"]).read_text(encoding="utf-8-sig")
        assert "基础测试通过：2" in overview
        assert "基础通过但增强失败：1" in overview
        plan = json.loads(Path(artifact["files"]["training_plan"]).read_text())
        assert plan["evaluation"]["correct"] == 1
        assert plan["evaluation_protocol"]["pass_sources"] == ["plus"]
    checkpoint = eval_node._batch_checkpoint_path(state)
    assert eval_node._load_batch_checkpoint(checkpoint, expected_count=4, input_fingerprint="changed") is None


def test_legacy_boolean_input_and_false_string_rejection(tmp_path):
    path = tmp_path / "old.jsonl"
    write_rows(path, [{"task_id": "old", "passed": True, "completion": "return 1"}])
    source, normalized = load(path)
    assert normalized[0]["passed"] is True
    assert "schema" not in source
    write_rows(path, [{"passed": "false"}])
    with pytest.raises(ValueError, match="boolean"):
        load(path)


def test_pass_at_one_is_question_weighted_when_rollout_counts_differ(tmp_path):
    path = tmp_path / "custom_result.jsonl"
    rows = [{"task_id": task, "completion_id": index, "solution": "pass", "base_status": "pass",
             "plus_status": "pass" if task == "a" else "fail"} for task, n in (("a", 1), ("b", 3)) for index in range(n)]
    write_rows(path, rows)
    (tmp_path / "custom_summary.json").write_text(json.dumps({"pass_source": "plus", "pass@1": .5}))
    source, normalized = load(path)
    assert sum(row["passed"] for row in normalized) / len(normalized) == .25
    assert source["evaluation"]["judger_pass_at_1"] == .5


def test_intermediate_summary_does_not_fabricate_pass_at_ten_or_missing_metrics(tmp_path):
    eval_node = importlib.import_module("loopai.skills.Analyzer.nodes.eval_model")
    path, _ = make_bench(tmp_path / "input")
    source, rows = load(path)
    summary_json, _ = eval_node._build_and_write_summary(rows, tmp_path / "reports", "fixture", "code", [source])
    summary = json.loads(Path(summary_json).read_text())
    assert summary["pass_at_k_task"] == {"1": 1 / 3}
    assert summary["loc_distribution"] == {}
    assert summary["kw_distribution"] == {}
