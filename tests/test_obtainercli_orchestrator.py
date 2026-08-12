from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from loopai.skills.ObtainerCLI.cli import run
from loopai.skills.ObtainerCLI.orchestrator_agent import (
    FINAL_REPORT_FILE,
    PHASE_FILE,
    STATUS_FILE,
    STATE_FILE,
)


def _last_json(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out
    return json.loads(out[-1])


def _dm(root: Path, args: list[str], capsys) -> dict:
    exit_code = run(["dm", "--root", str(root), *args])
    payload = _last_json(capsys)
    assert exit_code == 0, payload
    if payload["command"].startswith("dm.obtainer-orchestrator"):
        return payload
    return payload["result"]


def _set_starter_model_pool(tmp_path: Path, monkeypatch) -> Path:
    starter_config = tmp_path / "starter.yaml"
    starter_config.write_text(
        yaml.safe_dump(
            {
                "system": {
                    "api_port": 8855,
                    "codex_model_pool_name": "codex",
                    "model": {
                        "default_tier": "low",
                        "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
                        "pool": [
                            {
                                "tier": "medium",
                                "name": "codex",
                                "model_name": "deepseek-chat",
                                "base_url": "http://deepseek.example/v1",
                                "api_key": "deepseek-key",
                            },
                        ],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STARTER_CONFIG", str(starter_config))
    return starter_config


def test_obtainer_orchestrator_start_dry_run_writes_state_and_prompt(tmp_path: Path, capsys, monkeypatch) -> None:
    _set_starter_model_pool(tmp_path, monkeypatch)
    run_dir = tmp_path / "orch_run"

    result = _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "buggy and fixed Python code pairs",
            "--keywords",
            "python",
            "--target-datasets",
            "2",
            "--dry-run",
        ],
        capsys,
    )
    assert result["status"] == "dry_run"
    assert (run_dir / PHASE_FILE).exists()
    assert (run_dir / STATE_FILE).exists()
    assert (run_dir / STATUS_FILE).exists()
    assert (run_dir / "worker_prompt.md").exists()
    assert (run_dir / "policy.md").exists()

    thread = json.loads((run_dir / STATE_FILE).read_text(encoding="utf-8"))
    assert thread["objective"] == "buggy and fixed Python code pairs"
    assert thread["keywords"] == ["python"]
    assert thread["target_datasets"] == 2
    assert thread["mode"] == "start"

    policy = (run_dir / "policy.md").read_text(encoding="utf-8")
    assert "Obtainer Orchestrator" in policy

    prompt = (run_dir / "worker_prompt.md").read_text(encoding="utf-8")
    # The worker policy reuses the existing obtainer skill.
    assert "Main-Agent Use: Delegate to the Obtainer Orchestrator" in prompt
    assert "dataset-acquisition-agent" in prompt


def test_obtainer_orchestrator_phase_and_status_contract(tmp_path: Path, capsys, monkeypatch) -> None:
    _set_starter_model_pool(tmp_path, monkeypatch)
    run_dir = tmp_path / "orch_run"
    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "python code repair SFT",
            "--dry-run",
        ],
        capsys,
    )

    # phase transition
    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "phase",
            "--run",
            str(run_dir),
            "--state",
            "running",
            "--phase",
            "acquiring",
            "--message",
            "SearchAgent 进行中",
            "--next-action",
            "poll",
        ],
        capsys,
    )
    status = _dm(tmp_path, ["obtainer-orchestrator", "status", "--run", str(run_dir)], capsys)
    assert status["schema_version"] == 1
    assert status["state"] == "running"
    assert status["phase"] == "acquiring"
    assert status["phase_label"] == "数据集获取"
    assert status["progress"] == 0.1
    assert status["next_action"] == "poll"
    assert status["stale"] is False
    assert status["subtasks"] == []
    assert status["gates"] == []

    # subtask + gate + authoritative progress from the sub-agent status.json
    acq_run = tmp_path / "acq"
    acq_run.mkdir()
    (acq_run / STATUS_FILE).write_text(
        json.dumps({"state": "running", "download": {"processed": 2, "total": 10}}),
        encoding="utf-8",
    )
    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "phase",
            "--run",
            str(run_dir),
            "--subtask",
            "dataset-acquisition-agent",
            "--subtask-run-dir",
            str(acq_run),
            "--subtask-state",
            "running",
            "--subtask-progress",
            "0.2",
        ],
        capsys,
    )
    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "phase",
            "--run",
            str(run_dir),
            "--gate",
            "lake_volume",
            "--gate-ok",
            "false",
            "--gate-detail",
            "code=4000/10000",
        ],
        capsys,
    )
    status = _dm(tmp_path, ["obtainer-orchestrator", "status", "--run", str(run_dir)], capsys)
    assert status["subtasks"][0]["name"] == "dataset-acquisition-agent"
    assert status["subtasks"][0]["run_dir"] == str(acq_run)
    # enriched from the real download counters (2/10)
    assert status["subtasks"][0]["progress"] == 0.2
    assert status["gates"][0] == {"name": "lake_volume", "ok": False, "detail": "code=4000/10000"}
    # acquiring base 0.10 + 0.30 * 0.2
    assert status["progress"] == 0.16

    # terminal state from a successful final_report.json
    (run_dir / FINAL_REPORT_FILE).write_text(
        json.dumps({"ok": True, "summary": "done"}), encoding="utf-8"
    )
    status = _dm(tmp_path, ["obtainer-orchestrator", "status", "--run", str(run_dir)], capsys)
    assert status["state"] == "completed"
    assert status["phase"] == "completed"
    assert status["progress"] == 1.0
    assert status["next_action"] == "report"
    assert status["final_report"].endswith("final_report.json")


