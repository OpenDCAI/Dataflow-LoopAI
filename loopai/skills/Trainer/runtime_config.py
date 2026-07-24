from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from loopai.common.tracking import is_retired_tracking_key, strip_retired_tracking_fields
from loopai.skills.Trainer.rewards import normalize_reward_preset


_DEFAULT_OUTPUT_DIR = "./outputs"
_DEFAULT_THREAD_ID = "trainer-default"
_DEFAULT_TRAIN_FRAMEWORK = "llamafactory"
_DEFAULT_TRAIN_STAGE = "sft"
_DEFAULT_CUDA_VISIBLE_DEVICES = "0"
_DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "templates"
    / "qwen2_5_coder_bird_full_sft.yaml"
)
_DEFAULT_VERL_GRPO_TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "templates"
    / "verl_grpo.yaml"
)

_REQUIRED_TRAINER_FIELDS = (
    "train_framework",
    "train_stage",
    "train_input_dataset_path",
    "train_input_task_description",
    "train_input_config_template_path",
    "train_input_model_name",
)

_TRAINER_FIELD_HINTS: Dict[str, Dict[str, Any]] = {
    "train_framework": {
        "required": True,
        "source": "auto",
        "default": _DEFAULT_TRAIN_FRAMEWORK,
        "description": "Training backend: llamafactory for SFT or verl for GRPO.",
    },
    "train_stage": {
        "required": True,
        "source": "auto",
        "default": _DEFAULT_TRAIN_STAGE,
        "description": "Training stage. Supported values are sft and grpo.",
    },
    "train_input_dataset_path": {
        "required": True,
        "source": "user",
        "description": "Absolute path to the training dataset file, usually json/jsonl for SFT.",
        "example": "/path/to/data/alpaca_en_demo.json",
    },
    "train_input_model_name": {
        "required": True,
        "source": "user",
        "description": "Base model name or local model directory.",
        "example": "/path/to/models/Qwen3-0.6B/",
    },
    "train_input_task_description": {
        "required": True,
        "source": "user",
        "description": "Natural language description of the training objective.",
        "example": "Train a chat assistant for simple daily QA.",
    },
    "train_input_config_template_path": {
        "required": True,
        "source": "auto",
        "default": str(_DEFAULT_TEMPLATE_PATH),
        "description": "Backend-specific YAML template; Trainer selects the SFT or GRPO default when omitted.",
    },
    "llamafactory_dir": {
        "required": False,
        "source": "user_or_system",
        "description": "Local LLaMA-Factory repository directory.",
        "example": "/path/to/LLaMA-Factory/",
    },
    "llamafactory_env_path": {
        "required": False,
        "source": "user_or_system",
        "description": "Python environment bin directory for LLaMA-Factory.",
        "example": "/path/to/miniconda3/envs/llamafactory/bin/",
    },
    "verl_dir": {
        "required": False,
        "source": "user_or_system",
        "description": "Local verl repository directory, required for GRPO.",
        "example": "/path/to/verl/",
    },
    "verl_env_path": {
        "required": False,
        "source": "user_or_system",
        "default": "verl",
        "description": "Conda environment name or Python environment path used by verl.",
        "example": "verl",
    },
    "train_input_eval_dataset_path": {
        "required": False,
        "source": "user",
        "description": "Validation parquet required by the initial verl GRPO adapter.",
        "example": "/path/to/validation.parquet",
    },
    "verl_reward_function_path": {
        "required": False,
        "source": "user",
        "description": "Optional custom reward function Python file for verl GRPO.",
        "example": "/path/to/reward.py",
    },
    "verl_reward_function_name": {
        "required": False,
        "source": "auto",
        "default": "compute_score",
        "description": "Callable name in verl_reward_function_path.",
    },
    "verl_reward_mode": {
        "required": False,
        "source": "auto",
        "default": "auto",
        "description": "Reward source: auto, preset, or custom.",
    },
    "verl_reward_preset": {
        "required": False,
        "source": "auto",
        "default": "auto",
        "description": "Named LoopAI reward preset used by auto/preset mode.",
    },
    "verl_reward_kwargs": {
        "required": False,
        "source": "user",
        "default": {},
        "description": "Optional keyword arguments passed to the selected reward.",
    },
    "CUDA_VISIBLE_DEVICES": {
        "required": False,
        "source": "auto",
        "default": _DEFAULT_CUDA_VISIBLE_DEVICES,
        "description": "CUDA devices used by training. Defaults to single GPU 0.",
    },
    "trainer_persistent_worker": {
        "required": False,
        "source": "auto",
        "default": True,
        "description": "Keep training, progress persistence, and finalization alive after the caller disconnects.",
    },
}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any, field_name: str) -> Dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be a JSON object") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field_name} must be a mapping")


