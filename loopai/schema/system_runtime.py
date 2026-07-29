from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model_pool import StarterModelPool, load_starter_system_config_sync, resolve_secret


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def unwrap_config_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return unwrap_config_value(value.get("value"))
    if isinstance(value, dict):
        return {str(key): unwrap_config_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unwrap_config_value(item) for item in value]
    return value


def load_runtime_system_config(
    *,
    task_id: str | None = None,
    db_path: str | os.PathLike[str] | None = None,
    system_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the plain system config used by agents and standalone skills.

    A supplied config wins. Task-scoped snapshots are preferred when DB_PATH and
    TASK_ID are available; otherwise the current starter config is loaded.
    """
    if isinstance(system_config, dict):
        unwrapped = unwrap_config_value(system_config)
        return unwrapped if isinstance(unwrapped, dict) else {}

    resolved_task_id = str(task_id or os.getenv("TASK_ID") or os.getenv("task_id") or "").strip()
    resolved_db_path = str(db_path or os.getenv("DB_PATH") or "").strip()
    if resolved_task_id and resolved_db_path:
        try:
            from loopai.common.db_tool import get_task_system_config_sync

            result = get_task_system_config_sync(resolved_db_path, resolved_task_id)
            unwrapped = unwrap_config_value(result.get("config"))
            if isinstance(unwrapped, dict):
                return unwrapped
        except Exception:
            pass

    return load_starter_system_config_sync(workspace=PROJECT_ROOT, prefer_db=True)


def integration_config(system_config: dict[str, Any], name: str) -> dict[str, Any]:
    integrations = system_config.get("integrations")
    if not isinstance(integrations, dict):
        return {}
    value = integrations.get(name)
    return value if isinstance(value, dict) else {}


def resolve_integration_value(
    system_config: dict[str, Any],
    integration: str,
    field: str,
    *,
    explicit_values: Iterable[Any] = (),
    env_keys: Iterable[str] = (),
    legacy_system_keys: Iterable[str] = (),
    legacy_values: Iterable[Any] = (),
) -> str:
    """Resolve an integration setting without putting credentials in task state.

    Environment variables override stored values. New nested system config wins
    over legacy flat system keys; task-state values are accepted last during the
    compatibility window only.
    """
    env_value = _first_non_empty(*(os.getenv(key) for key in env_keys))
    nested_value = integration_config(system_config, integration).get(field)
    legacy_system_value = _first_non_empty(
        *(system_config.get(key) for key in legacy_system_keys)
    )
    value = _first_non_empty(
        *explicit_values,
        env_value,
        nested_value,
        legacy_system_value,
        *legacy_values,
    )
    return resolve_secret(value)


def has_explicit_model_pool(system_config: dict[str, Any]) -> bool:
    model_value = system_config.get("model")
    return bool(
        isinstance(model_value, list)
        or (
            isinstance(model_value, dict)
            and isinstance(
                model_value.get("pool")
                or model_value.get("models")
                or model_value.get("entries"),
                list,
            )
        )
        or isinstance(system_config.get("models"), list)
    )


@dataclass(frozen=True)
class RuntimeModelConfig:
    model: str
    base_url: str
    api_key: str
    source: str

    @property
    def ready(self) -> bool:
        return bool(self.model and self.base_url and self.api_key)


def resolve_runtime_model_config(
    system_config: dict[str, Any],
    *,
    requested: str | None = None,
    tier: str | None = None,
    legacy_model: Any = None,
    legacy_base_url: Any = None,
    legacy_api_key: Any = None,
) -> RuntimeModelConfig:
    """Resolve a runtime model, using the model pool as the single new source."""
    requested_name = str(requested or "").strip() or None
    if has_explicit_model_pool(system_config):
        pool = StarterModelPool(system_config)
        resolved_tier = tier or pool.default_tier
        entry = pool.find_entry(requested_name, tier=resolved_tier)
        provider_request = requested_name
        if entry is not None and requested_name and requested_name not in entry.aliases():
            provider_request = entry.name or entry.tier or entry.model_name
        provider = pool.resolve_proxy_provider(provider_request, tier=resolved_tier)
        if provider is not None:
            return RuntimeModelConfig(
                model=provider.model,
                base_url=provider.base_url,
                api_key=provider.api_key,
                source=provider.source,
            )

    model = _first_non_empty(
        requested_name,
        legacy_model,
        system_config.get("starter_model_name"),
        system_config.get("starter_model_path"),
        system_config.get("codex_model"),
    )
    base_url = _first_non_empty(
        legacy_base_url,
        system_config.get("starter_base_url"),
        system_config.get("codex_base_url"),
    )
    api_key = _first_non_empty(
        legacy_api_key,
        system_config.get("starter_api_key"),
        system_config.get("codex_api_key"),
    )
    return RuntimeModelConfig(
        model=str(model or ""),
        base_url=str(base_url or ""),
        api_key=resolve_secret(api_key),
        source="legacy.system",
    )


def build_integrations_from_legacy(
    system_config: dict[str, Any],
    default_states: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return normalized integrations while preserving already migrated values."""
    default_states = default_states or {}
    obtainer = default_states.get("obtainer") if isinstance(default_states.get("obtainer"), dict) else {}
    webcrawler = (
        default_states.get("webcrawler")
        if isinstance(default_states.get("webcrawler"), dict)
        else {}
    )
    current = system_config.get("integrations")
    integrations = dict(current) if isinstance(current, dict) else {}

    def ensure(name: str, values: dict[str, Any]) -> None:
        existing = integrations.get(name)
        section = dict(existing) if isinstance(existing, dict) else {}
        for key, value in values.items():
            if key not in section or section.get(key) in (None, ""):
                section[key] = value or ""
        integrations[name] = section

    ensure(
        "tavily",
        {
            "api_key": _first_non_empty(
                system_config.get("tavily_api_key"),
                obtainer.get("tavily_api_key"),
                webcrawler.get("tavily_api_key"),
                "",
            )
        },
    )
    ensure(
        "kaggle",
        {
            "username": _first_non_empty(
                system_config.get("kaggle_username"), obtainer.get("kaggle_username"), ""
            ),
            "key": _first_non_empty(system_config.get("kaggle_key"), obtainer.get("kaggle_key"), ""),
        },
    )
    ensure(
        "rag",
        {
            "base_url": _first_non_empty(
                system_config.get("rag_api_base_url"), obtainer.get("rag_api_base_url"), ""
            ),
            "api_key": _first_non_empty(
                system_config.get("rag_api_key"), obtainer.get("rag_api_key"), ""
            ),
        },
    )
    return integrations


def export_system_integrations_to_env(system_config: dict[str, Any]) -> dict[str, bool]:
    """Expose system integrations to legacy libraries without copying them to State."""
    mappings = {
        "TAVILY_API_KEY": resolve_integration_value(
            system_config,
            "tavily",
            "api_key",
            env_keys=("TAVILY_API_KEY",),
            legacy_system_keys=("tavily_api_key",),
        ),
        "KAGGLE_USERNAME": resolve_integration_value(
            system_config,
            "kaggle",
            "username",
            env_keys=("KAGGLE_USERNAME",),
            legacy_system_keys=("kaggle_username",),
        ),
        "KAGGLE_KEY": resolve_integration_value(
            system_config,
            "kaggle",
            "key",
            env_keys=("KAGGLE_KEY",),
            legacy_system_keys=("kaggle_key",),
        ),
        "RAG_API_BASE_URL": resolve_integration_value(
            system_config,
            "rag",
            "base_url",
            env_keys=("RAG_API_BASE_URL",),
            legacy_system_keys=("rag_api_base_url",),
        ),
        "RAG_API_KEY": resolve_integration_value(
            system_config,
            "rag",
            "api_key",
            env_keys=("RAG_API_KEY",),
            legacy_system_keys=("rag_api_key",),
        ),
    }
    exported: dict[str, bool] = {}
    for env_key, value in mappings.items():
        if value:
            os.environ.setdefault(env_key, value)
        exported[env_key] = bool(os.getenv(env_key))
    return exported


LEGACY_SYSTEM_INTEGRATION_KEYS = {
    "tavily_api_key",
    "kaggle_username",
    "kaggle_key",
    "rag_api_base_url",
    "rag_api_key",
}

LEGACY_STATE_CREDENTIAL_FIELDS = {
    "obtainer": {"api_key", "tavily_api_key", "rag_api_key", "kaggle_username", "kaggle_key"},
    "constructor": {"api_key"},
    "webcrawler": {"deepseek_api_key", "tavily_api_key"},
}


def migrate_legacy_credentials(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize stored config in place, excluding Trainer credentials by design."""
    system = config.setdefault("system", {})
    if not isinstance(system, dict):
        system = {}
        config["system"] = system
    default_states = config.setdefault("default_states", {})
    if not isinstance(default_states, dict):
        default_states = {}
        config["default_states"] = default_states

    system["integrations"] = build_integrations_from_legacy(system, default_states)
    for key in LEGACY_SYSTEM_INTEGRATION_KEYS:
        system.pop(key, None)
    for section_name, fields in LEGACY_STATE_CREDENTIAL_FIELDS.items():
        section = default_states.get(section_name)
        if not isinstance(section, dict):
            continue
        for field in fields:
            section.pop(field, None)
    return config


def strip_legacy_state_credentials(state: dict[str, Any]) -> dict[str, Any]:
    """Remove persisted credentials from an existing task state in place."""
    for section_name, fields in LEGACY_STATE_CREDENTIAL_FIELDS.items():
        section = state.get(section_name)
        if not isinstance(section, dict):
            continue
        for field in fields:
            section.pop(field, None)
    return state
