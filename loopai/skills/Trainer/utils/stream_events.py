from __future__ import annotations

from pathlib import Path
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


def get_trainer_version_id(state: dict[str, Any]) -> str | None:
    trainer_state = state.get("trainer", {}) if isinstance(state, dict) else {}
    version_id = (
        trainer_state.get("trainer_version_id")
        or trainer_state.get("version_id")
        or trainer_state.get("trainer_task_id")
        or trainer_state.get("trainer_training_task_id")
        or trainer_state.get("training_task_id")
    )
    return str(version_id) if version_id else None


def get_trainer_event_dir(state: dict[str, Any]) -> str | None:
    if not isinstance(state, dict):
        return None
    output_dir = state.get("output_dir") or "./outputs"
    return str(Path(str(output_dir)) / get_trainer_context_id(state))


def get_trainer_event_log_path(state: dict[str, Any]) -> str:
    event_dir = get_trainer_event_dir(state)
    if event_dir:
        return f"{event_dir.rstrip('/')}/{TRAINER_CURRENT}.pkl"
    return f"./outputs/default/{TRAINER_CURRENT}.pkl"


def prepare_trainer_run(
    state: dict[str, Any],
    *,
    version_id: str | None = None,
    trainer_output_dir: str | None = None,
    force_new: bool = False,
):
    """Bind one writer-generated version ID to all Trainer run identifiers."""
    trainer_state = state.setdefault("trainer", {})
    context_id = get_trainer_context_id(state)
    output_root = str(state.get("output_dir") or "./outputs")

    explicit_version_id = str(version_id).strip() if version_id else None
    existing_version_id = (
        trainer_state.get("trainer_version_id")
        or trainer_state.get("trainer_task_id")
        or trainer_state.get("trainer_training_task_id")
        or trainer_state.get("training_task_id")
    )
    initial_version_id = explicit_version_id
    if initial_version_id is None and not force_new and existing_version_id:
        initial_version_id = str(existing_version_id)

    seed_writer = get_event_writer(
        name=TRAINER_CURRENT,
        context_id=context_id,
        log_file_path=output_root,
        version_id=initial_version_id,
        event_dir=get_trainer_event_dir(state),
    )
    if seed_writer.version_id is None:
        seed_writer.refresh_version_id()
    run_id = str(seed_writer.version_id)

    if trainer_output_dir:
        run_dir = Path(trainer_output_dir).expanduser().resolve()
    else:
        run_dir = (
            Path(output_root).expanduser().resolve()
            / context_id
            / TRAINER_CURRENT
            / run_id
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    for field in (
        "trainer_version_id",
        "trainer_task_id",
        "trainer_training_task_id",
        "training_task_id",
    ):
        old_value = trainer_state.get(field)
        if old_value and str(old_value) != run_id:
            logger.warning(f"统一 Trainer 运行 ID: {field}={old_value} -> {run_id}")
        trainer_state[field] = run_id

    trainer_state["trainer_output_dir"] = str(run_dir)
    trainer_state["output_dir"] = str(run_dir)

    return get_event_writer(
        name=TRAINER_CURRENT,
        context_id=context_id,
        log_file_path=output_root,
        version_id=run_id,
        event_dir=get_trainer_event_dir(state),
    )


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
        version_id = event.version_id or get_trainer_version_id(state)
        writer = prepare_trainer_run(
            state,
            version_id=version_id,
        )
        event.version_id = writer.version_id
        persisted_payload = event.json()
        if event.status == "completed":
            writer.set_completed(persisted_payload)
        elif event.status in {"failed", "cancelled"}:
            writer.set_failed(persisted_payload)
        else:
            writer.set_running(persisted_payload)
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
            version_id=get_trainer_version_id(state),
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
        version_id=get_trainer_version_id(state),
        error=error,
    )
    _persist_trainer_event(state, event)
    payload = event.json()
    try:
        from loopai.skills.Trainer.utils.persistent_worker import record_worker_event

        record_worker_event(state, payload)
    except Exception as exc:
        logger.warning(f"更新 Trainer Worker 状态失败: {exc}")
    writer(payload)
    return event
