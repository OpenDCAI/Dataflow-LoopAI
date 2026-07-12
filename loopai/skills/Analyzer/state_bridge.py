from __future__ import annotations

import os
from typing import Any, Dict, Optional

from loopai.common.exception import ErrorCode, build_error_payload, build_success_payload


def _unwrap_config_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def unwrap_config_section(section_config: Any) -> Dict[str, Any]:
    if not isinstance(section_config, dict):
        return {}
    return {str(key): _unwrap_config_value(value) for key, value in section_config.items()}


def _task_id_from_env_or_arg(task_id: Optional[str] = None) -> Optional[str]:
    return task_id or os.getenv("TASK_ID") or os.getenv("task_id")


def load_analyzer_state_from_configer(task_id: Optional[str] = None) -> Dict[str, Any]:
    """Load task Analyzer state through Configer, returning a plain state dict.

    Configer owns DB access and schema handling. This helper only converts its
    UI/config-shaped values into the plain LoopAI state shape Analyzer nodes use.
    """
    from loopai.skills.Configer import get_configer_task_state_config

    resolved_task_id = _task_id_from_env_or_arg(task_id)
    result = get_configer_task_state_config("analyzer", task_id=resolved_task_id)
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or "failed to load analyzer config")

    data = result.get("data") or {}
    analyzer_config = unwrap_config_section(data.get("config"))
    return {
        "task_id": data.get("task_id") or resolved_task_id,
        "analyzer": analyzer_config,
    }


def load_system_runtime_config(task_id: Optional[str] = None) -> Dict[str, Any]:
    """Read task system runtime config when DB_PATH/TASK_ID are available."""
    db_path = os.getenv("DB_PATH")
    resolved_task_id = _task_id_from_env_or_arg(task_id)
    if not db_path or not resolved_task_id:
        return {}

    try:
        from loopai.common.db_tool import get_task_system_config_sync

        system_result = get_task_system_config_sync(db_path, resolved_task_id)
        return unwrap_config_section(system_result.get("config"))
    except Exception:
        return {}


def update_analyzer_state_via_configer(
    state: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist schema-compatible Analyzer fields through Configer.

    Non-schema runtime fields such as current/last_completed stay in memory and
    event stream; Configer intentionally rejects unknown fields.
    """
    try:
        from loopai.schema.states import get_state_config_schema
        from loopai.skills.Configer import update_configer_task_state_config
    except Exception as exc:
        return build_config_error(exc, "Analyzer Configer sync is unavailable.")

    analyzer = state.get("analyzer") if isinstance(state, dict) else {}
    if not isinstance(analyzer, dict):
        analyzer = {}

    allowed_fields = set(get_state_config_schema().get("analyzer", {}).keys())
    updates = {
        key: value
        for key, value in analyzer.items()
        if key in allowed_fields
    }
    if not updates:
        return build_success_payload(data={"config": {}}, message="No Analyzer state updates to persist.")

    result = update_configer_task_state_config(
        "analyzer",
        updates,
        task_id=_task_id_from_env_or_arg(task_id) or state.get("task_id"),
    )
    if not result.get("ok"):
        return result
    return result


def build_config_error(exc: BaseException, message: str) -> Dict[str, Any]:
    return build_error_payload(
        exc,
        code=ErrorCode.CONFIG_ERROR,
        recoverable=True,
        message=message,
    )
