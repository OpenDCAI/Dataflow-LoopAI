from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopai.agents.Obtainer.datamixer import skill_library as lib


def _payload(
    name: str = "math-sft-quality-filter",
    function_key: str = "score and filter math sft rows",
    ops: tuple[str, ...] = ("quality_score", "minhash_dedup"),
) -> dict:
    return {
        "name": name,
        "description": "Score and filter math SFT rows with LLM quality checks and dedup",
        "body": (
            "Inspect the sample fields. Use PromptedEvaluator to score each row, "
            "then minhash_dedup with k=5. Keep sample_id as the join key."
        ),
        "files": [
            {
                "path": "references/pipeline_example.py",
                "content": "print('pipeline example')\n",
            }
        ],
        "manifest": {
            "display_name": "Math SFT Quality Filter",
            "short_description": "LLM quality scoring and dedup for math SFT",
            "default_prompt": "Use $math-sft-quality-filter to process math SFT rows.",
        },
        "function_key": function_key,
        "operator_chain": list(ops),
        "summary": "test payload",
    }


def _agent_result(pipeline: str = "/runs/df/pipeline.py") -> dict:
    return {
        "ok": True,
        "mode": "trial_run",
        "pipeline_path": pipeline,
        "operator_decision": {"ops": [{"name": "quality_score"}], "field_flow": [], "reason": "t"},
        "trial_rows_in": 20,
        "trial_rows_out": 15,
        "summary": "delivered trial pipeline",
    }


def _runner(payload: dict):
    def fake(prompt, prov, cwd, timeout, thread_id=None, network=True, on_event=None):
        assert "$" in prompt
        assert "skill-creator" in prompt.lower() or "SKILL.md" in prompt
        return {"ok": True, "skill": payload}

    return fake


@pytest.fixture(autouse=True)
def _isolated_library(tmp_path: Path, monkeypatch):
    home = tmp_path / "dataflow" / "skills"
    monkeypatch.setenv("DATAFLOW_AGENT_SKILLS_HOME", str(home))
    return home


def _internalize(payload: dict, run_dir: Path, agent_result: dict | None = None):
    return lib.internalize_run_artifacts(
        run_dir=run_dir,
        target="score GSM8K answer-focused SFT rows",
        dataset="math_sft",
        where=None,
        field="content",
        expected_outputs="math_answer_quality",
        agent_result=agent_result or _agent_result(),
        prov={"api_key": "k", "base_url": "http://x/v1", "model": "m"},
        cwd=str(run_dir.parent),
        timeout=60,
        runner=_runner(payload),
    )