def _unwrap_config_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return _unwrap_config_value(value.get("value"))
    if isinstance(value, dict):
        return {key: _unwrap_config_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap_config_value(item) for item in value]
    return value


def _load_task_trainer_config(task_id: str, db_path: str | None) -> Dict[str, Any]:
    if not task_id:
        raise ValueError("task_id is required for task-scoped Trainer config")
    if not db_path:
        raise ValueError("DB_PATH is required when TASK_ID is provided")

    from loopai.skills.Configer import get_configer_task_state_config

    os.environ["DB_PATH"] = str(db_path)
    result = get_configer_task_state_config(section_name="trainer", task_id=task_id)
    if not result.get("ok"):
        detail = (result.get("error") or {}).get("detail") or result.get("message")
        raise RuntimeError(detail or "failed to load Trainer task state config")

    raw_config = result.get("data", {}).get("config", {})
    return _unwrap_config_value(raw_config) if isinstance(raw_config, dict) else {}


def _load_config_state(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"trainer config file not found: {config_path}")

    if path.suffix.lower() == ".json":
        config = json.loads(path.read_text(encoding="utf-8"))
    else:
        import yaml

        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    if not isinstance(config, dict):
        raise ValueError(f"trainer config must be a mapping: {config_path}")

    if "default_states" in config:
        state = copy.deepcopy(config.get("default_states") or {})
        system_config = copy.deepcopy(config.get("system") or {})
        if system_config:
            state.setdefault("system", {}).update(system_config)
        return state

    return copy.deepcopy(config)


def _trainer(state: Dict[str, Any]) -> Dict[str, Any]:
    trainer = state.setdefault("trainer", {})
    if not isinstance(trainer, dict):
        state["trainer"] = {}
    return state["trainer"]


def _system(state: Dict[str, Any]) -> Dict[str, Any]:
    system = state.setdefault("system", {})
    if not isinstance(system, dict):
        state["system"] = {}
    return state["system"]


def _normalize_train_stage(value: Any, framework: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "rl": "grpo",
        "reinforcement_learning": "grpo",
        "reinforcement-learning": "grpo",
    }
    raw = aliases.get(raw, raw)
    if not raw:
        return "grpo" if framework == "verl" else _DEFAULT_TRAIN_STAGE
    if raw not in {"sft", "grpo"}:
        raise ValueError(f"unsupported Trainer stage: {raw}; expected sft or grpo")
    return raw


def _validate_backend_stage(framework: str, stage: str) -> None:
    expected = {
        "sft": "llamafactory",
        "grpo": "verl",
    }[stage]
    if framework != expected:
        raise ValueError(
            f"unsupported Trainer backend/stage combination: "
            f"train_framework={framework}, train_stage={stage}; "
            f"use {expected} for {stage}"
        )


def _existing_default_template_path(framework: str = _DEFAULT_TRAIN_FRAMEWORK, stage: str = _DEFAULT_TRAIN_STAGE) -> str:
    template_path = _DEFAULT_VERL_GRPO_TEMPLATE_PATH if framework == "verl" and stage == "grpo" else _DEFAULT_TEMPLATE_PATH
    return str(template_path) if template_path.exists() else ""


