from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from loopai.common.tracking import assert_no_retired_tracking, strip_retired_tracking_fields
from loopai.common.exception import ErrorCode, emit_error, emit_success
from loopai.skills.Trainer.runtime_config import (
    build_trainer_prefill_guide,
    resolve_trainer_runtime_config,
)
from loopai.skills.Trainer.utils.stream_events import prepare_trainer_run


_TRAINER_TASK_STATE_UPDATE_FIELDS = {
    "train_framework",
    "train_stage",
    "train_input_dataset_path",
    "train_input_eval_dataset_path",
    "train_input_model_name",
    "train_input_task_description",
    "train_input_config_template_path",
    "verl_source_dataset_path",
    "verl_source_dataset_origin",
    "verl_source_eval_dataset_path",
    "verl_data_adapter",
    "verl_data_source",
    "verl_validation_ratio",
    "verl_split_seed",
    "verl_reuse_previous_validation",
    "verl_reward_mode",
    "verl_reward_origin",
    "verl_reward_preset",
    "verl_reward_function_path",
    "verl_reward_function_name",
    "verl_reward_kwargs",
    "verl_inherit_previous_config",
    "verl_use_previous_best_model",
    "verl_multi_round_enabled",
    "trainer_parent_version_id",
    "trainer_round_index",
    "trainer_model_inheritance",
    "verl_data_manifest_path",
    "verl_data_prepare_result",
    "verl_reward_recommendation",
    "trainer_task_id",
    "data_check_passed",
    "data_check_result",
    "data_check_report_path",
    "data_check_error",
    "config_generation_success",
    "config_explanation_path",
    "config_generation_error",
    "training_success",
    "training_execution_time",
    "training_task_id",
    "training_final_status",
    "training_log_path",
    "training_report_path",
    "training_error",
    "current_training_status",
    "update_model_path",
    "trainer_event_log_path",
    "trainer_run_state_path",
    "trainer_worker_log_path",
    "trainer_worker_pid",
    "trainer_persistent_worker",
    "trainer_state_update_error",
    "trainer_version_id",
    "trainer_output_dir",
    "trainer_missing_fields",
    "trainer_prefill_guide",
    "trainer_result",
    "trainer_last_error",
    "trainer_result_analysis",
    "trainer_result_analysis_version_id",
    "trainer_result_summary",
    "trainer_best_checkpoint",
    "trainer_best_metric",
    "trainer_best_checkpoint_path",
    "trainer_model_export_error",
    "trainer_model_export_log_path",
    "train_config",
    "training_checkpoints",
    "training_step_losses",
    "trainer_data_check_result",
    "train_output_config_path",
    "train_output_data_check_report_path",
    "trainer_config_explanation_path",
    "train_output_training_log_path",
    "train_output_training_report_path",
    "trainer_training_task_id",
    "trainer_training_execution_time",
    "trainer_training_final_status",
}


_TRAINER_ROUND_TRANSIENT_FIELDS = {
    "data_check_passed",
    "data_check_result",
    "data_check_report_path",
    "data_check_error",
    "config_generation_success",
    "config_explanation_path",
    "config_generation_error",
    "training_success",
    "training_execution_time",
    "training_final_status",
    "training_log_path",
    "training_report_path",
    "training_error",
    "current_training_status",
    "update_model_path",
    "trainer_event_log_path",
    "trainer_run_state_path",
    "trainer_worker_log_path",
    "trainer_worker_pid",
    "trainer_state_update_error",
    "trainer_output_dir",
    "trainer_result",
    "trainer_last_error",
    "trainer_result_analysis",
    "trainer_result_analysis_version_id",
    "trainer_result_summary",
    "trainer_best_checkpoint",
    "trainer_best_metric",
    "trainer_best_checkpoint_path",
    "trainer_model_export_error",
    "trainer_model_export_log_path",
    "train_config",
    "training_checkpoints",
    "training_step_losses",
    "trainer_data_check_result",
    "trainer_data_check_passed",
    "trainer_data_check_error",
    "train_output_config_path",
    "train_output_data_check_report_path",
    "trainer_config_explanation_path",
    "trainer_config_generation_success",
    "trainer_config_generation_error",
    "train_output_training_log_path",
    "train_output_training_report_path",
    "train_output_training_error",
    "trainer_training_success",
    "trainer_training_execution_time",
    "trainer_training_final_status",
    "trainer_current_training_status",
    "verl_data_manifest_path",
    "verl_data_prepare_result",
    "verl_reward_recommendation",
}


