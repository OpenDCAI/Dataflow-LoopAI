from __future__ import annotations

import os
from typing import Any, Dict, Optional

from loopai.common.event_tool import get_event_writer
from loopai.common.exception import ErrorCode, emit_error, emit_success
from loopai.skills.Trainer.runtime_config import resolve_trainer_runtime_config


_TRAINER_TASK_STATE_UPDATE_FIELDS = {
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
    "swanlab_url",
    "train_output_swanlab_log_path",
    "trainer_event_log_path",
    "trainer_result",
    "trainer_last_error",
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


def run_trainer_standalone(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    config_path: Optional[str] = None,
    emit_result: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the existing TrainerAgent from the skill layer."""
    try:
        runtime = resolve_trainer_runtime_config(
            state=state,
            thread_id=thread_id,
            config_path=config_path,
            **kwargs,
        )
    except Exception as exc:
        fallback_writer = get_event_writer(
            name="trainer",
            context_id=_fallback_context_id(state, thread_id, kwargs),
            log_file_path=_fallback_output_dir(state, kwargs),
        )
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
    event_writer = get_event_writer(
        name="trainer",
        context_id=runtime["thread_id"],
        log_file_path=resolved_state.get("output_dir", "./outputs"),
    )
    resolved_state.setdefault("trainer", {})["trainer_event_log_path"] = str(event_writer.event_path)

    missing_fields = runtime["missing_fields"]
    if missing_fields:
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
        resolved_state.setdefault("trainer", {})["trainer_result"] = payload
        resolved_state.setdefault("trainer", {})["trainer_last_error"] = payload["error"]
        _update_trainer_task_state(runtime, resolved_state.setdefault("trainer", {}))
        raise exc

    from loopai.agents.Trainer.trainer_agent import TrainerAgent
    from loopai.memory import checkpointer, store

    trainer = TrainerAgent(checkpointer=checkpointer, store=store)
    graph = trainer()
    graph_config = {
        "configurable": {
            "thread_id": kwargs.get("graph_thread_id") or f"trainer_{runtime['thread_id']}",
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

    trainer_state = result.get("trainer", {}) if isinstance(result, dict) else {}
    success_data = {
        "task_id": runtime["thread_id"],
        "trainer_training_task_id": trainer_state.get("trainer_training_task_id"),
        "trainer_training_success": trainer_state.get("trainer_training_success"),
        "trainer_training_final_status": trainer_state.get("trainer_training_final_status"),
        "train_output_config_path": trainer_state.get("train_output_config_path"),
        "train_output_training_log_path": trainer_state.get("train_output_training_log_path"),
        "train_output_training_report_path": trainer_state.get("train_output_training_report_path"),
    }
    payload = emit_success(
        data=success_data,
        message="Trainer skill completed.",
        stream_writer=event_writer,
        exit_process=emit_result,
        print_payload=emit_result,
    )
    if isinstance(result, dict):
        result.setdefault("trainer", {})["trainer_result"] = payload
        result.setdefault("trainer", {})["trainer_last_error"] = {}
        result.setdefault("trainer", {})["trainer_event_log_path"] = str(event_writer.event_path)
        _update_trainer_task_state(runtime, result.setdefault("trainer", {}))

    return result
