from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .state_bridge import load_system_runtime_config

_DEFAULT_CHECKPOINT_PATH = "outputs/analyzer_checkpoints.sqlite"
_DEFAULT_THREAD_ID = "analyzer-default"


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _analyzer(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    analyzer = state.setdefault("analyzer", {})
    if not isinstance(analyzer, dict):
        state["analyzer"] = {}
        return state["analyzer"]
    return analyzer


def _system_runtime(state: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _first_non_empty(
        kwargs.get("thread_id"),
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state.get("task_id") if isinstance(state, dict) else None,
    )
    return load_system_runtime_config(task_id)


def _needs_llm(analyzer: Dict[str, Any], kwargs: Dict[str, Any]) -> bool:
    explicit = kwargs.get("require_api_key")
    if explicit is not None:
        return bool(explicit)
    return bool(
        _first_non_empty(
            kwargs.get("analyzer_model"),
            kwargs.get("model"),
            os.getenv("ANALYZER_MODEL"),
            analyzer.get("analyze_model_path"),
            analyzer.get("model"),
        )
        or _first_non_empty(
            kwargs.get("analyzer_base_url"),
            kwargs.get("base_url"),
            os.getenv("ANALYZER_BASE_URL"),
            analyzer.get("analyze_base_url"),
            analyzer.get("base_url"),
        )
    )


def resolve_analyzer_runtime_config(
    state: Optional[Dict[str, Any]],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Resolve Analyzer runtime values from kwargs, env, state, defaults.

    Priority is kwargs > env > state["analyzer"] > default, except
    ANALYZER_API_KEY is env-priority by design and only falls back to config
    for legacy compatibility.
    """
    analyzer = _analyzer(state)
    system_runtime = _system_runtime(state, kwargs)

    env_api_key = os.getenv("ANALYZER_API_KEY")
    system_api_key = _first_non_empty(
        system_runtime.get("analyzer_api_key"),
        system_runtime.get("analyze_api_key"),
        system_runtime.get("starter_api_key"),
        system_runtime.get("codex_api_key"),
        system_runtime.get("api_key"),
    )
    legacy_api_key = analyzer.get("analyze_api_key") or analyzer.get("api_key")
    api_key = _first_non_empty(
        kwargs.get("analyzer_api_key"),
        kwargs.get("api_key"),
        system_api_key,
        env_api_key,
        legacy_api_key,
    )

    model = _first_non_empty(
        kwargs.get("analyzer_model"),
        kwargs.get("model"),
        system_runtime.get("analyzer_model"),
        system_runtime.get("analyze_model"),
        system_runtime.get("starter_model_name"),
        system_runtime.get("starter_model_path"),
        os.getenv("ANALYZER_MODEL"),
        analyzer.get("analyze_model_path"),
        analyzer.get("model"),
    )
    base_url = _first_non_empty(
        kwargs.get("analyzer_base_url"),
        kwargs.get("base_url"),
        system_runtime.get("analyzer_base_url"),
        system_runtime.get("analyze_base_url"),
        system_runtime.get("starter_base_url"),
        os.getenv("ANALYZER_BASE_URL"),
        analyzer.get("analyze_base_url"),
        analyzer.get("base_url"),
    )
    task_id = _first_non_empty(
        kwargs.get("thread_id"),
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state.get("task_id") if isinstance(state, dict) else None,
        _DEFAULT_THREAD_ID,
    )
    db_path = _first_non_empty(
        kwargs.get("db_path"),
        os.getenv("DB_PATH"),
        analyzer.get("db_path"),
    )
    checkpoint_path = _first_non_empty(
        kwargs.get("checkpoint_path"),
        os.getenv("ANALYZER_CHECKPOINT_PATH"),
        analyzer.get("checkpoint_path"),
        _DEFAULT_CHECKPOINT_PATH,
    )

    require_api_key = kwargs.get("require_api_key")
    needs_llm = bool(require_api_key) if require_api_key is not None else bool(model or base_url)
    if needs_llm and not api_key:
        raise RuntimeError("missing required env: ANALYZER_API_KEY")

    if isinstance(state, dict):
        if task_id and not state.get("task_id"):
            state["task_id"] = task_id
        if db_path:
            state["DB_PATH"] = db_path

    if model:
        analyzer["analyze_model_path"] = model
    if base_url:
        analyzer["analyze_base_url"] = base_url
    if api_key:
        analyzer["analyze_api_key"] = api_key
    if checkpoint_path:
        analyzer["checkpoint_path"] = checkpoint_path
    if db_path:
        analyzer["db_path"] = db_path

    return {
        "thread_id": str(task_id or _DEFAULT_THREAD_ID),
        "checkpoint_path": str(checkpoint_path or _DEFAULT_CHECKPOINT_PATH),
        "db_path": db_path,
        "analyzer_model": model,
        "analyzer_base_url": base_url,
        "has_analyzer_api_key": bool(api_key),
        "api_key_source": (
            "kwargs"
            if _first_non_empty(kwargs.get("analyzer_api_key"), kwargs.get("api_key"))
            else "system"
            if system_api_key
            else "env"
            if env_api_key
            else "legacy_config"
            if legacy_api_key
            else None
        ),
    }
