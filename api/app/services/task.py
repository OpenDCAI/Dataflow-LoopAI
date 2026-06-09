from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..utils.config.config import get_state_config


CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent
API_DIR = APP_DIR.parent
PROJECT_ROOT = API_DIR.parent


def _merge_state(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_state(merged[key], value)
        else:
            merged[key] = value
    return merged


def _unwrap_state_config(config: dict[str, Any], task_id: str) -> dict[str, Any]:
    state: dict[str, Any] = {"messages": []}

    default_section = config.get("default", {})
    for key, item in default_section.items():
        if isinstance(item, dict) and "value" in item:
            state[key] = item["value"]

    for section_name, section_config in config.items():
        if section_name == "default":
            continue
        section_state: dict[str, Any] = {}
        if isinstance(section_config, dict):
            for key, item in section_config.items():
                if isinstance(item, dict) and "value" in item:
                    section_state[key] = item["value"]
        state[section_name] = section_state

    state["task_id"] = task_id
    return state


async def build_initial_task_state(
    task_id: str,
    state_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_config = await get_state_config(str(PROJECT_ROOT))
    base_state = _unwrap_state_config(state_config["config"], task_id)
    if state_overrides:
        base_state = _merge_state(base_state, state_overrides)
        base_state["task_id"] = task_id
        base_state.setdefault("messages", [])
    return base_state


def parse_task_state_overrides(raw_state: str | None) -> dict[str, Any] | None:
    if not raw_state:
        return None
    parsed = json.loads(raw_state)
    if not isinstance(parsed, dict):
        raise ValueError("state must be a JSON object")
    return parsed
