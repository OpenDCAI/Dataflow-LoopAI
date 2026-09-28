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
MODEL_ROLES = ("codex", "medium", "mid", "default", "looper", "rollout")
ROLE_ALIASES = {"mid": "medium", "medium_strength": "medium", "operator": "medium", "dataflow": "rollout"}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _text_or_empty(value: Any) -> str:
    """Convert optional config values without turning ``None`` into ``"None"``."""
    return "" if value is None else str(value)


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
    model_payload = {
        "proxy_base_url": pool.proxy_base_url(),
        "proxy_api_key": pool.proxy_api_key(),
        "default_model": pool.default_model,
        "codex_model": pool.codex_model,
        "looper_model": pool.looper_model,
        "default_tier": pool.default_tier,
        "pool": [entry.config_dict(include_secret=True) for entry in pool.entries],
    }
    # Preserve role selectors and service sub-configs when normalizing a
    # config loaded from YAML/DB.  Earlier normalization accidentally dropped
    # these fields, making medium/rollout role selection impossible on the
    # second load.
    raw_model = system_config.get("model") if isinstance(system_config.get("model"), dict) else {}
    for key in (
        "medium_model", "mid_model", "analyzer_model", "analyze_model",
        "dataflow_model", "operator_model", "operator_llm_model",
        "rollout_model", "eval_model", "judger_rollout_model", "embedding", "mineru",
    ):
        if key in raw_model:
            model_payload[key] = raw_model[key]
    normalized["model"] = model_payload
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
            api_key=_text_or_empty(_first_non_empty(raw.get("api_key"), raw.get("key"), "")),
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
        # Keep env references intact when serializing a loaded config.  This
        # avoids expanding secrets into normalized YAML/DB payloads while
        # literal keys remain available to the runtime caller.
        if include_secret:
            payload["api_key"] = (
                self.api_key if self.api_key.startswith("env:") else self.resolved_api_key()
            )
        else:
            payload["api_key"] = mask_secret(self.api_key)
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
    role: str = "default"
    fallback: bool = False
    requested: str = ""

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
            "role": self.role,
            "fallback": self.fallback,
            "requested_model": self.requested,
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

    def _role_request(self, role: str) -> str:
        """Return an optional explicit selector for a model-pool role."""
        role = str(role or "default").strip().lower()
        keys = {
            "codex": ("codex_model", "codex_model_pool_name", "codex_model_name"),
            "medium": (
                "medium_model", "mid_model", "analyzer_model", "analyze_model",
                "dataflow_model", "operator_model", "operator_llm_model",
            ),
            "default": ("default_model",),
            "looper": ("looper_model",),
            "rollout": ("rollout_model", "eval_model", "judger_rollout_model"),
        }
        for key in keys.get(role, ()):
            value = self.model_config.get(key)
            if value is None:
                value = self.system_config.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

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
        if self.entries:
            port = self.system_config.get("api_port") or 8855
            return normalize_v1_base_url(f"http://127.0.0.1:{port}/responseProxy")
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

    def resolve_role_entry(
        self,
        role: str = "default",
        requested: str | None = None,
        *,
        include_disabled: bool = False,
    ) -> tuple[ModelPoolEntry | None, bool, str]:
        """Resolve the canonical model entry for an agent role.

        Roles are deliberately resolved here rather than independently in
        Judger/Analyzer/Trainer/Obtainer.  ``medium`` falls back to the
        configured default entry when no medium-tier model exists; ``rollout``
        prefers an explicitly registered Judger/vLLM entry and otherwise uses
        medium.  The boolean in the return tuple records whether fallback was
        used, which is useful for state/report provenance.
        """
        role = ROLE_ALIASES.get(str(role or "default").strip().lower(), str(role or "default").strip().lower())
        if role not in MODEL_ROLES:
            role = "default"
        requested_name = str(requested or "").strip() or self._role_request(role)
        if requested_name:
            matched = self.get_entry_by_name(requested_name, include_disabled=include_disabled)
            if matched is not None:
                return matched, False, "explicit"

        if role == "codex":
            matched = self.codex_entry(include_disabled=include_disabled)
            if matched is not None:
                return matched, True, "default_fallback"
            # codex_entry already falls back to default; keep provenance clear.
            return self.default_entry(include_disabled=include_disabled), True, "default_fallback"
        if role == "looper":
            matched = self.looper_entry(include_disabled=include_disabled)
            return matched, True, "default_fallback"
        if role == "rollout":
            # A running Judger vLLM is intentionally kept alive and marked in
            # ``extra``.  It is the preferred rollout driver for difficulty
            # screening when no explicit selector was provided.
            for entry in reversed(self._candidates(include_disabled=include_disabled)):
                extra = entry.extra or {}
                if entry.source == "judger.vllm" or extra.get("source") == "judger.vllm" or extra.get("keep_alive") is True:
                    return entry, False, "judger_vllm"
            medium, _, _ = self.resolve_role_entry("medium", include_disabled=include_disabled)
            return medium, True, "medium_fallback"
        if role == "medium":
            medium = self.find_entry(tier="medium", include_disabled=include_disabled)
            if medium is not None and medium.tier == "medium":
                return medium, False, "tier_medium"
            fallback = self.default_entry(include_disabled=include_disabled)
            return fallback, True, "default_fallback"
        default = self.default_entry(include_disabled=include_disabled)
        return default, False, "default"

    def resolve_role_provider(
        self,
        role: str = "default",
        requested: str | None = None,
    ) -> ResolvedModelProvider | None:
        """Resolve a role and route it through the shared response proxy."""
        normalized_role = ROLE_ALIASES.get(str(role or "default").strip().lower(), str(role or "default").strip().lower())
        entry, fallback, source = self.resolve_role_entry(normalized_role, requested)
        if entry is None:
            return None
        # Proxy aliases are request keys.  Keep upstream model_name separate;
        # changing ``model`` to the upstream name would bypass proxy routing.
        via_proxy = self.has_proxy()
        return ResolvedModelProvider(
            base_url=self.proxy_base_url() if via_proxy else entry.base_url,
            api_key=self.proxy_api_key() if via_proxy else entry.resolved_api_key(),
            model=entry.name if via_proxy else entry.model_name,
            tier=entry.tier,
            name=entry.name,
            upstream_model_name=entry.model_name,
            source=f"{entry.source}:{source}",
            role=normalized_role,
            fallback=fallback,
            requested=str(requested or ""),
        )

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
        # The proxy request must always use the registered alias.  Callers may
        # resolve an entry by upstream model name, but forwarding that value
        # would make transport provenance ambiguous and can bypass role
        # selection in downstream clients.
        model_alias = str(entry.name or entry.tier or entry.model_name)
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
    db_candidates: list[Path] = []
    explicit_db = os.getenv("DB_PATH")
    if explicit_db:
        db_candidates.append(Path(explicit_db))
    roots = []
    if workspace:
        roots.append(Path(workspace))
    roots.append(Path.cwd())
    db_candidates.extend(root / "api" / "db" / "db.sqlite3" for root in roots)
    seen: set[str] = set()
    for db_path in db_candidates:
        key = str(db_path.expanduser().resolve()) if db_path.exists() else str(db_path)
        if key in seen:
            continue
        seen.add(key)
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


