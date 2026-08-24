import asyncio
import json
import pickle
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from api.app.utils.config.config import get_system_config
from loopai.common.db_tool.runtime import get_latest_task_runtime_sync
from loopai.common.tracking import assert_no_retired_tracking
from loopai.skills.Trainer.runner import inspect_prepared_trainer_config
from loopai.skills.Trainer.runtime_config import resolve_trainer_runtime_config
from loopai.skills.Trainer.utils.config_generator import ConfigGenerator
from loopai.skills.Trainer.utils.persistent_worker import load_worker_result
from loopai.skills.Trainer.utils.task_manager import TaskManager
from loopai.skills.Trainer.utils.verl_config_generator import generate_verl_grpo_config
from loopai.skills.Trainer.utils.verl_launcher import load_verl_launch_config


def test_generated_sft_yaml_cannot_enable_swanlab(tmp_path) -> None:
    dataset_path = tmp_path / "train.json"
    dataset_path.write_text("[]\n", encoding="utf-8")
    template_path = tmp_path / "legacy-swanlab-template.yaml"
    template_path.write_text(
        "\n".join(
            (
                "stage: sft",
                "report_to: swanlab",
                "use_swanlab: true",
                "swanlab_mode: cloud",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    generator = ConfigGenerator()
    config = generator.generate_config(
        task_description="Run a small SFT job.",
        dataset_path=str(dataset_path),
        model_name="/models/base",
        output_dir=str(tmp_path / "output"),
        template_path=str(template_path),
    )
    generated_path = tmp_path / "training.yaml"
    assert generator.save_config_as_yaml(config, str(generated_path)) is True

    generated = yaml.safe_load(generated_path.read_text(encoding="utf-8"))
    assert generated["report_to"] == "none"
    assert generated.get("use_swanlab") in (None, False)
    assert "swanlab_mode" not in generated
    assert all(
        str(key) == "use_swanlab" and value is False
        for key, value in generated.items()
        if "swanlab" in str(key).lower()
    )


def test_runtime_config_discards_legacy_swanlab_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SWANLAB_API_KEY", "secret-from-parent-env")
    state = {
        "task_id": "no-swanlab-runtime",
        "output_dir": str(tmp_path / "outputs"),
        "system": {"swanlab_api_key": "secret-from-system-state"},
        "trainer": {
            "train_framework": "llamafactory",
            "train_stage": "sft",
            "train_input_dataset_path": str(tmp_path / "train.json"),
            "train_input_model_name": "/models/base",
            "train_input_task_description": "Run SFT without external tracking.",
            "swanlab_api_key": "secret-from-trainer-state",
        },
    }

    runtime = resolve_trainer_runtime_config(
        state=state,
        swanlab_api_key="secret-from-call",
    )
    resolved_state = runtime["state"]

    assert "swanlab_api_key" not in resolved_state.get("trainer", {})
    assert "swanlab_api_key" not in resolved_state.get("system", {})


def test_task_manager_does_not_inherit_or_inject_swanlab_secret(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SWANLAB_API_KEY", "secret-from-parent-env")
    manager = TaskManager(
        configs_dir=str(tmp_path / "configs"),
        logs_dir=str(tmp_path / "logs"),
        runs_dir=str(tmp_path / "runs"),
        app_config={
            "CUDA_VISIBLE_DEVICES": "0",
            "swanlab_api_key": "secret-from-app-config",
        },
    )

    try:
        child_env = manager._get_safe_env()
    finally:
        manager.executor.shutdown(wait=False)

    assert "SWANLAB_API_KEY" not in child_env


def test_legacy_approved_yaml_is_rejected(tmp_path) -> None:
    config_path = tmp_path / "approved.yaml"
    config_path.write_text(
        "stage: sft\nreport_to: swanlab\nuse_swanlab: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="retired SwanLab settings"):
        inspect_prepared_trainer_config(str(config_path))


def test_config_api_filters_legacy_system_secret() -> None:
    record = SimpleNamespace(
        id=1,
        name="starter",
        config=json.dumps(
            {
                "system": {
                    "swanlab_api_key": "legacy-secret",
                    "CUDA_VISIBLE_DEVICES": "0",
                },
                "default_states": {"language": "en"},
            }
        ),
    )

    with patch(
        "api.app.utils.config.config.check_config_from_db",
        new=AsyncMock(return_value=record),
    ):
        result = asyncio.run(get_system_config("/tmp"))

    assert "swanlab_api_key" not in result["config"]
    assert result["config"]["CUDA_VISIBLE_DEVICES"]["value"] == "0"


def test_verl_generator_forces_local_loggers(tmp_path) -> None:
    template_path = tmp_path / "legacy-verl.yaml"
    template_path.write_text(
        yaml.safe_dump(
            {
                "framework": "verl",
                "stage": "grpo",
                "entrypoint": "verl.trainer.main_ppo",
                "environment": {},
                "overrides": {"trainer.logger": ["console", "swanlab"]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    state = {
        "trainer": {
            "trainer_output_dir": str(tmp_path / "run"),
            "trainer_version_id": "version-1",
            "train_input_dataset_path": str(tmp_path / "train.parquet"),
            "train_input_eval_dataset_path": str(tmp_path / "eval.parquet"),
            "train_input_model_name": "/models/base",
            "verl_dir": "/opt/verl",
            "verl_env_path": "verl",
            "CUDA_VISIBLE_DEVICES": "0",
        }
    }

    generated = generate_verl_grpo_config(state, str(template_path))

    assert generated["overrides"]["trainer.logger"] == ["console", "file"]
    assert_no_retired_tracking(generated)


def test_direct_verl_launcher_rejects_retired_logger(tmp_path) -> None:
    config_path = tmp_path / "legacy-verl.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "framework": "verl",
                "stage": "grpo",
                "entrypoint": "verl.trainer.main_ppo",
                "environment": {},
                "overrides": {"trainer.logger": ["console", "swanlab"]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="retired SwanLab settings"):
        load_verl_launch_config(str(config_path))


def test_legacy_verl_shell_script_cannot_start_retired_tracker(tmp_path) -> None:
    manager = TaskManager(
        configs_dir=str(tmp_path / "configs"),
        logs_dir=str(tmp_path / "logs"),
        runs_dir=str(tmp_path / "runs"),
        app_config={},
    )
    script_path = tmp_path / "legacy.sh"
    script_path.write_text("python -m swanlab\n", encoding="utf-8")

    try:
        with pytest.raises(ValueError, match="retired experiment tracker"):
            manager._run_verl_training(
                {"status": "running"},
                str(script_path),
                str(tmp_path / "train.log"),
                object(),
            )
    finally:
        manager.executor.shutdown(wait=False)


def test_legacy_worker_result_is_sanitized_on_load(tmp_path) -> None:
    result_path = tmp_path / "worker_result.pkl"
    with result_path.open("wb") as file:
        pickle.dump(
            {
                "ok": True,
                "state": {
                    "system": {"swanlab_api_key": "system-secret"},
                    "trainer": {"swanlab_api_key": "trainer-secret"},
                },
            },
            file,
        )

    payload = load_worker_result(result_path)

    assert "swanlab_api_key" not in payload["state"]["system"]
    assert "swanlab_api_key" not in payload["state"]["trainer"]
    with result_path.open("rb") as file:
        persisted_payload = pickle.load(file)
    assert "swanlab_api_key" not in persisted_payload["state"]["system"]
    assert "swanlab_api_key" not in persisted_payload["state"]["trainer"]


def test_legacy_task_runtime_state_is_purged_on_read(tmp_path) -> None:
    db_path = tmp_path / "runtime.db"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            create table taskruntime(
                id integer primary key,
                task_id text,
                node_name text,
                version text,
                state text,
                status text,
                createdAt text,
                updatedAt text
            )
            """
        )
        con.execute(
            """
            insert into taskruntime(task_id, node_name, version, state, status, createdAt, updatedAt)
            values(?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                "task-1",
                "trainer",
                "version-1",
                json.dumps({"trainer": {"swanlab_api_key": "legacy-secret"}}),
                "completed",
            ),
        )
        con.commit()
    finally:
        con.close()

    runtime = get_latest_task_runtime_sync(db_path, "task-1", "trainer")

    assert "swanlab_api_key" not in json.loads(runtime["state"])["trainer"]
    con = sqlite3.connect(db_path)
    try:
        persisted_state = con.execute("select state from taskruntime").fetchone()[0]
    finally:
        con.close()
    assert "swanlab_api_key" not in json.loads(persisted_state)["trainer"]
