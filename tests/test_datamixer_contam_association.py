import json

import pytest

from loopai.agents.Obtainer.datamixer import contam
from loopai.agents.Obtainer.datamixer import dataflow_agent
from loopai.agents.Obtainer.datamixer.dataflow_agent import build_dataflow_agent_prompt


def test_register_persists_benchmark_dataset_association(tmp_path):
    meta = contam.register(
        tmp_path,
        "humaneval",
        ["write a Python function"],
        benchmark_dataset={"id": "ds-benchmark", "name": "humaneval"},
    )

    stored = json.loads((tmp_path / "contam" / "humaneval.json").read_text())
    assert meta["benchmark_dataset"] == {
        "id": "ds-benchmark",
        "name": "humaneval",
    }
    assert stored["benchmark_dataset"] == meta["benchmark_dataset"]


def test_register_rejects_incomplete_benchmark_dataset_association(tmp_path):
    with pytest.raises(ValueError, match="requires non-empty id and name"):
        contam.register(
            tmp_path,
            "humaneval",
            ["write a Python function"],
            benchmark_dataset={"id": "ds-benchmark"},
        )


def test_dataflow_prompt_does_not_inject_expected_output_fields(tmp_path):
    prompt = build_dataflow_agent_prompt(
        target="Improve HumanEval performance",
        trial_jsonl=tmp_path / "trial.jsonl",
        work_dir=tmp_path,
        field="raw_content",
        expected_outputs="instruction,input,output",
    )

    assert "Expected outputs" not in prompt
    assert "instruction,input,output" not in prompt
    assert "DataFlow-Skills root" not in prompt
    assert ".cache_codex" not in prompt


def test_dataflow_home_installs_repository_owned_skill_and_agents(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setattr(dataflow_agent, "dataflow_codex_home", lambda: home)

    generated = dataflow_agent.ensure_dataflow_codex_home()

    installed_skill = generated / "skills" / "generating-dataflow-pipeline"
    assert not installed_skill.is_symlink()
    assert (installed_skill / "SKILL.md").read_bytes() == (
        dataflow_agent.dataflow_pipeline_skill_asset() / "SKILL.md"
    ).read_bytes()
    assert (generated / "AGENTS.md").read_text(encoding="utf-8") == (
        dataflow_agent.dataflow_agents_template()
    )
