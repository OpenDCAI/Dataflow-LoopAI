from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from omegaconf import OmegaConf
from tortoise import Tortoise

from api.app.models.db_models import StarterConfig, TaskModel
from loopai.schema.states import get_state_config_schema


def sqlite_db_url(db_path: str | os.PathLike[str]) -> str:
    return f"sqlite://{Path(db_path).resolve()}"


async def init_sqlite_db(db_path: str | os.PathLike[str]) -> None:
    await Tortoise.init(
        db_url=sqlite_db_url(db_path),
        modules={"models": ["api.app.models.db_models"]},
    )


async def close_db() -> None:
    await Tortoise.close_connections()


@asynccontextmanager
async def sqlite_db_session(db_path: str | os.PathLike[str]) -> AsyncIterator[None]:
    await init_sqlite_db(db_path)
    try:
        yield
    finally:
        await close_db()


def wrap_attr(val: Any) -> dict[str, Any]:
    type_name = "str"
    if type(val) is int:
        type_name = "int"
    elif type(val) is bool:
        type_name = "bool"
    elif type(val) is float:
        type_name = "float"
    elif val is None:
        type_name = "none"
    return {
        "value": val,
        "default_value": val,
        "type": type_name,
    }


def format_value(item: dict[str, Any]) -> dict[str, Any]:
    type_name = item.get("type", "str")
    value = item.get("value")
    if value is None:
        value = item.get("default")
    if value is None:
        item["value"] = None
        return item
    item["value"] = value
    if type_name == "bool":
        item["value"] = bool(item["value"])
    else:
        if type(item["value"]) in {int, float}:
            return item
        if type_name == "int":
            try:
                item["value"] = int(item["value"])
            except Exception:
                item["value"] = float(item["value"])
        elif type_name == "float":
            item["value"] = float(item["value"])
        else:
            item["value"] = str(item["value"])
    return item


def _normalize_system_config(system_config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: wrap_attr(value)
        for key, value in system_config.items()
    }


def _build_state_config(states_data: dict[str, Any], language: str | None = None) -> dict[str, Any]:
    language = language or states_data.get("language", "zh")
    nested_states_schema = get_state_config_schema(language)
    default_schema = nested_states_schema.get("default", {})
    nested_keys = list(nested_states_schema.keys())
    nested_keys.remove("default")

    result: dict[str, Any] = {}
    for series_key in dict.fromkeys(list(states_data.keys()) + list(default_schema.keys())):
        if series_key in nested_keys:
            continue
        schema_val = default_schema.get(series_key, {})
        if series_key in states_data:
            cur_val = wrap_attr(states_data[series_key])
        elif "default" in schema_val:
            cur_val = wrap_attr(schema_val["default"])
        else:
            cur_val = {"value": None, "default_value": None, "type": "none"}
        result.setdefault("default", {})[series_key] = {
            **schema_val,
            **cur_val,
        }

    for series_key in nested_keys:
        result.setdefault(series_key, {})
        for key, schema_val in nested_states_schema.get(series_key, {}).items():
            if key in states_data.get(series_key, {}):
                cur_val = wrap_attr(states_data.get(series_key, {})[key])
            elif "default" in schema_val:
                cur_val = wrap_attr(schema_val["default"])
            else:
                cur_val = {"value": None, "default_value": None, "type": "none"}
            result[series_key][key] = {
                **schema_val,
                **cur_val,
            }
    return result


def _extract_state_section(states_config: dict[str, Any], section_name: str) -> dict[str, Any]:
    if section_name == "default":
        return states_config.get("default", {})
    return states_config.get(section_name, {})


def _coerce_update_item(raw_item: Any) -> dict[str, Any]:
    if isinstance(raw_item, dict):
        return format_value(dict(raw_item))
    return {"value": raw_item}


def _merge_state_section(default_states: dict[str, Any], section_name: str, updates: dict[str, Any]) -> dict[str, Any]:
    if section_name == "default":
        for key, raw_item in updates.items():
            item = _coerce_update_item(raw_item)
            default_states[key] = item["value"]
        return default_states

    section = default_states.setdefault(section_name, {})
    for key, raw_item in updates.items():
        item = _coerce_update_item(raw_item)
        section[key] = item["value"]
    return default_states


