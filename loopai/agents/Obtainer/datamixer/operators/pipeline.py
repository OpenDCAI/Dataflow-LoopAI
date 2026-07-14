"""Declarative operator pipelines (L4 scheduling/materialization/lineage).

A pipeline is described in YAML (see ``examples/pipeline.yaml``); the runner
streams batches of catalog samples through the operator DAG and writes the
enriched metadata back, recording lineage. This is the single-node Ray-Data
stand-in: same authoring model, swappable engine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import yaml

from .. import utils
from ..store import DataStore
from . import base
from . import builtin  # noqa: F401 - registers built-in operators


@dataclass
class StageResult:
    name: str
    kind: str
    version: str
    rows_in: int
    rows_out: int


@dataclass
class PipelineResult:
    name: str
    source_dataset: str | None
    selected: int = 0
    stages: list[StageResult] = field(default_factory=list)
    written: int = 0
    dropped: int = 0
    embedded: int = 0
    run_id: str = ""
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "pipeline": self.name,
            "run_id": self.run_id,
            "source_dataset": self.source_dataset,
            "selected": self.selected,
            "written": self.written,
            "dropped": self.dropped,
            "embedded": self.embedded,
            "elapsed_s": round(self.elapsed_s, 3),
            "stages": [s.__dict__ for s in self.stages],
        }


def load_pipeline(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if "pipeline" not in doc:
        raise ValueError("pipeline file must have a top-level 'pipeline' key")
    return doc["pipeline"]


def run_pipeline(
    store: DataStore,
    spec: dict,
    batch_size: int = 512,
    seed: int = 0,
) -> PipelineResult:
    name = spec.get("name", "pipeline")
    src = spec.get("source", {}) or {}
    dataset = src.get("dataset")
    where = src.get("filter")
    dataset_id = store.catalog.resolve_dataset(dataset) if dataset else None

    run_id = "run-" + utils.fingerprint({"p": name, "ts": time.time()})
    result = PipelineResult(name=name, source_dataset=dataset, run_id=run_id)
    t0 = time.time()

    # build the operator chain once; setup each
    ops = []
    for op_spec in spec.get("operators", []):
        args = op_spec.get("args", {}) or {}
        op = base.create(op_spec["name"], **args)
        op_ctx = base.OperatorContext(
            run_id=run_id, seed=seed, device=op_spec.get("device", "cpu"),
            root=str(store.root), extra=args)
        op.setup(op_ctx)
        ops.append((op, op_ctx))
        result.stages.append(
            StageResult(op_spec["name"], op.spec.kind, op.spec.version, 0, 0))

    reserved = {"content", "sample_id", "dataset_id", "cid", "created_at",
                "version", "tags", "embedding"}  # 'embedding' -> vector index
    from array import array as _array
    counters = {"written": 0, "embedded": 0}

    def writeback(rows):
        for r in rows:
            sid = r.get("sample_id")
            if not sid:
                continue
            if r.get("embedding") is not None:
                store.index.vectors.add(sid, _array("f", r["embedding"]))
                counters["embedded"] += 1
            updates = {k: v for k, v in r.items() if k not in reserved}
            if updates:
                store.catalog.update_fields(sid, updates)
            counters["written"] += 1
        store.catalog.commit()

    # single pass, constant memory: stream batches through the whole chain
    selected = 0
    for batch in store.catalog.iter_query(where=where, dataset_id=dataset_id,
                                          batch_size=batch_size):
        selected += len(batch)
        for r in batch:
            try:
                r["content"] = store.get_content(r["cid"])
            except KeyError:
                r["content"] = None
        cur = batch
        for i, (op, op_ctx) in enumerate(ops):
            result.stages[i].rows_in += len(cur)
            cur = op.process(cur, op_ctx)
            result.stages[i].rows_out += len(cur)
        writeback(cur)

    for op, op_ctx in ops:                 # flush stateful operators
        flushed = op.finalize(op_ctx)
        if flushed:
            for b in flushed:
                writeback(b)
        op.teardown(op_ctx)
    if counters["embedded"]:
        store.index.vectors.flush()

    result.selected = selected
    result.written = counters["written"]
    result.dropped = selected - counters["written"]
    result.embedded = counters["embedded"]
    result.elapsed_s = time.time() - t0
    _write_lineage(store, result, spec)
    return result


def _write_lineage(store: DataStore, result: PipelineResult, spec: dict) -> None:
    lineage_dir = store.root / "lineage"
    lineage_dir.mkdir(exist_ok=True)
    doc = {
        "kind": "pipeline_run",
        "run_id": result.run_id,
        "timestamp": time.time(),
        "spec_fingerprint": utils.fingerprint(spec),
        "result": result.to_dict(),
    }
    (lineage_dir / f"{result.run_id}.json").write_text(
        utils.canonical_json(doc).decode()
    )
