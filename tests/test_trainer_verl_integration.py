import json
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from loopai.skills.Trainer import prepare
from loopai.skills.Trainer.results import analyze_results, select_best_checkpoint
from loopai.skills.Trainer.rewards import compute_score, normalize_reward_preset
from loopai.skills.Trainer.runtime_config import (
    _DEFAULT_VERL_GRPO_TEMPLATE_PATH,
    resolve_trainer_runtime_config,
)
from loopai.skills.Trainer.utils.realtime_log_parser import MetricsExtractor
from loopai.skills.Trainer.utils.task_manager import TaskManager
from loopai.skills.Trainer.utils.task_status import TaskStatus
from loopai.skills.Trainer.utils.verl_config_generator import generate_verl_grpo_config
from loopai.skills.Trainer.utils.verl_data_checker import check_verl_grpo_inputs
from loopai.skills.Trainer.utils.verl_exporter import export_verl_checkpoint
from loopai.skills.Trainer.utils.verl_launcher import build_verl_launch


_VERL_GRPO_SMOKE_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "loopai"
    / "skills"
    / "Trainer"
    / "templates"
    / "verl_grpo_smoke.yaml"
)


def _write_rl_parquet(path: Path) -> None:
    table = pa.table({
        "prompt": [[{"role": "user", "content": "2+2?"}]],
        "data_source": ["unit_test"],
        "reward_model": [{"ground_truth": "4"}],
        "extra_info": [{"index": 0}],
    })
    pq.write_table(table, path)


def _verl_state(tmp_path: Path) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    train_path = tmp_path / "train.parquet"
    validation_path = tmp_path / "validation.parquet"
    _write_rl_parquet(train_path)
    _write_rl_parquet(validation_path)
    reward_path = tmp_path / "reward.py"
    reward_path.write_text("def compute_score(data_source, solution_str, ground_truth, extra_info=None):\n    return 1.0\n")
    verl_dir = tmp_path / "verl-repo"
    (verl_dir / "verl" / "trainer").mkdir(parents=True)
    return {
        "task_id": "verl-task",
        "output_dir": str(tmp_path / "outputs"),
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "verl_dir": str(verl_dir),
            "train_input_dataset_path": str(train_path),
            "train_input_eval_dataset_path": str(validation_path),
            "train_input_model_name": "/models/base/",
            "train_input_task_description": "Optimize unit-test reward with GRPO.",
            "verl_reward_function_path": str(reward_path),
            "CUDA_VISIBLE_DEVICES": "0,1",
        },
    }


def test_runtime_routes_sft_and_grpo_to_only_supported_backends(tmp_path: Path) -> None:
    runtime = resolve_trainer_runtime_config(state=_verl_state(tmp_path))
    trainer = runtime["state"]["trainer"]
    assert runtime["missing_fields"] == []
    assert trainer["train_input_config_template_path"] == str(_DEFAULT_VERL_GRPO_TEMPLATE_PATH)
    assert trainer["verl_env_path"] == "verl"

    bad_state = _verl_state(tmp_path / "bad")
    bad_state["trainer"]["train_framework"] = "llamafactory"
    with pytest.raises(ValueError, match="backend/stage combination"):
        resolve_trainer_runtime_config(state=bad_state)


def test_verl_data_and_reward_preflight(tmp_path: Path) -> None:
    state = _verl_state(tmp_path)
    trainer = state["trainer"]
    result = check_verl_grpo_inputs(
        trainer["train_input_dataset_path"],
        trainer["train_input_eval_dataset_path"],
        trainer["verl_reward_function_path"],
        "compute_score",
    )
    assert result["is_valid"] is True
    assert result["train"]["rows"] == 1
    assert {"prompt", "data_source", "reward_model"}.issubset(result["train"]["columns"])


def test_named_reward_preset_accepts_custom_data_source_and_auto_rejects_it(tmp_path: Path) -> None:
    state = _verl_state(tmp_path)
    trainer = state["trainer"]

    preset_result = check_verl_grpo_inputs(
        trainer["train_input_dataset_path"],
        trainer["train_input_eval_dataset_path"],
        reward_mode="preset",
        reward_preset="math_boxed",
    )
    assert preset_result["is_valid"] is True
    assert preset_result["reward"]["preset"] == "math_boxed"
    assert preset_result["train"]["semantic_sample_size"] == 1

    auto_result = check_verl_grpo_inputs(
        trainer["train_input_dataset_path"],
        trainer["train_input_eval_dataset_path"],
        reward_mode="auto",
    )
    assert auto_result["is_valid"] is False
    assert "unit_test" in auto_result["errors"][0]


