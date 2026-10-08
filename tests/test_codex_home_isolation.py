from __future__ import annotations

from pathlib import Path

from loopai.agents.Obtainer.datamixer import codex
from loopai.skills.ObtainerCLI import orchestrator_agent as orchestrator
from loopai.skills.ObtainerCLI import sft_export_agent as sft_export


def test_run_via_sdk_explicit_home_wins_over_inherited_home(tmp_path: Path, monkeypatch) -> None:
    interactive_home = tmp_path / "interactive"
    managed_home = tmp_path / "managed"
    monkeypatch.setenv("CODEX_HOME", str(interactive_home))
    captured: dict[str, object] = {}

    def fake_runner(env, prompt, timeout, on_stdout_payload=None):
        captured["env"] = env
        return 0, '{"type":"completed","result":{"finalResponse":"{}"}}\n', ""

    monkeypatch.setattr(codex, "_run_loopai_codex_runner", fake_runner)
    codex.run_via_sdk(
        "ping",
        {
            "base_url": "http://127.0.0.1:8855/responseProxy/v1",
            "api_key": "worker-key",
            "model": "worker-model",
        },
        cwd=str(tmp_path),
        timeout=1,
        codex_home_override=managed_home,
    )

    assert captured["env"]["CODEX_HOME"] == str(managed_home)
    assert (managed_home / "config.toml").is_file()
    assert not (interactive_home / "config.toml").exists()


def test_orchestrator_background_home_is_isolated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "interactive"))
    monkeypatch.setattr(orchestrator, "_workspace", lambda: tmp_path)
    run_dir = tmp_path / "run"
    prompt_path = run_dir / "worker_prompt.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("prompt", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakePopen:
        pid = 1234

        def __init__(self, cmd, cwd, env, stdout, stderr, start_new_session):
            captured["env"] = env

    monkeypatch.setattr(orchestrator.subprocess, "Popen", FakePopen)
    orchestrator._spawn_background(
        run_dir=run_dir,
        warehouse=str(tmp_path / "warehouse"),
        prompt_path=prompt_path,
        timeout=10,
        model="worker-model",
    )

    assert captured["env"]["CODEX_HOME"] == str(
        tmp_path / "outputs" / "obtainer" / ".codex" / "orchestrator" / "run"
    )


def test_sft_background_home_is_isolated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "interactive"))
    monkeypatch.setattr(sft_export, "_workspace", lambda: tmp_path)
    run_dir = tmp_path / "run"
    prompt_path = run_dir / "worker_prompt.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("prompt", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakePopen:
        pid = 5678

        def __init__(self, cmd, cwd, env, stdout, stderr, start_new_session):
            captured["env"] = env

    monkeypatch.setattr(sft_export.subprocess, "Popen", FakePopen)
    sft_export._spawn_background(
        run_dir=run_dir,
        warehouse=tmp_path / "warehouse",
        prompt_path=prompt_path,
        timeout=10,
        model="worker-model",
    )

    assert captured["env"]["CODEX_HOME"] == str(
        tmp_path / "outputs" / "obtainer" / ".codex" / "sft" / "run"
    )


def test_sft_foreground_home_is_isolated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "interactive"))
    monkeypatch.setattr(sft_export, "_workspace", lambda: tmp_path)
    run_dir = tmp_path / "run"
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return {"thread_id": "thread-1"}

    monkeypatch.setattr(sft_export.codex, "run_via_sdk", fake_run)
    monkeypatch.setattr(sft_export, "_acceptance_check", lambda _run_dir: {"ok": True, "issues": []})
    result = sft_export._run_worker(
        run_dir=run_dir,
        prompt="prompt",
        prov={"api_key": "worker-key", "base_url": "http://proxy/v1", "model": "worker"},
        provider_meta={},
        timeout=10,
    )

    assert result["ok"] is True
    assert captured["codex_home_override"] == str(
        tmp_path / "outputs" / "obtainer" / ".codex" / "sft" / "run"
    )
