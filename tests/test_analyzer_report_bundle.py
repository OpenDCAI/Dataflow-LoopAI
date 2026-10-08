import copy
import importlib
import json
from pathlib import Path

import pytest

from loopai.skills.Analyzer.math_rollout_report import build_rollout_evidence, assess_training_readiness
from loopai.skills.Analyzer.math_training_plan import build_training_plan
from loopai.skills.Analyzer.oj_report_evidence import adapt_oj_report_evidence
from loopai.skills.Analyzer.oj_report_bundle import publish_oj_report_bundles
from loopai.skills.Analyzer.report_bundle import REPORT_FILENAMES, safe_dataset_names, write_report_bundle


def samples(task_type="code", counts=(4, 3, 2, 1, 0), n=4, dataset="fixture"):
    return [{"bench_name": dataset, "task_id": str(problem), "sample_index": generation,
             "question": "Return the sorted input" if task_type == "code" else "Count orders for each customer",
             "topic": "数组排序" if task_type == "code" else "分组聚合",
             "completion": "return sorted(values)" if task_type == "code" else "SELECT customer, COUNT(*) FROM orders GROUP BY customer",
             "passed": generation < correct, "formatted": True, "truncated": False,
             "model": "fixture-model", "val_n": n, "judge": {
                 "stage": "wrong_answer" if task_type == "code" else "semantic", "tags": ["边界遗漏"],
                 "reason": "没有覆盖空输入。" if task_type == "code" else "分组字段错误导致聚合口径不符。"}}
            for problem, correct in enumerate(counts) for generation in range(n)]


def make_state(tmp_path, rows, task_type="code", quick=True):
    source = tmp_path / "judger_enriched.jsonl"
    source.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"results_file": str(source)}))
    return {"analyzer": {"analyze_task_type": task_type, "report_quick": quick,
                         "runtime_output_dir": str(tmp_path), "analyze_output_summary_path": str(summary)}}


def emit(*args, **kwargs):
    pass


@pytest.mark.parametrize("task_type", ["code", "text2sql"])
def test_flat_adapter_preserves_every_verdict_and_all_failures(task_type):
    rows = samples(task_type)
    original = copy.deepcopy(rows)
    context, normalized = adapt_oj_report_evidence(rows, "fixture", task_type, "/unused/input.json")
    assert rows == original
    evidence = build_rollout_evidence(context, normalized)
    assert evidence["stats"]["rollouts"] == 20
    assert evidence["stats"]["failed"] == 10
    assert evidence["stats"]["missing_critiques"] == 0
    assert [len(b["groups"]) for b in evidence["bands"].values()] == [1, 1, 1, 1, 1]
    assert sum(len(b["critiques"]) for b in evidence["bands"].values()) == 10
    assert context["warnings"] == []


@pytest.mark.parametrize("task_type", ["code", "text2sql"])
def test_output_contract_seven_texts_plus_json(tmp_path, task_type):
    state = make_state(tmp_path, samples(task_type), task_type)
    source = tmp_path / "judger_enriched.jsonl"
    before = source.read_bytes()
    publish_oj_report_bundles(state, emit=emit)
    cfg = state["analyzer"]
    manifest = cfg["report_artifacts"]["fixture"]
    directory = Path(manifest["directory"])
    assert set(p.name for p in directory.iterdir()) == set(REPORT_FILENAMES.values()) | {"09_oj_enriched.jsonl"}
    assert set(manifest["files"]) == set(REPORT_FILENAMES) | {"enriched_oj"}
    for path in directory.glob("*.txt"):
        raw = path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")
        assert "数学题库" not in raw.decode("utf-8-sig")
    plan = json.loads(Path(cfg["training_plan_path"]).read_text(encoding="utf-8"))
    assert plan["task_type"] == task_type
    assert plan["evaluation"]["rollouts"] == 20
    assert plan["evaluation"]["failed"] == 10
    assert all(type(plan[k]) is bool for k in ("sft_completed", "is_sft", "is_rl"))
    assert plan["domains"]
    assert all(d["domain_id"].startswith(task_type + "-") for d in plan["domains"])
    assert all(d["training_stage"] in {"sft", "rl"} for d in plan["domains"])
    assert all(d["is_sft"] != d["is_rl"] for d in plan["domains"])
    assert "08_training_plan.json" in Path(manifest["files"]["final_report"]).read_text(encoding="utf-8-sig")
    assert Path(cfg["analyze_output_summary_path"]).suffix == ".json"
    assert cfg["analyze_output_report_text_path"] == manifest["files"]["report"]
    assert source.read_bytes() == before