def test_verl_data_preflight_rejects_missing_ground_truth(tmp_path: Path) -> None:
    train_path = tmp_path / "train.parquet"
    validation_path = tmp_path / "validation.parquet"
    invalid = pa.table({
        "prompt": [[{"role": "user", "content": "2+2?"}]],
        "data_source": ["unit_test"],
        "reward_model": [{"ground_truth": None}],
    })
    pq.write_table(invalid, train_path)
    pq.write_table(invalid, validation_path)

    result = check_verl_grpo_inputs(
        str(train_path),
        str(validation_path),
        reward_mode="preset",
        reward_preset="math_boxed",
    )
    assert result["is_valid"] is False
    assert any("reward_model.ground_truth" in error for error in result["errors"])


def test_reward_router_uses_verl_implementation_without_copying_it() -> None:
    reward_score = ModuleType("verl.utils.reward_score")
    reward_score.math_reward = SimpleNamespace(
        compute_score=lambda solution_str, ground_truth: solution_str == ground_truth
    )
    verl_module = ModuleType("verl")
    utils_module = ModuleType("verl.utils")

    with patch.dict(
        sys.modules,
        {
            "verl": verl_module,
            "verl.utils": utils_module,
            "verl.utils.reward_score": reward_score,
        },
    ):
        assert compute_score("any", "4", "4", preset="math") == 1.0

    assert normalize_reward_preset("qa-em") == "qa_exact_match"