def register_model_pool_entry(
    system_config: dict[str, Any],
    *,
    name: str,
    model_name: str,
    base_url: str,
    api_key: str = "",
    tier: str = "medium",
    wire_api: str = "chat",
    source: str = "system.model.pool",
    extra: dict[str, Any] | None = None,
    enabled: bool = True,
) -> ModelPoolEntry:
    """Register or update one entry in an in-memory Starter config.

    This is the small, persistence-neutral primitive used by Judger when it
    starts a model-serving process.  Callers may persist the returned config
    through their normal Configer path; no credentials are logged or copied to
    task state by this helper.
    """
    if not isinstance(system_config, dict):
        raise TypeError("system_config must be a mapping")
    model_cfg = system_config.setdefault("model", {})
    if isinstance(model_cfg, list):
        pool_raw = model_cfg
    elif isinstance(model_cfg, dict):
        pool_raw = model_cfg.setdefault("pool", [])
        if not isinstance(pool_raw, list):
            pool_raw = []
            model_cfg["pool"] = pool_raw
    else:
        model_cfg = {"pool": []}
        system_config["model"] = model_cfg
        pool_raw = model_cfg["pool"]
    entry_raw = {
        "tier": tier,
        "name": name,
        "model_name": model_name,
        "base_url": base_url,
        "api_key": api_key,
        "wire_api": wire_api,
        "source": source,
        "enabled": enabled,
        "extra": dict(extra or {}),
    }
    replaced = False
    for index, raw in enumerate(pool_raw):
        if isinstance(raw, dict) and str(raw.get("name") or "").strip() == str(name).strip():
            pool_raw[index] = {**raw, **entry_raw}
            replaced = True
            break
    if not replaced:
        pool_raw.append(entry_raw)
    return ModelPoolEntry.from_raw(entry_raw, source=source)