def build_trainer_prefill_guide(
    state: Optional[Dict[str, Any]] = None,
    *,
    task_type: str | None = None,
) -> Dict[str, Any]:
    """Build a guide that tells Codex/user how to complete Trainer config."""
    state = copy.deepcopy(state) if isinstance(state, dict) else {"trainer": {}}
    state = strip_retired_tracking_fields(state)
    trainer = _trainer(state)
    requested_task = str(task_type or "").strip().lower()
    inferred_framework = "verl" if requested_task in {"grpo", "rl", "reinforcement_learning"} else _DEFAULT_TRAIN_FRAMEWORK
    framework = str(trainer.get("train_framework") or inferred_framework).strip().lower()
    stage = _normalize_train_stage(trainer.get("train_stage") or task_type, framework)
    _validate_backend_stage(framework, stage)
    defaults = {
        "train_framework": framework,
        "train_stage": stage,
        "train_input_config_template_path": (
            trainer.get("train_input_config_template_path") or _existing_default_template_path(framework, stage)
        ),
        "CUDA_VISIBLE_DEVICES": trainer.get("CUDA_VISIBLE_DEVICES") or _DEFAULT_CUDA_VISIBLE_DEVICES,
    }
    for key, value in defaults.items():
        if value is not None and value != "":
            trainer.setdefault(key, value)

    missing_fields = get_missing_trainer_fields(state)
    auto_fields = [
        field
        for field, hint in _TRAINER_FIELD_HINTS.items()
        if hint.get("source") == "auto"
    ]
    user_required_fields = [
        field
        for field in missing_fields
        if _TRAINER_FIELD_HINTS.get(field, {}).get("source") != "auto"
    ]
    examples = {
        "minimal_state": {
            "trainer": {
                "train_framework": defaults["train_framework"],
                "train_stage": defaults["train_stage"],
                "train_input_dataset_path": (
                    "/path/to/train.parquet" if stage == "grpo" else "/path/to/data.json"
                ),
                "train_input_model_name": "/path/to/base-model",
                "train_input_task_description": (
                    "Optimize the target task with GRPO." if stage == "grpo"
                    else "SFT a chat assistant for the target task."
                ),
                "train_input_config_template_path": defaults["train_input_config_template_path"],
                "CUDA_VISIBLE_DEVICES": defaults["CUDA_VISIBLE_DEVICES"],
                **(
                    {"verl_dir": "/path/to/verl/"}
                    if stage == "grpo"
                    else {"llamafactory_dir": "/path/to/LLaMA-Factory/"}
                ),
            }
        }
    }
    return {
        "task_type": stage,
        "ready": not missing_fields,
        "missing_fields": missing_fields,
        "user_required_fields": user_required_fields,
        "auto_filled_fields": auto_fields,
        "field_hints": _TRAINER_FIELD_HINTS,
        "defaults": defaults,
        "examples": examples,
        "message": (
            "Trainer config is ready."
            if not missing_fields
            else "Fill user_required_fields before launching Trainer; auto_filled_fields can use defaults."
        ),
    }


