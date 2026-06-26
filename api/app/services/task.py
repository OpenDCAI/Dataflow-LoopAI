from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..models.db_models import TaskRuntime
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


def _serialize_task_runtime(runtime: TaskRuntime) -> dict[str, Any]:
    return {
        "id": runtime.id,
        "task_id": runtime.task_id,
        "node_name": runtime.node_name,
        "version": runtime.version,
        "status": runtime.status,
        "createdAt": runtime.createdAt,
        "updatedAt": runtime.updatedAt,
    }


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


async def create_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    runtime = await TaskRuntime.create(
        task_id=task_id,
        node_name=node_name,
        version=version,
        status=status,
    )
    return _serialize_task_runtime(runtime)


async def update_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any] | None:
    runtime = await TaskRuntime.filter(
        task_id=task_id,
        node_name=node_name,
        version=version,
    ).order_by("-updatedAt", "-id").first()
    if not runtime:
        return None
    runtime.status = status
    await runtime.save()
    return _serialize_task_runtime(runtime)


async def upsert_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    runtime = await TaskRuntime.filter(
        task_id=task_id,
        node_name=node_name,
        version=version,
    ).order_by("-updatedAt", "-id").first()
    if runtime:
        runtime.status = status
        await runtime.save()
        return _serialize_task_runtime(runtime)
    return await create_task_runtime(
        task_id=task_id,
        node_name=node_name,
        version=version,
        status=status,
    )


async def get_latest_task_runtime(
    task_id: str,
    node_name: str,
) -> dict[str, Any] | None:
    runtime = await TaskRuntime.filter(
        task_id=task_id,
        node_name=node_name,
    ).order_by("-updatedAt", "-id").first()
    if not runtime:
        return None
    return _serialize_task_runtime(runtime)


async def list_task_runtime_history(
    task_id: str,
    node_name: str,
) -> list[dict[str, Any]]:
    runtimes = await TaskRuntime.filter(
        task_id=task_id,
        node_name=node_name,
    ).order_by("-updatedAt", "-id")
    return [_serialize_task_runtime(runtime) for runtime in runtimes]


async def list_latest_task_runtimes(task_id: str) -> list[dict[str, Any]]:
    runtimes = await TaskRuntime.filter(task_id=task_id).order_by(
        "node_name",
        "-updatedAt",
        "-id",
    )

    latest_runtimes: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()

    for runtime in runtimes:
        node_name = runtime.node_name or ""
        if node_name in seen_nodes:
            continue
        seen_nodes.add(node_name)
        latest_runtimes.append(_serialize_task_runtime(runtime))

    return latest_runtimes
