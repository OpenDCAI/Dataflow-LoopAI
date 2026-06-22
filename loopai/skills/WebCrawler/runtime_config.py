from __future__ import annotations

import os
from typing import Any, Dict, Optional

from pydantic import ValidationError

from loopai.schema.states import WebCrawlerState
from loopai.skills.Configer import get_configer_task_state_config

_DEFAULT_OUTPUT_DIR = "./outputs"
_DEFAULT_THREAD_ID = "webcrawler-default"


class WebCrawlerRuntimeState(WebCrawlerState):
    """Standalone sub-agent runtime model (inherits LoopAIState sub-state)."""


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _unwrap_config_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return _unwrap_config_value(value.get("value"))
    if isinstance(value, dict):
        return {k: _unwrap_config_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unwrap_config_value(v) for v in value]
    return value


def _resolve_task_id(state: Optional[Dict[str, Any]], **kwargs: Any) -> str:
    return str(
        _first_non_empty(
            kwargs.get("thread_id"),
            kwargs.get("task_id"),
            os.getenv("TASK_ID"),
            state.get("task_id") if isinstance(state, dict) else None,
            _DEFAULT_THREAD_ID,
        )
    )


def _resolve_output_dir(state: Optional[Dict[str, Any]], **kwargs: Any) -> str:
    return str(
        _first_non_empty(
            kwargs.get("output_dir"),
            os.getenv("OUTPUT_DIR"),
            state.get("output_dir") if isinstance(state, dict) else None,
            _DEFAULT_OUTPUT_DIR,
        )
    )


def _load_task_webcrawler_config(task_id: str, db_path: Optional[str]) -> Dict[str, Any]:
    if not task_id:
        raise ValueError("task_id is required for task-scoped webcrawler config")
    if not db_path:
        raise ValueError("DB_PATH is required when task_id is provided")

    # Configer skill API reads DB_PATH from env; align with existing skill behavior.
    os.environ["DB_PATH"] = str(db_path)
    result = get_configer_task_state_config(section_name="webcrawler", task_id=task_id)
    if not result.get("ok"):
        raise RuntimeError(result.get("message", "failed to load webcrawler config"))

    raw_config = result.get("data", {}).get("config", {})
    return _unwrap_config_value(raw_config) if isinstance(raw_config, dict) else {}


def _build_runtime_webcrawler_config(
    state: Optional[Dict[str, Any]],
    db_config: Dict[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    in_state = state.get("webcrawler", {}) if isinstance(state, dict) else {}
    if not isinstance(in_state, dict):
        in_state = {}

    merged = dict(db_config)
    merged.update(in_state)

    overrides = {
        "deepseek_api_key": _first_non_empty(
            kwargs.get("webcrawler_deepseek_api_key"),
            kwargs.get("deepseek_api_key"),
            os.getenv("DEEPSEEK_API_KEY"),
        ),
        "tavily_api_key": _first_non_empty(
            kwargs.get("webcrawler_tavily_api_key"),
            kwargs.get("tavily_api_key"),
            os.getenv("TAVILY_API_KEY"),
        ),
        "deepseek_api_base": _first_non_empty(
            kwargs.get("webcrawler_deepseek_api_base"),
            kwargs.get("deepseek_api_base"),
            os.getenv("WEBCRAWLER_DEEPSEEK_API_BASE"),
        ),
        "model": _first_non_empty(
            kwargs.get("webcrawler_model"),
            kwargs.get("model"),
            os.getenv("WEBCRAWLER_MODEL"),
        ),
        "temperature": _first_non_empty(
            kwargs.get("webcrawler_temperature"),
            kwargs.get("temperature"),
            os.getenv("WEBCRAWLER_TEMPERATURE"),
        ),
    }

    for key, value in overrides.items():
        if value is not None:
            merged[key] = value

    return merged


def _validate_webcrawler_required_fields(config: Dict[str, Any]) -> None:
    required_fields = ("deepseek_api_key", "tavily_api_key")
    missing = [field for field in required_fields if not config.get(field)]
    if missing:
        raise ValueError(f"missing required config: {', '.join(missing)}")


def resolve_webcrawler_runtime_config(
    state: Optional[Dict[str, Any]],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Inject task-scoped runtime config and validate required parameters.

    Single entry point for:
    1) runtime parameter injection,
    2) optional task-scoped DB config loading,
    3) required-field validation.
    """
    if state is None:
        state = {}

    thread_id = _resolve_task_id(state, **kwargs)
    output_dir = _resolve_output_dir(state, **kwargs)
    db_path = _first_non_empty(kwargs.get("db_path"), os.getenv("DB_PATH"), state.get("DB_PATH"))

    db_config: Dict[str, Any] = {}
    if thread_id and thread_id != _DEFAULT_THREAD_ID:
        db_config = _load_task_webcrawler_config(thread_id, db_path)

    merged = _build_runtime_webcrawler_config(state, db_config, **kwargs)
    _validate_webcrawler_required_fields(merged)

    try:
        normalized = WebCrawlerRuntimeState(**merged).model_dump()
    except ValidationError as exc:
        raise ValueError(f"invalid webcrawler config: {exc}") from exc

    state["webcrawler"] = normalized
    state["task_id"] = thread_id
    state["output_dir"] = output_dir
    if db_path:
        state["DB_PATH"] = str(db_path)

    user_query = _first_non_empty(
        kwargs.get("user_query"),
        kwargs.get("query"),
        state.get("automated_query"),
    )
    if user_query:
        state["automated_query"] = str(user_query)

    return {
        "thread_id": thread_id,
        "output_dir": output_dir,
        "db_path": db_path,
        "required_fields_ok": True,
    }


__all__ = ["resolve_webcrawler_runtime_config"]

