"""DataFlow interoperability (L4): run native OpenDCAI/DataFlow operators.

DataMixer operators speak ``process(batch: list[dict], ctx)``. The open-source
`DataFlow <https://github.com/OpenDCAI/DataFlow>`_ project speaks a different,
storage-centric contract::

    @OPERATOR_REGISTRY.register()
    class WordNumberFilter:
        def __init__(self, min_words=20, max_words=100000): ...
        def run(self, storage, input_key, output_key="word_number_filter_label"):
            df = storage.read("dataframe")      # a pandas DataFrame
            ...                                  # add columns / drop rows
            storage.write(df)                    # persist the result

This module bridges the two so *every* DataFlow operator becomes usable from
DataMixer's existing ``op run`` / ``pipeline run`` / web-console machinery with
**no per-operator wrapping**:

    datamixer op run dataflow --dataset demo \
        --arg op=WordNumberFilter --arg min_words=20 --arg kind=filter

The bridge:

1. turns the current batch of catalog rows into a tabular frame whose
   ``input_key`` column is each row's extracted text (plus a hidden ``__dm_idx``
   so survivors can be mapped back);
2. hands that frame to the DataFlow operator through an in-memory
   :class:`DataFlowStorage` shim (``read("dataframe")`` / ``write(df)``);
3. merges every column the operator produced back onto the originating rows, and
   for filter-style operators drops the rows the operator removed.

DataFlow itself depends on pandas (and much more); DataMixer stays
zero-dependency. The import is therefore lazy: the bridge only needs the
``dataflow`` package + pandas when it is actually *run*. ``op list`` and the
registry stay import-free.
"""
from __future__ import annotations

from typing import Any

from .. import utils
from .base import Batch, Operator, OperatorContext, OperatorSpec, register

# Hidden column carrying the originating row's position, so a DataFlow operator
# that drops/reorders rows can still be mapped back onto DataMixer rows.
_IDX = "__dm_idx"

# Keys the bridge consumes itself; everything else in ``--arg`` is forwarded to
# the wrapped DataFlow operator's ``__init__``.
_META_KEYS = {"op", "input_key", "output_key", "kind", "version", "run_kwargs"}


# ---------------------------------------------------------------------------
# storage shim
# ---------------------------------------------------------------------------

class DataFlowStorage:
    """Minimal in-memory implementation of DataFlow's storage surface.

    DataFlow operators call ``storage.read("dataframe")`` to get their input and
    ``storage.write(df)`` to persist their output. We hold a single frame in
    memory for the lifetime of one ``process`` call. ``read`` accepts (and
    ignores) the ``"dataframe"`` key DataFlow passes.
    """

    def __init__(self, frame: Any):
        self._frame = frame
        self.result: Any = None
        self.written = False

    def read(self, key: str = "dataframe") -> Any:  # noqa: ARG002 - parity
        return self._frame

    def write(self, frame: Any) -> Any:
        self.result = frame
        self.written = True
        return frame


# ---------------------------------------------------------------------------
# frame <-> rows (pandas when present, list-of-dicts otherwise)
# ---------------------------------------------------------------------------

def _to_frame(records: list[dict]) -> Any:
    """Build the frame handed to the DataFlow operator.

    Uses a real pandas ``DataFrame`` (what DataFlow operators expect). If pandas
    is unavailable we fall back to the plain ``records`` list so the bridge and
    its tests still exercise the read/write/merge contract without the heavy
    dependency.
    """
    try:
        import pandas as pd
    except ImportError:
        return records
    return pd.DataFrame(records)


def _from_frame(frame: Any) -> list[dict]:
    """Normalize whatever the operator wrote back into a list of dict records."""
    if frame is None:
        return []
    to_dict = getattr(frame, "to_dict", None)
    if callable(to_dict):                       # pandas DataFrame
        return to_dict("records")
    return list(frame)                          # list-of-dicts fallback


def _rows_to_records(batch: Batch, input_key: str) -> list[dict]:
    """Project DataMixer rows into flat records for the operator.

    Each record carries the extracted text under ``input_key`` and a hidden
    ``__dm_idx`` back-pointer. Pre-existing scalar metadata on the row is copied
    through (excluding the heavy/reserved ``content``) so operators that read
    other columns still see them.
    """
    records = []
    for i, row in enumerate(batch):
        rec = {k: v for k, v in row.items()
               if k not in ("content",) and not k.startswith("__")}
        rec[input_key] = utils.extract_text(row.get("content", row))
        rec[_IDX] = i
        records.append(rec)
    return records


def _merge_records(batch: Batch, records: list[dict], input_key: str,
                   is_filter: bool) -> Batch:
    """Fold operator output back onto the originating rows.

    New/changed columns are written onto the matching row by ``__dm_idx``; the
    synthetic ``input_key`` and ``__dm_idx`` columns are dropped. For filter
    operators only rows the operator kept are returned (others are left
    untouched and simply not re-emitted).
    """
    by_idx: dict[int, dict] = {}
    for rec in records:
        idx = rec.get(_IDX)
        if idx is None:
            continue
        by_idx[int(idx)] = rec

    out: Batch = []
    for i, row in enumerate(batch):
        rec = by_idx.get(i)
        if rec is None:
            if not is_filter:
                out.append(row)         # map/score op should never lose rows
            continue                    # filter dropped it
        for k, v in rec.items():
            if k == _IDX or k == input_key:
                continue
            row[k] = v
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# DataFlow package discovery
# ---------------------------------------------------------------------------