def test_single_sample_missing_metadata_does_not_claim_rl(tmp_path):
    rows = [{"task_id": "q", "passed": False, "result": "timed out", "syntax_error": False,
             "judge": {"reason": "执行超时。", "stage": "timeout"}}]
    context, normalized = adapt_oj_report_evidence(rows, "bench", "code", str(tmp_path / "input.json"))
    evidence = build_rollout_evidence(context, normalized)
    assert evidence["stats"]["format_rate"] is None
    assert evidence["stats"]["truncation_rate"] is None
    assert context["warnings"]
    topics = {context["groups"][0]["question_key"]: {
        "topic": context["groups"][0]["topic"], "source": "capability_evidence"}}
    evidence = build_rollout_evidence(context, normalized, topics)
    plan = build_training_plan(evidence, normalized, assess_training_readiness(evidence))
    assert plan["sft_completed"] is False
    assert plan["is_rl"] is False
    assert plan["domains"][0]["tag_type"] == "capability"
    assert plan["domains"][0]["question_tags"] == []


@pytest.mark.parametrize("rows", [[], [{"passed": "false"}], [{"passed": True, "correct": False}], samples() + [samples()[0]]])
def test_invalid_or_duplicate_judger_rows_are_rejected(rows):
    with pytest.raises(ValueError):
        adapt_oj_report_evidence(rows, "bench", "code", "/unused/input.json")


def test_declared_incomplete_rollouts_are_audited():
    rows = samples(counts=(3,))[:-1]
    context, normalized = adapt_oj_report_evidence(rows, "bench", "code", "/unused/input.json")
    assert any("不一致" in warning for warning in context["warnings"])
    assert len(normalized) == 3
    evidence = build_rollout_evidence(context, normalized)
    assert build_training_plan(evidence, normalized, assess_training_readiness(evidence))["sft_completed"] is False


def test_bench_and_run_identity_isolation_and_portable_names(tmp_path):
    rows = samples(counts=(3,), dataset="a/b") + samples(counts=(1,), dataset="a:b")
    state = make_state(tmp_path, rows)
    publish_oj_report_bundles(state, emit=emit)
    cfg = state["analyzer"]
    manifests = cfg["report_artifacts"]
    assert len({m["directory"] for m in manifests.values()}) == 2
    assert "training_plan_path" not in cfg
    totals = [json.loads(Path(m["files"]["training_plan"]).read_text())["evaluation"]["correct"] for m in manifests.values()]
    assert sorted(totals) == [1, 3]
    variants = samples(counts=(3,)) + [{**r, "model": "another-model"} for r in samples(counts=(2,))]
    context, normalized = adapt_oj_report_evidence(variants, "bench", "code", "/unused/input.json")
    assert context["num_groups"] == 2
    assert build_rollout_evidence(context, normalized)["stats"]["comparison_cohorts"] == 2
    names = safe_dataset_names(["CON", "con", "../a", "a/b", "a:b", "foo", "FOO"])
    assert len({s.casefold() for s in names.values()}) == len(names)
    assert all("/" not in s and ":" not in s and s.upper() != "CON" for s in names.values())


def test_all_correct_and_missing_diagnoses_still_get_fixed_reports(tmp_path):
    rows = samples(counts=(4, 0))
    for row in rows:
        row.pop("judge")
    state = make_state(tmp_path, rows)
    publish_oj_report_bundles(state, emit=emit)
    plan = json.loads(Path(state["analyzer"]["training_plan_path"]).read_text())
    assert plan["evaluation"]["missing_critiques"] == 4
    assert plan["sft_completed"] is False
    assert plan["is_rl"] is False
    text = Path(state["analyzer"]["rollout_report_path"]).read_text(encoding="utf-8-sig")
    assert "缺少完整错因/短评 4 次" in text


def test_model_stages_cached_on_resume_and_failures_not_published(tmp_path, monkeypatch):
    profiles = importlib.import_module("loopai.skills.Analyzer.nodes.analyze_metric_report_node")
    calls, profile_calls = [], []
    def profile(llm, critiques, **kwargs):
        profile_calls.append(len(critiques))
        return {"coverage": {"processed_short_critiques": len(critiques)}, "error_profile": [], "crawl_recommendations": []}
    monkeypatch.setattr(profiles, "_build_critique_profile", profile)
    state = make_state(tmp_path, samples(), quick=False)
    cfg = state["analyzer"]
    cfg["quick_brief"] = True  # Existing short-critique flag must not disable model reports.
    fail_review = True
    def invoke(model, prompt):
        calls.append(prompt)
        if "training_plan" in prompt and "domain_id" in prompt:
            if fail_review:
                raise TimeoutError("fixture timeout")
            payload = json.loads(prompt.split("。\n", 1)[-1])
            return json.dumps({"assessment": "SFT 转段条件未全部通过；按已计算规则准备数据。", "domains": [
                {"domain_id": d["domain_id"], "reason": "有对应失败证据。", "data_requirements": ["独立同类题与可验证实现。"]}
                for d in payload["training_plan"]["domains"]]})
        return "基于当前数据的中文分析正文。"
    with pytest.raises(TimeoutError):
        publish_oj_report_bundles(state, emit=emit, llm=object(), invoke=invoke)
    assert "report_artifacts" not in cfg
    assert len(profile_calls) == 4
    fail_review = False
    before = len(calls)
    publish_oj_report_bundles(state, emit=emit, llm=object(), invoke=invoke)
    assert len(calls) == before + 1
    assert len(profile_calls) == 4
    before = len(calls)
    publish_oj_report_bundles(state, emit=emit, llm=object(), invoke=invoke)
    assert len(calls) == before