def test_generated_yaml_uses_loopai_reward_preset_and_kwargs(tmp_path: Path) -> None:
    state = _verl_state(tmp_path)
    trainer = state["trainer"]
    trainer.pop("verl_reward_function_path")
    trainer["verl_reward_mode"] = "preset"
    trainer["verl_reward_preset"] = "gsm8k_exact"
    trainer["verl_reward_kwargs"] = {"method": "flexible"}

    runtime = resolve_trainer_runtime_config(state=state)
    config = generate_verl_grpo_config(
        runtime["state"],
        str(_DEFAULT_VERL_GRPO_TEMPLATE_PATH),
    )
    overrides = config["overrides"]

    assert config["loopai_reward"]["mode"] == "preset"
    assert config["loopai_reward"]["preset"] == "gsm8k_exact"
    assert overrides["reward.custom_reward_function.path"].endswith(
        "loopai/skills/Trainer/rewards/router.py"
    )
    assert overrides["reward.custom_reward_function.name"] == "compute_score"
    assert overrides["+reward.custom_reward_function.reward_kwargs"] == {
        "method": "flexible",
        "preset": "gsm8k_exact",
    }

    config_path = tmp_path / "preset-launch.yaml"
    config_path.write_text(
        __import__("yaml").safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    with patch("loopai.skills.Trainer.utils.verl_launcher.shutil.which", return_value="/conda"):
        command, _, _ = build_verl_launch(str(config_path))
    assert (
        '+reward.custom_reward_function.reward_kwargs={method:"flexible",preset:"gsm8k_exact"}'
        in command
    )


def test_prepare_generates_approvable_verl_yaml_without_training(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TASK_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    result = prepare(state=_verl_state(tmp_path), thread_id="verl-task")
    approval = result["trainer"]["trainer_result"]["data"]
    config = approval["config"]

    assert approval["approval_required"] is True
    assert config["framework"] == "verl"
    assert config["stage"] == "grpo"
    assert config["environment"]["verl_env_path"] == "verl"
    assert config["overrides"]["algorithm.adv_estimator"] == "grpo"
    assert config["overrides"]["actor_rollout_ref.model.path"] == "/models/base"
    assert config["overrides"]["actor_rollout_ref.actor.use_dynamic_bsz"] is True
    assert config["overrides"]["actor_rollout_ref.rollout.log_prob_use_dynamic_bsz"] is True
    assert config["overrides"]["trainer.n_gpus_per_node"] == 2
    assert config["overrides"]["trainer.max_actor_ckpt_to_keep"] == 10


def test_smoke_template_limits_work_and_supports_eight_gpus(tmp_path: Path) -> None:
    state = _verl_state(tmp_path)
    state["trainer"]["CUDA_VISIBLE_DEVICES"] = "0,1,2,3,4,5,6,7"
    state["trainer"]["verl_max_actor_ckpt_to_keep"] = 1
    config = generate_verl_grpo_config(state, str(_VERL_GRPO_SMOKE_TEMPLATE_PATH))
    overrides = config["overrides"]

    assert overrides["data.train_batch_size"] == 8
    assert overrides["data.train_max_samples"] == 80
    assert overrides["data.val_max_samples"] == 8
    assert overrides["actor_rollout_ref.rollout.n"] == 2
    assert overrides["actor_rollout_ref.actor.use_torch_compile"] is False
    assert overrides["trainer.total_training_steps"] == 10
    assert overrides["trainer.test_freq"] == 1
    assert overrides["trainer.n_gpus_per_node"] == 8
    assert overrides["trainer.max_actor_ckpt_to_keep"] == 1
    assert config["result"]["export_huggingface"] is False


def test_production_template_saves_at_validation_cadence() -> None:
    config = yaml.safe_load(Path(_DEFAULT_VERL_GRPO_TEMPLATE_PATH).read_text(encoding="utf-8"))
    overrides = config["overrides"]
    assert overrides["trainer.save_freq"] > 0
    assert overrides["trainer.save_freq"] == overrides["trainer.test_freq"]
    assert overrides["trainer.max_actor_ckpt_to_keep"] == 10


def test_launcher_uses_conda_verl_and_no_shell(tmp_path: Path) -> None:
    state = _verl_state(tmp_path)
    state["trainer"]["trainer_output_dir"] = str(tmp_path / "run")
    config = generate_verl_grpo_config(state, str(_DEFAULT_VERL_GRPO_TEMPLATE_PATH))
    config_path = tmp_path / "launch.yaml"
    config_path.write_text(__import__("yaml").safe_dump(config, sort_keys=False), encoding="utf-8")

    with patch("loopai.skills.Trainer.utils.verl_launcher.shutil.which", return_value="/conda"):
        cmd, cwd, env = build_verl_launch(str(config_path))

    assert cmd[:7] == ["/conda", "run", "--no-capture-output", "-n", "verl", "python", "-m"]
    assert cmd[7] == "verl.trainer.main_ppo"
    assert any(item == "algorithm.adv_estimator=\"grpo\"" for item in cmd)
    assert cwd == state["trainer"]["verl_dir"]
    assert env["VERL_FILE_LOGGER_PATH"].endswith("metrics/verl_metrics.jsonl")


def test_verl_console_metrics_are_normalized() -> None:
    metrics = MetricsExtractor().extract_metrics(
        "step:10 - critic/rewards/mean:0.75 - actor/pg_loss:-0.12 - "
        "val-core/unit_test/acc/mean@8:0.8 - training/global_step:10"
    )
    assert metrics["step"] == 10
    assert metrics["reward"] == 0.75
    assert metrics["policy_loss"] == -0.12
    assert metrics["val_score"] == 0.8


def test_results_select_verl_global_step_by_validation_metric(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    metrics_dir = run_dir / "metrics"
    checkpoints_dir = run_dir / "checkpoints"
    metrics_dir.mkdir(parents=True)
    for step in (5, 10):
        actor_dir = checkpoints_dir / f"global_step_{step}" / "actor"
        actor_dir.mkdir(parents=True)
        (actor_dir / "model_world_size_2_rank_0.pt").write_bytes(b"checkpoint")
    metric_name = "val-core/unit_test/acc/mean@8"
    with (metrics_dir / "verl_metrics.jsonl").open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"step": 5, "data": {metric_name: 0.2}}) + "\n")
        stream.write(json.dumps({"step": 10, "data": {metric_name: 0.8}}) + "\n")

    payload = analyze_results(
        training_output_dir=str(run_dir),
        trainer_state={
            "verl_selection_metric": "val-core/*/acc/mean@*",
            "verl_selection_mode": "max",
            "train_config": {"overrides": {"trainer.default_local_dir": str(checkpoints_dir)}},
        },
    )
    best = payload["data"]["best_checkpoint"]
    assert best["name"] == "global_step_10"
    assert best["metric"]["value"] == 0.8
    assert best["needs_merge"] is True


def test_approved_yaml_selection_metric_overrides_runtime_default(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    metrics_dir = run_dir / "metrics"
    checkpoints_dir = run_dir / "checkpoints"
    metrics_dir.mkdir(parents=True)
    for step in (5, 10):
        (checkpoints_dir / f"global_step_{step}" / "actor").mkdir(parents=True)
    with (metrics_dir / "verl_metrics.jsonl").open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "step": 5,
            "data": {
                "approved/score": 0.9,
                "val-core/unit_test/acc/mean@8": 0.2,
            },
        }) + "\n")
        stream.write(json.dumps({
            "step": 10,
            "data": {
                "approved/score": 0.1,
                "val-core/unit_test/acc/mean@8": 0.8,
            },
        }) + "\n")

    payload = analyze_results(
        training_output_dir=str(run_dir),
        trainer_state={
            "verl_selection_metric": "val-core/*/acc/mean@*",
            "verl_selection_mode": "max",
            "train_config": {
                "overrides": {"trainer.default_local_dir": str(checkpoints_dir)},
                "result": {
                    "selection_metric": "approved/score",
                    "selection_mode": "max",
                },
            },
        },
    )

    assert payload["data"]["best_checkpoint"]["name"] == "global_step_5"


