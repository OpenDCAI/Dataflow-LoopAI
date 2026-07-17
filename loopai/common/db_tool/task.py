from __future__ import annotations

import json
import os
from typing import Any

from omegaconf import OmegaConf

from api.app.models.db_models import StarterConfig
from loopai.schema.model_pool import StarterModelPool
# 懒加载，避免 MCP 启动时级联导入 langgraph → torch

from .base import _sqlite_connect, require_db_path


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
    elif type_name in {"dict", "list", "json", "object", "array"}:
        return item
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


def _resolve_model_pool_config(system_config: dict[str, Any], model_name: str | None = None, *, selector: str = "default_model") -> dict[str, Any]:
    pool = StarterModelPool(system_config)
    return pool.model_config_by_name(model_name, selector=selector, include_disabled=True)


def _build_state_config(states_data: dict[str, Any], language: str | None = None) -> dict[str, Any]:
    from loopai.schema.states import get_state_config_schema  # 懒加载，避免 MCP 级联导入

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


def _ensure_default_starter_config_sync(
    db_path: str | os.PathLike[str],
    starter_yaml_path: str | os.PathLike[str],
) -> tuple[int, str, str]:
    con = _sqlite_connect(db_path)
    try:
        row = con.execute("select id, name, config from starterconfig where name=?", ("starter",)).fetchone()
        if row is not None:
            return row

        cfg = OmegaConf.load(str(starter_yaml_path))
        config_obj = OmegaConf.to_container(cfg, resolve=True)
        config_json = json.dumps(config_obj, ensure_ascii=False)
        cur = con.execute(
            "insert into starterconfig(name, config) values(?, ?)",
            ("starter", config_json),
        )
        con.commit()
        return (int(cur.lastrowid), "starter", config_json)
    finally:
        con.close()