def test_math_and_oj_share_filenames():
    from loopai.skills.Analyzer.nodes.analyze_metric_report_node import MATH_REPORT_FILENAMES, MATH_ROLLOUT_REPORT_FILENAMES
    assert {**MATH_REPORT_FILENAMES, **MATH_ROLLOUT_REPORT_FILENAMES} == REPORT_FILENAMES


def test_writer_rejects_incomplete_bundle(tmp_path):
    with pytest.raises(ValueError):
        write_report_bundle(tmp_path, {"summary": "not enough"}, {})


@pytest.mark.parametrize("task_type", ["code", "text2sql"])
def test_real_draw_node_publishes_bundle_and_resumes(tmp_path, monkeypatch, task_type):
    from loopai.skills.Analyzer.oj_report_bundle import _summary
    draw = importlib.import_module("loopai.skills.Analyzer.nodes.draw_conclusion")
    state = make_state(tmp_path, samples(task_type), task_type)
    cfg = state["analyzer"]
    summary = _summary(samples(task_type), "fixture", task_type, str(tmp_path / "judger_enriched.jsonl"))
    summary.update(run_ts="fixture", bench_summaries={"fixture": {"total_samples": 20}})
    Path(cfg["analyze_output_summary_path"]).write_text(json.dumps(summary))
    monkeypatch.setattr(draw, "get_safe_stream_writer", lambda: None)
    monkeypatch.setattr(draw, "checkpoint_analyzer_progress", lambda *a, **kw: None)
    monkeypatch.setattr(draw, "get_analyzer_resume_progress", lambda: 0)
    monkeypatch.setattr(draw, "init_model", lambda state: object())
    monkeypatch.setattr(draw, "_batch_one_with_heartbeat", lambda *a, **kw: "fixture dataset background")
    draw.draw_conclusion_node(state)
    assert Path(cfg["analyze_output_final_report_text_path"]).name == REPORT_FILENAMES["final_report"]
    assert Path(cfg["analyze_output_final_report_json_path"]).is_file()
    assert len(list(Path(cfg["report_dataset_dir"]).glob("*.txt"))) == 7
    initial = Path(cfg["training_plan_path"]).read_bytes()
    monkeypatch.setattr(draw, "get_analyzer_resume_progress", lambda: 0.98)
    monkeypatch.setattr(draw, "init_model", lambda state: pytest.fail("completed legacy stages must be reused"))
    draw.draw_conclusion_node(state)
    assert Path(cfg["training_plan_path"]).read_bytes() == initial


def test_cache_changes_with_input_and_model_and_progress_never_decreases(tmp_path, monkeypatch):
    profiles = importlib.import_module("loopai.skills.Analyzer.nodes.analyze_metric_report_node")
    monkeypatch.setattr(profiles, "_build_critique_profile", lambda *a, **kw: {"error_profile": [], "crawl_recommendations": []})
    state = make_state(tmp_path, samples(counts=(4,)), quick=False)
    calls, progresses = [], []
    def invoke(llm, prompt):
        calls.append(prompt)
        return json.dumps({"assessment": "仅作本次规则初筛。", "domains": []}) if "training_plan" in prompt else "中文分析。"
    def progress(message, *, progress=None, data=None):
        progresses.append(progress)
    publish_oj_report_bundles(state, emit=progress, llm=object(), invoke=invoke)
    assert progresses == sorted(progresses)
    count = len(calls)
    state["analyzer"]["analyze_model_path"] = "changed-model"
    publish_oj_report_bundles(state, emit=emit, llm=object(), invoke=invoke)
    assert len(calls) > count
    count = len(calls)
    source = tmp_path / "judger_enriched.jsonl"
    rows = samples(counts=(4, 4))
    source.write_text("\n".join(json.dumps(row) for row in rows))
    publish_oj_report_bundles(state, emit=emit, llm=object(), invoke=invoke)
    assert len(calls) > count
    assert json.loads(Path(state["analyzer"]["training_plan_path"]).read_text())["evaluation"]["rollouts"] == 8
