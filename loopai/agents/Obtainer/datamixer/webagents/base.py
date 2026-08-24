"""Registry and contracts for DataMixer web-agent plugins."""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WebAgentSpec:
    """Metadata exposed by a registered web-agent plugin."""

    name: str
    version: str
    description: str
    input_type: str = "query"
    output_type: str = "url"


class WebAgent(ABC):
    """A DataMixer input agent that discovers and materializes web data."""

    spec: WebAgentSpec

    @abstractmethod
    def run(self, query: str, *, store, dataset: str, **kwargs) -> Any:
        """Run one query and materialize its result into ``store``."""


_REGISTRY: dict[str, Callable[..., WebAgent]] = {}


def register(name: str):
    """Register a web-agent factory under a stable DataMixer plugin name."""

    def deco(factory: Callable[..., WebAgent]):
        _REGISTRY[name] = factory
        return factory

    return deco


def create(name: str, **kwargs) -> WebAgent:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown webagent {name!r}; available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](**kwargs)


def available() -> list[WebAgentSpec]:
    specs = []
    for factory in _REGISTRY.values():
        spec = getattr(factory, "spec", None)
        if isinstance(spec, WebAgentSpec):
            specs.append(spec)
    return sorted(specs, key=lambda item: item.name)


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def param_info(name: str) -> list[dict[str, Any]]:
    factory = _REGISTRY[name]
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return []
    out = []
    for pname, param in signature.parameters.items():
        if pname == "self" or param.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        default = None if param.default is inspect.Parameter.empty else param.default
        out.append({
            "name": pname,
            "default": default,
            "kind": type(default).__name__ if default is not None else "str",
        })
    return out
