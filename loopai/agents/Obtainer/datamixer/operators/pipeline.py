"""Declarative operator pipelines (L4 scheduling/materialization/lineage).

A pipeline is described in YAML (see ``examples/pipeline.yaml``); the runner
streams batches of catalog samples through the operator DAG, optionally creates
derived datasets at declared stage outputs, and records lineage. This is the
single-node Ray-Data stand-in: same authoring model, swappable engine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import yaml

from .. import schema, tokenizers, utils
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
    materialized: int = 0
    output_dataset: str | None = None


@dataclass
class PipelineResult:
    name: str
    source_dataset: str | None
    selected: int = 0
    stages: list[StageResult] = field(default_factory=list)
    written: int = 0
    dropped: int = 0
    embedded: int = 0
    materialized: int = 0
    outputs: dict[str, int] = field(default_factory=dict)
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
            "materialized": self.materialized,
            "outputs": dict(self.outputs),
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
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> PipelineResult:
    name = spec.get("name", "pipeline")
    src = spec.get("source", {}) or {}
    dataset = src.get("dataset")
    where = src.get("filter")
    dataset_id = store.catalog.resolve_dataset(dataset) if dataset else None
    if dataset and dataset_id is None:
        raise ValueError(f"source dataset not found: {dataset}")

    run_id = "run-" + utils.fingerprint({"p": name, "ts": time.time()})
    result = PipelineResult(name=name, source_dataset=dataset, run_id=run_id)
    t0 = time.time()

    def emit_progress(current_index: int | None, status: str) -> None:
        """Best-effort stage signal for schedulers; never break data work."""
        if progress_callback is None:
            return
        stage_states = []
        for index, item in enumerate(result.stages):
            stage_states.append({
                "name": item.name,
                "kind": item.kind,
                "rows_in": item.rows_in,
                "rows_out": item.rows_out,
                "materialized": item.materialized,
                "state": (
                    "running" if status == "running" and index == current_index
                    else "completed" if status == "completed" or (
                        current_index is not None and index < current_index
                    )
                    else "waiting"
                ),
            })
        try:
            progress_callback({
                "status": status,
                "current_stage": (
                    result.stages[current_index].name
                    if current_index is not None and current_index < len(result.stages)
                    else ""
                ),
                "stages": stage_states,
                "selected": result.selected,
            })
        except Exception:
            pass

    # build the operator chain once; setup each
    ops = []
    for op_spec in spec.get("operators", []):
        args = op_spec.get("args", {}) or {}
        op = base.create(op_spec["name"], **args)
        op_ctx = base.OperatorContext(
            run_id=run_id, seed=seed, device=op_spec.get("device", "cpu"),
            root=str(store.root), extra=args)
        op.setup(op_ctx)
        output = _prepare_output(store, spec, op_spec, name)
        ops.append((op, op_ctx, output))
        result.stages.append(
            StageResult(
                op_spec["name"], op.spec.kind, op.spec.version, 0, 0,
                output_dataset=(output or {}).get("dataset_name"),
            )
        )
    emit_progress(None, "waiting")

    reserved = {"content", "sample_id", "dataset_id", "cid", "created_at",
                "version", "tags", "embedding"}  # 'embedding' -> vector index
    from array import array as _array
    counters = {"written": 0, "embedded": 0, "materialized": 0,
                "emitted": 0}

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
        result.selected = selected
        for r in batch:
            try:
                r["content"] = store.get_content(r["cid"])
            except KeyError:
                r["content"] = None
        cur = batch
        for i, (op, op_ctx, output) in enumerate(ops):
            emit_progress(i, "running")
            result.stages[i].rows_in += len(cur)
            cur = op.process(cur, op_ctx)
            result.stages[i].rows_out += len(cur)
            if output:
                cur = _materialize_rows(store, cur, output, name, run_id,
                                        op.spec.name)
                n = len(cur)
                result.stages[i].materialized += n
                counters["materialized"] += n
                out_name = output["dataset_name"]
                result.outputs[out_name] = result.outputs.get(out_name, 0) + n
        writeback(cur)
        counters["emitted"] += len(cur)

    for i, (op, op_ctx, output) in enumerate(ops):  # flush stateful operators
        flushed = op.finalize(op_ctx)
        if flushed:
            for b in flushed:
                if output:
                    b = _materialize_rows(store, b, output, name, run_id,
                                          op.spec.name)
                    n = len(b)
                    result.stages[i].materialized += n
                    counters["materialized"] += n
                    out_name = output["dataset_name"]
                    result.outputs[out_name] = result.outputs.get(out_name, 0) + n
                writeback(b)
                counters["emitted"] += len(b)
        op.teardown(op_ctx)
    if counters["embedded"]:
        store.index.vectors.flush()

    result.selected = selected
    result.written = counters["written"]
    result.dropped = max(0, selected - counters["emitted"])
    result.embedded = counters["embedded"]
    result.materialized = counters["materialized"]
    result.elapsed_s = time.time() - t0
    _write_lineage(store, result, spec)
    emit_progress(None, "completed")
    return result


def _prepare_output(store: DataStore, pipeline_spec: dict, op_spec: dict,
                    pipeline_name: str) -> dict | None:
    """Resolve an operator's optional derived-dataset materialization target."""
    output = op_spec.get("output", op_spec.get("materialize"))
    if output is None:
        return None
    if not isinstance(output, dict):
        raise ValueError("operator output/materialize must be a mapping")
    dataset_name = output.get("dataset")
    if not dataset_name:
        raise ValueError("materialized operator output requires a dataset name")
    quality_level = schema.validate_quality_level(output.get("quality_level"))
    stage = output.get("stage")
    if stage is not None and stage not in schema.STAGES:
        raise ValueError(
            f"invalid materialized stage {stage!r}; expected one of: "
            + ", ".join(schema.STAGES)
        )
    dataset_id = store.catalog.resolve_dataset(dataset_name)
    if dataset_id is None:
        src = pipeline_spec.get("source", {}) or {}
        dataset_id = store.catalog.add_dataset(
            name=dataset_name,
            source=output.get("source") or src.get("dataset"),
            license=output.get("license"),
            owner=output.get("owner"),
            description=output.get("description") or (
                f"Derived by DataMixer pipeline {pipeline_name}."
            ),
            meta={
                "pipeline": pipeline_name,
                "quality_level": quality_level,
                "stage": stage,
            },
        )
    return {
        **output,
        "dataset_name": dataset_name,
        "dataset_id": dataset_id,
        "quality_level": quality_level,
        "tokenizer_impl": tokenizers.resolve(output.get("tokenizer")),
    }


