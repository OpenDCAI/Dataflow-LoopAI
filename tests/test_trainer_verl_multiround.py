import json
import sqlite3
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from loopai.common.db_tool.task import update_task_state_section_config_sync
from loopai.skills.Trainer import prepare
from loopai.skills.Trainer.runner import (
    _hydrate_state_from_approved_config,
    _prepare_fresh_trainer_round,
)
from loopai.skills.Trainer.runtime_config import resolve_trainer_runtime_config
from loopai.skills.Trainer.utils.verl_config_generator import generate_verl_grpo_config
from loopai.skills.Trainer.utils.verl_dataset_builder import prepare_verl_grpo_datasets


_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "loopai"
    / "skills"
    / "Trainer"
    / "templates"
    / "verl_grpo_smoke.yaml"
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_native_verl_parquet(path: Path, rows: list[dict]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def _generated_state(tmp_path: Path, source: Path) -> dict:
    return {
        "task_id": "round-task",
        "output_dir": str(tmp_path / "outputs"),
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "trainer_output_dir": str(tmp_path / "run"),
            "verl_source_dataset_path": str(source),
            "train_input_task_description": "Optimize mathematical reasoning with GRPO.",
            "verl_reward_mode": "auto",
            "verl_reward_preset": "auto",
            "verl_data_adapter": "auto",
            "verl_validation_ratio": 0.34,
            "verl_split_seed": 7,
        },
    }


