import hashlib
import json
from unittest.mock import patch

import pytest

from loopai.skills.Trainer import (
    inspect_prepared_config,
    prepare,
    run_prepared,
)
from loopai.skills.Trainer.runtime_config import _DEFAULT_TEMPLATE_PATH
from loopai.skills.Trainer.trainer_agent import TrainerAgent


def test_prepare_uses_config_only_mode() -> None:
    with patch("loopai.skills.Trainer.runner.run_trainer_standalone", return_value={}) as run:
        prepare(thread_id="task-1")

    assert run.call_args.kwargs["prepare_only"] is True


def test_run_prepared_passes_approval_digest() -> None:
    with patch("loopai.skills.Trainer.runner.run_trainer_standalone", return_value={}) as run:
        run_prepared(
            "/tmp/training.yaml",
            "abc123",
            thread_id="task-1",
            version_id="version-1",
        )

    assert run.call_args.kwargs["prepared_config_path"] == "/tmp/training.yaml"
    assert run.call_args.kwargs["expected_config_sha256"] == "abc123"
    assert run.call_args.kwargs["version_id"] == "version-1"


def test_inspect_prepared_config_returns_exact_text_and_digest(tmp_path) -> None:
    config_path = tmp_path / "training.yaml"
    config_yaml = "model_name_or_path: /models/base\nnum_train_epochs: 3\n"
    config_path.write_text(config_yaml, encoding="utf-8")

    inspected = inspect_prepared_config(str(config_path))

    assert inspected["config_yaml"] == config_yaml
    assert inspected["config"]["num_train_epochs"] == 3
    assert inspected["config_sha256"] == hashlib.sha256(config_yaml.encode("utf-8")).hexdigest()


def test_prepare_stops_after_config_generation() -> None:
    state = {
        "trainer": {
            "trainer_config_generation_success": True,
            "_trainer_prepare_only": True,
        }
    }

    assert TrainerAgent.should_continue_after_config_generation(state) == "end"


def test_approved_config_skips_config_regeneration() -> None:
    state = {
        "trainer": {
            "trainer_data_check_passed": True,
            "_trainer_use_prepared_config": True,
        }
    }

    assert TrainerAgent.should_continue_after_data_check(state) == "training_execution"


def test_prepare_generates_yaml_without_starting_training(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TASK_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    dataset_path = tmp_path / "data.json"
    dataset_path.write_text(
        json.dumps([{"instruction": "hello", "input": "", "output": "world"}]),
        encoding="utf-8",
    )

    def fail_if_training_starts(*args, **kwargs):
        raise AssertionError("prepare() must not start training")

    monkeypatch.setattr(
        "loopai.skills.Trainer.trainer_agent.training_execution_node",
        fail_if_training_starts,
    )
    result = prepare(
        state={
            "task_id": "approval-task",
            "output_dir": str(tmp_path),
            "trainer": {
                "train_framework": "llamafactory",
                "llamafactory_dir": str(tmp_path),
                "CUDA_VISIBLE_DEVICES": "0",
                "train_input_dataset_path": str(dataset_path),
                "train_input_model_name": "/models/base",
                "train_input_task_description": "SFT a small assistant",
                "train_input_config_template_path": str(_DEFAULT_TEMPLATE_PATH),
                "train_input_use_swanlab": False,
            },
        },
        thread_id="approval-task",
    )

    approval = result["trainer"]["trainer_result"]["data"]
    assert approval["approval_required"] is True
    assert approval["trainer_version_id"]
    assert approval["config_yaml"]
    assert approval["config_sha256"] == hashlib.sha256(
        approval["config_yaml"].encode("utf-8")
    ).hexdigest()

    config_path = approval["config_path"]
    with open(config_path, "a", encoding="utf-8") as config_file:
        config_file.write("\n# changed after approval\n")

    with pytest.raises(ValueError, match="changed after approval"):
        run_prepared(
            prepared_config_path=config_path,
            expected_config_sha256=approval["config_sha256"],
            state=result,
            thread_id="approval-task",
            version_id=approval["trainer_version_id"],
        )


def test_run_prepared_uses_exact_yaml_without_regenerating(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TASK_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    dataset_path = tmp_path / "data.json"
    dataset_path.write_text(
        json.dumps([{"instruction": "hello", "input": "", "output": "world"}]),
        encoding="utf-8",
    )
    thread_id = f"approval-run-{tmp_path.name}"
    prepared = prepare(
        state={
            "task_id": thread_id,
            "output_dir": str(tmp_path),
            "trainer": {
                "train_framework": "llamafactory",
                "llamafactory_dir": str(tmp_path),
                "CUDA_VISIBLE_DEVICES": "0",
                "train_input_dataset_path": str(dataset_path),
                "train_input_model_name": "/models/base",
                "train_input_task_description": "SFT a small assistant",
                "train_input_config_template_path": str(_DEFAULT_TEMPLATE_PATH),
                "train_input_use_swanlab": False,
            },
        },
        thread_id=thread_id,
    )
    approval = prepared["trainer"]["trainer_result"]["data"]

    def fail_if_config_is_regenerated(*args, **kwargs):
        raise AssertionError("run_prepared() must not regenerate the approved YAML")

    def fake_training(state, writer=None):
        state["trainer"]["trainer_training_success"] = True
        state["trainer"]["trainer_training_final_status"] = {"status": "completed"}
        return state

    monkeypatch.setattr(
        "loopai.skills.Trainer.trainer_agent.config_generation_node",
        fail_if_config_is_regenerated,
    )
    monkeypatch.setattr(
        "loopai.skills.Trainer.trainer_agent.training_execution_node",
        fake_training,
    )
    result = run_prepared(
        prepared_config_path=approval["config_path"],
        expected_config_sha256=approval["config_sha256"],
        state=prepared,
        thread_id=thread_id,
        version_id=approval["trainer_version_id"],
    )

    assert result["trainer"]["trainer_training_success"] is True
    assert result["trainer"]["train_output_config_path"] == approval["config_path"]
