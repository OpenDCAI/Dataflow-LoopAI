from __future__ import annotations

from typing import Any

from loopai.common.event_tool import StreamEvent, get_event_writer
from loopai.common.exception import (
    ErrorCode,
    build_error_payload,
    build_success_payload,
)
from loopai.logger import get_logger


TRAINER_CURRENT = "trainer"
logger = get_logger()


def get_trainer_context_id(state: dict[str, Any]) -> str:
    trainer_state = state.get("trainer", {}) if isinstance(state, dict) else {}
    context_id = (
        state.get("task_id")
        or trainer_state.get("trainer_task_id")
        or trainer_state.get("trainer_training_task_id")
        or "default"
    )
    return str(context_id)


def get_trainer_event_log_path(state: dict[str, Any]) -> str:
    output_dir = state.get("output_dir") or state.get("trainer", {}).get("output_dir") or "./outputs"
    context_id = get_trainer_context_id(state)
    return f"{output_dir.rstrip('/')}/{context_id}/{TRAINER_CURRENT}.pkl"


def build_trainer_success_payload(
    data: Any = None,
    *,
    message: str = "Trainer completed.",
) -> dict[str, Any]:
    return build_success_payload(data=data, message=message)


def build_trainer_error_payload(
    exc: BaseException,
    *,
    code: str | ErrorCode = ErrorCode.UNHANDLED_EXCEPTION,
    recoverable: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    return build_error_payload(
        exc,
        code=code,
        recoverable=recoverable,
        message=message,
    )


def build_trainer_error(
    exc: BaseException,
    *,
    code: str | ErrorCode = ErrorCode.UNHANDLED_EXCEPTION,
    recoverable: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    error = build_error_payload(
        exc,
        code=code,
        recoverable=recoverable,
        message=message,
    )["error"]
    return error


def record_trainer_result(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    trainer_state = state.setdefault("trainer", {})
    trainer_state["trainer_result"] = payload
    trainer_state["trainer_last_error"] = payload.get("error") or {}
    return state


def _persist_trainer_event(state: dict[str, Any], event: StreamEvent) -> None:
    try:
        output_dir = state.get("output_dir") or state.get("trainer", {}).get("output_dir") or "./outputs"
        context_id = event.context_id or get_trainer_context_id(state)
        writer = get_event_writer(
            name=TRAINER_CURRENT,
            context_id=context_id,
            log_file_path=output_dir,
        )
        writer(event)
        state.setdefault("trainer", {})["trainer_event_log_path"] = str(writer.event_path)
    except Exception as exc:
        logger.warning(f"写入 Trainer 事件日志失败: {exc}")


def emit_trainer_event(
    writer: Any,
    state: dict[str, Any],
    node: str,
    status: str,
    *,
    progress: float | None = None,
    progress_num: int | None = None,
    total: int | None = None,
    message: str | None = None,
    data: Any = None,
    error: Any = None,
) -> StreamEvent | None:
    if not writer:
        event = StreamEvent(
            current=TRAINER_CURRENT,
            progress=progress,
            progress_num=progress_num,
            total=total,
            message=message,
            data=data,
            node=node,
            status=status,
            context_id=get_trainer_context_id(state),
            error=error,
        )
        _persist_trainer_event(state, event)
        return event

    event = StreamEvent(
        current=TRAINER_CURRENT,
        progress=progress,
        progress_num=progress_num,
        total=total,
        message=message,
        data=data,
        node=node,
        status=status,
        context_id=get_trainer_context_id(state),
        error=error,
    )
    payload = event.json()
    _persist_trainer_event(state, event)
    writer(payload)
    return event