def _to_configer_update_item(value: Any) -> Dict[str, Any]:
    if isinstance(value, bool):
        type_name = "bool"
    elif isinstance(value, int) and not isinstance(value, bool):
        type_name = "int"
    elif isinstance(value, float):
        type_name = "float"
    elif isinstance(value, dict):
        type_name = "dict"
    elif isinstance(value, list):
        type_name = "list"
    elif value is None:
        type_name = "none"
    else:
        type_name = "str"
    return {"value": value, "type": type_name}


def _normalize_trainer_task_state_updates(trainer_state: Dict[str, Any]) -> Dict[str, Any]:
    updates = {
        field: trainer_state[field]
        for field in _TRAINER_TASK_STATE_UPDATE_FIELDS
        if field in trainer_state
    }

    aliases = {
        "trainer_data_check_passed": "data_check_passed",
        "trainer_data_check_error": "data_check_error",
        "train_output_data_check_report_path": "data_check_report_path",
        "trainer_config_generation_success": "config_generation_success",
        "trainer_config_generation_error": "config_generation_error",
        "trainer_config_explanation_path": "config_explanation_path",
        "trainer_training_success": "training_success",
        "trainer_training_execution_time": "training_execution_time",
        "trainer_training_task_id": "training_task_id",
        "trainer_training_final_status": "training_final_status",
        "train_output_training_log_path": "training_log_path",
        "train_output_training_report_path": "training_report_path",
        "train_output_training_error": "training_error",
        "trainer_current_training_status": "current_training_status",
    }
    for source, target in aliases.items():
        if source in trainer_state and target not in updates:
            updates[target] = trainer_state[source]

    return {
        key: _to_configer_update_item(value)
        for key, value in updates.items()
    }


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _fallback_context_id(
    state: Optional[Dict[str, Any]],
    thread_id: Optional[str],
    kwargs: Dict[str, Any],
) -> str:
    state_task_id = state.get("task_id") if isinstance(state, dict) else None
    return str(_first_non_empty(
        thread_id,
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state_task_id,
        "trainer-default",
    ))