def dataflow_available() -> bool:
    """True if the ``dataflow`` (open-dataflow) package is importable."""
    import importlib.util
    return importlib.util.find_spec("dataflow") is not None


def _operator_registry():
    """Return DataFlow's global ``OPERATOR_REGISTRY`` (import paths vary by ver)."""
    last = None
    for mod in ("dataflow.utils.registry", "dataflow.core.registry",
                "dataflow.registry", "dataflow"):
        try:
            m = __import__(mod, fromlist=["OPERATOR_REGISTRY"])
        except ImportError as e:
            last = e
            continue
        reg = getattr(m, "OPERATOR_REGISTRY", None)
        if reg is not None:
            return reg
    raise ImportError(
        "could not locate DataFlow's OPERATOR_REGISTRY; install the operator "
        "framework with `pip install open-dataflow` "
        f"(last import error: {last})")


def load_dataflow_operator(name: str, **kwargs) -> Any:
    """Instantiate a DataFlow operator by its registered class name.

    Looks the class up in DataFlow's ``OPERATOR_REGISTRY`` (falling back to a
    direct attribute on the registry / module) and constructs it with
    ``kwargs``. Raises a clear, actionable error when DataFlow is not installed
    or the operator name is unknown.
    """
    if not dataflow_available():
        raise RuntimeError(
            "the DataFlow operator framework is not installed. Install it with "
            "`pip install open-dataflow` to use `op run dataflow` "
            "(see docs/DATAFLOW.md).")
    reg = _operator_registry()
    cls = None
    for getter in (lambda: reg.get(name), lambda: reg[name],
                   lambda: getattr(reg, name)):
        try:
            cls = getter()
        except (KeyError, AttributeError, TypeError):
            continue
        if cls is not None:
            break
    if cls is None:
        raise KeyError(
            f"unknown DataFlow operator {name!r}; check the operator catalog at "
            "https://opendcai.github.io/DataFlow-Doc/")
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# the registered bridge operator
# ---------------------------------------------------------------------------

@register("dataflow")
class DataFlowBridge(Operator):
    """Run any installed OpenDCAI/DataFlow operator inside a DataMixer pipeline.

    Args (via ``--arg`` or pipeline ``args``):

    * ``op``         — the DataFlow operator's registered class name (required at
      run time), e.g. ``WordNumberFilter``, ``RemoveExtraSpacesRefiner``.
    * ``input_key``  — text column the operator reads (default ``raw_content``).
    * ``output_key`` — column the operator writes (passed through to ``run``);
      omit to use the operator's own default.
    * ``kind``       — DataMixer kind for scheduling/lineage: ``filter`` if the
      operator drops rows, else ``map``/``score`` (default ``map``).
    * any other key  — forwarded to the DataFlow operator's ``__init__``
      (e.g. ``min_words=20``).
    """

    def __init__(self, op: str | None = None, input_key: str = "raw_content",
                 output_key: str | None = None, kind: str = "map",
                 version: str | None = None, run_kwargs: dict | None = None,
                 **op_kwargs: Any):
        self.op_name = op
        self.input_key = input_key
        self.output_key = output_key
        self.run_kwargs = run_kwargs or {}
        self.op_kwargs = op_kwargs
        valid = {"map", "filter", "score", "dedup", "embed", "agg"}
        kind = kind if kind in valid else "map"
        self.spec = OperatorSpec(
            "dataflow", version or "1.0", kind,
            description=(f"Bridge to DataFlow operator {op!r}." if op else
                        "Bridge to an installed DataFlow operator "
                        "(set --arg op=<ClassName>)."))
        self._op: Any = None

    def setup(self, ctx: OperatorContext) -> None:
        if not self.op_name:
            raise ValueError(
                "the `dataflow` operator needs an operator name: "
                "`--arg op=<DataFlowClassName>` (e.g. op=WordNumberFilter)")
        self._op = load_dataflow_operator(self.op_name, **self.op_kwargs)

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        if not batch:
            return batch
        if self._op is None:                     # tolerate direct process() use
            self.setup(ctx)
        records = _rows_to_records(batch, self.input_key)
        storage = DataFlowStorage(_to_frame(records))
        run_kwargs = dict(self.run_kwargs)
        if self.output_key is not None:
            run_kwargs.setdefault("output_key", self.output_key)
        self._op.run(storage, self.input_key, **run_kwargs)
        out_frame = storage.result if storage.written else storage.read()
        out_records = _from_frame(out_frame)
        return _merge_records(batch, out_records, self.input_key,
                              is_filter=self.spec.kind == "filter")
