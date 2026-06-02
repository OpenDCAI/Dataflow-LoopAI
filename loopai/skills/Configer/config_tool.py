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


def _normalize_updates_payload(updates: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(updates, str):
        payload = json.loads(updates)
    elif isinstance(updates, dict):
        payload = updates
    else:
        raise TypeError("updates must be a JSON string or dict")

    if "states" in payload:
        payload = payload["states"]

    if not isinstance(payload, dict):
        raise ValueError("updates payload must be a dict of state sections")

    return payload


def _validate_updates_against_schema(
    updates: dict[str, Any],
    schema_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for section_name, section_updates in updates.items():
        if section_name == "system":
            raise ValueError("system section cannot be modified by Configer skill")
        if section_name not in schema_config:
            raise ValueError(f"unknown state section: {section_name}")
        if not isinstance(section_updates, dict):
            raise ValueError(f"section '{section_name}' must be a dict")

        allowed_fields = set(schema_config[section_name].keys())
        if section_name == "default":
            blocked_fields = PROTECTED_DEFAULT_FIELDS
        else:
            blocked_fields = set()

        normalized_section: dict[str, Any] = {}
        for field_name, field_value in section_updates.items():
            if field_name in blocked_fields:
                raise ValueError(f"field '{section_name}.{field_name}' cannot be modified by Configer skill")
            if field_name not in allowed_fields:
                raise ValueError(f"unknown config field: {section_name}.{field_name}")
            normalized_section[field_name] = field_value
        normalized[section_name] = normalized_section
    return normalized


async def get_configer_state_schema_async(
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
        states_schema = {
            section_name: states_schema[section_name],
        }

    return build_success_payload(
        data={
            "states": states_schema,
        },
        message="Non-system state schema loaded.",
    )


async def update_configer_state_config_async(updates: str | dict[str, Any]) -> dict[str, Any]:
    try:
        from loopai.common.db_tool import (
            get_default_states_config,
            get_task_states_config,
            sqlite_db_session,
            update_default_state_section_config,
            update_task_state_section_config,
        )

        db_path = _get_db_path_from_env()
        task_id = _get_task_id_from_env()
        parsed_updates = _normalize_updates_payload(updates)
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=False, message="Invalid Configer update request.")

    try:
        async with sqlite_db_session(db_path):
            if task_id:
                current_states = await get_task_states_config(task_id)
                scope = "task"
            else:
                current_states = await get_default_states_config()
                scope = "default"

            validated_updates = _validate_updates_against_schema(parsed_updates, current_states["config"])

            updated_sections: dict[str, Any] = {}
            for section_name, section_updates in validated_updates.items():
                if task_id:
                    updated = await update_task_state_section_config(task_id, section_name, section_updates)
                else:
                    updated = await update_default_state_section_config(section_name, section_updates)
                updated_sections[section_name] = updated["config"]

            if task_id:
                latest_states = await get_task_states_config(task_id)
            else:
                latest_states = await get_default_states_config()
    except Exception as exc:
        code = ErrorCode.CONFIG_ERROR if ErrorCode else "CONFIG_ERROR"
        return build_error_payload(exc, code=code, recoverable=True, message="Failed to update non-system state config.")

    return build_success_payload(
        data={
            "scope": scope,
            "task_id": task_id,
            "updated_sections": updated_sections,
            "states": latest_states["config"],
        },
        message="Non-system state config updated.",
    )


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def get_configer_state_schema(
    language: str = "zh",
    section_name: str | None = None,
) -> dict[str, Any]:
    return _run_async(get_configer_state_schema_async(language=language, section_name=section_name))


def update_configer_state_config(updates: str | dict[str, Any]) -> dict[str, Any]:
    return _run_async(update_configer_state_config_async(updates))
