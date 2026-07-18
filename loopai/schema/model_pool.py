from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


TIERS = ("high", "medium", "low")
DEFAULT_TIER = "medium"
DEFAULT_PROXY_API_KEY = "loopai-local-proxy"


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def normalize_v1_base_url(base_url: str) -> str:
    trimmed = str(base_url or "").strip().rstrip("/")
    if not trimmed:
        return ""
    if trimmed.endswith("/v1"):
        return trimmed
    return f"{trimmed}/v1"


def chat_completions_url(base_url: str) -> str:
    trimmed = str(base_url or "").strip().rstrip("/")
    if not trimmed:
        return ""
    if trimmed.endswith("/chat/completions"):
        return trimmed
    if trimmed.endswith("/v1"):
        return f"{trimmed}/chat/completions"
    return f"{trimmed}/v1/chat/completions"


def responses_url(base_url: str) -> str:
    trimmed = str(base_url or "").strip().rstrip("/")
    if not trimmed:
        return ""
    if trimmed.endswith("/responses"):
        return trimmed
    if trimmed.endswith("/v1"):
        return f"{trimmed}/responses"
    return f"{trimmed}/v1/responses"


def normalize_wire_api(value: Any) -> str:
    raw = str(value or "auto").strip().lower().replace("_", "-")
    if raw in {"", "auto", "detect"}:
        return "auto"
    if raw in {"response", "responses", "openai-response", "openai-responses"}:
        return "responses"
    if raw in {
        "chat",
        "chat-completion",
        "chat-completions",
        "completion",
        "completions",
        "openaichat",
        "openai-chat",
    }:
        return "chat"
    return raw


def resolve_secret(value: Any) -> str:
    text = str(value or "")
    if text.startswith("env:"):
        return os.environ.get(text[4:], "")
    return text


def mask_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("env:"):
        return text
    if len(text) <= 4:
        return "***"
    return f"{text[:4]}***"