def _get_default_starter_row_sync(
    db_path: str | os.PathLike[str],
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> tuple[int, str, str]:
    if starter_yaml_path is not None:
        return _ensure_default_starter_config_sync(db_path, starter_yaml_path)

    con = _sqlite_connect(db_path)
    try:
        row = con.execute("select id, name, config from starterconfig where name=?", ("starter",)).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError("starter config not found")
    return row


def _get_task_row_sync(db_path: str | os.PathLike[str], task_id: str) -> tuple[int, str, str, str, str | None]:
    con = _sqlite_connect(db_path)
    try:
        row = con.execute(
            "select id, task_id, name, config, state from taskmodel where task_id=?",
            (task_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f"task not found: {task_id}")
    return row


def get_default_system_config_sync(
    db_path: str | os.PathLike[str],
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    config_id, name, raw_config = _get_default_starter_row_sync(db_path, starter_yaml_path)
    config_data = json.loads(raw_config or "{}")
    return {
        "id": config_id,
        "name": name,
        "config": _normalize_system_config(config_data.get("system", {})),
    }


def get_default_states_config_sync(
    db_path: str | os.PathLike[str],
    starter_yaml_path: str | os.PathLike[str] | None = None,
    section_name: str | None = None,
) -> dict[str, Any]:
    config_id, name, raw_config = _get_default_starter_row_sync(db_path, starter_yaml_path)
    config_data = json.loads(raw_config or "{}")
    states_config = _build_state_config(config_data.get("default_states", {}))
    return {
        "id": config_id,
        "name": name,
        "config": _extract_state_section(states_config, section_name) if section_name else states_config,
    }


def update_default_state_section_config_sync(
    db_path: str | os.PathLike[str],
    section_name: str,
    updates: dict[str, Any],
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    config_id, _, raw_config = _get_default_starter_row_sync(db_path, starter_yaml_path)
    config_data = json.loads(raw_config or "{}")
    default_states = config_data.setdefault("default_states", {})
    _merge_state_section(default_states, section_name, updates)

    con = _sqlite_connect(db_path)
    try:
        con.execute(
            "update starterconfig set config=? where id=?",
            (json.dumps(config_data, ensure_ascii=False), config_id),
        )
        con.commit()
    finally:
        con.close()

    return get_default_states_config_sync(db_path, section_name=section_name)


def get_task_config_sync(db_path: str | os.PathLike[str], task_id: str) -> dict[str, Any]:
    row_id, row_task_id, name, raw_config, _ = _get_task_row_sync(db_path, task_id)
    config_data = json.loads(raw_config or "{}")
    return {
        "id": row_id,
        "task_id": row_task_id,
        "name": name,
        "config": config_data,
    }


def get_task_system_config_sync(db_path: str | os.PathLike[str], task_id: str) -> dict[str, Any]:
    task_config = get_task_config_sync(db_path, task_id)
    system_config = task_config["config"].get("system", {})
    return {
        "id": task_config["id"],
        "task_id": task_config["task_id"],
        "name": task_config["name"],
        "config": _normalize_system_config(system_config),
    }


def get_task_states_config_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    section_name: str | None = None,
) -> dict[str, Any]:
    row_id, row_task_id, name, _, raw_state = _get_task_row_sync(db_path, task_id)
    state_data = json.loads(raw_state or "{}") if raw_state else {}
    if not isinstance(state_data, dict):
        state_data = {}
    states_config = _build_state_config(state_data)
    return {
        "id": row_id,
        "task_id": row_task_id,
        "name": name,
        "config": _extract_state_section(states_config, section_name) if section_name else states_config,
    }


def get_task_state_section_config_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    section_name: str,
) -> dict[str, Any]:
    return get_task_states_config_sync(db_path, task_id, section_name=section_name)


def update_task_state_section_config_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    section_name: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    row_id, _, _, _, raw_state = _get_task_row_sync(db_path, task_id)
    state_data = json.loads(raw_state or "{}") if raw_state else {}
    if not isinstance(state_data, dict):
        state_data = {}
    _merge_state_section(state_data, section_name, updates)

    con = _sqlite_connect(db_path)
    try:
        con.execute(
            "update taskmodel set state=? where id=?",
            (json.dumps(state_data, ensure_ascii=False), row_id),
        )
        con.commit()
    finally:
        con.close()

    return get_task_states_config_sync(db_path, task_id, section_name=section_name)


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
    return get_default_system_config_sync(require_db_path(), starter_yaml_path)


async def get_default_states_config(
    starter_yaml_path: str | os.PathLike[str] | None = None,
    section_name: str | None = None,
) -> dict[str, Any]:
    return get_default_states_config_sync(require_db_path(), starter_yaml_path, section_name)


async def update_default_state_section_config(
    section_name: str,
    updates: dict[str, Any],
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    return update_default_state_section_config_sync(require_db_path(), section_name, updates, starter_yaml_path)


async def get_task_config(task_id: str) -> dict[str, Any]:
    return get_task_config_sync(require_db_path(), task_id)


async def get_task_system_config(task_id: str) -> dict[str, Any]:
    return get_task_system_config_sync(require_db_path(), task_id)


def get_default_model_config_sync(
    db_path: str | os.PathLike[str],
    model_name: str | None = None,
    *,
    selector: str = "default_model",
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    config_id, name, raw_config = _get_default_starter_row_sync(db_path, starter_yaml_path)
    config_data = json.loads(raw_config or "{}")
    system_config = config_data.get("system", {}) if isinstance(config_data.get("system"), dict) else {}
    return {
        "id": config_id,
        "name": name,
        "model": model_name or "",
        "selector": selector,
        "config": _resolve_model_pool_config(system_config, model_name, selector=selector),
    }


def get_task_model_config_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    model_name: str | None = None,
    *,
    selector: str = "default_model",
) -> dict[str, Any]:
    task_config = get_task_config_sync(db_path, task_id)
    system_config = task_config["config"].get("system", {})
    if not isinstance(system_config, dict):
        system_config = {}
    return {
        "id": task_config["id"],
        "task_id": task_config["task_id"],
        "name": task_config["name"],
        "model": model_name or "",
        "selector": selector,
        "config": _resolve_model_pool_config(system_config, model_name, selector=selector),
    }


async def get_default_model_config(
    model_name: str | None = None,
    *,
    selector: str = "default_model",
    starter_yaml_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    return get_default_model_config_sync(require_db_path(), model_name, selector=selector, starter_yaml_path=starter_yaml_path)


async def get_task_model_config(
    task_id: str,
    model_name: str | None = None,
    *,
    selector: str = "default_model",
) -> dict[str, Any]:
    return get_task_model_config_sync(require_db_path(), task_id, model_name, selector=selector)


async def get_task_states_config(task_id: str, section_name: str | None = None) -> dict[str, Any]:
    return get_task_states_config_sync(require_db_path(), task_id, section_name)


async def get_task_state_section_config(task_id: str, section_name: str) -> dict[str, Any]:
    return get_task_state_section_config_sync(require_db_path(), task_id, section_name)


async def update_task_state_section_config(
    task_id: str,
    section_name: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    return update_task_state_section_config_sync(require_db_path(), task_id, section_name, updates)
