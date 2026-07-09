from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


_DEFAULT_OUTPUT_DIR = "./outputs"
_DEFAULT_THREAD_ID = "trainer-default"
_DEFAULT_TRAIN_FRAMEWORK = "llamafactory"
_DEFAULT_CUDA_VISIBLE_DEVICES = "0"
_DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "loopai"
    / "agents"
    / "Trainer"
    / "templates"
    / "qwen2_5_coder_bird_full_sft.yaml"
)

_REQUIRED_TRAINER_FIELDS = (
    "train_framework",
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
        "description": "Training backend. Use llamafactory for SFT by default.",
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
        "description": "LLaMA-Factory YAML template. The default SFT template is used when omitted.",
    },
    "llamafactory_dir": {
        "required": True,
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
    "CUDA_VISIBLE_DEVICES": {
        "required": False,
        "source": "auto",
        "default": _DEFAULT_CUDA_VISIBLE_DEVICES,
        "description": "CUDA devices used by training. Defaults to single GPU 0.",
    },
    "train_input_use_swanlab": {
        "required": False,
        "source": "auto",
        "default": False,
        "description": "Whether to enable SwanLab logging.",
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


def _existing_default_template_path() -> str:
    return str(_DEFAULT_TEMPLATE_PATH) if _DEFAULT_TEMPLATE_PATH.exists() else ""


def build_trainer_prefill_guide(
    state: Optional[Dict[str, Any]] = None,
    *,
    task_type: str = "sft",
) -> Dict[str, Any]:
    """Build a guide that tells Codex/user how to complete Trainer config."""
    state = copy.deepcopy(state) if isinstance(state, dict) else {"trainer": {}}
    trainer = _trainer(state)
    defaults = {
        "train_framework": trainer.get("train_framework") or _DEFAULT_TRAIN_FRAMEWORK,
        "train_input_config_template_path": (
            trainer.get("train_input_config_template_path") or _existing_default_template_path()
        ),
        "CUDA_VISIBLE_DEVICES": trainer.get("CUDA_VISIBLE_DEVICES") or _DEFAULT_CUDA_VISIBLE_DEVICES,
        "train_input_use_swanlab": _as_bool(trainer.get("train_input_use_swanlab"), default=False),
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
                "train_input_dataset_path": "/path/to/data.json",
                "train_input_model_name": "/path/to/base-model",
                "train_input_task_description": "SFT a chat assistant for the target task.",
                "train_input_config_template_path": defaults["train_input_config_template_path"],
                "llamafactory_dir": "/path/to/LLaMA-Factory/",
                "CUDA_VISIBLE_DEVICES": defaults["CUDA_VISIBLE_DEVICES"],
            }
        }
    }
    return {
        "task_type": task_type,
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
        _DEFAULT_TRAIN_FRAMEWORK,
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
        _existing_default_template_path(),
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
            _DEFAULT_CUDA_VISIBLE_DEVICES,
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

    prefill_guide = build_trainer_prefill_guide(
        state,
        task_type=str(kwargs.get("task_type") or trainer.get("task_type") or "sft"),
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
    return missing