def _normalize_loaded_system_config(system_config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(system_config, dict):
        return {}
    normalized = dict(system_config)
    pool = StarterModelPool(normalized)
    normalized["model"] = {
        "proxy_base_url": pool.proxy_base_url(),
        "proxy_api_key": pool.proxy_api_key(),
        "default_model": pool.default_model,
        "codex_model": pool.codex_model,
        "looper_model": pool.looper_model,
        "default_tier": pool.default_tier,
        "pool": [entry.config_dict(include_secret=True) for entry in pool.entries],
    }
    return normalized


@dataclass
class ModelPoolEntry:
    tier: str
    name: str
    model_name: str
    base_url: str
    api_key: str = ""
    maxworker: int = 1
    wire_api: str = "auto"
    enabled: bool = True
    source: str = "system.model.pool"
    response_format: str = ""
    note: str = ""
    extra: dict[str, Any] | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any], *, index: int = 0, source: str = "system.model.pool") -> "ModelPoolEntry":
        tier = str(raw.get("tier") or raw.get("level") or DEFAULT_TIER).strip().lower()
        if tier not in TIERS:
            tier = DEFAULT_TIER
        model_name = str(
            _first_non_empty(
                raw.get("model_name"),
                raw.get("model"),
                raw.get("model_path"),
                raw.get("name"),
                f"{tier}-{index + 1}",
            )
        )
        name = str(_first_non_empty(raw.get("name"), raw.get("alias"), tier, model_name)).strip()
        base_url = normalize_v1_base_url(str(_first_non_empty(raw.get("base_url"), raw.get("api_url"), raw.get("url"), "")))
        try:
            maxworker = int(_first_non_empty(raw.get("maxworker"), raw.get("max_worker"), raw.get("max_workers"), 1))
        except (TypeError, ValueError):
            maxworker = 1
        wire_api = normalize_wire_api(_first_non_empty(raw.get("wire_api"), raw.get("response_format"), raw.get("format"), "auto"))
        return cls(
            tier=tier,
            name=name,
            model_name=model_name,
            base_url=base_url,
            api_key=str(_first_non_empty(raw.get("api_key"), raw.get("key"), "")),
            maxworker=max(1, maxworker),
            wire_api=wire_api,
            enabled=bool(raw.get("enabled", True)),
            source=str(raw.get("source") or source),
            response_format=str(raw.get("response_format") or ""),
            note=str(raw.get("note") or ""),
            extra=dict(raw.get("extra") or {}),
        )

    def resolved_api_key(self) -> str:
        return resolve_secret(self.api_key)

    def aliases(self) -> set[str]:
        return {item for item in {self.name, self.tier, self.model_name} if item}

    def public_dict(self, *, include_secret: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        payload["api_key"] = self.resolved_api_key() if include_secret else mask_secret(self.api_key)
        payload["api_key_set"] = bool(self.resolved_api_key())
        payload["aliases"] = sorted(self.aliases())
        return payload

    def config_dict(self, *, include_secret: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["api_key"] = self.resolved_api_key() if include_secret else mask_secret(self.api_key)
        return payload

    @classmethod
    def empty(cls, *, name: str = "starter", tier: str = DEFAULT_TIER, source: str = "system.model.pool.empty") -> "ModelPoolEntry":
        return cls(
            tier=tier if tier in TIERS else DEFAULT_TIER,
            name=name or "starter",
            model_name="",
            base_url="",
            api_key="",
            maxworker=1,
            wire_api="chat",
            enabled=False,
            source=source,
            response_format="",
            note="",
            extra={},
        )


@dataclass
class ResolvedModelProvider:
    base_url: str
    api_key: str
    model: str
    tier: str
    name: str
    upstream_model_name: str
    source: str

    def as_provider(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "model_provider": "loopai_model_pool_proxy",
            "provider_name": "LoopAI Model Pool Proxy",
            "wire_api": "responses",
            "supports_websockets": False,
        }

    def meta(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model_pool_name": self.name,
            "tier": self.tier,
            "base_url": self.base_url,
            "model": self.model,
            "upstream_model_name": self.upstream_model_name,
            "api_key_source": "starter_model_pool_proxy",
        }


class StarterModelPool:
    def __init__(self, system_config: dict[str, Any] | None = None):
        self.system_config = system_config or {}
        self.model_config = self._model_config(self.system_config)
        self.entries = self._entries_from_system(self.system_config)
        self.default_tier = str(self.model_config.get("default_tier") or DEFAULT_TIER).strip().lower()
        if self.default_tier not in TIERS:
            self.default_tier = DEFAULT_TIER
        self.default_model = str(self.model_config.get("default_model") or "").strip()
        self.codex_model = str(self.model_config.get("codex_model") or "").strip()
        self.looper_model = str(self.model_config.get("looper_model") or "").strip()

    @staticmethod
    def _model_config(system_config: dict[str, Any]) -> dict[str, Any]:
        value = system_config.get("model")
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _raw_pool(system_config: dict[str, Any]) -> list[dict[str, Any]]:
        model_value = system_config.get("model")
        if isinstance(model_value, list):
            return [item for item in model_value if isinstance(item, dict)]
        if isinstance(model_value, dict):
            for key in ("pool", "models", "entries"):
                raw = model_value.get(key)
                if isinstance(raw, list):
                    return [item for item in raw if isinstance(item, dict)]
        return []

    @classmethod
    def _entries_from_system(cls, system_config: dict[str, Any]) -> list[ModelPoolEntry]:
        return [
            ModelPoolEntry.from_raw(raw, index=index)
            for index, raw in enumerate(cls._raw_pool(system_config))
        ]

    def proxy_base_url(self) -> str:
        configured = self.model_config.get("proxy_base_url")
        if configured:
            return normalize_v1_base_url(str(configured))
        return ""

    def proxy_api_key(self) -> str:
        return str(_first_non_empty(self.model_config.get("proxy_api_key"), DEFAULT_PROXY_API_KEY))

    def has_proxy(self) -> bool:
        return bool(self.proxy_base_url())

    def empty_entry(self, *, name: str = "starter", tier: str | None = None) -> ModelPoolEntry:
        return ModelPoolEntry.empty(name=name, tier=tier or self.default_tier)

    def public_entries(self) -> list[dict[str, Any]]:
        return [entry.public_dict() for entry in self.entries]

    def _candidates(self, *, include_disabled: bool = False) -> list[ModelPoolEntry]:
        if include_disabled:
            return list(self.entries)
        return [entry for entry in self.entries if entry.enabled]

    def get_entry_by_name(self, name: str | None, *, include_disabled: bool = False) -> ModelPoolEntry | None:
        requested = str(name or "").strip()
        if not requested:
            return None
        for entry in self._candidates(include_disabled=include_disabled):
            if requested == entry.name:
                return entry
        for entry in self._candidates(include_disabled=include_disabled):
            if requested == entry.model_name:
                return entry
        return None

    def default_entry(self, *, include_disabled: bool = False) -> ModelPoolEntry | None:
        entry = self.get_entry_by_name(self.default_model, include_disabled=include_disabled)
        if entry is not None:
            return entry
        return self.find_entry(tier=self.default_tier, include_disabled=include_disabled)

    def codex_entry(self, *, include_disabled: bool = False) -> ModelPoolEntry | None:
        entry = self.get_entry_by_name(self.codex_model, include_disabled=include_disabled)
        if entry is not None:
            return entry
        return self.default_entry(include_disabled=include_disabled)

    def looper_entry(self, *, include_disabled: bool = False) -> ModelPoolEntry | None:
        entry = self.get_entry_by_name(self.looper_model, include_disabled=include_disabled)
        if entry is not None:
            return entry
        return self.default_entry(include_disabled=include_disabled)

    def model_config_by_name(
        self,
        name: str | None = None,
        *,
        selector: str | None = None,
        include_disabled: bool = True,
    ) -> dict[str, Any]:
        entry = None
        if name:
            entry = self.get_entry_by_name(name, include_disabled=include_disabled)
        elif selector == "codex_model":
            entry = self.codex_entry(include_disabled=include_disabled)
        elif selector == "looper_model":
            entry = self.looper_entry(include_disabled=include_disabled)
        else:
            entry = self.default_entry(include_disabled=include_disabled)
        return (entry or self.empty_entry()).config_dict(include_secret=True)

    def find_entry(
        self,
        requested: str | None = None,
        *,
        tier: str | None = None,
        include_disabled: bool = False,
    ) -> ModelPoolEntry | None:
        candidates = self._candidates(include_disabled=include_disabled)
        if not candidates:
            return None
        requested = str(requested or "").strip()
        if requested:
            matched = self.get_entry_by_name(requested, include_disabled=include_disabled)
            if matched is not None:
                return matched
            if requested.lower() not in TIERS and not tier:
                return None
        resolved_tier = str(tier or requested or self.default_tier).strip().lower()
        if resolved_tier in TIERS:
            for entry in candidates:
                if entry.tier == resolved_tier:
                    return entry
        if self.default_model:
            matched = self.get_entry_by_name(self.default_model, include_disabled=include_disabled)
            if matched is not None:
                return matched
        for entry in candidates:
            if entry.tier == self.default_tier:
                return entry
        return candidates[0]

    def resolve_proxy_provider(self, requested: str | None = None, *, tier: str | None = None) -> ResolvedModelProvider | None:
        entry = self.find_entry(requested, tier=tier)
        if entry is None:
            return None
        model_alias = str(requested or entry.name or entry.tier or entry.model_name)
        return ResolvedModelProvider(
            base_url=self.proxy_base_url(),
            api_key=self.proxy_api_key(),
            model=model_alias,
            tier=entry.tier,
            name=entry.name,
            upstream_model_name=entry.model_name,
            source=entry.source,
        )

    def to_config_dict(self) -> dict[str, Any]:
        return {
            "proxy_base_url": self.proxy_base_url(),
            "proxy_api_key": mask_secret(self.proxy_api_key()),
            "default_model": self.default_model,
            "codex_model": self.codex_model,
            "looper_model": self.looper_model,
            "default_tier": self.default_tier,
            "pool": self.public_entries(),
        }


def starter_config_candidates(workspace: str | Path | None = None, starter_config: str | Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    explicit = starter_config or os.getenv("STARTER_CONFIG") or os.getenv("STARTER_CONFIG_PATH")
    if explicit:
        candidates.append(Path(explicit))
    if workspace:
        root = Path(workspace)
        candidates.extend([root / "starter.yaml", root / "examples" / "config" / "starter.yaml"])
    candidates.extend([Path.cwd() / "starter.yaml", Path.cwd() / "examples" / "config" / "starter.yaml"])
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser().resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        try:
            from omegaconf import OmegaConf

            loaded = OmegaConf.to_container(OmegaConf.load(str(path)), resolve=True)
        except ImportError:
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_starter_config_from_yaml(workspace: str | Path | None = None, starter_config: str | Path | None = None) -> dict[str, Any]:
    for path in starter_config_candidates(workspace=workspace, starter_config=starter_config):
        if not path.exists():
            continue
        loaded = load_yaml_config(path)
        if loaded:
            system = loaded.get("system")
            if isinstance(system, dict):
                loaded = dict(loaded)
                loaded["system"] = _normalize_loaded_system_config(system)
            return loaded
    return {}


def load_starter_config_from_db(workspace: str | Path | None = None) -> dict[str, Any]:
    roots = []
    if workspace:
        roots.append(Path(workspace))
    roots.append(Path.cwd())
    for root in roots:
        db_path = root / "api" / "db" / "db.sqlite3"
        if not db_path.exists():
            continue
        try:
            con = sqlite3.connect(str(db_path))
            try:
                row = con.execute("select config from starterconfig where name=?", ("starter",)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if not row or not row[0]:
            continue
        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            system = payload.get("system")
            if isinstance(system, dict):
                payload = dict(payload)
                payload["system"] = _normalize_loaded_system_config(system)
            return payload
    return {}


def load_starter_system_config_sync(
    workspace: str | Path | None = None,
    starter_config: str | Path | None = None,
    *,
    prefer_db: bool = True,
) -> dict[str, Any]:
    explicit_config = starter_config or os.getenv("STARTER_CONFIG") or os.getenv("STARTER_CONFIG_PATH")
    payload = load_starter_config_from_db(workspace) if prefer_db and explicit_config is None else {}
    if not payload:
        payload = load_starter_config_from_yaml(workspace=workspace, starter_config=explicit_config)
    system = payload.get("system", {}) if isinstance(payload, dict) else {}
    if not isinstance(system, dict):
        return {}
    return _normalize_loaded_system_config(system)