def test_configer_preserves_direct_and_wrapped_mapping_values(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            create table taskmodel (
                id integer primary key,
                task_id text not null,
                name text not null,
                config text,
                state text
            )
            """
        )
        connection.execute(
            "insert into taskmodel(task_id, name, config, state) values (?, ?, ?, ?)",
            ("task", "task", "{}", '{"constructor": {}}'),
        )
        connection.commit()

    first = {"output_file": "/tmp/round-1.jsonl", "mapped_records": 2}
    update_task_state_section_config_sync(
        db_path,
        "task",
        "constructor",
        {"mapping_results": first},
    )
    with sqlite3.connect(db_path) as connection:
        state = json.loads(connection.execute("select state from taskmodel").fetchone()[0])
    assert state["constructor"]["mapping_results"] == first

    second = {"output_file": "/tmp/round-2.jsonl", "mapped_records": 3}
    update_task_state_section_config_sync(
        db_path,
        "task",
        "constructor",
        {"mapping_results": {"value": second}},
    )
    with sqlite3.connect(db_path) as connection:
        state = json.loads(connection.execute("select state from taskmodel").fetchone()[0])
    assert state["constructor"]["mapping_results"] == second


def test_generated_alpaca_is_converted_to_versioned_verl_parquet(tmp_path: Path) -> None:
    source = tmp_path / "generated.jsonl"
    _write_jsonl(
        source,
        [
            {"instruction": "2+2?", "input": "", "output": "4"},
            {"instruction": "3+5?", "input": "", "output": "8"},
            {"instruction": "5+7?", "input": "", "output": "12"},
        ],
    )
    state = _generated_state(tmp_path, source)

    result = prepare_verl_grpo_datasets(state)

    trainer = state["trainer"]
    assert trainer["verl_reward_mode"] == "preset"
    assert trainer["verl_reward_preset"] == "math_boxed"
    assert Path(result["train_path"]).parent == tmp_path / "run" / "prepared_data"
    assert result["train_records"] == 2
    assert result["validation_records"] == 1
    assert result["rejected_records"] == 0
    assert Path(trainer["verl_data_manifest_path"]).is_file()

    train_rows = pq.read_table(result["train_path"]).to_pylist()
    assert train_rows[0]["data_source"] == "loopai/math_boxed"
    assert train_rows[0]["reward_model"]["ground_truth"]
    assert "\\boxed" in train_rows[0]["prompt"][-1]["content"]
    assert all(row["extra_info"]["split"] == "train" for row in train_rows)


def test_generated_data_does_not_guess_ambiguous_reward(tmp_path: Path) -> None:
    source = tmp_path / "classification.jsonl"
    _write_jsonl(
        source,
        [
            {"instruction": "Classify A", "output": "yes"},
            {"instruction": "Classify B", "output": "no"},
        ],
    )
    state = _generated_state(tmp_path, source)
    state["trainer"]["train_input_task_description"] = "Binary classification"

    with pytest.raises(ValueError, match="cannot safely infer a reward"):
        prepare_verl_grpo_datasets(state)


def test_prepare_surfaces_ambiguous_reward_error_without_masking_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("TASK_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    source = tmp_path / "classification.jsonl"
    _write_jsonl(
        source,
        [
            {"instruction": "Classify A", "output": "yes"},
            {"instruction": "Classify B", "output": "no"},
        ],
    )
    state = _generated_state(tmp_path, source)
    state["trainer"].update({
        "verl_dir": str(tmp_path),
        "train_input_model_name": "/models/base",
        "train_input_task_description": "Binary classification",
    })

    with pytest.raises(ValueError, match="cannot safely infer a reward"):
        prepare(state=state, thread_id=f"ambiguous-{tmp_path.name}")


def test_user_reward_preset_wins_for_generated_data(tmp_path: Path) -> None:
    source = tmp_path / "qa.jsonl"
    _write_jsonl(
        source,
        [
            {"question": "Capital of France?", "answer": "Paris"},
            {"question": "Capital of Japan?", "answer": "Tokyo"},
        ],
    )
    state = _generated_state(tmp_path, source)
    state["trainer"].update({
        "train_input_task_description": "general task",
        "verl_reward_mode": "preset",
        "verl_reward_preset": "qa_exact_match",
    })

    result = prepare_verl_grpo_datasets(state)
    rows = pq.read_table(result["train_path"]).to_pylist() + pq.read_table(
        result["validation_path"]
    ).to_pylist()

    assert state["trainer"]["verl_reward_preset"] == "qa_exact_match"
    assert rows[0]["reward_model"]["ground_truth"]["target"]
    assert "<answer>" in rows[0]["prompt"][-1]["content"]


def test_custom_reward_generated_data_gets_stable_default_data_source(tmp_path: Path) -> None:
    source = tmp_path / "custom.jsonl"
    _write_jsonl(
        source,
        [
            {"instruction": "Task A", "output": "reference A"},
            {"instruction": "Task B", "output": "reference B"},
        ],
    )
    state = _generated_state(tmp_path, source)
    state["trainer"].update({
        "train_input_task_description": "custom task",
        "verl_reward_mode": "custom",
        "verl_reward_function_path": str(tmp_path / "reward.py"),
    })

    result = prepare_verl_grpo_datasets(state)
    rows = pq.read_table(result["train_path"]).to_pylist() + pq.read_table(
        result["validation_path"]
    ).to_pylist()

    assert {row["data_source"] for row in rows} == {"loopai/custom"}


def test_next_round_inherits_hyperparameters_but_replaces_dynamic_fields(tmp_path: Path) -> None:
    previous = {
        "framework": "verl",
        "stage": "grpo",
        "entrypoint": "verl.trainer.main_ppo",
        "environment": {"verl_dir": "/old/verl", "cuda_visible_devices": "0"},
        "overrides": {
            "data.train_files": ["/old/train.parquet"],
            "data.val_files": ["/old/validation.parquet"],
            "actor_rollout_ref.model.path": "/old/model",
            "actor_rollout_ref.actor.optim.lr": 3.0e-7,
            "actor_rollout_ref.actor.ppo_mini_batch_size": 3,
            "actor_rollout_ref.rollout.n": 7,
            "trainer.experiment_name": "old-version",
            "trainer.default_local_dir": "/old/checkpoints",
            "trainer.save_freq": 4,
            "trainer.test_freq": 4,
        },
        "result": {"selection_metric": "old", "selection_mode": "min", "export_huggingface": True},
    }
    state = {
        "trainer": {
            "trainer_output_dir": str(tmp_path / "new-run"),
            "trainer_version_id": "new-version",
            "trainer_parent_version_id": "old-version",
            "trainer_round_index": 2,
            "_trainer_previous_config": previous,
            "verl_inherit_previous_config": True,
            "verl_multi_round_enabled": True,
            "train_input_dataset_path": str(tmp_path / "new-train.parquet"),
            "train_input_eval_dataset_path": str(tmp_path / "new-validation.parquet"),
            "train_input_model_name": "/new/model/",
            "verl_dir": "/new/verl",
            "verl_env_path": "verl",
            "CUDA_VISIBLE_DEVICES": "0,1",
            "verl_reward_mode": "preset",
            "verl_reward_preset": "math_boxed",
            "verl_reward_kwargs": {},
            "verl_rollout_backend": "vllm",
            "verl_model_backend": "fsdp",
            "verl_selection_metric": "val/reward",
            "verl_selection_mode": "max",
            "verl_max_actor_ckpt_to_keep": 10,
        }
    }

    config = generate_verl_grpo_config(state, str(_TEMPLATE))
    overrides = config["overrides"]

    assert overrides["actor_rollout_ref.actor.optim.lr"] == 3.0e-7
    assert overrides["actor_rollout_ref.actor.ppo_mini_batch_size"] == 3
    assert overrides["actor_rollout_ref.rollout.n"] == 7
    assert overrides["data.train_files"] == [str((tmp_path / "new-train.parquet").resolve())]
    assert overrides["actor_rollout_ref.model.path"] == "/new/model"
    assert overrides["trainer.experiment_name"] == "new-version"
    assert overrides["trainer.default_local_dir"] == str((tmp_path / "new-run" / "checkpoints").resolve())
    assert config["result"]["selection_metric"] == "val/reward"
    assert config["loopai_round"]["inherited_previous_config"] is True


def test_new_round_promotes_previous_exported_hf_model_and_clears_outputs(tmp_path: Path) -> None:
    model = tmp_path / "exported-model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights")
    previous_config = {
        "framework": "verl",
        "stage": "grpo",
        "overrides": {"actor_rollout_ref.actor.optim.lr": 1.0e-6},
    }
    state = {
        "trainer": {
            "train_framework": "verl",
            "trainer_version_id": "round-1",
            "trainer_round_index": 1,
            "trainer_training_success": True,
            "trainer_training_final_status": {"status": "completed"},
            "train_config": previous_config,
            "update_model_path": str(model),
            "train_input_model_name": "/models/base",
            "verl_inherit_previous_config": True,
            "verl_use_previous_best_model": True,
            "verl_reuse_previous_validation": True,
            "trainer_result": {"ok": True},
        }
    }

    _prepare_fresh_trainer_round(state, kwargs={})
    trainer = state["trainer"]

    assert trainer["trainer_parent_version_id"] == "round-1"
    assert trainer["trainer_round_index"] == 2
    assert trainer["train_input_model_name"] == str(model.resolve())
    assert trainer["trainer_model_inheritance"]["applied"] is True
    assert trainer["_trainer_previous_config"] == previous_config
    assert "train_config" not in trainer
    assert "update_model_path" not in trainer
    assert "trainer_result" not in trainer


def test_current_round_model_override_wins_over_previous_best(tmp_path: Path) -> None:
    exported_model = tmp_path / "exported-model"
    exported_model.mkdir()
    (exported_model / "config.json").write_text("{}", encoding="utf-8")
    (exported_model / "model.safetensors").write_bytes(b"weights")
    state = {
        "trainer": {
            "train_framework": "verl",
            "trainer_version_id": "round-1",
            "trainer_training_success": True,
            "train_config": {
                "framework": "verl",
                "stage": "grpo",
                "overrides": {"actor_rollout_ref.model.path": "/models/round-1-input"},
            },
            "update_model_path": str(exported_model),
            "train_input_model_name": "/models/user-override",
            "verl_use_previous_best_model": True,
        }
    }

    _prepare_fresh_trainer_round(
        state,
        kwargs={"train_input_model_name": "/models/user-override"},
    )

    assert state["trainer"]["train_input_model_name"] == "/models/user-override"
    assert state["trainer"]["trainer_model_inheritance"]["applied"] is False
    assert "explicit model override" in state["trainer"]["trainer_model_inheritance"]["reason"]


def test_runtime_prefers_current_constructor_output_over_persisted_user_seed(tmp_path: Path) -> None:
    current = tmp_path / "current.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    state = {
        "task_id": "task",
        "output_dir": str(tmp_path),
        "constructor": {"mapping_results": {"output_file": str(current)}},
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "verl_dir": str(tmp_path),
            "verl_source_dataset_path": "/old/generated.jsonl",
            "verl_source_dataset_origin": "user",
            "verl_data_prepare_result": {"source_path": "/old/generated.jsonl"},
            "train_input_dataset_path": "/old/prepared/train.parquet",
            "train_input_model_name": "/models/base",
            "train_input_task_description": "math",
        },
    }

    runtime = resolve_trainer_runtime_config(state=state)

    assert runtime["state"]["trainer"]["verl_source_dataset_path"] == str(current)
    assert runtime["state"]["trainer"]["train_input_dataset_path"] == str(current)
    assert runtime["state"]["trainer"]["_verl_source_dataset_explicit"] is False
    assert runtime["state"]["trainer"]["verl_source_dataset_origin"] == "constructor"


def test_runtime_recovers_legacy_serialized_constructor_mapping(tmp_path: Path) -> None:
    current = tmp_path / "current.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    state = {
        "task_id": "task",
        "output_dir": str(tmp_path),
        "constructor": {
            "mapping_results": str({"output_file": str(current), "mapped_records": 1})
        },
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "verl_dir": str(tmp_path),
            "verl_source_dataset_path": "/old/train.parquet",
            "verl_source_dataset_origin": "user",
            "train_input_dataset_path": "/old/train.parquet",
            "train_input_model_name": "/models/base",
            "train_input_task_description": "math",
        },
    }

    runtime = resolve_trainer_runtime_config(state=state)

    assert runtime["state"]["constructor"]["mapping_results"]["output_file"] == str(current)
    assert runtime["state"]["trainer"]["verl_source_dataset_path"] == str(current)
    assert runtime["state"]["trainer"]["verl_source_dataset_origin"] == "constructor"


def test_verl_round_source_replacement_does_not_clear_sft_runtime_input(tmp_path: Path) -> None:
    generated = tmp_path / "generated.jsonl"
    configured = tmp_path / "configured.jsonl"
    generated.write_text("{}\n", encoding="utf-8")
    configured.write_text("{}\n", encoding="utf-8")
    state = {
        "task_id": "sft-task",
        "output_dir": str(tmp_path),
        "constructor": {"mapping_results": {"output_file": str(generated)}},
        "trainer": {
            "train_framework": "llamafactory",
            "train_stage": "sft",
            "train_input_dataset_path": str(configured),
            "train_input_model_name": "/models/base",
            "train_input_task_description": "SFT",
            "llamafactory_dir": str(tmp_path),
        },
    }

    runtime = resolve_trainer_runtime_config(state=state)

    assert runtime["state"]["trainer"]["train_input_dataset_path"] == str(configured)
    assert runtime["state"]["trainer"]["_verl_source_dataset_replaced"] is False


def test_runtime_respects_user_source_override_even_when_constructor_has_output(tmp_path: Path) -> None:
    current = tmp_path / "current.jsonl"
    explicit = tmp_path / "explicit.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    explicit.write_text("{}\n", encoding="utf-8")
    state = {
        "task_id": "task",
        "output_dir": str(tmp_path),
        "constructor": {"mapping_results": {"output_file": str(current)}},
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "verl_dir": str(tmp_path),
            "train_input_model_name": "/models/base",
            "train_input_task_description": "math",
        },
    }

    runtime = resolve_trainer_runtime_config(
        state=state,
        verl_source_dataset_path=str(explicit),
    )

    trainer = runtime["state"]["trainer"]
    assert trainer["verl_source_dataset_path"] == str(explicit)
    assert trainer["_verl_source_dataset_explicit"] is True
    assert trainer["verl_source_dataset_origin"] == "user"


def test_task_scoped_runtime_loads_constructor_output_for_trainer_only_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    generated = tmp_path / "generated.jsonl"
    generated.write_text("{}\n", encoding="utf-8")

    def fake_section(section_name, task_id, db_path):
        assert task_id == "task-from-db"
        if section_name == "trainer":
            return {
                "train_framework": "verl",
                "train_stage": "grpo",
                "verl_dir": str(tmp_path),
                "train_input_model_name": "/models/base",
                "train_input_task_description": "math",
            }
        if section_name == "constructor":
            return {"mapping_results": {"output_file": str(generated)}}
        return {}

    monkeypatch.setattr(
        "loopai.skills.Trainer.runtime_config._load_task_section_config",
        fake_section,
    )

    runtime = resolve_trainer_runtime_config(
        state={"task_id": "task-from-db", "output_dir": str(tmp_path)},
        thread_id="task-from-db",
        db_path=str(tmp_path / "state.db"),
    )

    trainer = runtime["state"]["trainer"]
    assert runtime["task_state_loaded"] is True
    assert trainer["verl_source_dataset_path"] == str(generated)
    assert trainer["verl_source_dataset_origin"] == "constructor"


def test_approved_verl_yaml_hydrates_preflight_paths_and_reward() -> None:
    trainer = {
        "train_input_dataset_path": "/stale/train.parquet",
        "verl_reward_mode": "auto",
    }
    config = {
        "framework": "verl",
        "stage": "grpo",
        "environment": {
            "verl_dir": "/approved/verl",
            "verl_env_path": "verl",
            "cuda_visible_devices": "0,1",
        },
        "overrides": {
            "data.train_files": ["/approved/train.parquet"],
            "data.val_files": ["/approved/validation.parquet"],
            "actor_rollout_ref.model.path": "/approved/model",
            "actor_rollout_ref.rollout.name": "sglang",
            "+reward.custom_reward_function.reward_kwargs": {
                "preset": "math_boxed",
                "strict": True,
            },
        },
        "loopai_reward": {"mode": "preset", "preset": "math_boxed"},
        "result": {"selection_metric": "val/reward", "selection_mode": "max"},
    }

    _hydrate_state_from_approved_config(trainer, config)

    assert trainer["train_input_dataset_path"] == "/approved/train.parquet"
    assert trainer["train_input_eval_dataset_path"] == "/approved/validation.parquet"
    assert trainer["train_input_model_name"] == "/approved/model"
    assert trainer["verl_reward_mode"] == "preset"
    assert trainer["verl_reward_preset"] == "math_boxed"
    assert trainer["verl_reward_kwargs"] == {"strict": True}


def test_two_prepare_rounds_convert_new_data_and_inherit_model_and_yaml(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("TASK_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setattr(
        "loopai.skills.Trainer.utils.verl_data_checker._smoke_test_preset",
        lambda *args, **kwargs: {"status": "skipped", "reason": "unit test"},
    )
    first_source = tmp_path / "round-1.jsonl"
    second_source = tmp_path / "round-2.jsonl"
    _write_jsonl(
        first_source,
        [
            {"instruction": "1+1?", "output": "2"},
            {"instruction": "2+3?", "output": "5"},
        ],
    )
    _write_jsonl(
        second_source,
        [
            {"instruction": "3+4?", "output": "7"},
            {"instruction": "6+7?", "output": "13"},
        ],
    )
    verl_dir = tmp_path / "verl"
    verl_dir.mkdir()
    task_id = f"two-round-{tmp_path.name}"
    state = {
        "task_id": task_id,
        "output_dir": str(tmp_path / "outputs"),
        "constructor": {"mapping_results": {"output_file": str(first_source)}},
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "verl_dir": str(verl_dir),
            "verl_env_path": "verl",
            "train_input_model_name": "/models/base",
            "train_input_task_description": "Mathematics GRPO",
            "verl_reward_mode": "auto",
            "CUDA_VISIBLE_DEVICES": "0,1",
        },
    }

    first = prepare(state=state, thread_id=task_id)
    first_trainer = first["trainer"]
    first_approval = first_trainer["trainer_result"]["data"]
    first_validation = first_trainer["train_input_eval_dataset_path"]

    exported_model = tmp_path / "round-1-model"
    exported_model.mkdir()
    (exported_model / "config.json").write_text("{}", encoding="utf-8")
    (exported_model / "model.safetensors").write_bytes(b"weights")
    first_trainer["trainer_training_success"] = True
    first_trainer["trainer_training_final_status"] = {"status": "completed"}
    first_trainer["update_model_path"] = str(exported_model)
    first["constructor"] = {"mapping_results": {"output_file": str(second_source)}}

    second = prepare(state=first, thread_id=task_id)
    second_trainer = second["trainer"]
    second_approval = second_trainer["trainer_result"]["data"]
    second_config = second_approval["config"]

    assert second_approval["trainer_version_id"] != first_approval["trainer_version_id"]
    assert second_trainer["trainer_parent_version_id"] == first_approval["trainer_version_id"]
    assert second_trainer["trainer_round_index"] == 2
    assert second_trainer["train_input_model_name"] == str(exported_model.resolve())
    assert second_config["overrides"]["actor_rollout_ref.model.path"] == str(exported_model.resolve())
    assert second_config["loopai_round"]["inherited_previous_config"] is True
    assert second_config["loopai_round"]["parent_version_id"] == first_approval["trainer_version_id"]
    assert second_trainer["verl_source_dataset_path"] == str(second_source)
    assert Path(second_trainer["train_input_dataset_path"]).parent.parent.name == second_approval[
        "trainer_version_id"
    ]
    assert second_trainer["train_input_eval_dataset_path"] == first_validation


def test_second_round_replaces_user_seed_data_and_rematches_reward(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("TASK_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setattr(
        "loopai.skills.Trainer.utils.verl_data_checker._smoke_test_preset",
        lambda *args, **kwargs: {"status": "skipped", "reason": "unit test"},
    )
    old_train = tmp_path / "math-train.parquet"
    old_validation = tmp_path / "math-validation.parquet"
    _write_native_verl_parquet(
        old_train,
        [
            {
                "prompt": [{"role": "user", "content": "1+1?"}],
                "data_source": "DigitalLearningGmbH/MATH-lighteval",
                "reward_model": {"style": "rule", "ground_truth": "2"},
            },
            {
                "prompt": [{"role": "user", "content": "2+3?"}],
                "data_source": "DigitalLearningGmbH/MATH-lighteval",
                "reward_model": {"style": "rule", "ground_truth": "5"},
            },
        ],
    )
    _write_native_verl_parquet(
        old_validation,
        [
            {
                "prompt": [{"role": "user", "content": "3+4?"}],
                "data_source": "DigitalLearningGmbH/MATH-lighteval",
                "reward_model": {"style": "rule", "ground_truth": "7"},
            }
        ],
    )
    new_source = tmp_path / "searchr1-round-2.jsonl"
    _write_jsonl(
        new_source,
        [
            {"question": "Capital of France?", "answer": "Paris"},
            {"question": "Capital of Japan?", "answer": "Tokyo"},
            {"question": "Capital of China?", "answer": "Beijing"},
        ],
    )

    task_id = f"seed-to-generated-{tmp_path.name}"
    state = {
        "task_id": task_id,
        "output_dir": str(tmp_path / "outputs"),
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "verl_dir": str(tmp_path / "verl"),
            "verl_env_path": "verl",
            "verl_source_dataset_path": str(old_train),
            "verl_source_dataset_origin": "user",
            "train_input_dataset_path": str(old_train),
            "train_input_eval_dataset_path": str(old_validation),
            "train_input_model_name": "/models/base",
            "train_input_task_description": "Mathematics GRPO",
            "verl_reward_mode": "auto",
            "CUDA_VISIBLE_DEVICES": "0,1",
        },
    }
    (tmp_path / "verl").mkdir()

    first = prepare(state=state, thread_id=task_id)
    first_trainer = first["trainer"]
    first_config = first_trainer["trainer_result"]["data"]["config"]
    assert first_config["overrides"]["data.train_files"] == [str(old_train.resolve())]
    assert first_config["loopai_reward"]["mode"] == "auto"
    assert first_config["loopai_reward"]["origin"] == "auto"

    exported_model = tmp_path / "round-1-model"
    exported_model.mkdir()
    (exported_model / "config.json").write_text("{}", encoding="utf-8")
    (exported_model / "model.safetensors").write_bytes(b"weights")
    first_trainer["trainer_training_success"] = True
    first_trainer["trainer_training_final_status"] = {"status": "completed"}
    first_trainer["update_model_path"] = str(exported_model)
    first_trainer["train_input_task_description"] = "SearchR1 exact match QA"
    first["constructor"] = {"mapping_results": {"output_file": str(new_source)}}

    second = prepare(state=first, thread_id=task_id)
    trainer = second["trainer"]
    approval = trainer["trainer_result"]["data"]
    config = approval["config"]
    manifest = trainer["verl_data_prepare_result"]

    assert trainer["trainer_round_index"] == 2
    assert trainer["verl_source_dataset_path"] == str(new_source)
    assert trainer["verl_source_dataset_origin"] == "constructor"
    assert manifest["source_path"] == str(new_source.resolve())
    assert manifest["status"] == "prepared"
    assert manifest["reused_validation"] is False
    assert manifest["reward"]["mode"] == "preset"
    assert manifest["reward"]["origin"] == "auto"
    assert manifest["reward"]["preset"] == "qa_exact_match"
    assert config["loopai_reward"]["mode"] == "preset"
    assert config["loopai_reward"]["origin"] == "auto"
    assert config["loopai_reward"]["preset"] == "qa_exact_match"
    assert config["overrides"]["data.train_files"] == [trainer["train_input_dataset_path"]]
    assert str(old_train.resolve()) not in config["overrides"]["data.train_files"]
    assert str(old_validation.resolve()) not in config["overrides"]["data.val_files"]

    rows = pq.read_table(trainer["train_input_dataset_path"]).to_pylist()
    rows += pq.read_table(trainer["train_input_eval_dataset_path"]).to_pylist()
    assert len(rows) == 3
    assert {row["data_source"] for row in rows} == {"loopai/qa_exact_match"}
    assert all(row["reward_model"]["ground_truth"]["target"] for row in rows)
    assert all("<answer>" in row["prompt"][-1]["content"] for row in rows)