def _fallback_output_dir(state: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> str:
    state_output_dir = state.get("output_dir") if isinstance(state, dict) else None
    return str(_first_non_empty(
        kwargs.get("output_dir"),
        os.getenv("OUTPUT_DIR"),
        state_output_dir,
        "./outputs",
    ))


def _resolve_trainer_version_id(kwargs: Dict[str, Any]) -> str | None:
    version_id = _first_non_empty(
        kwargs.get("version_id"),
        kwargs.get("trainer_version_id"),
        os.getenv("VERSION_ID"),
    )
    return str(version_id) if version_id else None


def _resolve_explicit_trainer_output_dir(kwargs: Dict[str, Any]) -> str | None:
    explicit_dir = _first_non_empty(
        kwargs.get("trainer_output_dir"),
        os.getenv("TRAINER_OUTPUT_DIR"),
    )
    if explicit_dir:
        return str(Path(str(explicit_dir)).resolve())
    return None


def _previous_training_completed(trainer_state: Dict[str, Any]) -> bool:
    if trainer_state.get("trainer_training_success") is True or trainer_state.get("training_success") is True:
        return True
    final_status = (
        trainer_state.get("trainer_training_final_status")
        or trainer_state.get("training_final_status")
        or {}
    )
    return isinstance(final_status, dict) and str(final_status.get("status") or "").lower() == "completed"


def _is_huggingface_model_dir(path_value: Any) -> bool:
    if not path_value:
        return False
    path = Path(str(path_value)).expanduser().resolve()
    if not path.is_dir() or not (path / "config.json").is_file():
        return False
    candidates = (
        "model.safetensors",
        "model.safetensors.index.json",
        "pytorch_model.bin",
        "pytorch_model.bin.index.json",
        "adapter_model.safetensors",
        "adapter_model.bin",
    )
    return any((path / name).is_file() for name in candidates)


def _prepare_fresh_trainer_round(
    state: Dict[str, Any],
    *,
    kwargs: Dict[str, Any],
) -> None:
    """Snapshot the previous successful Verl round and clear stale outputs."""
    trainer_state = state.setdefault("trainer", {})
    if trainer_state.get("train_framework") != "verl":
        return

    previous_version = str(trainer_state.get("trainer_version_id") or "")
    previous_round = int(trainer_state.get("trainer_round_index") or 0)
    previous_completed = _previous_training_completed(trainer_state)
    previous_config = trainer_state.get("train_config")
    if (
        previous_completed
        and trainer_state.get("verl_inherit_previous_config", True)
        and isinstance(previous_config, dict)
        and previous_config.get("framework") == "verl"
        and previous_config.get("stage") == "grpo"
    ):
        inherited_config = copy.deepcopy(previous_config)
    else:
        inherited_config = None

    previous_model = _first_non_empty(
        trainer_state.get("update_model_path"),
        trainer_state.get("trainer_best_checkpoint_path"),
    )
    explicit_model = _first_non_empty(
        kwargs.get("train_input_model_name"),
        kwargs.get("model_path"),
        os.getenv("TRAIN_MODEL_PATH"),
    )
    model_inheritance: Dict[str, Any] = {
        "applied": False,
        "source_version_id": previous_version or None,
        "source_model_path": str(previous_model) if previous_model else None,
    }
    inherited_model: str | None = None
    if explicit_model:
        model_inheritance["reason"] = "current round supplied an explicit model override"
    elif not trainer_state.get("verl_use_previous_best_model", True):
        model_inheritance["reason"] = "verl_use_previous_best_model is disabled"
    elif not previous_completed:
        model_inheritance["reason"] = "no previous successful training round"
    elif trainer_state.get("trainer_model_export_error"):
        model_inheritance["reason"] = "previous Verl model export failed"
    elif _is_huggingface_model_dir(previous_model):
        inherited_model = str(Path(str(previous_model)).expanduser().resolve())
        model_inheritance.update({
            "applied": True,
            "model_path": inherited_model,
            "reason": "previous best checkpoint is a loadable Hugging Face model",
        })
    else:
        model_inheritance["reason"] = "previous best checkpoint is not a loadable Hugging Face model directory"

    for field in _TRAINER_ROUND_TRANSIENT_FIELDS:
        trainer_state.pop(field, None)

    trainer_state["trainer_parent_version_id"] = previous_version if previous_completed else ""
    trainer_state["trainer_round_index"] = max(1, previous_round + 1)
    trainer_state["trainer_model_inheritance"] = model_inheritance
    if inherited_config is not None:
        trainer_state["_trainer_previous_config"] = inherited_config
    else:
        trainer_state.pop("_trainer_previous_config", None)
    if inherited_model:
        trainer_state["train_input_model_name"] = inherited_model
    explicit_eval = _first_non_empty(
        kwargs.get("train_input_eval_dataset_path"),
        kwargs.get("eval_dataset_path"),
        kwargs.get("verl_source_eval_dataset_path"),
        os.getenv("TRAIN_EVAL_DATASET_PATH"),
        os.getenv("VERL_SOURCE_EVAL_DATASET_PATH"),
        trainer_state.get("verl_source_eval_dataset_path"),
    )
    previous_reward = previous_config.get("loopai_reward") if isinstance(previous_config, dict) else None
    trainer_state["_trainer_eval_explicit"] = bool(explicit_eval)
    if previous_completed and isinstance(previous_reward, dict):
        trainer_state["_trainer_previous_reward"] = copy.deepcopy(previous_reward)
    else:
        trainer_state.pop("_trainer_previous_reward", None)
    reward_contract_changed = False
    if (
        previous_completed
        and isinstance(previous_reward, dict)
        and trainer_state.get("verl_reward_origin") != "auto"
    ):
        previous_mode = str(previous_reward.get("mode") or "")
        current_mode = str(trainer_state.get("verl_reward_mode") or "auto")
        previous_reward_signature = (
            previous_mode,
            str(previous_reward.get("preset") or ""),
            str(previous_reward.get("function_path") or "") if previous_mode == "custom" else "",
            str(previous_reward.get("function_name") or "") if previous_mode == "custom" else "",
        )
        current_reward_signature = (
            current_mode,
            str(trainer_state.get("verl_reward_preset") or ""),
            str(trainer_state.get("verl_reward_function_path") or "") if current_mode == "custom" else "",
            str(trainer_state.get("verl_reward_function_name") or "compute_score") if current_mode == "custom" else "",
        )
        reward_contract_changed = previous_reward_signature != current_reward_signature
    if (
        not trainer_state.get("verl_reuse_previous_validation", True)
        or reward_contract_changed
    ) and not explicit_eval:
        trainer_state["train_input_eval_dataset_path"] = ""


def _first_config_path(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value not in (None, "") else None


def _hydrate_state_from_approved_config(
    trainer_state: Dict[str, Any],
    config: Dict[str, Any],
) -> None:
    """Make preflight validate the exact paths/reward contract in approved YAML."""
    framework = str(config.get("framework") or trainer_state.get("train_framework") or "")
    stage = str(config.get("stage") or trainer_state.get("train_stage") or "")
    if framework:
        trainer_state["train_framework"] = framework
    if stage:
        trainer_state["train_stage"] = stage
    if framework != "verl":
        return

    overrides = config.get("overrides") or {}
    environment = config.get("environment") or {}
    result = config.get("result") or {}
    reward = config.get("loopai_reward") or {}
    train_path = _first_config_path(overrides.get("data.train_files"))
    eval_path = _first_config_path(overrides.get("data.val_files"))
    if train_path:
        trainer_state["train_input_dataset_path"] = train_path
    if eval_path:
        trainer_state["train_input_eval_dataset_path"] = eval_path
    if overrides.get("actor_rollout_ref.model.path"):
        trainer_state["train_input_model_name"] = str(overrides["actor_rollout_ref.model.path"])
    if overrides.get("actor_rollout_ref.rollout.name"):
        trainer_state["verl_rollout_backend"] = str(overrides["actor_rollout_ref.rollout.name"])
    if environment.get("verl_dir"):
        trainer_state["verl_dir"] = str(environment["verl_dir"])
    if environment.get("verl_env_path"):
        trainer_state["verl_env_path"] = str(environment["verl_env_path"])
    if environment.get("cuda_visible_devices") is not None:
        trainer_state["CUDA_VISIBLE_DEVICES"] = str(environment["cuda_visible_devices"])
    if reward.get("mode"):
        trainer_state["verl_reward_mode"] = str(reward["mode"])
    if reward.get("origin"):
        trainer_state["verl_reward_origin"] = str(reward["origin"])
    if reward.get("preset"):
        trainer_state["verl_reward_preset"] = str(reward["preset"])
    if reward.get("mode") == "custom":
        trainer_state["verl_reward_function_path"] = str(reward.get("function_path") or "")
        trainer_state["verl_reward_function_name"] = str(
            reward.get("function_name") or "compute_score"
        )
    reward_kwargs = overrides.get("+reward.custom_reward_function.reward_kwargs")
    if isinstance(reward_kwargs, dict):
        reward_kwargs = copy.deepcopy(reward_kwargs)
        reward_kwargs.pop("preset", None)
        trainer_state["verl_reward_kwargs"] = reward_kwargs
    if result.get("selection_metric"):
        trainer_state["verl_selection_metric"] = str(result["selection_metric"])
    if result.get("selection_mode"):
        trainer_state["verl_selection_mode"] = str(result["selection_mode"])


def inspect_prepared_trainer_config(config_path: str) -> Dict[str, Any]:
    """Return the exact YAML text and digest used by the approval workflow."""
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"prepared Trainer config does not exist: {path}")
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError(f"prepared Trainer config must be YAML: {path}")

    config_yaml = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(config_yaml)
    if not isinstance(parsed, dict):
        raise ValueError(f"prepared Trainer config must contain a YAML mapping: {path}")
    assert_no_retired_tracking(parsed)

    return {
        "config_path": str(path),
        "config_yaml": config_yaml,
        "config": parsed,
        "config_sha256": hashlib.sha256(config_yaml.encode("utf-8")).hexdigest(),
    }


def _update_trainer_task_state(runtime: Dict[str, Any], trainer_state: Dict[str, Any]) -> None:
    if not runtime.get("task_state_loaded"):
        return

    task_id = runtime.get("thread_id")
    db_path = runtime.get("db_path")
    if not task_id or not db_path:
        return

    updates = _normalize_trainer_task_state_updates(trainer_state)
    if not updates:
        return

    from loopai.skills.Configer import update_configer_task_state_config

    os.environ["DB_PATH"] = str(db_path)
    result = update_configer_task_state_config(
        section_name="trainer",
        updates=updates,
        task_id=str(task_id),
    )
    if not result.get("ok"):
        detail = (result.get("error") or {}).get("detail") or result.get("message")
        raise RuntimeError(detail or "failed to update Trainer task state config")


def finalize_trainer_result_state(
    result: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze artifacts and export a Verl checkpoint in an idempotent way."""
    if not isinstance(result, dict):
        return result

    trainer_state = result.setdefault("trainer", {})
    version_id = str(trainer_state.get("trainer_version_id") or "")
    if (
        isinstance(trainer_state.get("trainer_result_analysis"), dict)
        and str(trainer_state.get("trainer_result_analysis_version_id") or "") == version_id
    ):
        return result
    try:
        from loopai.skills.Trainer.results import analyze_results

        analysis_payload = analyze_results(
            task_id=task_id or result.get("task_id"),
            output_dir=result.get("output_dir", "./outputs"),
            trainer_state=trainer_state,
        )
        analysis_data = analysis_payload.get("data") or {}
        summary = analysis_data.get("summary") or {}
        best_checkpoint = analysis_data.get("best_checkpoint") or {}
        best_metric = analysis_data.get("best_metric") or {}
        if (
            trainer_state.get("train_framework") == "verl"
            and best_checkpoint.get("needs_merge")
            and ((trainer_state.get("train_config") or {}).get("result") or {}).get(
                "export_huggingface", True
            )
        ):
            from loopai.skills.Trainer.utils.verl_exporter import export_verl_checkpoint

            export_log_path = str(Path(trainer_state["trainer_output_dir"]) / "model_export.log")
            try:
                exported_path = export_verl_checkpoint(
                    str(trainer_state.get("train_output_config_path") or ""),
                    best_checkpoint,
                    export_log_path,
                    app_config={
                        "verl_dir": trainer_state.get("verl_dir"),
                        "verl_env_path": trainer_state.get("verl_env_path"),
                        "CUDA_VISIBLE_DEVICES": trainer_state.get("CUDA_VISIBLE_DEVICES"),
                    },
                )
                best_checkpoint["raw_checkpoint_path"] = best_checkpoint.get("path")
                best_checkpoint["path"] = exported_path
                best_checkpoint["needs_merge"] = False
                best_checkpoint["export_log_path"] = export_log_path
                summary["best_checkpoint_path"] = exported_path
                summary["model_export_status"] = "completed"
            except Exception as export_exc:
                trainer_state["trainer_model_export_error"] = str(export_exc)
                trainer_state["trainer_model_export_log_path"] = export_log_path
                summary["model_export_status"] = "failed"
        compact_analysis = {
            "ok": analysis_payload.get("ok"),
            "status": analysis_payload.get("status"),
            "message": analysis_payload.get("message"),
            "summary": summary,
            "best_checkpoint": best_checkpoint,
            "best_metric": best_metric,
            "model_export_error": trainer_state.get("trainer_model_export_error"),
            "error": analysis_payload.get("error"),
        }
        trainer_state["trainer_result_analysis"] = compact_analysis
        trainer_state["trainer_result_analysis_version_id"] = version_id
        trainer_state["trainer_result_summary"] = summary
        trainer_state["trainer_best_checkpoint"] = best_checkpoint
        trainer_state["trainer_best_metric"] = best_metric
        if best_checkpoint.get("path") and not best_checkpoint.get("needs_merge"):
            trainer_state["trainer_best_checkpoint_path"] = best_checkpoint["path"]
            trainer_state["update_model_path"] = best_checkpoint["path"]
        if analysis_data.get("checkpoints") and not trainer_state.get("training_checkpoints"):
            trainer_state["training_checkpoints"] = [
                item.get("name") for item in analysis_data["checkpoints"] if item.get("name")
            ]
        if analysis_data.get("metric_records") and not trainer_state.get("training_step_losses"):
            trainer_state["training_step_losses"] = analysis_data["metric_records"]
    except Exception as exc:
        trainer_state["trainer_result_analysis"] = {
            "ok": False,
            "status": "failed",
            "message": "Trainer result analysis failed.",
            "error": {
                "type": type(exc).__name__,
                "detail": str(exc),
            },
        }
        trainer_state["trainer_result_analysis_version_id"] = version_id
    return result


def run_trainer_standalone(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    config_path: Optional[str] = None,
    emit_result: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the Trainer pipeline from the skill layer."""
    prepare_only = bool(kwargs.get("prepare_only", False))
    prepared_config_path = kwargs.get("prepared_config_path")
    expected_config_sha256 = str(kwargs.get("expected_config_sha256") or "").strip().lower()
    if prepare_only and prepared_config_path:
        raise ValueError("prepare_only and prepared_config_path cannot be used together")

    try:
        runtime = resolve_trainer_runtime_config(
            state=state,
            thread_id=thread_id,
            config_path=config_path,
            **kwargs,
        )
    except Exception as exc:
        fallback_task_id = _fallback_context_id(state, thread_id, kwargs)
        fallback_output_root = _fallback_output_dir(state, kwargs)
        explicit_version_id = _resolve_trainer_version_id(kwargs)
        fallback_state = {
            "task_id": fallback_task_id,
            "output_dir": fallback_output_root,
            "trainer": strip_retired_tracking_fields(
                dict((state or {}).get("trainer") or {}) if isinstance(state, dict) else {}
            ),
        }
        fallback_writer = prepare_trainer_run(
            fallback_state,
            version_id=explicit_version_id,
            trainer_output_dir=_resolve_explicit_trainer_output_dir(kwargs),
            force_new=explicit_version_id is None,
        )
        fallback_version_id = str(fallback_writer.version_id)
        fallback_output_dir = fallback_state["trainer"]["trainer_output_dir"]
        fallback_writer.set_running({
            "current": "trainer.run",
            "message": "Trainer skill started.",
            "data": {
                "task_id": fallback_task_id,
                "trainer_version_id": fallback_version_id,
                "trainer_output_dir": fallback_output_dir,
            },
            "progress": 0.0,
        })
        emit_error(
            exc,
            code=ErrorCode.CONFIG_ERROR,
            recoverable=True,
            stream_writer=fallback_writer,
            message="Trainer runtime config is incomplete.",
            exit_process=emit_result,
            print_payload=emit_result,
        )
        raise

    resolved_state = runtime["state"]
    trainer_state = resolved_state.setdefault("trainer", {})
    explicit_version_id = _resolve_trainer_version_id(kwargs)
    if prepared_config_path and explicit_version_id is None:
        existing_version = trainer_state.get("trainer_version_id")
        explicit_version_id = str(existing_version) if existing_version else None
    if explicit_version_id is None and not prepared_config_path:
        _prepare_fresh_trainer_round(resolved_state, kwargs=kwargs)
    event_writer = prepare_trainer_run(
        resolved_state,
        version_id=explicit_version_id,
        trainer_output_dir=_resolve_explicit_trainer_output_dir(kwargs),
        force_new=explicit_version_id is None,
    )
    version_id = str(event_writer.version_id)
    trainer_output_dir = trainer_state["trainer_output_dir"]
    event_writer.set_running({
        "current": "trainer.run",
        "message": "Trainer skill started.",
        "data": {
            "task_id": runtime["thread_id"],
            "trainer_version_id": version_id,
            "trainer_output_dir": trainer_output_dir,
        },
        "progress": 0.0,
    })
    trainer_state["trainer_event_log_path"] = str(event_writer.event_path)

    missing_fields = runtime["missing_fields"]
    if missing_fields:
        prefill_guide = runtime.get("prefill_guide") or build_trainer_prefill_guide(resolved_state)
        trainer_state["trainer_missing_fields"] = missing_fields
        trainer_state["trainer_prefill_guide"] = prefill_guide
        exc = ValueError(f"missing required Trainer config fields: {', '.join(missing_fields)}")
        payload = emit_error(
            exc,
            code=ErrorCode.CONFIG_ERROR,
            recoverable=True,
            stream_writer=event_writer,
            message="Trainer runtime config is incomplete.",
            exit_process=emit_result,
            print_payload=emit_result,
        )
        trainer_state["trainer_result"] = payload
        trainer_state["trainer_last_error"] = payload["error"]
        _update_trainer_task_state(runtime, trainer_state)
        raise exc

    # Always set both graph-control flags so a reused LangGraph thread cannot
    # inherit approval-mode state from an earlier invocation.
    trainer_state["_trainer_prepare_only"] = prepare_only
    trainer_state["_trainer_use_prepared_config"] = False

    if prepared_config_path:
        try:
            prepared_config = inspect_prepared_trainer_config(str(prepared_config_path))
            actual_digest = prepared_config["config_sha256"]
            if not expected_config_sha256:
                raise ValueError("expected_config_sha256 is required for an approved Trainer config")
            if actual_digest != expected_config_sha256:
                raise ValueError(
                    "prepared Trainer config changed after approval: "
                    f"expected sha256 {expected_config_sha256}, got {actual_digest}"
                )
            trainer_state["train_output_config_path"] = prepared_config["config_path"]
            trainer_state["train_config"] = prepared_config["config"]
            _hydrate_state_from_approved_config(trainer_state, prepared_config["config"])
            # Carry the exact approved bytes into the trusted worker request.
            # The worker materializes this snapshot instead of rereading a
            # user-editable source path after the approval digest check.
            trainer_state["_trainer_approved_config_yaml"] = prepared_config["config_yaml"]
            trainer_state["_trainer_approved_config_sha256"] = actual_digest
            trainer_state["trainer_config_generation_success"] = True
            trainer_state["_trainer_use_prepared_config"] = True
        except Exception as exc:
            payload = emit_error(
                exc,
                code=ErrorCode.CONFIG_ERROR,
                recoverable=True,
                stream_writer=event_writer,
                message="Prepared Trainer config validation failed.",
                exit_process=emit_result,
                print_payload=emit_result,
            )
            trainer_state["trainer_result"] = payload
            trainer_state["trainer_last_error"] = payload["error"]
            _update_trainer_task_state(runtime, trainer_state)
            raise

    if not prepare_only:
        trainer_state["_trainer_worker_runtime"] = {
            "task_state_loaded": runtime.get("task_state_loaded", False),
            "thread_id": runtime.get("thread_id"),
            "db_path": runtime.get("db_path"),
        }

    from loopai.memory import checkpointer, store
    from loopai.skills.Trainer.trainer_agent import TrainerAgent

    trainer = TrainerAgent(checkpointer=checkpointer, store=store)
    graph = trainer()
    graph_config = {
        "configurable": {
            "thread_id": kwargs.get("graph_thread_id") or f"trainer_{runtime['thread_id']}_{version_id}",
        }
    }

    try:
        result = graph.invoke(resolved_state, config=graph_config)
    except Exception as exc:
        payload = emit_error(
            exc,
            code=ErrorCode.UNHANDLED_EXCEPTION,
            recoverable=True,
            stream_writer=event_writer,
            message="Trainer skill failed.",
            exit_process=emit_result,
            print_payload=emit_result,
        )
        resolved_state.setdefault("trainer", {})["trainer_result"] = payload
        resolved_state.setdefault("trainer", {})["trainer_last_error"] = payload["error"]
        _update_trainer_task_state(runtime, resolved_state.setdefault("trainer", {}))
        raise

    trainer_state = result.setdefault("trainer", {}) if isinstance(result, dict) else {}
    trainer_state.pop("_trainer_prepare_only", None)
    trainer_state.pop("_trainer_use_prepared_config", None)
    trainer_state.pop("_trainer_worker_runtime", None)
    trainer_state.pop("_trainer_approved_config_yaml", None)
    trainer_state.pop("_trainer_approved_config_sha256", None)
    trainer_state.pop("_trainer_previous_config", None)
    trainer_state.pop("_trainer_previous_reward", None)
    trainer_state.pop("_trainer_eval_explicit", None)
    trainer_state.pop("_verl_source_dataset_explicit", None)
    trainer_state.pop("_verl_source_dataset_replaced", None)

    if prepare_only:
        if not trainer_state.get("trainer_config_generation_success"):
            detail = _first_non_empty(
                trainer_state.get("trainer_data_check_error"),
                trainer_state.get("trainer_config_generation_error"),
                "Trainer did not generate a configuration",
            )
            exc = ValueError(str(detail))
            payload = emit_error(
                exc,
                code=ErrorCode.INVALID_INPUT,
                recoverable=True,
                stream_writer=event_writer,
                message="Trainer config preparation failed.",
                exit_process=emit_result,
                print_payload=emit_result,
            )
            trainer_state["trainer_result"] = payload
            trainer_state["trainer_last_error"] = payload["error"]
            _update_trainer_task_state(runtime, trainer_state)
            raise exc
        try:
            prepared_config = inspect_prepared_trainer_config(
                str(trainer_state.get("train_output_config_path") or "")
            )
        except Exception as exc:
            payload = emit_error(
                exc,
                code=ErrorCode.CONFIG_ERROR,
                recoverable=True,
                stream_writer=event_writer,
                message="Trainer config preparation failed.",
                exit_process=emit_result,
                print_payload=emit_result,
            )
            trainer_state["trainer_result"] = payload
            trainer_state["trainer_last_error"] = payload["error"]
            _update_trainer_task_state(runtime, trainer_state)
            raise

        preparation_data = {
            "task_id": runtime["thread_id"],
            "trainer_version_id": trainer_state.get("trainer_version_id"),
            "trainer_parent_version_id": trainer_state.get("trainer_parent_version_id"),
            "trainer_round_index": trainer_state.get("trainer_round_index"),
            "trainer_output_dir": trainer_state.get("trainer_output_dir"),
            "trainer_model_inheritance": trainer_state.get("trainer_model_inheritance"),
            "verl_data_manifest_path": trainer_state.get("verl_data_manifest_path"),
            "verl_data_prepare_result": trainer_state.get("verl_data_prepare_result"),
            "verl_reward_recommendation": trainer_state.get("verl_reward_recommendation"),
            "approval_required": True,
            **prepared_config,
        }
        payload = emit_success(
            data=preparation_data,
            message="Trainer config prepared; explicit user approval is required before training.",
            stream_writer=event_writer,
            exit_process=emit_result,
            print_payload=emit_result,
        )
        trainer_state["trainer_result"] = payload
        trainer_state["trainer_last_error"] = {}
        trainer_state["trainer_event_log_path"] = str(event_writer.event_path)
        _update_trainer_task_state(runtime, trainer_state)
        return result

    if isinstance(result, dict):
        finalize_trainer_result_state(result, task_id=runtime["thread_id"])

    training_success = trainer_state.get("trainer_training_success") is True
    if not training_success:
        existing_payload = trainer_state.get("trainer_result")
        if isinstance(existing_payload, dict) and existing_payload.get("ok") is False:
            payload = existing_payload
            event_writer.set_failed(payload)
            if emit_result:
                print(json.dumps(payload, ensure_ascii=False))
        else:
            final_status = trainer_state.get("trainer_training_final_status") or {}
            terminal_status = str(final_status.get("status") or "failed").lower()
            error_detail = (
                trainer_state.get("train_output_training_error")
                or final_status.get("error_message")
                or f"training finished with status: {terminal_status}"
            )
            payload = emit_error(
                RuntimeError(str(error_detail)),
                code=(
                    ErrorCode.INTERRUPTED
                    if terminal_status == "cancelled"
                    else ErrorCode.UNHANDLED_EXCEPTION
                ),
                recoverable=terminal_status != "cancelled",
                stream_writer=event_writer,
                message="Trainer skill failed.",
                exit_process=False,
                print_payload=emit_result,
            )
        trainer_state["trainer_result"] = payload
        trainer_state["trainer_last_error"] = payload["error"]
        trainer_state["trainer_event_log_path"] = str(event_writer.event_path)
        _update_trainer_task_state(runtime, trainer_state)
        if emit_result:
            raise SystemExit(1)
        return result

    success_data = {
        "task_id": runtime["thread_id"],
        "trainer_version_id": trainer_state.get("trainer_version_id"),
        "trainer_parent_version_id": trainer_state.get("trainer_parent_version_id"),
        "trainer_round_index": trainer_state.get("trainer_round_index"),
        "trainer_output_dir": trainer_state.get("trainer_output_dir"),
        "trainer_training_task_id": trainer_state.get("trainer_training_task_id"),
        "trainer_training_success": trainer_state.get("trainer_training_success"),
        "trainer_training_final_status": trainer_state.get("trainer_training_final_status"),
        "train_output_config_path": trainer_state.get("train_output_config_path"),
        "train_output_training_log_path": trainer_state.get("train_output_training_log_path"),
        "train_output_training_report_path": trainer_state.get("train_output_training_report_path"),
        "trainer_result_summary": trainer_state.get("trainer_result_summary"),
        "trainer_best_checkpoint": trainer_state.get("trainer_best_checkpoint"),
        "trainer_model_export_error": trainer_state.get("trainer_model_export_error"),
        "trainer_model_export_log_path": trainer_state.get("trainer_model_export_log_path"),
    }
    payload = emit_success(
        data=success_data,
        message="Trainer skill completed.",
        stream_writer=event_writer,
        exit_process=False,
        print_payload=emit_result,
    )
    if isinstance(result, dict):
        result.setdefault("trainer", {})["trainer_result"] = payload
        result.setdefault("trainer", {})["trainer_last_error"] = {}
        result.setdefault("trainer", {})["trainer_event_log_path"] = str(event_writer.event_path)
        _update_trainer_task_state(runtime, result.setdefault("trainer", {}))

    if emit_result:
        raise SystemExit(0)

    return result
