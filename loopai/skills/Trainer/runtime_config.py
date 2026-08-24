from __future__ import annotations

import ast
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
        "source": "user_or_upstream",
        "description": "SFT dataset or native Verl Parquet; Verl may instead use the latest generated source.",
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
        "description": "Optional validation data. Trainer can deterministically split generated Verl data when omitted.",
        "example": "/path/to/validation.parquet",
    },
    "verl_source_dataset_path": {
        "required": False,
        "source": "user_or_upstream",
        "description": "Generated JSON/JSONL/Parquet source; falls back to legacy Constructor task state or current Obtainer output.",
        "example": "/path/to/generated.jsonl",
    },
    "verl_source_eval_dataset_path": {
        "required": False,
        "source": "user",
        "description": "Optional generated validation source before Trainer converts it to Verl Parquet.",
        "example": "/path/to/generated-validation.jsonl",
    },
    "verl_data_adapter": {
        "required": False,
        "source": "auto",
        "default": "auto",
        "description": "Generated-data adapter: auto, native, messages, alpaca, or qa.",
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
    "verl_reward_origin": {
        "required": False,
        "source": "auto",
        "default": "",
        "description": "Whether the reward contract is automatically inferred or user-selected.",
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
    "verl_inherit_previous_config": {
        "required": False,
        "source": "auto",
        "default": True,
        "description": "Use the previous approved Verl YAML as the next round's hyperparameter baseline.",
    },
    "verl_use_previous_best_model": {
        "required": False,
        "source": "auto",
        "default": True,
        "description": "Use the previous successful exported Hugging Face checkpoint as the next round model.",
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


def _as_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_merge_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (dict, list, tuple, set)) and not value:
        return False
    return True


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


def _normalize_mapping_results(value: Any, section_name: str) -> Dict[str, Any]:
    """Return one upstream mapping payload, including legacy serialized values.

    Older Configer calls could persist a mapping as Python ``repr`` text.  Read
    those snapshots safely so an existing task can continue, while rejecting
    malformed values instead of silently falling back to a previous dataset.
    """
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str):
        raw = value.strip()
        parsed: Any
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(raw)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(
                    f"{section_name}.mapping_results must be a mapping"
                ) from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{section_name}.mapping_results must be a mapping")


def _upstream_mapping_results(state: Dict[str, Any], section_name: str) -> Dict[str, Any]:
    section = state.get(section_name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError(f"{section_name} state must be a mapping")
    mapping = _normalize_mapping_results(section.get("mapping_results"), section_name)
    if mapping:
        section["mapping_results"] = mapping
    return mapping


def _unwrap_config_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return _unwrap_config_value(value.get("value"))
    if isinstance(value, dict):
        return {key: _unwrap_config_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap_config_value(item) for item in value]
    return value


def _load_task_section_config(
    section_name: str,
    task_id: str,
    db_path: str | None,
) -> Dict[str, Any]:
    if not task_id:
        raise ValueError(f"task_id is required for task-scoped {section_name} config")
    if not db_path:
        raise ValueError("DB_PATH is required when TASK_ID is provided")

    from loopai.skills.Configer import get_configer_task_state_config

    os.environ["DB_PATH"] = str(db_path)
    result = get_configer_task_state_config(section_name=section_name, task_id=task_id)
    if not result.get("ok"):
        detail = (result.get("error") or {}).get("detail") or result.get("message")
        raise RuntimeError(detail or f"failed to load {section_name} task state config")

    raw_config = result.get("data", {}).get("config", {})
    return _unwrap_config_value(raw_config) if isinstance(raw_config, dict) else {}


def _load_task_trainer_config(task_id: str, db_path: str | None) -> Dict[str, Any]:
    return _load_task_section_config("trainer", task_id, db_path)


def _merge_optional_task_section(
    state: Dict[str, Any],
    section_name: str,
    task_id: str,
    db_path: str | None,
) -> None:
    """Load only upstream mapping results; never pull unrelated section secrets."""
    try:
        persisted = _load_task_section_config(section_name, task_id, db_path)
    except Exception:
        return
    caller_section = state.get(section_name)
    caller_mapping = (
        caller_section.get("mapping_results")
        if isinstance(caller_section, dict)
        else None
    )
    persisted_mapping = (
        persisted.get("mapping_results")
        if isinstance(persisted, dict)
        else None
    )
    mapping_results = caller_mapping if _has_merge_value(caller_mapping) else persisted_mapping
    if _has_merge_value(mapping_results):
        merged = copy.deepcopy(caller_section) if isinstance(caller_section, dict) else {}
        merged["mapping_results"] = copy.deepcopy(mapping_results)
        state[section_name] = merged


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
    caller_trainer_config = copy.deepcopy(trainer)
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
        merged_trainer = {
            key: value
            for key, value in db_trainer_config.items()
            if value is not None and value != "" and not is_retired_tracking_key(key)
        }
        # Task state is the persistent baseline. Values supplied by the current
        # caller are newer and therefore win, matching the documented
        # kwargs > env > state > system > defaults contract.
        caller_overrides = {
            key: value
            for key, value in caller_trainer_config.items()
            if _has_merge_value(value) and not is_retired_tracking_key(key)
        }
        merged_trainer.update(caller_overrides)
        state["trainer"] = merged_trainer
        trainer = _trainer(state)
        # A Trainer-only skill invocation still needs the current round's
        # generated artifact. Read upstream task sections without mutating them.
        _merge_optional_task_section(
            state,
            "constructor",
            str(explicit_task_id),
            str(db_path) if db_path else None,
        )
        _merge_optional_task_section(
            state,
            "obtainer",
            str(explicit_task_id),
            str(db_path) if db_path else None,
        )
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
    constructor_mapping = _upstream_mapping_results(state, "constructor")
    obtainer_mapping = _upstream_mapping_results(state, "obtainer")
    generated_dataset_path = _first_non_empty(
        constructor_mapping.get("output_file"),
        obtainer_mapping.get("output_file"),
    )
    generated_dataset_origin = (
        "constructor"
        if constructor_mapping.get("output_file")
        else "obtainer" if obtainer_mapping.get("output_file") else ""
    )
    requested_verl_source = _first_non_empty(
        kwargs.get("verl_source_dataset_path"),
        os.getenv("VERL_SOURCE_DATASET_PATH"),
    )
    persisted_verl_source = trainer.get("verl_source_dataset_path")
    persisted_source_origin = str(trainer.get("verl_source_dataset_origin") or "").strip().lower()
    # Only kwargs/environment values are explicit for *this* invocation.  A
    # task-state path marked ``user`` may simply be the previous round's seed
    # dataset and must not permanently mask current upstream compatibility data.
    explicit_verl_source = requested_verl_source
    selected_verl_source = _first_non_empty(
        explicit_verl_source,
        generated_dataset_path,
        persisted_verl_source,
        (
            trainer.get("train_input_dataset_path")
            if str(trainer.get("train_input_dataset_path") or "").lower().endswith(
                (".json", ".jsonl")
            )
            else None
        ),
    )
    trainer["_verl_source_dataset_explicit"] = bool(explicit_verl_source)
    trainer["verl_source_dataset_path"] = selected_verl_source
    if explicit_verl_source:
        trainer["verl_source_dataset_origin"] = "user"
    elif generated_dataset_path:
        trainer["verl_source_dataset_origin"] = generated_dataset_origin
    elif persisted_verl_source:
        trainer["verl_source_dataset_origin"] = persisted_source_origin or "user"
    source_replaced_by_upstream = bool(
        trainer.get("train_framework") == "verl"
        and generated_dataset_path
        and not explicit_verl_source
        and str(generated_dataset_path) != str(persisted_verl_source or "")
    )
    trainer["_verl_source_dataset_replaced"] = source_replaced_by_upstream
    if source_replaced_by_upstream:
        # These fields describe Parquet derived from the previous source.  The
        # data builder will repopulate them from the new upstream artifact.
        trainer["train_input_dataset_path"] = ""
        trainer.pop("verl_data_manifest_path", None)
        trainer.pop("verl_data_prepare_result", None)
    if trainer.get("train_framework") == "verl" and not trainer.get("train_input_dataset_path"):
        # The graph's generic required-field gate still expects this alias.
        # data_check_node replaces it with the version-scoped prepared Parquet.
        trainer["train_input_dataset_path"] = trainer.get("verl_source_dataset_path")
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
    trainer["verl_source_eval_dataset_path"] = _first_non_empty(
        kwargs.get("verl_source_eval_dataset_path"),
        os.getenv("VERL_SOURCE_EVAL_DATASET_PATH"),
        caller_trainer_config.get("verl_source_eval_dataset_path"),
        trainer.get("verl_source_eval_dataset_path"),
    )
    trainer["verl_data_adapter"] = str(_first_non_empty(
        kwargs.get("verl_data_adapter"),
        os.getenv("VERL_DATA_ADAPTER"),
        trainer.get("verl_data_adapter"),
        "auto",
    )).strip().lower()
    if trainer["verl_data_adapter"] not in {"auto", "native", "messages", "alpaca", "qa"}:
        raise ValueError("verl_data_adapter must be auto, native, messages, alpaca, or qa")
    trainer["verl_data_source"] = str(_first_non_empty(
        kwargs.get("verl_data_source"),
        os.getenv("VERL_DATA_SOURCE"),
        trainer.get("verl_data_source"),
        "",
    ) or "").strip()
    trainer["verl_validation_ratio"] = _as_float(
        _first_non_empty(
            kwargs.get("verl_validation_ratio"),
            os.getenv("VERL_VALIDATION_RATIO"),
            trainer.get("verl_validation_ratio"),
        ),
        default=0.05,
    )
    if not 0.0 < trainer["verl_validation_ratio"] < 1.0:
        raise ValueError("verl_validation_ratio must be between 0 and 1")
    trainer["verl_split_seed"] = _as_int(
        _first_non_empty(
            kwargs.get("verl_split_seed"),
            os.getenv("VERL_SPLIT_SEED"),
            trainer.get("verl_split_seed"),
        ),
        default=42,
    )
    trainer["verl_reuse_previous_validation"] = _as_bool(
        _first_non_empty(
            kwargs.get("verl_reuse_previous_validation"),
            os.getenv("VERL_REUSE_PREVIOUS_VALIDATION"),
            trainer.get("verl_reuse_previous_validation"),
        ),
        default=True,
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
    requested_reward_mode = _first_non_empty(
        kwargs.get("verl_reward_mode"),
        os.getenv("VERL_REWARD_MODE"),
    )
    requested_reward_preset = _first_non_empty(
        kwargs.get("verl_reward_preset"),
        os.getenv("VERL_REWARD_PRESET"),
    )
    explicit_reward_override = any(
        _has_merge_value(value)
        for value in (
            requested_reward_mode,
            requested_reward_preset,
            kwargs.get("verl_reward_function_path"),
            os.getenv("VERL_REWARD_FUNCTION_PATH"),
        )
    )
    raw_reward_mode = _first_non_empty(
        requested_reward_mode,
        trainer.get("verl_reward_mode"),
    )
    # A path without the new mode field is the legacy custom-reward contract.
    if not raw_reward_mode:
        raw_reward_mode = "custom" if trainer.get("verl_reward_function_path") else "auto"
    persisted_reward_origin = str(trainer.get("verl_reward_origin") or "").strip().lower()
    previous_recommendation = trainer.get("verl_reward_recommendation") or {}
    if explicit_reward_override:
        reward_origin = "auto" if str(raw_reward_mode).strip().lower() == "auto" else "user"
    elif persisted_reward_origin in {"auto", "user"}:
        reward_origin = persisted_reward_origin
    elif (
        isinstance(previous_recommendation, dict)
        and previous_recommendation.get("source") == "trainer_generated_data_inference"
    ):
        reward_origin = "auto"
    else:
        reward_origin = "auto" if str(raw_reward_mode).strip().lower() == "auto" else "user"

    # An automatically selected preset belongs to the previous source.  Reset
    # it before adapting a newly generated dataset so reward inference runs
    # again.  User-selected preset/custom rewards remain untouched.
    if source_replaced_by_upstream and reward_origin == "auto" and not explicit_reward_override:
        raw_reward_mode = "auto"
        trainer["verl_reward_preset"] = "auto"
    trainer["verl_reward_mode"] = str(raw_reward_mode).strip().lower()
    if trainer["verl_reward_mode"] not in {"auto", "preset", "custom"}:
        raise ValueError("verl_reward_mode must be auto, preset, or custom")
    trainer["verl_reward_origin"] = reward_origin

    if trainer["verl_reward_mode"] == "auto":
        trainer["verl_reward_preset"] = "auto"
    elif trainer["verl_reward_mode"] == "preset":
        trainer["verl_reward_preset"] = normalize_reward_preset(_first_non_empty(
            requested_reward_preset,
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
    trainer["verl_inherit_previous_config"] = _as_bool(
        _first_non_empty(
            kwargs.get("verl_inherit_previous_config"),
            os.getenv("VERL_INHERIT_PREVIOUS_CONFIG"),
            trainer.get("verl_inherit_previous_config"),
        ),
        default=True,
    )
    trainer["verl_use_previous_best_model"] = _as_bool(
        _first_non_empty(
            kwargs.get("verl_use_previous_best_model"),
            os.getenv("VERL_USE_PREVIOUS_BEST_MODEL"),
            trainer.get("verl_use_previous_best_model"),
        ),
        default=True,
    )
    trainer["verl_multi_round_enabled"] = _as_bool(
        _first_non_empty(
            kwargs.get("verl_multi_round_enabled"),
            os.getenv("VERL_MULTI_ROUND_ENABLED"),
            trainer.get("verl_multi_round_enabled"),
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
    missing = [
        field
        for field in _REQUIRED_TRAINER_FIELDS
        if field != "train_input_dataset_path" and not trainer.get(field)
    ]
    if not (
        trainer.get("train_input_dataset_path")
        or trainer.get("verl_source_dataset_path")
    ):
        missing.append("train_input_dataset_path")
    if trainer.get("train_framework") == "llamafactory" and not trainer.get("llamafactory_dir"):
        missing.append("llamafactory_dir")
    if trainer.get("train_framework") == "verl" and not trainer.get("verl_dir"):
        missing.append("verl_dir")
    if (
        trainer.get("train_framework") == "verl"
        and trainer.get("verl_reward_mode") == "custom"
        and not trainer.get("verl_reward_function_path")
    ):
        missing.append("verl_reward_function_path")
    return missing
