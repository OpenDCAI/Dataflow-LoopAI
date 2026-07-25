from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

import yaml

from loopai.common.tracking import assert_no_retired_tracking, strip_retired_tracking_config
from loopai.skills.Trainer.rewards import normalize_reward_preset


_REWARD_ROUTER_PATH = (
    Path(__file__).resolve().parents[1]
    / "rewards"
    / "router.py"
)


def _gpu_count(cuda_visible_devices: Any) -> int:
    devices = [item.strip() for item in str(cuda_visible_devices or "0").split(",") if item.strip()]
    return max(1, len(devices))


def generate_verl_grpo_config(state: Dict[str, Any], template_path: str) -> Dict[str, Any]:
    """Create the task-scoped, user-approvable Verl GRPO launch YAML."""
    path = Path(template_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Verl GRPO template does not exist: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Verl GRPO template must contain a YAML mapping: {path}")

    config = copy.deepcopy(raw)
    if config.get("framework") != "verl" or config.get("stage") != "grpo":
        raise ValueError("Verl template must declare framework: verl and stage: grpo")

    trainer = state.get("trainer") or {}
    run_dir = Path(str(trainer.get("trainer_output_dir") or trainer.get("output_dir") or "./outputs")).resolve()
    version_id = str(trainer.get("trainer_version_id") or run_dir.name)
    train_value = trainer.get("train_input_dataset_path")
    train_path = Path(str(train_value)).expanduser() if train_value else None
    eval_value = trainer.get("train_input_eval_dataset_path")
    eval_path = Path(str(eval_value)).expanduser() if eval_value else None
    # Verl's copy_to_local helper rejects model sources ending in "/". Normalize
    # the user-provided value here so every generated/approved config is safe,
    # including values restored from an older task snapshot.
    model_path = str(trainer.get("train_input_model_name") or "").strip().rstrip("/")

    config["entrypoint"] = str(trainer.get("verl_entrypoint") or config.get("entrypoint") or "verl.trainer.main_ppo")
    environment = config.setdefault("environment", {})
    environment["verl_dir"] = str(trainer.get("verl_dir") or "")
    environment["verl_env_path"] = str(trainer.get("verl_env_path") or "verl")
    environment["cuda_visible_devices"] = str(trainer.get("CUDA_VISIBLE_DEVICES") or "0")
    environment["model_backend"] = str(trainer.get("verl_model_backend") or "fsdp")
    environment["metrics_jsonl"] = str(run_dir / "metrics" / "verl_metrics.jsonl")

    overrides = config.setdefault("overrides", {})
    overrides["algorithm.adv_estimator"] = "grpo"
    overrides["data.train_files"] = [str(train_path.resolve())] if train_path else []
    overrides["data.val_files"] = [str(eval_path.resolve())] if eval_path else []
    overrides["actor_rollout_ref.model.path"] = model_path
    overrides["actor_rollout_ref.rollout.name"] = str(trainer.get("verl_rollout_backend") or "vllm")
    overrides["trainer.experiment_name"] = version_id
    overrides["trainer.default_local_dir"] = str(run_dir / "checkpoints")
    overrides["trainer.n_gpus_per_node"] = _gpu_count(trainer.get("CUDA_VISIBLE_DEVICES"))
    overrides["trainer.max_actor_ckpt_to_keep"] = int(trainer.get("verl_max_actor_ckpt_to_keep") or 10)

    reward_path = trainer.get("verl_reward_function_path")
    raw_reward_mode = str(trainer.get("verl_reward_mode") or "").strip().lower()
    reward_mode = raw_reward_mode or ("custom" if reward_path else "auto")
    if reward_mode not in {"auto", "preset", "custom"}:
        raise ValueError("verl_reward_mode must be auto, preset, or custom")
    reward_kwargs = trainer.get("verl_reward_kwargs") or {}
    if not isinstance(reward_kwargs, dict):
        raise ValueError("verl_reward_kwargs must be a mapping")
    reward_kwargs = copy.deepcopy(reward_kwargs)

    if reward_mode == "custom":
        if not reward_path:
            raise ValueError("custom reward mode requires verl_reward_function_path")
        resolved_reward_path = str(Path(str(reward_path)).expanduser().resolve())
        reward_function_name = str(trainer.get("verl_reward_function_name") or "compute_score")
        reward_preset = ""
    else:
        reward_preset = normalize_reward_preset(
            "auto" if reward_mode == "auto" else trainer.get("verl_reward_preset")
        )
        resolved_reward_path = str(_REWARD_ROUTER_PATH.resolve())
        reward_function_name = "compute_score"
        reward_kwargs["preset"] = reward_preset

    overrides["reward.custom_reward_function.path"] = resolved_reward_path
    overrides["reward.custom_reward_function.name"] = reward_function_name
    # The current Verl structured config does not declare reward_kwargs even
    # though get_custom_reward_fn supports it, so Hydra requires the + prefix.
    overrides["+reward.custom_reward_function.reward_kwargs"] = reward_kwargs

    loopai_reward = config.setdefault("loopai_reward", {})
    loopai_reward["mode"] = reward_mode
    loopai_reward["preset"] = reward_preset or None
    loopai_reward["function_path"] = resolved_reward_path
    loopai_reward["function_name"] = reward_function_name

    result = config.setdefault("result", {})
    result["selection_metric"] = str(
        trainer.get("verl_selection_metric") or "val-core/*/acc/mean@*"
    )
    result["selection_mode"] = str(trainer.get("verl_selection_mode") or "max")
    config = strip_retired_tracking_config(config)
    config.setdefault("overrides", {})["trainer.logger"] = ["console", "file"]
    assert_no_retired_tracking(config)
    return config


def save_verl_grpo_config(config: Dict[str, Any], output_path: str) -> str:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return str(path)


def generate_verl_config_explanation(config: Dict[str, Any]) -> str:
    overrides = config.get("overrides") or {}
    result = config.get("result") or {}
    loopai_reward = config.get("loopai_reward") or {}
    lines = [
        "Verl GRPO configuration",
        f"- entrypoint: {config.get('entrypoint')}",
        f"- train files: {overrides.get('data.train_files')}",
        f"- validation files: {overrides.get('data.val_files')}",
        f"- model: {overrides.get('actor_rollout_ref.model.path')}",
        f"- rollout backend: {overrides.get('actor_rollout_ref.rollout.name')}",
        f"- GPUs per node: {overrides.get('trainer.n_gpus_per_node')}",
        f"- checkpoints: {overrides.get('trainer.default_local_dir')}",
        f"- max actor checkpoints: {overrides.get('trainer.max_actor_ckpt_to_keep')}",
        f"- reward mode: {loopai_reward.get('mode')}",
        f"- reward preset: {loopai_reward.get('preset') or 'custom'}",
        f"- reward function: {overrides.get('reward.custom_reward_function.path')}",
        f"- selection metric: {result.get('selection_metric')} ({result.get('selection_mode')})",
    ]
    return "\n".join(lines) + "\n"