def test_metric_step_uses_nearest_previous_checkpoint() -> None:
    checkpoints = [
        {"name": "global_step_5", "step": 5, "path": "/ckpt/5"},
        {"name": "global_step_10", "step": 10, "path": "/ckpt/10"},
    ]
    best = select_best_checkpoint(
        checkpoints,
        [{"step": 8, "val_score": 0.9}],
        selection_metric="val_score",
        selection_mode="max",
    )
    assert best["name"] == "global_step_5"
    assert "not after" in best["selection_reason"]


def test_checkpoint_selection_ignores_non_finite_metrics() -> None:
    checkpoints = [
        {"name": "global_step_5", "step": 5, "path": "/ckpt/5"},
        {"name": "global_step_10", "step": 10, "path": "/ckpt/10"},
    ]
    best = select_best_checkpoint(
        checkpoints,
        [
            {"step": 5, "val_score": float("nan")},
            {"step": 8, "val_score": float("inf")},
            {"step": 10, "val_score": 0.5},
        ],
        selection_metric="val_score",
        selection_mode="max",
    )
    assert best["name"] == "global_step_10"
    assert best["metric"]["value"] == 0.5


def test_invalid_checkpoint_selection_mode_is_rejected(tmp_path: Path) -> None:
    checkpoints = [{"name": "global_step_1", "step": 1, "path": "/ckpt/1"}]
    with pytest.raises(ValueError, match="selection_mode must be max or min"):
        select_best_checkpoint(
            checkpoints,
            [{"step": 1, "val_score": 0.5}],
            selection_metric="val_score",
            selection_mode="maximize",
        )

    state = _verl_state(tmp_path)
    config = generate_verl_grpo_config(state, str(_DEFAULT_VERL_GRPO_TEMPLATE_PATH))
    config["result"]["selection_mode"] = "maximize"
    config_path = tmp_path / "invalid-selection.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="result.selection_mode must be max or min"):
        build_verl_launch(str(config_path))


def test_task_manager_launches_verl_in_dedicated_process_group(tmp_path: Path) -> None:
    manager = TaskManager(
        configs_dir=str(tmp_path / "configs"),
        logs_dir=str(tmp_path / "logs"),
        runs_dir=str(tmp_path / "runs"),
        app_config={"verl_dir": str(tmp_path)},
    )
    task_info = {
        "status": TaskStatus.RUNNING,
        "process": None,
        "process_group_id": None,
        "cancel_requested": False,
    }
    process = Mock(pid=43210)
    process.wait.return_value = 0
    log_parser = Mock()
    launch = (["conda", "run", "-n", "verl", "python", "-m", "verl.trainer.main_ppo"], str(tmp_path), {})

    with patch("loopai.skills.Trainer.utils.task_manager.build_verl_launch", return_value=launch), patch(
        "loopai.skills.Trainer.utils.task_manager.subprocess.Popen", return_value=process
    ) as popen:
        manager._run_verl_training(
            task_info,
            str(tmp_path / "train.yaml"),
            str(tmp_path / "train.log"),
            log_parser,
        )

    assert popen.call_args.kwargs["start_new_session"] is (__import__("os").name == "posix")
    log_parser.start_monitoring.assert_called_once_with()
    assert task_info["status"] == TaskStatus.COMPLETED


def test_selected_verl_checkpoint_is_exported_with_same_conda_environment(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "global_step_10"
    actor_dir = checkpoint_root / "actor"
    actor_dir.mkdir(parents=True)
    config_path = tmp_path / "train.yaml"
    config_path.write_text("framework: verl\nstage: grpo\n", encoding="utf-8")
    launch = (
        ["/conda", "run", "--no-capture-output", "-n", "verl", "python", "-m", "verl.trainer.main_ppo"],
        str(tmp_path),
        {},
    )

    def fake_run(cmd, **kwargs):
        target_dir = Path(cmd[cmd.index("--target_dir") + 1])
        target_dir.mkdir(parents=True)
        (target_dir / "model.safetensors").write_bytes(b"weights")
        return Mock(returncode=0)

    with patch(
        "loopai.skills.Trainer.utils.verl_exporter.build_verl_launch",
        return_value=launch,
    ), patch(
        "loopai.skills.Trainer.utils.verl_exporter.subprocess.run",
        side_effect=fake_run,
    ) as run:
        output = export_verl_checkpoint(
            str(config_path),
            {"checkpoint_path": str(checkpoint_root)},
            str(tmp_path / "export.log"),
        )

    cmd = run.call_args.args[0]
    assert cmd[:7] == launch[0][:7]
    assert cmd[7:11] == ["verl.model_merger", "merge", "--backend", "fsdp"]
    assert output.endswith("global_step_10/merged_huggingface")
