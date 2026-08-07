from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from loopai.skills import Trainer
from loopai.skills.Trainer import cli


REPO_ROOT = Path(__file__).resolve().parents[1]


def _json_output(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_setup_registers_trainer_console_script() -> None:
    setup_text = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")

    assert "loopai-trainer=loopai.skills.Trainer.cli:main" in setup_text


def test_prepare_forwards_runtime_args_and_only_prints_public_result(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    captured = {}
    public_result = {
        "ok": True,
        "status": "completed",
        "message": "ready",
        "data": {
            "config_yaml": "model_name_or_path: /models/base\n",
            "config_sha256": "abc123",
            "trainer_version_id": "version-1",
        },
        "error": None,
    }

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {
            "system": {"starter_api_key": "must-not-leak"},
            "trainer": {"trainer_result": public_result},
        }

    monkeypatch.setattr(Trainer, "prepare", fake_prepare)
    monkeypatch.delenv("TASK_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("VERSION_ID", raising=False)
    monkeypatch.delenv("TRAINER_OUTPUT_DIR", raising=False)

    exit_code = cli.run(
        [
            "prepare",
            "--config",
            str(tmp_path / "starter.yaml"),
            "--thread-id",
            "thread-1",
            "--task-id",
            "task-1",
            "--db-path",
            str(tmp_path / "db.sqlite3"),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--version-id",
            "version-1",
            "--trainer-output-dir",
            str(tmp_path / "outputs" / "task-1" / "trainer" / "version-1"),
        ]
    )

    payload = _json_output(capsys)
    assert exit_code == 0
    assert payload == public_result
    assert "must-not-leak" not in json.dumps(payload)
    assert captured == {
        "thread_id": "thread-1",
        "config_path": str(tmp_path / "starter.yaml"),
        "task_id": "task-1",
        "db_path": str(tmp_path / "db.sqlite3"),
        "output_dir": str(tmp_path / "outputs"),
        "version_id": "version-1",
        "trainer_output_dir": str(
            tmp_path / "outputs" / "task-1" / "trainer" / "version-1"
        ),
    }


def test_run_prepared_requires_version_id(monkeypatch) -> None:
    monkeypatch.delenv("VERSION_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli.run(
            [
                "run-prepared",
                "--prepared-config",
                "/tmp/training.yaml",
                "--sha256",
                "abc123",
            ]
        )

    assert exc_info.value.code == 2


def test_run_prepared_forwards_approval_and_returns_failure(
    monkeypatch,
    capsys,
) -> None:
    captured = {}
    failure = {
        "ok": False,
        "status": "failed",
        "message": "training failed",
        "data": None,
        "error": {"detail": "synthetic failure"},
    }

    def fake_run_prepared(**kwargs):
        captured.update(kwargs)
        return {
            "system": {"api_key": "must-not-leak"},
            "trainer": {"trainer_result": failure},
        }

    monkeypatch.setattr(Trainer, "run_prepared", fake_run_prepared)
    monkeypatch.delenv("VERSION_ID", raising=False)

    exit_code = cli.run(
        [
            "run-prepared",
            "--prepared-config",
            "/tmp/training.yaml",
            "--sha256",
            "abc123",
            "--config",
            "/tmp/starter.yaml",
            "--task-id",
            "task-1",
            "--version-id",
            "version-1",
        ]
    )

    payload = _json_output(capsys)
    assert exit_code == 1
    assert payload == failure
    assert captured == {
        "prepared_config_path": "/tmp/training.yaml",
        "expected_config_sha256": "abc123",
        "thread_id": None,
        "config_path": "/tmp/starter.yaml",
        "task_id": "task-1",
        "version_id": "version-1",
    }


def test_inspect_emits_structured_json(tmp_path, monkeypatch, capsys) -> None:
    prepared_config = tmp_path / "training.yaml"
    inspected = {
        "config_path": str(prepared_config),
        "config_yaml": "model_name_or_path: /models/base\n",
        "config": {"model_name_or_path": "/models/base"},
        "config_sha256": "abc123",
    }
    monkeypatch.setattr(
        Trainer,
        "inspect_prepared_config",
        lambda path: inspected if path == str(prepared_config) else None,
    )

    exit_code = cli.run(
        ["inspect", "--prepared-config", str(prepared_config)]
    )

    payload = _json_output(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["data"] == inspected


def test_events_forwards_filters(monkeypatch, capsys) -> None:
    captured = {}

    def fake_load_events(**kwargs):
        captured.update(kwargs)
        return [{"status": "running", "version_id": "version-1"}]

    monkeypatch.setattr(Trainer, "load_events", fake_load_events)

    exit_code = cli.run(
        [
            "events",
            "--task-id",
            "task-1",
            "--output-dir",
            "/tmp/outputs",
            "--version-id",
            "version-1",
            "--trainer-output-dir",
            "/tmp/run",
        ]
    )

    payload = _json_output(capsys)
    assert exit_code == 0
    assert payload["data"][0]["status"] == "running"
    assert captured == {
        "task_id": "task-1",
        "output_dir": "/tmp/outputs",
        "version_id": "version-1",
        "trainer_output_dir": "/tmp/run",
    }


def test_status_reads_persisted_state_and_latest_metric(tmp_path, capsys) -> None:
    run_dir = tmp_path / "outputs" / "task-1" / "trainer" / "version-1"
    metrics_dir = run_dir / "metrics"
    metrics_dir.mkdir(parents=True)
    (run_dir / "run_state.json").write_text(
        json.dumps(
            {
                "status": "running",
                "task_id": "task-1",
                "version_id": "version-1",
                "current_step": 2,
            }
        ),
        encoding="utf-8",
    )
    (metrics_dir / "metrics.json").write_text(
        json.dumps(
            {
                "task_info": {"framework": "llamafactory"},
                "metrics": [
                    {"step": 1, "loss": 2.0},
                    {"step": 2, "loss": 1.0},
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = cli.run(
        [
            "status",
            "--task-id",
            "task-1",
            "--version-id",
            "version-1",
            "--output-dir",
            str(tmp_path / "outputs"),
        ]
    )

    payload = _json_output(capsys)
    assert exit_code == 0
    assert payload["data"]["run_state"]["status"] == "running"
    assert payload["data"]["latest_metric"] == {"step": 2, "loss": 1.0}
    assert payload["data"]["metrics_task_info"]["framework"] == "llamafactory"


def test_status_requires_an_unambiguous_run(capsys) -> None:
    exit_code = cli.run(["status"])

    payload = _json_output(capsys)
    assert exit_code == 1
    assert payload["error"]["type"] == "ValueError"
    assert "--task-id and --version-id" in payload["error"]["detail"]


def test_analyze_preserves_structured_failure(monkeypatch, capsys) -> None:
    failure = {
        "ok": False,
        "status": "failed",
        "message": "not found",
        "data": None,
        "error": {"code": "NOT_FOUND"},
    }
    monkeypatch.setattr(Trainer, "analyze_results", lambda **kwargs: failure)

    exit_code = cli.run(
        [
            "analyze",
            "--task-id",
            "task-1",
            "--training-output-dir",
            "/tmp/training",
        ]
    )

    assert exit_code == 1
    assert _json_output(capsys) == failure


def test_guide_redacts_secrets_without_redacting_tokenizer(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        Trainer,
        "prefill_guide",
        lambda **kwargs: {
            "task_type": kwargs["task_type"],
            "api_key": "secret-value",
            "tokenizer": "qwen-tokenizer",
        },
    )

    exit_code = cli.run(["guide", "--task-type", "grpo"])

    payload = _json_output(capsys)
    assert exit_code == 0
    assert payload["data"]["api_key"] == "***"
    assert payload["data"]["tokenizer"] == "qwen-tokenizer"


def test_unexpected_error_is_json_and_does_not_escape(
    monkeypatch,
    capsys,
) -> None:
    def fail(**kwargs):
        raise RuntimeError("synthetic CLI failure")

    monkeypatch.setattr(Trainer, "prepare", fail)

    exit_code = cli.run(["prepare"])

    payload = _json_output(capsys)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"]["type"] == "RuntimeError"
    assert payload["error"]["detail"] == "synthetic CLI failure"


def test_main_exits_with_run_status(monkeypatch) -> None:
    monkeypatch.setattr(cli, "run", lambda argv: 7)
    monkeypatch.setattr(sys, "argv", ["loopai-trainer", "guide"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 7
