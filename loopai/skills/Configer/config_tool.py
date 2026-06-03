from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from loopai.common.exception import ErrorCode, build_error_payload, build_success_payload


DEFAULT_TASK_ENV_KEYS = ("task_id", "TASK_ID")
PROTECTED_DEFAULT_FIELDS = {"task_id"}


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


def _normalize_section_updates_payload(updates: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(updates, str):
        payload = json.loads(updates)
    elif isinstance(updates, dict):
        payload = updates
    else:
        raise TypeError("updates must be a JSON string or dict")

    if not isinstance(payload, dict):
        raise ValueError("updates payload must be a dict")

    return payload


def _validate_section_updates_against_schema(
    section_name: str,
    updates: dict[str, Any],
    schema_config: dict[str, Any],
) -> dict[str, Any]:
    if section_name == "system":
        raise ValueError("system section cannot be modified by Configer skill")
    if section_name not in schema_config:
        raise ValueError(f"unknown state section: {section_name}")
    if not isinstance(updates, dict):
        raise ValueError(f"section '{section_name}' updates must be a dict")

    allowed_fields = set(schema_config[section_name].keys())
    if section_name == "default":
        blocked_fields = PROTECTED_DEFAULT_FIELDS
    else:
        blocked_fields = set()

    normalized_section: dict[str, Any] = {}
    for field_name, field_value in updates.items():
        if field_name in blocked_fields:
            raise ValueError(f"field '{section_name}.{field_name}' cannot be modified by Configer skill")
        if field_name not in allowed_fields:
            raise ValueError(f"unknown config field: {section_name}.{field_name}")
        normalized_section[field_name] = field_value
    return normalized_section


def _extract_field_config(
    section_name: str,
    section_config: Any,
    field_name: str | None = None,
) -> Any:
    if field_name is None:
        return section_config
    if not isinstance(section_config, dict):
        raise ValueError(f"section '{section_name}' does not support field lookup")
    if field_name not in section_config:
        raise ValueError(f"unknown config field: {section_name}.{field_name}")
    return section_config[field_name]


async def get_configer_state_schema_async(
    language: str = "zh",
    section_name: str | None = None,
) -> dict[str, Any]:
    return get_configer_state_schema(language, section_name)


async def get_configer_state_config_async(
    section_name: str,
    field_name: str | None = None,
) -> dict[str, Any]:
    return get_configer_state_config(section_name, field_name)


async def update_configer_state_config_async(
    section_name: str,
    updates: str | dict[str, Any],
) -> dict[str, Any]:
    return update_configer_state_config(section_name, updates)


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def get_configer_state_schema(
    language: str = "zh",
    section_name: str | None = None,
) -> dict[str, Any]:
    try:
        from loopai.schema.states import get_state_config_schema
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=False, message="Failed to load non-system state schema.")

    states_schema = get_state_config_schema(language)
    if section_name is not None:
        if section_name not in states_schema:
            exc = ValueError(f"unknown state section: {section_name}")
            code = ErrorCode.INVALID_INPUT if ErrorCode else "INVALID_INPUT"
            return build_error_payload(exc, code=code, recoverable=False, message="Requested state section does not exist.")
        states_schema = {section_name: states_schema[section_name]}

    return build_success_payload(
        data={"states": states_schema},
        message="Non-system state schema loaded.",
    )


def get_configer_state_config(
    section_name: str,
    field_name: str | None = None,
) -> dict[str, Any]:
    try:
        from loopai.common.db_tool import get_default_states_config_sync, get_task_states_config_sync

        db_path = _get_db_path_from_env()
        task_id = _get_task_id_from_env()
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=False, message="Invalid Configer read request.")

    try:
        if task_id:
            current_states = get_task_states_config_sync(db_path, task_id, section_name=section_name)
            scope = "task"
        else:
            current_states = get_default_states_config_sync(db_path, section_name=section_name)
            scope = "default"

        section_config = current_states["config"]
        result_config = _extract_field_config(section_name, section_config, field_name)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=True, message="Failed to load non-system state config.")

    return build_success_payload(
        data={
            "scope": scope,
            "task_id": task_id,
            "section_name": section_name,
            "field_name": field_name,
            "config": result_config,
        },
        message="Non-system state config loaded.",
    )


def update_configer_state_config(
    section_name: str,
    updates: str | dict[str, Any],
) -> dict[str, Any]:
    try:
        from loopai.common.db_tool import (
            get_default_states_config_sync,
            get_task_states_config_sync,
            update_default_state_section_config_sync,
            update_task_state_section_config_sync,
        )

        db_path = _get_db_path_from_env()
        task_id = _get_task_id_from_env()
        parsed_updates = _normalize_section_updates_payload(updates)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=False, message="Invalid Configer update request.")

    try:
        if task_id:
            current_states = get_task_states_config_sync(db_path, task_id)
            scope = "task"
        else:
            current_states = get_default_states_config_sync(db_path)
            scope = "default"

        validated_updates = _validate_section_updates_against_schema(
            section_name,
            parsed_updates,
            current_states["config"],
        )

        if task_id:
            updated = update_task_state_section_config_sync(db_path, task_id, section_name, validated_updates)
        else:
            updated = update_default_state_section_config_sync(db_path, section_name, validated_updates)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=True, message="Failed to update non-system state config.")

    return build_success_payload(
        data={
            "scope": scope,
            "task_id": task_id,
            "section_name": section_name,
            "config": updated["config"],
        },
        message="Non-system state config updated.",
    )