def _run_worker_with_mock(
    tmp_path: Path,
    monkeypatch,
    side_effects,
    *,
    running_subtask: bool = False,
) -> tuple[dict, list]:
    import loopai.skills.ObtainerCLI.orchestrator_agent as orch

    run_dir = tmp_path / "orch_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    if running_subtask:
        orch._write_phase(
            run_dir,
            state="running",
            phase="acquiring",
            message="acquisition running",
            subtask={
                "name": "acquisition",
                "run_dir": str(tmp_path / "acq"),
                "state": "running",
                "progress": 0.5,
            },
            emit=False,
        )

    calls = []
    def fake_run(prompt, prov, cwd, timeout, thread_id=None):
        calls.append(prompt)
        effect = side_effects[min(len(calls) - 1, len(side_effects) - 1)]
        if effect.get("write_report"):
            (run_dir / "final_report.json").write_text(
                json.dumps({"ok": True, "summary": "done"}), encoding="utf-8"
            )
        if effect.get("thread_id"):
            return {**effect.get("result", {}), "thread_id": effect["thread_id"]}
        return effect.get("result", {})

    monkeypatch.setattr(orch.codex, "run_via_sdk", fake_run)
    result = orch._run_worker(
        run_dir=run_dir,
        prompt="initial prompt",
        prov={"api_key": "k", "base_url": "http://x/v1", "model": "m"},
        provider_meta={},
        timeout=60,
    )
    return result, calls


def test_obtainer_orchestrator_progress_uses_download_progress_json(tmp_path: Path, capsys, monkeypatch) -> None:
    _set_starter_model_pool(tmp_path, monkeypatch)
    run_dir = tmp_path / "orch_run"
    _dm(tmp_path, ["obtainer-orchestrator", "start", "--run", str(run_dir), "--objective", "x", "--dry-run"], capsys)
    acq = tmp_path / "acq"
    (acq / "downloads").mkdir(parents=True, exist_ok=True)
    (acq / "downloads" / "download_progress.json").write_text(
        json.dumps({"completed": 4, "total": 9, "state": "running",
                    "updated_at": "2026-08-12T13:22:33.126251+00:00"}),
        encoding="utf-8",
    )
    _dm(tmp_path, ["obtainer-orchestrator", "phase", "--run", str(run_dir), "--state", "running", "--phase", "acquiring",
                   "--subtask", "acquisition", "--subtask-run-dir", str(acq), "--subtask-state", "running"], capsys)
    status = _dm(tmp_path, ["obtainer-orchestrator", "status", "--run", str(run_dir)], capsys)
    subtask = status["subtasks"][0]
    assert subtask["name"] == "acquisition"
    # real counters: 4/9 downloads (status.json has no download fields)
    assert abs(subtask["progress"] - round(4 / 9, 3)) < 1e-6
    assert "4/9" in subtask["message"]
    # overall = acquiring base 0.10 + 0.30 * (4/9)
    assert abs(status["progress"] - round(0.10 + 0.30 * 4 / 9, 3)) < 1e-6


def test_obtainer_orchestrator_acceptance_loop_retries_narrative_then_completes(tmp_path: Path, monkeypatch) -> None:
    # 1st turn: narrative without JSON -> retried; 2nd turn: ok=true + report -> completed
    result, calls = _run_worker_with_mock(
        tmp_path,
        monkeypatch,
        [
            {"result": {"summary": "Phase updated. Let me continue polling..."}},
            {"result": {"ok": True, "summary": "done"}, "write_report": True, "thread_id": "th-1"},
        ],
    )
    assert len(calls) == 2
    assert "Acceptance issues" in calls[1]
    assert result["status"] == "completed"
    assert result["ok"] is True
    assert (tmp_path / "orch_run" / "final_report.json").exists()


def test_obtainer_orchestrator_acceptance_loop_interrupts_when_subagent_running(tmp_path: Path, monkeypatch) -> None:
    result, calls = _run_worker_with_mock(
        tmp_path,
        monkeypatch,
        [{"result": {"ok": True, "summary": "done"}, "write_report": True}],
        running_subtask=True,
    )
    assert result["status"] == "interrupted"
    assert result["ok"] is False
    assert result["attempt"] == 4  # initial + MAX_ACCEPTANCE_REVISIONS retries
    assert len(calls) == 4


