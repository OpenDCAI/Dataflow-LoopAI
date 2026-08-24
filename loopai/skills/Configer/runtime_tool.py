from __future__ import annotations

import os

from loopai.common.exception import ErrorCode, build_error_payload, build_success_payload


DEFAULT_TASK_ENV_KEYS = ("task_id", "TASK_ID")


def _get_db_path_from_env() -> str:
    db_path = os.getenv("DB_PATH")
    if not db_path:
        raise ValueError("DB_PATH environment variable is required")
    return db_path


def _get_task_id_from_env() -> str | None:
    for key in DEFAULT_TASK_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


def _require_task_id(task_id: str | None = None) -> str:
    resolved_task_id = str(task_id).strip() if task_id is not None else (_get_task_id_from_env() or "").strip()
    if not resolved_task_id:
        raise ValueError("TASK_ID or task_id environment variable is required")
    return resolved_task_id


def get_runtime_task_node_latest(
    node_name: str,
    task_id: str | None = None,
) -> dict:
    try:
        from loopai.common.db_tool.runtime import get_latest_task_runtime_sync

        db_path = _get_db_path_from_env()
        resolved_task_id = _require_task_id(task_id)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=False, message="Invalid runtime latest request.")

    try:
        runtime = get_latest_task_runtime_sync(db_path, resolved_task_id, node_name)
        if runtime is None:
            raise ValueError(f"latest runtime not found for task_id={resolved_task_id}, node_name={node_name}")
    except Exception as exc:
        code = ErrorCode.NOT_FOUND if ErrorCode else "NOT_FOUND"
        return build_error_payload(exc, code=code, recoverable=True, message="Failed to load latest task runtime.")

    return build_success_payload(
        data={
            "task_id": resolved_task_id,
            "node_name": node_name,
            "runtime": runtime,
        },
        message="Latest task runtime loaded.",
    )


def get_runtime_task_node_history(
    node_name: str,
    task_id: str | None = None,
) -> dict:
    try:
        from loopai.common.db_tool.runtime import list_task_runtime_history_sync

        db_path = _get_db_path_from_env()
        resolved_task_id = _require_task_id(task_id)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=False, message="Invalid runtime history request.")

    try:
        runtimes = list_task_runtime_history_sync(db_path, resolved_task_id, node_name)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=True, message="Failed to load task runtime history.")

    return build_success_payload(
        data={
            "task_id": resolved_task_id,
            "node_name": node_name,
            "runtimes": runtimes,
        },
        message="Task runtime history loaded.",
    )


def get_runtime_task_latest_runtimes(
    task_id: str | None = None,
) -> dict:
    try:
        from loopai.common.db_tool.runtime import list_latest_task_runtimes_sync

        db_path = _get_db_path_from_env()
        resolved_task_id = _require_task_id(task_id)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=False, message="Invalid latest runtimes request.")

    try:
        runtimes = list_latest_task_runtimes_sync(db_path, resolved_task_id)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=True, message="Failed to load latest task runtimes.")

    return build_success_payload(
        data={
            "task_id": resolved_task_id,
            "runtimes": runtimes,
        },
        message="Latest task runtimes loaded.",
    )


__all__ = [
    "get_runtime_task_latest_runtimes",
    "get_runtime_task_node_history",
    "get_runtime_task_node_latest",
]