async def ensure_default_starter_config(
    starter_yaml_path: str | os.PathLike[str],
) -> StarterConfig:
    config = await StarterConfig.filter(name="starter").first()
    if config:
        return config

    cfg = OmegaConf.load(str(starter_yaml_path))
    config_obj = OmegaConf.to_container(cfg, resolve=True)
    await StarterConfig.create(name="starter", config=json.dumps(config_obj))
    created = await StarterConfig.filter(name="starter").first()
    if created is None:
        raise RuntimeError("failed to create starter config")
    return created


async def get_default_system_config(
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if starter_yaml_path is not None:
        config = await ensure_default_starter_config(starter_yaml_path)
    else:
        config = await StarterConfig.filter(name="starter").first()
        if config is None:
            raise ValueError("starter config not found")
    config_data = json.loads(config.config or "{}")
    return {
        "id": config.id,
        "name": config.name,
        "config": _normalize_system_config(config_data.get("system", {})),
    }


async def get_default_states_config(
    starter_yaml_path: str | os.PathLike[str] | None = None,
    section_name: str | None = None,
) -> dict[str, Any]:
    if starter_yaml_path is not None:
        config = await ensure_default_starter_config(starter_yaml_path)
    else:
        config = await StarterConfig.filter(name="starter").first()
        if config is None:
            raise ValueError("starter config not found")
    config_data = json.loads(config.config or "{}")
    states_config = _build_state_config(config_data.get("default_states", {}))
    return {
        "id": config.id,
        "name": config.name,
        "config": _extract_state_section(states_config, section_name) if section_name else states_config,
    }


async def update_default_state_section_config(
    section_name: str,
    updates: dict[str, Any],
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if starter_yaml_path is not None:
        config = await ensure_default_starter_config(starter_yaml_path)
    else:
        config = await StarterConfig.filter(name="starter").first()
        if config is None:
            raise ValueError("starter config not found")

    config_data = json.loads(config.config or "{}")
    default_states = config_data.setdefault("default_states", {})
    _merge_state_section(default_states, section_name, updates)

    config.config = json.dumps(config_data, ensure_ascii=False)
    await config.save()

    return await get_default_states_config(section_name=section_name)


async def get_task_config(task_id: str) -> dict[str, Any]:
    task = await TaskModel.get_or_none(task_id=task_id)
    if task is None:
        raise ValueError(f"task not found: {task_id}")
    config_data = json.loads(task.config or "{}")
    return {
        "id": task.id,
        "task_id": task.task_id,
        "name": task.name,
        "config": config_data,
    }


async def get_task_system_config(task_id: str) -> dict[str, Any]:
    task_config = await get_task_config(task_id)
    system_config = task_config["config"].get("system", {})
    return {
        "id": task_config["id"],
        "task_id": task_config["task_id"],
        "name": task_config["name"],
        "config": _normalize_system_config(system_config),
    }


async def get_task_states_config(task_id: str, section_name: str | None = None) -> dict[str, Any]:
    task_config = await get_task_config(task_id)
    states_data = task_config["config"].get("default_states", {})
    states_config = _build_state_config(states_data)
    return {
        "id": task_config["id"],
        "task_id": task_config["task_id"],
        "name": task_config["name"],
        "config": _extract_state_section(states_config, section_name) if section_name else states_config,
    }


async def get_task_state_section_config(task_id: str, section_name: str) -> dict[str, Any]:
    return await get_task_states_config(task_id, section_name=section_name)


async def update_task_state_section_config(
    task_id: str,
    section_name: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    task = await TaskModel.get_or_none(task_id=task_id)
    if task is None:
        raise ValueError(f"task not found: {task_id}")

    config_data = json.loads(task.config or "{}")
    default_states = config_data.setdefault("default_states", {})
    _merge_state_section(default_states, section_name, updates)

    task.config = json.dumps(config_data, ensure_ascii=False)
    await task.save()

    return await get_task_states_config(task_id, section_name=section_name)
