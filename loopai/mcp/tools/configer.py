from __future__ import annotations

import enum
from typing import Any

if not hasattr(enum, "StrEnum"):
    class _CompatStrEnum(str, enum.Enum):
        pass

    enum.StrEnum = _CompatStrEnum  # type: ignore[attr-defined]

from ..base import mcp
from loopai.skills.Configer import (  # noqa: E402
    get_configer_state_config,
    get_configer_state_schema,
    get_configer_task_state_config,
    update_configer_state_config,
    update_configer_task_state_config,
)


@mcp.tool(
    name="configer_get_schema",
    description=(
        "Read LoopAI non-system state schema for one section or all sections. "
        "Use this first when you need to know which sections and fields exist, what values are allowed, "
        "or what a field means before reading or updating config. "
        "Prefer this before any config_update or config_update_task call when field names, constraints, "
        "or ownership are not already certain."
    ),
)
def config_get_schema(language: str = "zh", section_name: str | None = None) -> dict[str, Any]:
    return get_configer_state_schema(language=language, section_name=section_name)


@mcp.tool(
    name="configer_get",
    description=(
        "Read the current default or env-scoped LoopAI state config. "
        "Use this after config_get_schema when you need the actual current value, not just schema defaults. "
        "This reads task-scoped config only when TASK_ID is already present in the environment; otherwise it reads "
        "the global default config. "
        "Prefer this before config_update so you can confirm the existing value and avoid blind overwrites."
    ),
)
def config_get(section_name: str, field_name: str | None = None) -> dict[str, Any]:
    return get_configer_state_config(section_name=section_name, field_name=field_name)


@mcp.tool(
    name="configer_get_task",
    description=(
        "Read the current LoopAI task state config for a specific task_id. "
        "Use this when the request is explicitly about a known task, or when you must avoid relying on TASK_ID from the environment. "
        "Prefer this over config_get for task-specific inspection because it makes the task target explicit."
    ),
)
def config_get_task(section_name: str, field_name: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    return get_configer_task_state_config(section_name=section_name, field_name=field_name, task_id=task_id)


@mcp.tool(
    name="configer_update",
    description=(
        "Update the default or env-scoped LoopAI state config for one section. "
        "Before calling this, first use config_get_schema to verify the section and fields, then use config_get to inspect current values. "
        "Use this only after the target change is clear and confirmed. "
        "Do not use this to modify the system section. "
        "Do not use this to modify default.task_id. "
        "If TASK_ID is present in the environment this call updates that task-scoped config; otherwise it updates the global default config."
    ),
)
def config_update(section_name: str, updates: dict[str, Any]) -> dict[str, Any]:
    return update_configer_state_config(section_name=section_name, updates=updates)


@mcp.tool(
    name="configer_update_task",
    description=(
        "Update the LoopAI task state config for one section and task_id. "
        "Before calling this, first use config_get_schema to verify the section and fields, then use config_get_task to inspect current values for that task. "
        "Use this when the target task must be explicit rather than inferred from TASK_ID. "
        "Do not use this to modify the system section. "
        "Do not use this for fields that do not belong to the chosen section."
    ),
)
def config_update_task(section_name: str, updates: dict[str, Any], task_id: str | None = None) -> dict[str, Any]:
    return update_configer_task_state_config(section_name=section_name, updates=updates, task_id=task_id)