def test_obtainer_orchestrator_acceptance_loop_interrupts_when_report_missing(tmp_path: Path, monkeypatch) -> None:
    result, calls = _run_worker_with_mock(
        tmp_path,
        monkeypatch,
        [{"result": {"ok": True, "summary": "done"}}],
    )
    assert result["status"] == "interrupted"
    assert result["ok"] is False
    assert result["attempt"] == 4


def test_obtainer_orchestrator_acceptance_loop_accepts_blocker_json(tmp_path: Path, monkeypatch) -> None:
    result, calls = _run_worker_with_mock(
        tmp_path,
        monkeypatch,
        [{"result": {"ok": False, "summary": "blocked by download failure", "blockers": ["x"]}}],
    )
    assert len(calls) == 1
    assert result["status"] == "completed_with_errors"
    assert result["ok"] is False
    assert "blocked by download failure" in str(result.get("result", {}).get("summary"))


def test_obtainer_orchestrator_progress_aggregates_aliased_subtask_name(tmp_path: Path, capsys, monkeypatch) -> None:
    _set_starter_model_pool(tmp_path, monkeypatch)
    run_dir = tmp_path / "orch_run"
    _dm(tmp_path, ["obtainer-orchestrator", "start", "--run", str(run_dir), "--objective", "x", "--dry-run"], capsys)
    acq = tmp_path / "acq"
    acq.mkdir()
    (acq / STATUS_FILE).write_text(json.dumps({"state": "running", "download": {"processed": 5, "total": 10}}), encoding="utf-8")
    # worker reports the subtask under the short alias "acquisition"
    _dm(tmp_path, ["obtainer-orchestrator", "phase", "--run", str(run_dir), "--state", "running", "--phase", "acquiring",
                   "--subtask", "acquisition", "--subtask-run-dir", str(acq), "--subtask-state", "running"], capsys)
    status = _dm(tmp_path, ["obtainer-orchestrator", "status", "--run", str(run_dir)], capsys)
    # acquiring base 0.10 + 0.30 * (download 5/10) = 0.25
    assert status["subtasks"][0]["name"] == "acquisition"
    assert status["subtasks"][0]["progress"] == 0.5
    assert status["progress"] == 0.25


def test_obtainer_orchestrator_stale_detection_and_stop(tmp_path: Path, capsys, monkeypatch) -> None:
    _set_starter_model_pool(tmp_path, monkeypatch)
    run_dir = tmp_path / "orch_run"
    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "web QA pairs",
            "--dry-run",
        ],
        capsys,
    )
    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "phase",
            "--run",
            str(run_dir),
            "--state",
            "running",
            "--phase",
            "bootstrap",
            "--message",
            "loading lake",
        ],
        capsys,
    )
    # age the phase.json so the poller sees a stale heartbeat
    phase = json.loads((run_dir / PHASE_FILE).read_text(encoding="utf-8"))
    phase["updated_at"] = 1.0
    (run_dir / PHASE_FILE).write_text(json.dumps(phase), encoding="utf-8")
    status = _dm(tmp_path, ["obtainer-orchestrator", "status", "--run", str(run_dir)], capsys)
    assert status["stale"] is True

    # stop marks the run stopped/blocked and never kills an unrelated pid
    (run_dir / STATUS_FILE).write_text(
        json.dumps({"state": "running", "pid": 999999999, "updated_at": 2.0}),
        encoding="utf-8",
    )
    stopped = _dm(tmp_path, ["obtainer-orchestrator", "stop", "--run", str(run_dir)], capsys)
    assert stopped["killed"] is False
    status = _dm(tmp_path, ["obtainer-orchestrator", "status", "--run", str(run_dir)], capsys)
    assert status["state"] == "stopped"
    assert status["next_action"] == "blocked"


def test_obtainer_orchestrator_phase_emits_obtainercli_events(tmp_path: Path, capsys, monkeypatch) -> None:
    _set_starter_model_pool(tmp_path, monkeypatch)
    run_dir = tmp_path / "orch_run"
    event_dir = tmp_path / "events"
    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "math QA pairs",
            "--event-output-dir",
            str(event_dir),
            "--dry-run",
        ],
        capsys,
    )
    # Bind a task_id so the event writer has a context to write under.
    thread = json.loads((run_dir / STATE_FILE).read_text(encoding="utf-8"))
    thread["task_id"] = "task-orch-1"
    (run_dir / STATE_FILE).write_text(json.dumps(thread), encoding="utf-8")

    _dm(
        tmp_path,
        [
            "obtainer-orchestrator",
            "phase",
            "--run",
            str(run_dir),
            "--state",
            "running",
            "--phase",
            "acquiring",
            "--message",
            "dispatched acquisition",
            "--subtask",
            "dataset-acquisition-agent",
            "--subtask-state",
            "running",
        ],
        capsys,
    )
    event_root = event_dir / "task-orch-1" / "obtainercli"
    assert event_root.exists()
    pkls = list(event_root.rglob("*.pkl"))
    assert pkls, "expected at least one obtainercli event pkl"
