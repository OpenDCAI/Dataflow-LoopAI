from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


_DEFAULT_OUTPUT_DIR = "./outputs"
_DEFAULT_THREAD_ID = "trainer-default"

_REQUIRED_TRAINER_FIELDS = (
    "train_framework",
    "train_input_dataset_path",
    "train_input_task_description",
    "train_input_config_template_path",
    "train_input_model_name",
)


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
            if value is not None and value != ""
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

    trainer["train_framework"] = _first_non_empty(
        kwargs.get("train_framework"),
        os.getenv("TRAIN_FRAMEWORK"),
        trainer.get("train_framework"),
        "llamafactory",
    )
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
    trainer["train_input_config_template_path"] = _first_non_empty(
        kwargs.get("train_input_config_template_path"),
        kwargs.get("config_template_path"),
        os.getenv("TRAIN_CONFIG_TEMPLATE_PATH"),
        trainer.get("train_input_config_template_path"),
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
    trainer["CUDA_VISIBLE_DEVICES"] = str(
        _first_non_empty(
            kwargs.get("cuda_visible_devices"),
            kwargs.get("CUDA_VISIBLE_DEVICES"),
            os.getenv("CUDA_VISIBLE_DEVICES"),
            trainer.get("CUDA_VISIBLE_DEVICES"),
            system.get("CUDA_VISIBLE_DEVICES"),
            "0",
        )
    )
    trainer["swanlab_api_key"] = _first_non_empty(
        kwargs.get("swanlab_api_key"),
        os.getenv("SWANLAB_API_KEY"),
        trainer.get("swanlab_api_key"),
        system.get("swanlab_api_key"),
        "",
    )
    trainer["train_input_use_swanlab"] = _as_bool(
        _first_non_empty(
            kwargs.get("train_input_use_swanlab"),
            kwargs.get("use_swanlab"),
            os.getenv("TRAIN_USE_SWANLAB"),
            trainer.get("train_input_use_swanlab"),
        ),
        default=False,
    )
    if kwargs.get("train_input_swanlab_project") or kwargs.get("swanlab_project"):
        trainer["train_input_swanlab_project"] = _first_non_empty(
            kwargs.get("train_input_swanlab_project"),
            kwargs.get("swanlab_project"),
        )

    return {
        "state": state,
        "thread_id": str(resolved_task_id),
        "missing_fields": get_missing_trainer_fields(state),
        "db_path": str(db_path) if db_path else None,
        "task_state_loaded": task_state_loaded,
    }


def get_missing_trainer_fields(state: Dict[str, Any]) -> list[str]:
    trainer = state.get("trainer") or {}
    missing = [field for field in _REQUIRED_TRAINER_FIELDS if not trainer.get(field)]
    if trainer.get("train_framework") == "llamafactory" and not trainer.get("llamafactory_dir"):
        missing.append("llamafactory_dir")
    return missing
