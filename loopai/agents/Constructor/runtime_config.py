from __future__ import annotations

import os
from typing import Any

from loopai.schema.system_runtime import (
    RuntimeModelConfig,
    load_runtime_system_config,
    resolve_runtime_model_config,
)


def resolve_constructor_model_config(
    state: dict[str, Any],
    *,
    legacy_model: Any = None,
    legacy_base_url: Any = None,
    legacy_api_key: Any = None,
) -> RuntimeModelConfig:
    constructor = state.get("constructor") if isinstance(state.get("constructor"), dict) else {}
    system = load_runtime_system_config(
        task_id=state.get("task_id"),
        db_path=state.get("DB_PATH") or os.getenv("DB_PATH"),
    )
    return resolve_runtime_model_config(
        system,
        requested=constructor.get("model_path") or legacy_model,
        legacy_model=legacy_model,
        legacy_base_url=constructor.get("base_url") or legacy_base_url,
        legacy_api_key=(
            legacy_api_key
            or os.getenv("LOOPAI_CONSTRUCTOR_API_KEY")
            or constructor.get("api_key")
        ),
    )


def resolve_constructor_api_key(state: dict[str, Any]) -> str:
    return resolve_constructor_model_config(state).api_key
