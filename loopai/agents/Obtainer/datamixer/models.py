"""Model pool (L4 config) -- a registry of LLM endpoints that operators bind to.

Persisted per-warehouse at ``<root>/models.json``. Register a model once with
``datamixer model add`` and reference it by name from any LLM operator
(``--arg model=<name>``). Required fields mirror the brief: name, note, api_url,
api_key, response_format, model; everything else has a default.

API keys may be stored literally or as ``env:VARNAME`` (resolved from the
environment at call time, so secrets need not be written to disk).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# response_format selects the request/response wire shape the LLM client uses.
RESPONSE_FORMATS = ("openaichat", "response")

# Defaults for the optional knobs ("其他参数要有缺省值").
DEFAULTS = {
    "temperature": 0.0,
    "max_tokens": 1024,
    "timeout": 60,
    "max_concurrency": 64,
    "top_p": 1.0,
}


@dataclass
class ModelSpec:
    name: str
    api_url: str
    api_key: str = ""
    response_format: str = "openaichat"
    model: str = ""
    note: str = ""
    temperature: float = DEFAULTS["temperature"]
    max_tokens: int = DEFAULTS["max_tokens"]
    timeout: int = DEFAULTS["timeout"]
    max_concurrency: int = DEFAULTS["max_concurrency"]
    top_p: float = DEFAULTS["top_p"]
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.response_format not in RESPONSE_FORMATS:
            raise ValueError(
                f"response_format must be one of {RESPONSE_FORMATS}, "
                f"got {self.response_format!r}")
        if not self.model:
            self.model = self.name

    def resolved_key(self) -> str:
        """Resolve ``env:VAR`` references at call time."""
        if self.api_key.startswith("env:"):
            return os.environ.get(self.api_key[4:], "")
        return self.api_key

    def masked(self) -> dict:
        d = asdict(self)
        k = self.api_key
        if k and not k.startswith("env:"):
            d["api_key"] = (k[:4] + "***") if len(k) > 4 else "***"
        return d


class ModelPool:
    def __init__(self, root):
        self.path = Path(root) / "models.json"
        self._models: dict[str, dict] = {}
        if self.path.exists():
            self._models = json.loads(self.path.read_text()).get("models", {})

    def _save(self):
        self.path.write_text(
            json.dumps({"models": self._models}, ensure_ascii=False, indent=2))

    def add(self, spec: ModelSpec) -> None:
        self._models[spec.name] = asdict(spec)
        self._save()

    def get(self, name: str) -> ModelSpec:
        if name not in self._models:
            raise KeyError(
                f"model {name!r} not in pool (have: {sorted(self._models)})")
        return ModelSpec(**self._models[name])

    def remove(self, name: str) -> bool:
        if name in self._models:
            del self._models[name]
            self._save()
            return True
        return False

    def names(self) -> list[str]:
        return sorted(self._models)

    def list(self) -> list[dict]:
        return [ModelSpec(**v).masked() for v in self._models.values()]