def resolve_trainer_runtime_config(
    state: Optional[Dict[str, Any]] = None,
    *,
    thread_id: Optional[str] = None,
    config_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Resolve Trainer runtime values from kwargs, env, state, and YAML."""
    if state is None:
        state = _load_config_state(config_path) if config_path else {}
    else:
        state = copy.deepcopy(state)

    # Existing databases and caller-provided states may still contain the
    # retired tracker secret.  Remove it before the state can reach a worker,
    # event, API response, or pickle payload.
    state = strip_retired_tracking_fields(state)

    trainer = _trainer(state)
    system = _system(state)

    explicit_task_id = _first_non_empty(
        thread_id,
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state.get("task_id"),
    )
    db_path = _first_non_empty(
        kwargs.get("db_path"),
        os.getenv("DB_PATH"),
        state.get("DB_PATH"),
    )
    task_state_loaded = False
    if explicit_task_id and explicit_task_id != _DEFAULT_THREAD_ID and (os.getenv("TASK_ID") or db_path):
        db_trainer_config = _load_task_trainer_config(str(explicit_task_id), str(db_path) if db_path else None)
        trainer.update({
            key: value
            for key, value in db_trainer_config.items()
            if value is not None and value != "" and not is_retired_tracking_key(key)
        })
        if db_path:
            state["DB_PATH"] = str(db_path)
        task_state_loaded = True

    resolved_task_id = _first_non_empty(
        thread_id,
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state.get("task_id"),
        _DEFAULT_THREAD_ID,
    )
    output_dir = _first_non_empty(
        kwargs.get("output_dir"),
        os.getenv("OUTPUT_DIR"),
        state.get("output_dir"),
        _DEFAULT_OUTPUT_DIR,
    )
    state["task_id"] = str(resolved_task_id)
    state["output_dir"] = str(output_dir)
    state.setdefault("language", kwargs.get("language") or state.get("language") or "en")
    state.setdefault(
        "prompt_template_dir",
        kwargs.get("prompt_template_dir") or state.get("prompt_template_dir") or "./loopai/common/prompts",
    )

    trainer["train_framework"] = str(_first_non_empty(
        kwargs.get("train_framework"),
        os.getenv("TRAIN_FRAMEWORK"),
        trainer.get("train_framework"),
        _DEFAULT_TRAIN_FRAMEWORK,
    )).strip().lower()
    if trainer["train_framework"] not in {"llamafactory", "verl"}:
        raise ValueError(
            f"unsupported Trainer framework: {trainer['train_framework']}; "
            "expected llamafactory or verl"
        )
    trainer["train_stage"] = _normalize_train_stage(
        _first_non_empty(
            kwargs.get("train_stage"),
            kwargs.get("task_type"),
            os.getenv("TRAIN_STAGE"),
            trainer.get("train_stage"),
        ),
        trainer["train_framework"],
    )
    _validate_backend_stage(trainer["train_framework"], trainer["train_stage"])
    trainer["train_input_dataset_path"] = _first_non_empty(
        kwargs.get("train_input_dataset_path"),
        kwargs.get("dataset_path"),
        os.getenv("TRAIN_DATASET_PATH"),
        trainer.get("train_input_dataset_path"),
    )
    trainer["train_input_model_name"] = _first_non_empty(
        kwargs.get("train_input_model_name"),
        kwargs.get("model_path"),
        os.getenv("TRAIN_MODEL_PATH"),
        trainer.get("train_input_model_name"),
    )
    trainer["train_input_task_description"] = _first_non_empty(
        kwargs.get("train_input_task_description"),
        kwargs.get("task_description"),
        os.getenv("TRAIN_TASK_DESCRIPTION"),
        trainer.get("train_input_task_description"),
    )
    trainer["train_input_eval_dataset_path"] = _first_non_empty(
        kwargs.get("train_input_eval_dataset_path"),
        kwargs.get("eval_dataset_path"),
        os.getenv("TRAIN_EVAL_DATASET_PATH"),
        trainer.get("train_input_eval_dataset_path"),
    )
    trainer["train_input_config_template_path"] = _first_non_empty(
        kwargs.get("train_input_config_template_path"),
        kwargs.get("config_template_path"),
        os.getenv("TRAIN_CONFIG_TEMPLATE_PATH"),
        trainer.get("train_input_config_template_path"),
        _existing_default_template_path(trainer["train_framework"], trainer["train_stage"]),
    )
    trainer["llamafactory_dir"] = _first_non_empty(
        kwargs.get("llamafactory_dir"),
        os.getenv("LLAMAFACTORY_DIR"),
        trainer.get("llamafactory_dir"),
        system.get("llamafactory_dir"),
    )
    trainer["llamafactory_env_path"] = _first_non_empty(
        kwargs.get("llamafactory_env_path"),
        os.getenv("LLAMAFACTORY_ENV_PATH"),
        trainer.get("llamafactory_env_path"),
        system.get("llamafactory_env_path"),
    )
    trainer["verl_dir"] = _first_non_empty(
        kwargs.get("verl_dir"),
        os.getenv("VERL_DIR"),
        trainer.get("verl_dir"),
        system.get("verl_dir"),
    )
    trainer["verl_env_path"] = _first_non_empty(
        kwargs.get("verl_env_path"),
        kwargs.get("verl_conda_env"),
        os.getenv("VERL_ENV_PATH"),
        os.getenv("VERL_CONDA_ENV"),
        trainer.get("verl_env_path"),
        system.get("verl_env_path"),
        "verl",
    )
    trainer["verl_algorithm"] = str(_first_non_empty(
        kwargs.get("verl_algorithm"),
        os.getenv("VERL_ALGORITHM"),
        trainer.get("verl_algorithm"),
        "grpo",
    )).strip().lower()
    if trainer["train_stage"] == "grpo" and trainer["verl_algorithm"] != "grpo":
        raise ValueError("the initial verl integration only supports verl_algorithm=grpo")
    trainer["verl_entrypoint"] = str(_first_non_empty(
        kwargs.get("verl_entrypoint"),
        os.getenv("VERL_ENTRYPOINT"),
        trainer.get("verl_entrypoint"),
        "verl.trainer.main_ppo",
    ))
    trainer["verl_rollout_backend"] = str(_first_non_empty(
        kwargs.get("verl_rollout_backend"),
        os.getenv("VERL_ROLLOUT_BACKEND"),
        trainer.get("verl_rollout_backend"),
        "vllm",
    )).strip().lower()
    if trainer["verl_rollout_backend"] not in {"vllm", "sglang"}:
        raise ValueError("verl_rollout_backend must be vllm or sglang")
    trainer["verl_model_backend"] = str(_first_non_empty(
        kwargs.get("verl_model_backend"),
        os.getenv("VERL_MODEL_BACKEND"),
        trainer.get("verl_model_backend"),
        "fsdp",
    )).strip().lower()
    if trainer["verl_model_backend"] != "fsdp":
        raise ValueError("the initial verl GRPO integration only supports verl_model_backend=fsdp")
    trainer["verl_reward_function_path"] = _first_non_empty(
        kwargs.get("verl_reward_function_path"),
        os.getenv("VERL_REWARD_FUNCTION_PATH"),
        trainer.get("verl_reward_function_path"),
    )
    trainer["verl_reward_function_name"] = str(_first_non_empty(
        kwargs.get("verl_reward_function_name"),
        os.getenv("VERL_REWARD_FUNCTION_NAME"),
        trainer.get("verl_reward_function_name"),
        "compute_score",
    ))
    raw_reward_mode = _first_non_empty(
        kwargs.get("verl_reward_mode"),
        os.getenv("VERL_REWARD_MODE"),
        trainer.get("verl_reward_mode"),
    )
    # A path without the new mode field is the legacy custom-reward contract.
    if not raw_reward_mode:
        raw_reward_mode = "custom" if trainer.get("verl_reward_function_path") else "auto"
    trainer["verl_reward_mode"] = str(raw_reward_mode).strip().lower()
    if trainer["verl_reward_mode"] not in {"auto", "preset", "custom"}:
        raise ValueError("verl_reward_mode must be auto, preset, or custom")

    if trainer["verl_reward_mode"] == "auto":
        trainer["verl_reward_preset"] = "auto"
    elif trainer["verl_reward_mode"] == "preset":
        trainer["verl_reward_preset"] = normalize_reward_preset(_first_non_empty(
            kwargs.get("verl_reward_preset"),
            os.getenv("VERL_REWARD_PRESET"),
            trainer.get("verl_reward_preset"),
            "auto",
        ))
    else:
        trainer["verl_reward_preset"] = ""
    trainer["verl_reward_kwargs"] = _as_dict(
        _first_non_empty(
            kwargs.get("verl_reward_kwargs"),
            os.getenv("VERL_REWARD_KWARGS"),
            trainer.get("verl_reward_kwargs"),
        ),
        "verl_reward_kwargs",
    )
    trainer["verl_selection_metric"] = str(_first_non_empty(
        kwargs.get("verl_selection_metric"),
        os.getenv("VERL_SELECTION_METRIC"),
        trainer.get("verl_selection_metric"),
        "val-core/*/acc/mean@*",
    ))
    trainer["verl_selection_mode"] = str(_first_non_empty(
        kwargs.get("verl_selection_mode"),
        os.getenv("VERL_SELECTION_MODE"),
        trainer.get("verl_selection_mode"),
        "max",
    )).strip().lower()
    if trainer["verl_selection_mode"] not in {"max", "min"}:
        raise ValueError("verl_selection_mode must be max or min")
    trainer["verl_max_actor_ckpt_to_keep"] = _as_int(
        _first_non_empty(
            kwargs.get("verl_max_actor_ckpt_to_keep"),
            os.getenv("VERL_MAX_ACTOR_CKPT_TO_KEEP"),
            trainer.get("verl_max_actor_ckpt_to_keep"),
        ),
        default=10,
    )
    trainer["CUDA_VISIBLE_DEVICES"] = str(
        _first_non_empty(
            kwargs.get("cuda_visible_devices"),
            kwargs.get("CUDA_VISIBLE_DEVICES"),
            os.getenv("CUDA_VISIBLE_DEVICES"),
            trainer.get("CUDA_VISIBLE_DEVICES"),
            system.get("CUDA_VISIBLE_DEVICES"),
            _DEFAULT_CUDA_VISIBLE_DEVICES,
        )
    )
    trainer["trainer_persistent_worker"] = _as_bool(
        _first_non_empty(
            kwargs.get("trainer_persistent_worker"),
            os.getenv("TRAINER_PERSISTENT_WORKER"),
            trainer.get("trainer_persistent_worker"),
            system.get("trainer_persistent_worker"),
        ),
        default=True,
    )
    prefill_guide = build_trainer_prefill_guide(
        state,
        task_type=trainer["train_stage"],
    )
    trainer["trainer_missing_fields"] = prefill_guide["missing_fields"]
    trainer["trainer_prefill_guide"] = prefill_guide

    return {
        "state": state,
        "thread_id": str(resolved_task_id),
        "missing_fields": prefill_guide["missing_fields"],
        "db_path": str(db_path) if db_path else None,
        "task_state_loaded": task_state_loaded,
        "prefill_guide": prefill_guide,
    }


def get_missing_trainer_fields(state: Dict[str, Any]) -> list[str]:
    trainer = state.get("trainer") or {}
    missing = [field for field in _REQUIRED_TRAINER_FIELDS if not trainer.get(field)]
    if trainer.get("train_framework") == "llamafactory" and not trainer.get("llamafactory_dir"):
        missing.append("llamafactory_dir")
    if trainer.get("train_framework") == "verl" and not trainer.get("verl_dir"):
        missing.append("verl_dir")
    if trainer.get("train_framework") == "verl" and not trainer.get("train_input_eval_dataset_path"):
        missing.append("train_input_eval_dataset_path")
    if (
        trainer.get("train_framework") == "verl"
        and trainer.get("verl_reward_mode") == "custom"
        and not trainer.get("verl_reward_function_path")
    ):
        missing.append("verl_reward_function_path")
    return missing
