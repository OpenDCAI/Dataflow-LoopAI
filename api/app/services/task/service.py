from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from tortoise.expressions import Q

from ...models.body import TaskItem
from ...models.db_models import TaskModel, TaskRuntime
from ...utils.config.config import get_state_config
from ...utils.task.task import apply_state_config_updates, build_task_state_config, config_format


CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent.parent
API_DIR = APP_DIR.parent
PROJECT_ROOT = API_DIR.parent


class TaskServiceError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


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


def _serialize_task_runtime(
    runtime: TaskRuntime,
    include_state: bool = False,
) -> dict[str, Any]:
    payload = {
        "id": runtime.id,
        "task_id": runtime.task_id,
        "node_name": runtime.node_name,
        "version": runtime.version,
        "status": runtime.status,
        "createdAt": runtime.createdAt,
        "updatedAt": runtime.updatedAt,
    }
    if include_state:
        payload["state"] = runtime.state
    return payload


def _serialize_task(task: TaskModel) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_id": task.task_id,
        "name": task.name,
        "config": task.config,
        "state": task.state,
        "ai_thread_id": task.ai_thread_id,
        "createdAt": task.createdAt,
        "updatedAt": task.updatedAt,
    }


def _serialize_task_summary(task: TaskModel) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_id": task.task_id,
        "name": task.name,
        "ai_thread_id": task.ai_thread_id,
        "createdAt": task.createdAt,
        "updatedAt": task.updatedAt,
    }


def _parse_task_config(raw_config: str | None) -> dict[str, Any]:
    try:
        return json.loads(raw_config)
    except Exception as exc:
        raise TaskServiceError("config格式错误") from exc


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


async def create_task(task_item: TaskItem) -> dict[str, Any]:
    task_id = str(uuid.uuid4())
    config = _parse_task_config(task_item.config)

    try:
        state_overrides = parse_task_state_overrides(task_item.state)
        initial_state = await build_initial_task_state(task_id, state_overrides=state_overrides)
    except Exception as exc:
        raise TaskServiceError(f"state格式错误: {exc}") from exc

    formatted_config = config_format(config, task_id)
    task = TaskModel(
        task_id=task_id,
        name=task_item.name,
        config=json.dumps(formatted_config),
        state=json.dumps(initial_state, ensure_ascii=False),
    )
    await task.save()
    return _serialize_task(task)


async def get_task(task_id: str) -> dict[str, Any] | None:
    task = await TaskModel.get_or_none(task_id=task_id)
    if not task:
        return None
    return _serialize_task(task)


async def _load_task_state(task: TaskModel) -> dict[str, Any]:
    base_state = await build_initial_task_state(task.task_id)
    if not task.state:
        return base_state
    try:
        current_state = json.loads(task.state)
    except Exception:
        return base_state
    if not isinstance(current_state, dict):
        return base_state
    merged_state = _merge_state(base_state, current_state)
    merged_state["task_id"] = task.task_id
    merged_state.setdefault("messages", [])
    return merged_state


def _parse_state_config_payload(raw_payload: Any) -> dict[str, Any]:
    if raw_payload is None:
        raise TaskServiceError("config不能为空")
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except Exception as exc:
            raise TaskServiceError("config格式错误") from exc
    if not isinstance(raw_payload, dict):
        raise TaskServiceError("config格式错误")
    if "states" in raw_payload:
        raw_payload = raw_payload.get("states")
    if not isinstance(raw_payload, dict):
        raise TaskServiceError("states格式错误")
    return raw_payload


async def get_task_state_config(task_id: str) -> dict[str, Any] | None:
    task = await TaskModel.get_or_none(task_id=task_id)
    if not task:
        return None
    state = await _load_task_state(task)
    states = build_task_state_config(state, language=state.get("language"))
    return {
        "id": task.id,
        "task_id": task.task_id,
        "name": task.name,
        "states": states,
    }


async def update_task_state_config(task_id: str, payload: Any) -> dict[str, Any]:
    task = await TaskModel.get_or_none(task_id=task_id)
    if not task:
        raise TaskServiceError("任务项不存在", code=404)

    states_config = _parse_state_config_payload(payload)
    state = await _load_task_state(task)
    apply_state_config_updates(state, states_config)
    state["task_id"] = task_id
    state.setdefault("messages", [])

    task.state = json.dumps(state, ensure_ascii=False)
    await task.save()
    updated = await get_task_state_config(task_id)
    if not updated:
        raise TaskServiceError("任务项不存在", code=404)
    return updated


async def list_tasks(
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    qs = TaskModel.all()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(task_id__icontains=search))

    tasks = await qs.offset(offset).limit(limit)
    return [_serialize_task_summary(task) for task in tasks]


async def update_task(task_item: TaskItem) -> dict[str, Any] | None:
    task = await TaskModel.get_or_none(id=task_item.id)
    if not task:
        return None

    task.name = task_item.name
    if task_item.config:
        config = _parse_task_config(task_item.config)
        formatted_config = config_format(config, task.task_id)
        task.config = json.dumps(formatted_config)

    await task.save()
    return _serialize_task(task)


async def delete_task(task_id: str) -> bool:
    task = await TaskModel.get_or_none(id=task_id)
    if not task:
        return False
    await task.delete()
    return True


def get_train_status(output_dir: str, task_id: str, train_task_id: str) -> list[Any]:
    watch_path = os.path.join(output_dir, task_id, "trainer", train_task_id)
    final_path = os.path.join(watch_path, "metrics", "metrics.json")
    if not os.path.exists(final_path):
        raise TaskServiceError(f"训练状态文件不存在:{final_path}", code=404)

    with open(final_path, "r") as file_obj:
        return json.load(file_obj)


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
    return _serialize_task_runtime(runtime, include_state=True)


async def list_task_runtime_history(
    task_id: str,
    node_name: str,
) -> list[dict[str, Any]]:
    runtimes = await TaskRuntime.filter(
        task_id=task_id,
        node_name=node_name,
    ).order_by("-updatedAt", "-id")
    return [_serialize_task_runtime(runtime, include_state=True) for runtime in runtimes]


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
