"""Pluggable operator interface (L4).

The brief specifies an Arrow-`RecordBatch` operator contract for zero-copy /
cross-language execution at scale (Ray Data, Spark). For the zero-dependency
MVP we run the *same* contract over batches of plain ``dict`` rows -- the lowest
common denominator that any third-party / DataFlow operator can already produce
and consume. Swapping the batch type to ``pa.RecordBatch`` later is mechanical
and does not change the operator authoring model.

An operator is a pure-ish unit: ``setup`` once per worker, ``process`` per
batch, optional ``finalize`` for stateful flush (global dedup), ``teardown``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

Row = dict[str, Any]
Batch = list[Row]

VALID_KINDS = {"map", "filter", "score", "dedup", "embed", "agg"}


@dataclass
class OperatorContext:
    """Runtime context handed to every operator method."""
    run_id: str = "local"
    num_workers: int = 1
    device: str = "cpu"
    seed: int = 0
    root: str = ""             # warehouse path (LLM operators load the model pool)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class OperatorSpec:
    """Declaration the platform uses for scheduling and lineage."""
    name: str
    version: str
    kind: str
    stateful: bool = False
    gpu_required: bool = False
    batchable: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(
                f"invalid operator kind {self.kind!r}; choose from {VALID_KINDS}"
            )


class Operator(ABC):
    spec: OperatorSpec

    def setup(self, ctx: OperatorContext) -> None:  # noqa: B027 - optional hook
        ...

    @abstractmethod
    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        """Core transform.

        * map/score/embed: return same number of rows, new/changed keys.
        * filter: return a subset of the rows.
        * dedup/agg (stateful): may accumulate and flush in ``finalize``.
        """

    def finalize(self, ctx: OperatorContext) -> Optional[Iterator[Batch]]:
        return None

    def teardown(self, ctx: OperatorContext) -> None:  # noqa: B027 - optional hook
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Callable[..., Operator]] = {}


def register(name: str):
    def deco(factory: Callable[..., Operator]):
        _REGISTRY[name] = factory
        return factory
    return deco


def create(name: str, **kwargs) -> Operator:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown operator {name!r}; available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](**kwargs)


def available() -> list[OperatorSpec]:
    specs = []
    for factory in _REGISTRY.values():
        try:
            specs.append(factory().spec)
        except Exception:  # pragma: no cover - defensive
            continue
    return specs


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def param_info(name: str) -> list[dict]:
    """Introspect an operator's configurable args (for UI edit forms).

    Returns ``[{name, default, kind}]`` derived from the factory signature,
    skipping ``self`` and ``*args``/``**kwargs``.
    """
    import inspect
    factory = _REGISTRY[name]
    try:
        sig = inspect.signature(factory)
    except (TypeError, ValueError):
        return []
    out = []
    for pname, p in sig.parameters.items():
        if pname == "self" or p.kind in (
            inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL
        ):
            continue
        default = None if p.default is inspect.Parameter.empty else p.default
        kind = type(default).__name__ if default is not None else "str"
        out.append({"name": pname, "default": default, "kind": kind})
    return out


# ---------------------------------------------------------------------------
# DataFlow / third-party adapter (zero-intrusion接入)
# ---------------------------------------------------------------------------

class DataFlowAdapter(Operator):
    """Wrap a DataFlow-style callable (consumes/produces dict rows) as an
    Operator. The wrapped object must expose ``run(rows: list[dict], **kw)``."""

    def __init__(self, dataflow_op: Any, spec: OperatorSpec):
        self._op = dataflow_op
        self.spec = spec

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        return self._op.run(batch, **ctx.extra)