def _materialize_rows(store: DataStore, rows: list[dict], output: dict,
                      pipeline_name: str, run_id: str,
                      operator_name: str) -> list[dict]:
    """Create child samples and retarget rows for downstream operators."""
    dataset_id = output["dataset_id"]
    tok = output["tokenizer_impl"]
    materialized = []
    for row in rows:
        parent_sample_id = row.get("sample_id")
        parent_dataset_id = row.get("dataset_id")
        parent_cid = row.get("cid")
        if not parent_sample_id:
            continue

        meta = _row_metadata(row)
        root_sample_id = meta.get("root_sample_id") or parent_sample_id
        root_dataset_id = meta.get("root_dataset_id") or parent_dataset_id
        meta.update(output.get("metadata", {}) or {})
        meta.update({
            "quality_level": output["quality_level"],
            "parent_sample_id": parent_sample_id,
            "parent_dataset_id": parent_dataset_id,
            "parent_cid": parent_cid,
            "root_sample_id": root_sample_id,
            "root_dataset_id": root_dataset_id,
            "pipeline_run_id": run_id,
            "pipeline_name": pipeline_name,
            "pipeline_operator": operator_name,
            "processing_level": output.get(
                "processing_level", output["quality_level"]
            ),
        })
        for key in ("stage", "modality", "domain", "lang", "source",
                    "license", "task_type"):
            if output.get(key) is not None:
                meta[key] = output[key]

        content = row.get("content")
        meta["n_tokens"] = tok.count(utils.extract_text(content))
        meta["tokenizer"] = tok.name
        cid, _ = store.cas.put_json(content)
        sample_id, _ = store.catalog.add_sample(
            dataset_id, cid, meta, salt=parent_sample_id
        )

        core, extra = schema.split_metadata(meta)
        row.update(core)
        row.update({
            "sample_id": sample_id,
            "dataset_id": dataset_id,
            "cid": cid,
            "tags": extra,
        })
        materialized.append(row)
    store.catalog.commit()
    return materialized


def _row_metadata(row: dict) -> dict:
    meta = dict(row.get("tags") or {})
    for key in schema.CORE_FIELD_NAMES:
        if row.get(key) is not None:
            meta[key] = row[key]
    system = {"content", "sample_id", "dataset_id", "cid", "created_at",
              "version", "tags", "embedding"}
    for key, value in row.items():
        if key not in system and key not in schema.CORE_FIELD_NAMES:
            meta[key] = value
    return meta


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