def register_running_vllm(
    model_name: str,
    base_url: str,
    *,
    api_key: str = "",
    name: str | None = None,
    source: str = "judger.vllm",
    task_id: str | None = None,
    run_id: str | None = None,
    pid: int | None = None,
    port: int | str | None = None,
    command: str | None = None,
    tier: str = "medium",
    system_config: dict[str, Any] | None = None,
    workspace: str | Path | None = None,
    starter_config: str | Path | None = None,
    persist: bool = False,
) -> ModelPoolEntry:
    """Register a live Judger vLLM endpoint for subsequent DataFlow rollout.

    ``system_config`` can be supplied by callers that already loaded Configer.
    With ``persist=True`` the helper updates an explicit starter YAML (or the
    local starter DB when available); persistence is best-effort and the
    returned entry remains authoritative for the current process.
    """
    if not str(model_name or "").strip():
        raise ValueError("model_name is required to register running vLLM")
    endpoint = normalize_v1_base_url(base_url)
    if not endpoint:
        raise ValueError("base_url is required to register running vLLM")
    entry_name = str(name or f"eval:{task_id or run_id or model_name}").strip()
    metadata = {
        "source": source,
        "task_id": task_id,
        "run_id": run_id,
        "pid": pid,
        "port": int(port) if str(port or "").isdigit() else port,
        "command": command or "",
        "managed": True,
        "keep_alive": True,
    }
    metadata = {key: value for key, value in metadata.items() if value not in (None, "")}
    if system_config is None:
        system_config = load_starter_system_config_sync(workspace=workspace, starter_config=starter_config, prefer_db=True)
    register_model_pool_entry(
        system_config,
        name=entry_name,
        model_name=str(model_name),
        base_url=endpoint,
        api_key=api_key,
        tier=tier,
        wire_api="chat",
        source=source,
        extra=metadata,
        enabled=True,
    )
    if isinstance(system_config.get("model"), dict):
        system_config["model"]["rollout_model"] = entry_name
    entry = StarterModelPool(system_config).get_entry_by_name(entry_name, include_disabled=True)
    assert entry is not None
    if persist:
        _persist_system_model_pool(system_config, workspace=workspace, starter_config=starter_config)
    return entry


def _persist_system_model_pool(
    system_config: dict[str, Any],
    *,
    workspace: str | Path | None = None,
    starter_config: str | Path | None = None,
) -> bool:
    """Persist only the model-pool projection without requiring app services."""
    def merge_judger_entries(target_system: dict[str, Any]) -> None:
        raw_model = system_config.get("model") if isinstance(system_config.get("model"), dict) else {}
        for raw in raw_model.get("pool", []) if isinstance(raw_model.get("pool"), list) else []:
            if not isinstance(raw, dict):
                continue
            extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
            if raw.get("source") != "judger.vllm" and extra.get("source") != "judger.vllm":
                continue
            register_model_pool_entry(
                target_system,
                name=str(raw.get("name") or ""),
                model_name=str(raw.get("model_name") or raw.get("model") or ""),
                base_url=str(raw.get("base_url") or raw.get("api_url") or ""),
                api_key=str(raw.get("api_key") or ""),
                tier=str(raw.get("tier") or "medium"),
                wire_api=str(raw.get("wire_api") or "chat"),
                source="judger.vllm",
                extra=extra,
                enabled=bool(raw.get("enabled", True)),
            )
            if isinstance(target_system.get("model"), dict):
                target_system["model"]["rollout_model"] = str(raw.get("name") or "")

    explicit = starter_config or os.getenv("STARTER_CONFIG") or os.getenv("STARTER_CONFIG_PATH")
    persisted = False
    path = Path(explicit).expanduser() if explicit else None
    if path is None and workspace:
        candidate = Path(workspace) / "starter.yaml"
        if candidate.exists():
            path = candidate
    if path is not None and path.exists():
        try:
            import yaml
            payload = load_yaml_config(path)
            payload["system"] = dict(payload.get("system") or {})
            merge_judger_entries(payload["system"])
            temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            os.replace(temp, path)
            persisted = True
        except Exception:
            pass
    # Prefer the task/application DB when it is available.  This updates only
    # ``system.model`` in the StarterConfig JSON row and leaves all other
    # settings untouched.
    db_path = os.getenv("DB_PATH")
    if not db_path and workspace:
        candidate_db = Path(workspace) / "api" / "db" / "db.sqlite3"
        if candidate_db.exists():
            db_path = str(candidate_db)
    if db_path:
        try:
            con = sqlite3.connect(str(db_path))
            try:
                row = con.execute("select id, config from starterconfig where name=?", ("starter",)).fetchone()
                if row:
                    payload = json.loads(row[1] or "{}")
                    payload["system"] = dict(payload.get("system") or {})
                    merge_judger_entries(payload["system"])
                    con.execute("update starterconfig set config=? where id=?", (json.dumps(payload, ensure_ascii=False), row[0]))
                    con.commit()
                    return True
            finally:
                con.close()
        except Exception:
            pass
    return persisted


__all__ = [
    "TIERS", "MODEL_ROLES", "ROLE_ALIASES", "ModelPoolEntry", "ResolvedModelProvider",
    "StarterModelPool", "register_model_pool_entry", "register_running_vllm",
    "load_starter_system_config_sync", "chat_completions_url", "responses_url",
]