def test_add_skill_writes_skill_and_registry(_isolated_library: Path, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    report = _internalize(_payload(), run_dir)
    assert report["ok"] is True
    assert report["action"] == "added"
    assert report["skill"] == "math-sft-quality-filter"

    skill_dir = _isolated_library / "math-sft-quality-filter"
    skill_md = skill_dir / "SKILL.md"
    assert skill_md.is_file()
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---\nname: math-sft-quality-filter")
    assert "description: Score and filter math SFT rows" in text
    assert "Inspect the sample fields." in text
    assert (skill_dir / "references" / "pipeline_example.py").is_file()
    assert (skill_dir / "agents" / "openai.yaml").is_file()
    assert (run_dir / "skill_internalize.json").is_file()

    registry = lib.load_library()
    assert len(registry["skills"]) == 1
    assert registry["skills"][0]["name"] == "math-sft-quality-filter"
    assert registry["skills"][0]["dedupe_count"] == 0


def test_functional_duplicate_keeps_earlier_and_resets_lifecycle(
    _isolated_library: Path, tmp_path: Path, monkeypatch
) -> None:
    now = iter(range(1, 10_000))
    monkeypatch.setattr(lib, "_now", lambda: float(next(now)))

    run_a = tmp_path / "run_a"
    first = _internalize(_payload(name="math-sft-filter-a"), run_a)
    assert first["action"] == "added"

    # same function_key, different name -> keep the earlier skill, reset lifecycle
    run_b = tmp_path / "run_b"
    second = _internalize(
        _payload(name="math-sft-filter-b", function_key="score and filter math sft rows"),
        run_b,
    )
    assert second["action"] == "duplicate_kept_existing"
    assert second["lifecycle_reset"] is True
    assert second["skill"] == "math-sft-filter-a"

    assert (_isolated_library / "math-sft-filter-a").is_dir()
    assert not (_isolated_library / "math-sft-filter-b").exists()
    registry = lib.load_library()
    assert len(registry["skills"]) == 1
    entry = registry["skills"][0]
    assert entry["name"] == "math-sft-filter-a"
    assert entry["dedupe_count"] == 1
    assert len(entry["superseded_runs"]) == 1
    assert entry["last_used_at"] == 2.0  # lifecycle reset bumps last_used_at


def test_max_20_skills_evicts_least_recently_used(
    _isolated_library: Path, tmp_path: Path, monkeypatch
) -> None:
    now = iter(range(1, 10_000))
    monkeypatch.setattr(lib, "_now", lambda: float(next(now)))

    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    for i in range(21):
        report = _internalize(
            _payload(
                name=f"skill-{i:02d}",
                function_key=f"function number {i}",
                ops=(f"op-{i}",),
            ),
            run_dir / f"run-{i:02d}",
        )
        assert report["action"] == "added", report

    registry = lib.load_library()
    assert len(registry["skills"]) == 20
    names = {e["name"] for e in registry["skills"]}
    assert "skill-00" not in names  # oldest inserted, least recently used -> evicted
    assert "skill-20" in names
    assert not (_isolated_library / "skill-00").exists()
    assert (_isolated_library / "skill-20").exists()


def test_internalize_skips_without_delivered_pipeline(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    report = lib.internalize_run_artifacts(
        run_dir=run_dir,
        target="t",
        dataset=None,
        where=None,
        field="content",
        expected_outputs=None,
        agent_result={"ok": True, "pipeline_path": None},
        prov={"api_key": "k", "base_url": "http://x/v1", "model": "m"},
        cwd=str(tmp_path),
        timeout=60,
        runner=lambda **_: (_ for _ in ()).throw(AssertionError("runner must not run")),
    )
    assert report["ok"] is False
    assert report["skipped"] is True


def test_internalizer_failure_is_reported_not_raised(
    _isolated_library: Path, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    report = lib.internalize_run_artifacts(
        run_dir=run_dir,
        target="t",
        dataset=None,
        where=None,
        field="content",
        expected_outputs=None,
        agent_result=_agent_result(),
        prov={"api_key": "k", "base_url": "http://x/v1", "model": "m"},
        cwd=str(tmp_path),
        timeout=60,
        runner=lambda prompt, prov, cwd, timeout, thread_id=None, network=True, on_event=None: {
            "ok": False,
            "error": "model unavailable",
        },
    )
    assert report["ok"] is False
    assert report["error"] == "model unavailable"
    assert not (_isolated_library / "math-sft-quality-filter").exists()


def test_validate_skill_payload_rejects_invalid() -> None:
    good = _payload()
    assert lib.validate_skill_payload(good) == []

    bad_name = dict(good, name="Bad_Name")
    assert any("hyphen-case" in e for e in lib.validate_skill_payload(bad_name))

    bad_desc = dict(good, description="uses <angle> brackets")
    assert any("angle brackets" in e for e in lib.validate_skill_payload(bad_desc))

    bad_desc_newline = dict(good, description="line one\nline two")
    assert any("single line" in e for e in lib.validate_skill_payload(bad_desc_newline))

    bad_file = dict(good, files=[{"path": "../evil.py", "content": "x"}])
    assert any("unsafe" in e for e in lib.validate_skill_payload(bad_file))

    no_key = dict(good)
    del no_key["function_key"]
    assert any("function_key" in e for e in lib.validate_skill_payload(no_key))
