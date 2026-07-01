from __future__ import annotations

import json
import random
from array import array
from pathlib import Path
from typing import Any, Iterable, Mapping

from loopai.agents.Obtainer.datamixer import utils as dm_utils
from loopai.agents.Obtainer.datamixer.index import store_text
from loopai.agents.Obtainer.datamixer.store import DataStore

from .config import read_lake_config_for_lake, resolve_lake_root, write_lake_config
from .errors import CandidateNotEnoughError
from .lock import commit_lock
from .models import canonical_json, main_text, parse_tags, sha256_text, utc_now
from .tables import ensure_tables, write_jsonl


AUDIT_TABLES = {"assets", "ingest_runs", "embeddings", "exports", "record_lineage"}


def warehouse_root(lake: str | Path) -> Path:
    lake_path = Path(lake)
    config = read_lake_config_for_lake(lake_path)
    if config.get("warehouse"):
        return Path(config["warehouse"]).expanduser().resolve()
    return resolve_lake_root(lake_path) / "warehouse"


def _audit_dir(lake_root: Path) -> Path:
    return warehouse_root(lake_root) / "obtainercli_audit"


def _audit_path(lake_root: Path, table: str) -> Path:
    return _audit_dir(lake_root) / f"{table}.jsonl"


def append_audit_rows(lake_root: str | Path, table: str, rows: Iterable[Mapping]) -> None:
    if table not in AUDIT_TABLES:
        raise ValueError(f"Unsupported audit table: {table}")
    path = _audit_path(Path(lake_root), table)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def read_audit_rows(lake_root: str | Path, table: str) -> list[dict]:
    path = _audit_path(Path(lake_root), table)
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def open_store(lake: str | Path) -> DataStore:
    root = warehouse_root(lake)
    if not (root / "datamixer.toml").exists():
        raise FileNotFoundError(f"DataMixer warehouse not initialized: {root}")
    return DataStore.open(root)


def init_datamixer_lake(
    *,
    root: str | Path,
    link_path: str | Path | None = None,
    if_not_exists: bool = False,
    auto_embed: bool = True,
    embedding_provider: str = "openai-compatible",
    embedding_base_url: str = "http://127.0.0.1:8000/v1",
    embedding_api_key: str = "",
    embedding_model: str = "BAAI/bge-small-zh-v1.5",
    embedding_backend: str = "local-jsonl",
    embedding_text_field: str = "text",
) -> dict:
    lake_root = Path(root).expanduser().resolve()
    if lake_root.exists() and any(lake_root.iterdir()) and not if_not_exists:
        raise FileExistsError(f"Lake root already exists: {lake_root}")
    lake_root.mkdir(parents=True, exist_ok=True)
    config_kwargs = {
        "catalog": "datamixer",
        "backend": "datamixer",
        "auto_embed": auto_embed,
        "embedding_provider": embedding_provider,
        "embedding_base_url": embedding_base_url,
        "embedding_api_key": embedding_api_key,
        "embedding_model": embedding_model,
        "embedding_backend": embedding_backend,
        "embedding_text_field": embedding_text_field,
    }
    write_lake_config(lake_root / "lake.yaml", root=lake_root, warehouse=lake_root / "warehouse", **config_kwargs)
    if link_path is not None:
        write_lake_config(Path(link_path), root=lake_root, warehouse=lake_root / "warehouse", **config_kwargs)
    store = DataStore.init(lake_root / "warehouse")
    store.close()
    ensure_tables(lake_root)
    return {
        "ok": True,
        "command": "lake init",
        "lake_root": str(lake_root),
        "lake_config": str(link_path) if link_path else str(lake_root / "lake.yaml"),
        "catalog": "datamixer",
        "backend": "datamixer",
        "status": "success",
        "warnings": [],
        "auto_embed": auto_embed,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "created_at": utc_now(),
    }


def _dataset_id(name: str) -> str:
    return "ds_" + sha256_text(name)[:16]


def _task_stage(task_type: str, legacy_stage: str) -> str:
    task = str(task_type or "").upper()
    if task == "PT":
        return "pretrain"
    if task == "SFT":
        return "sft"
    if legacy_stage in {"pretrain", "sft", "preference", "agent_trajectory"}:
        return legacy_stage
    return legacy_stage or "pretrain"


def _asset_id(dataset_id: str, input_path: Path) -> str:
    stat = input_path.stat()
    basis = f"{dataset_id}:{input_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return "asset_" + sha256_text(basis)[:24]


def _source_uri(row: Mapping[str, Any], input_file: Path, index: int) -> str:
    return str(row.get("source_uri") or f"{input_file.resolve()}#{index}")


def _content_for_row(row: Mapping[str, Any]) -> dict:
    content = dict(row)
    content.pop("quality_findings", None)
    return content


def _record_metadata(
    row: Mapping[str, Any],
    *,
    input_file: Path,
    index: int,
    asset_id: str,
    ingest_run_id: str,
    stage: str,
    domain: str,
    task_type: str,
    processing_level: str,
    source_kind: str,
    tags: Mapping[str, str],
) -> dict:
    source_uri = _source_uri(row, input_file, index)
    text = str(row.get("text") or main_text(dict(row)))
    meta = {
        "modality": "text",
        "stage": _task_stage(task_type, stage),
        "lake_stage": stage,
        "domain": domain,
        "task_type": task_type,
        "processing_level": processing_level,
        "source_kind": source_kind,
        "source_domain": str(row.get("source_domain", "")),
        "source_uri": source_uri,
        "asset_id": asset_id,
        "pipeline_run_id": ingest_run_id,
        "instruction": row.get("instruction", ""),
        "input": row.get("input", ""),
        "output": row.get("output", ""),
        "messages": row.get("messages", []),
        "split": row.get("split", ""),
        "payload": dict(row),
        "text": text,
        "dedup_key": "dedup_" + sha256_text(" ".join(text.split()).strip().lower()),
        "quality_findings": row.get("quality_findings") or [],
        **tags,
    }
    if row.get("quality_score") is not None:
        meta["quality_score"] = row.get("quality_score")
    return meta


def ingest_datamixer_path(
    *,
    lake: str | Path,
    input_path: str | Path,
    dataset: str,
    stage: str = "bronze",
    domain: str = "general",
    task_type: str = "PT",
    processing_level: str = "raw_web",
    source_kind: str = "local",
    tags: Iterable[str] | None = None,
    idempotency_key: str | None = None,
    on_duplicate: str = "skip",
) -> dict:
    if on_duplicate not in {"skip", "error"}:
        raise ValueError("on_duplicate must be skip|error")
    lake_root = resolve_lake_root(lake)
    ensure_tables(lake_root)
    input_file = Path(input_path)
    tag_map = parse_tags(tags)
    dataset_id = _dataset_id(dataset)
    asset_id = _asset_id(dataset_id, input_file)
    ingest_run_id = "ingest_" + sha256_text(idempotency_key or f"{dataset}:{input_file.resolve()}")[:24]

    with commit_lock(lake_root):
        previous_runs = read_audit_rows(lake_root, "ingest_runs")
        if idempotency_key and any(
            run.get("idempotency_key") == idempotency_key and run.get("status") == "succeeded"
            for run in previous_runs
        ):
            warning = {
                "code": "DUPLICATE_INGEST_SKIPPED",
                "message": f"Idempotency key already succeeded: {idempotency_key}",
            }
            append_audit_rows(
                lake_root,
                "ingest_runs",
                [
                    {
                        "ingest_run_id": ingest_run_id + "_skip_" + sha256_text(utc_now())[:8],
                        "idempotency_key": idempotency_key,
                        "command": "ingest path",
                        "input_uri": str(input_file),
                        "dataset_id": dataset_id,
                        "status": "skipped_duplicate_ingest",
                        "started_at": utc_now(),
                        "finished_at": utc_now(),
                        "rows_seen": 0,
                        "rows_written": 0,
                        "rows_quarantined": 0,
                        "error_summary": "",
                        "config_snapshot": {},
                    }
                ],
            )
            return {
                "ok": True,
                "command": "ingest path",
                "status": "success_with_warnings",
                "warnings": [warning],
                "lake_root": str(lake_root),
                "rows_seen": 0,
                "rows_written": 0,
                "rows_quarantined": 0,
            }

        raw_rows = []
        with input_file.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"JSONL row {lineno} must be an object")
                raw_rows.append(row)

        store = open_store(lake_root)
        if not store.catalog.get_dataset(dataset_id):
            store.catalog.add_dataset(
                name=dataset,
                source=source_kind,
                meta={"stage": stage, "domain": domain, "task_type": task_type, "default_tags": tag_map},
                dataset_id=dataset_id,
            )
        records = []
        for index, row in enumerate(raw_rows, 1):
            records.append(
                {
                    "content": _content_for_row(row),
                    **_record_metadata(
                        row,
                        input_file=input_file,
                        index=index,
                        asset_id=asset_id,
                        ingest_run_id=ingest_run_id,
                        stage=stage,
                        domain=domain,
                        task_type=task_type,
                        processing_level=processing_level,
                        source_kind=source_kind,
                        tags=tag_map,
                    ),
                }
            )
        result = store.ingest_records(dataset_id, records, content_key="content")
        if on_duplicate == "error" and result.merged:
            store.close()
            raise ValueError(f"Duplicate records: {result.merged}")
        store.close()

        append_audit_rows(
            lake_root,
            "assets",
            [
                {
                    "asset_id": asset_id,
                    "dataset_id": dataset_id,
                    "source_uri": str(input_file.resolve()),
                    "source_kind": source_kind,
                    "local_uri": str(input_file.resolve()),
                    "content_sha256": sha256_text(input_file.read_text(encoding="utf-8")),
                    "size_bytes": input_file.stat().st_size,
                    "mime_type": "application/jsonl",
                    "license": "",
                    "provenance": {"input_path": str(input_file.resolve())},
                    "ingest_run_id": ingest_run_id,
                    "created_at": utc_now(),
                }
            ],
        )
        append_audit_rows(
            lake_root,
            "ingest_runs",
            [
                {
                    "ingest_run_id": ingest_run_id,
                    "idempotency_key": idempotency_key or "",
                    "command": "ingest path",
                    "input_uri": str(input_file),
                    "dataset_id": dataset_id,
                    "status": "succeeded",
                    "started_at": utc_now(),
                    "finished_at": utc_now(),
                    "rows_seen": result.ingested,
                    "rows_written": result.written,
                    "rows_quarantined": 0,
                    "error_summary": "",
                    "config_snapshot": {
                        "stage": stage,
                        "domain": domain,
                        "task_type": task_type,
                        "processing_level": processing_level,
                        "source_kind": source_kind,
                        "tags": tag_map,
                    },
                }
            ],
        )
    return {
        "ok": True,
        "command": "ingest path",
        "status": "success",
        "warnings": [],
        "lake_root": str(lake_root),
        "rows_seen": result.ingested,
        "rows_written": result.written,
        "rows_quarantined": 0,
        "merged": result.merged,
        "new_blobs": result.new_blobs,
        "deduped_blobs": result.deduped_blobs,
    }


def _legacy_dataset_row(row: Mapping[str, Any]) -> dict:
    meta = {}
    try:
        meta = json.loads(row.get("meta_json") or "{}")
    except Exception:
        meta = {}
    return {
        "dataset_id": row.get("id", ""),
        "name": row.get("name", ""),
        "stage": meta.get("stage", ""),
        "domain": meta.get("domain", ""),
        "task_type": meta.get("task_type", ""),
        "description": row.get("description") or "",
        "owner": row.get("owner") or "",
        "source_kind": row.get("source") or "",
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("created_at") or ""),
        "schema_version": "obtainercli.datasets.v1",
        "default_tags": meta.get("default_tags", {}),
    }


def _legacy_record_row(store: DataStore, sample: Mapping[str, Any]) -> dict:
    tags = dict(sample.get("tags") or {})
    content = store.get_content(sample["cid"])
    if not isinstance(content, dict):
        content = {"text": dm_utils.extract_text(content)}
    payload = tags.get("payload") if isinstance(tags.get("payload"), dict) else content
    text = str(tags.get("text") or content.get("text") or dm_utils.extract_text(content))
    return {
        "record_id": sample.get("sample_id", ""),
        "dataset_id": sample.get("dataset_id", ""),
        "asset_id": tags.get("asset_id", ""),
        "stage": tags.get("lake_stage") or sample.get("stage", ""),
        "domain": sample.get("domain", ""),
        "processing_level": tags.get("processing_level", ""),
        "source_kind": tags.get("source_kind", ""),
        "source_domain": tags.get("source_domain", ""),
        "source_uri": tags.get("source_uri", content.get("source_uri", "")),
        "task_type": sample.get("task_type", ""),
        "payload": canonical_json(payload),
        "text": text,
        "instruction": tags.get("instruction", content.get("instruction", "")),
        "input": tags.get("input", content.get("input", "")),
        "output": tags.get("output", content.get("output", "")),
        "messages": tags.get("messages", content.get("messages", [])) or [],
        "tag_names": sorted(_legacy_tag_map(sample).keys()),
        "quality_score": sample.get("quality_score"),
        "dedup_key": tags.get("dedup_key") or "dedup_" + sha256_text(" ".join(text.split()).strip().lower()),
        "parent_record_ids": tags.get("parent_record_ids", []),
        "pipeline_run_id": tags.get("pipeline_run_id", ""),
        "split": tags.get("split", content.get("split", "")),
        "created_at": str(sample.get("created_at") or ""),
        "schema_version": "obtainercli.records.v1",
    }


def _legacy_tag_map(sample: Mapping[str, Any]) -> dict[str, Any]:
    tags = dict(sample.get("tags") or {})
    out = {
        "domain": sample.get("domain", ""),
        "stage": tags.get("lake_stage") or sample.get("stage", ""),
        "lang": sample.get("lang", ""),
        "source": sample.get("source", ""),
        "modality": sample.get("modality", ""),
        "processing_level": tags.get("processing_level", ""),
        "source_kind": tags.get("source_kind", ""),
        "task_type": sample.get("task_type", ""),
    }
    for key, value in tags.items():
        if key in {
            "payload",
            "text",
            "instruction",
            "input",
            "output",
            "messages",
            "quality_findings",
            "parent_record_ids",
        }:
            continue
        out[key] = value
    return {key: value for key, value in out.items() if value not in (None, "", [])}


def _legacy_tag_rows(samples: list[dict]) -> list[dict]:
    rows = []
    for sample in samples:
        for name, value in sorted(_legacy_tag_map(sample).items()):
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            rows.append(
                {
                    "record_id": sample["sample_id"],
                    "dataset_id": sample["dataset_id"],
                    "tag_name": name,
                    "tag_value": str(value),
                    "tag_source": "datamixer",
                    "confidence": 1.0,
                    "created_at": str(sample.get("created_at") or ""),
                }
            )
    return rows


def _legacy_quality_rows(samples: list[dict]) -> list[dict]:
    rows = []
    for sample in samples:
        tags = sample.get("tags") or {}
        findings = tags.get("quality_findings") or []
        if not isinstance(findings, list):
            continue
        for index, finding in enumerate(findings, 1):
            if not isinstance(finding, dict):
                continue
            rows.append(
                {
                    "finding_id": "finding_" + sha256_text(f"{sample['sample_id']}:{index}:{finding}")[:24],
                    "record_id": sample["sample_id"],
                    "dataset_id": sample["dataset_id"],
                    "processing_level": tags.get("processing_level", ""),
                    "source_kind": tags.get("source_kind", ""),
                    "finding_type": str(finding.get("finding_type", "quality_signal")),
                    "severity": str(finding.get("severity", "info")),
                    "score": finding.get("score"),
                    "metric_name": str(finding.get("metric_name", "")),
                    "metric_value": finding.get("metric_value"),
                    "detector": str(finding.get("detector", "")),
                    "detector_version": str(finding.get("detector_version", "")),
                    "details": finding.get("details", {}),
                    "pipeline_run_id": tags.get("pipeline_run_id", ""),
                    "created_at": str(sample.get("created_at") or ""),
                }
            )
    return rows


def _all_samples(store: DataStore) -> list[dict]:
    return store.catalog.query()


def read_virtual_table(lake_root: str | Path, table: str) -> list[dict]:
    lake_root = resolve_lake_root(lake_root)
    if table in AUDIT_TABLES:
        return read_audit_rows(lake_root, table)
    store = open_store(lake_root)
    try:
        if table == "datasets":
            return [_legacy_dataset_row(row) for row in store.catalog.list_datasets()]
        samples = _all_samples(store)
        if table == "records":
            return [_legacy_record_row(store, sample) for sample in samples]
        if table == "record_tags":
            return _legacy_tag_rows(samples)
        if table == "quality_findings":
            return _legacy_quality_rows(samples)
        return []
    finally:
        store.close()


def _audit_line_count(lake_root: Path, table: str) -> int:
    path = _audit_path(lake_root, table)
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _fast_virtual_table_counts(lake_root: Path) -> tuple[dict[str, int], list[dict[str, str]]]:
    counts = {table: _audit_line_count(lake_root, table) for table in AUDIT_TABLES}
    warnings: list[dict[str, str]] = []
    store = open_store(lake_root)
    try:
        conn = store.catalog.conn
        counts["datasets"] = conn.execute("SELECT COUNT(*) AS n FROM datasets").fetchone()["n"]
        record_count = int(conn.execute("SELECT COUNT(*) AS n FROM samples").fetchone()["n"])
        counts["records"] = record_count
        if record_count <= 100_000:
            counts["record_tags"] = conn.execute(
                "SELECT COUNT(*) AS n FROM samples, json_each(samples.tags_json)"
            ).fetchone()["n"]
            counts["quality_findings"] = conn.execute(
                "SELECT COALESCE(SUM(json_array_length(json_extract(tags_json, '$.quality_findings'))), 0) AS n "
                "FROM samples"
            ).fetchone()["n"]
        else:
            counts["record_tags"] = record_count
            counts["quality_findings"] = 0
            warnings.append(
                {
                    "code": "LARGE_LAKE_STATUS_APPROXIMATED",
                    "message": (
                        "Skipped expensive virtual-table expansion for record_tags "
                        "and quality_findings; counts are lightweight approximations."
                    ),
                }
            )
    finally:
        store.close()
    return counts, warnings


def _fast_storage_stats(lake_root: Path) -> dict[str, Any]:
    warehouse = warehouse_root(lake_root)
    catalog_db = warehouse / "catalog.db"
    store = open_store(lake_root)
    try:
        conn = store.catalog.conn
        samples = int(conn.execute("SELECT COUNT(*) AS n FROM samples").fetchone()["n"])
        token_sources = {
            str(row["tokenizer"] or "unknown"): int(row["n"])
            for row in conn.execute(
                "SELECT tokenizer, COUNT(*) AS n FROM samples GROUP BY tokenizer"
            ).fetchall()
        }
    finally:
        store.close()
    blob_root = warehouse / "blobs"
    stored_blob_bytes: int | None = 0
    unique_blobs: int | None = 0
    if samples <= 100_000 and blob_root.exists():
        for path in blob_root.rglob("*"):
            if path.is_file():
                unique_blobs += 1
                stored_blob_bytes += path.stat().st_size
    elif samples > 100_000:
        stored_blob_bytes = None
        unique_blobs = None
    db_size = catalog_db.stat().st_size if catalog_db.exists() else 0
    return {
        "samples": samples,
        "unique_blobs": unique_blobs,
        "logical_raw_bytes": None,
        "unique_raw_bytes": None,
        "stored_blob_bytes": stored_blob_bytes,
        "catalog_db_bytes": db_size,
        "total_on_disk_bytes": (stored_blob_bytes + db_size) if stored_blob_bytes is not None else db_size,
        "dedup_ratio": None,
        "compression_ratio": None,
        "content_savings_ratio": None,
        "token_sources": token_sources,
    }


def _fast_index_stats(lake_root: Path) -> dict[str, Any]:
    index_root = warehouse_root(lake_root) / "index"
    ids_path = index_root / "vectors.ids"
    fulltext_path = index_root / "fulltext.db"
    vector_count = 0
    if ids_path.exists():
        with ids_path.open("rb") as handle:
            vector_count = sum(1 for line in handle if line.strip())
    fulltext_docs = 0
    if fulltext_path.exists():
        import sqlite3

        conn = sqlite3.connect(str(fulltext_path))
        try:
            fulltext_docs = int(conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
        except Exception:
            fulltext_docs = 0
        finally:
            conn.close()
    return {"vectors": vector_count, "fulltext_docs": fulltext_docs, "dim": 16}


def lake_datamixer_status(*, lake: str | Path) -> dict:
    from .tables import TABLES, table_path

    lake_root = resolve_lake_root(lake)
    ensure_tables(lake_root)
    counts, warnings = _fast_virtual_table_counts(lake_root)
    paths = {}
    for table in TABLES:
        paths[table] = str(table_path(lake_root, table))
    stats = _fast_storage_stats(lake_root)
    index_stats = _fast_index_stats(lake_root)
    warnings.append(
        {
            "code": "FAST_STATUS_STORAGE_ESTIMATES",
            "message": "Storage ratios are omitted in fast status because exact ratios require reading and decompressing all blobs.",
        }
    )
    return {
        "ok": True,
        "command": "lake status",
        "status": "success",
        "warnings": warnings,
        "lake_root": str(lake_root),
        "backend": "datamixer",
        "tables": counts,
        "paths": paths,
        "storage": stats,
        "index": index_stats,
    }


def list_datamixer_tags(*, lake: str | Path) -> dict:
    lake_root = resolve_lake_root(lake)
    tags: dict[str, dict[str, int]] = {}
    for row in read_virtual_table(lake_root, "record_tags"):
        name = str(row.get("tag_name", ""))
        value = str(row.get("tag_value", ""))
        if not name or not value:
            continue
        tags.setdefault(name, {})
        tags[name][value] = tags[name].get(value, 0) + 1
    return {
        "ok": True,
        "command": "tag list",
        "status": "success",
        "warnings": [],
        "lake_root": str(lake_root),
        "tags": tags,
    }


def _dataset_ids_for_name(lake_root: Path, dataset: str | None) -> set[str] | None:
    if not dataset:
        return None
    return {row["dataset_id"] for row in read_virtual_table(lake_root, "datasets") if row.get("name") == dataset}


def _local_hash_vector(text: str, dim: int = 16) -> list[float]:
    digest = bytes.fromhex(sha256_text(text))
    return [round(digest[i] / 255.0, 6) for i in range(dim)]


def index_datamixer_embeddings(
    *,
    lake: str | Path,
    dataset: str | None = None,
    model: str = "local-hash-v1",
    backend: str = "local-jsonl",
    text_field: str = "text",
    dim: int = 16,
    provider: str = "local-hash",
    base_url: str = "",
    api_key: str = "",
    batch_size: int = 32,
) -> dict:
    from .index import _openai_compatible_embeddings

    lake_root = resolve_lake_root(lake)
    store = open_store(lake_root)
    dataset_ids = _dataset_ids_for_name(lake_root, dataset)
    existing = {
        (row.get("record_id"), row.get("embedding_model"), row.get("text_field"), row.get("index_backend"))
        for row in read_audit_rows(lake_root, "embeddings")
    }
    samples = store.catalog.query()
    candidates = []
    for sample in samples:
        if dataset_ids is not None and sample.get("dataset_id") not in dataset_ids:
            continue
        content = store.get_content(sample["cid"])
        legacy = _legacy_record_row(store, sample)
        text = str(legacy.get(text_field) or store_text(content))
        key = (sample["sample_id"], model, text_field, backend)
        if key in existing:
            continue
        candidates.append((sample, text))

    rows = []
    try:
        for offset in range(0, len(candidates), batch_size):
            batch = candidates[offset : offset + batch_size]
            texts = [text for _, text in batch]
            if provider == "local-hash":
                vectors = [_local_hash_vector(text, dim=dim) for text in texts]
            elif provider == "openai-compatible":
                vectors = _openai_compatible_embeddings(
                    base_url=base_url or "http://127.0.0.1:8000/v1",
                    api_key=api_key,
                    model=model,
                    inputs=texts,
                )
            else:
                raise ValueError(f"Unsupported embedding provider: {provider}")
            for (sample, text), vector in zip(batch, vectors):
                if len(vector) == store.index.vectors.dim:
                    store.index.vectors.add(sample["sample_id"], array("f", [float(v) for v in vector]))
                store.index.fulltext.add(sample["sample_id"], text)
                rows.append(
                    {
                        "record_id": sample["sample_id"],
                        "dataset_id": sample["dataset_id"],
                        "source_snapshot_id": "",
                        "text_field": text_field,
                        "chunk_id": "chunk_000000",
                        "chunk_text_sha256": sha256_text(str(text)),
                        "embedding_model": model,
                        "embedding_dim": len(vector),
                        "vector": [float(v) for v in vector],
                        "vector_uri": "",
                        "index_backend": backend,
                        "index_status": "indexed",
                        "created_at": utc_now(),
                    }
                )
        store.index.vectors.flush()
        store.index.fulltext.commit()
    finally:
        store.close()
    append_audit_rows(lake_root, "embeddings", rows)
    return {
        "ok": True,
        "command": "index embed",
        "status": "success",
        "warnings": [],
        "lake_root": str(lake_root),
        "rows_indexed": len(rows),
        "embedding_model": model,
        "index_backend": backend,
        "embedding_provider": provider,
    }


def _parse_balance_tag(sample: dict, tag_name: str) -> str:
    return str(_legacy_tag_map(sample).get(tag_name, ""))


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json_path(key: str) -> str:
    return '$."' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _legacy_filter_expr(key: str) -> str:
    if key in {"domain", "task_type", "lang", "modality"}:
        return key
    if key == "stage":
        return "COALESCE(json_extract(tags_json, '$.lake_stage'), stage)"
    if key == "source":
        return f"COALESCE(json_extract(tags_json, {_sql_quote(_json_path(key))}), source)"
    return f"json_extract(tags_json, {_sql_quote(_json_path(key))})"


def _sample_where_clause(
    *,
    domain: str | None,
    source_kind: str | None,
    processing_level: str | None,
    task_type: str | None,
    include: Mapping[str, str],
    exclude: Mapping[str, str],
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    deferred_include = dict(include)
    deferred_exclude = dict(exclude)
    if domain:
        clauses.append("domain = ?")
        params.append(domain)
    if task_type:
        clauses.append("task_type = ?")
        params.append(task_type)
    if processing_level:
        deferred_include.setdefault("processing_level", processing_level)
    if source_kind:
        deferred_include.setdefault("source_kind", source_kind)

    for key, value in deferred_include.items():
        clauses.append(f"{_legacy_filter_expr(key)} = ?")
        params.append(value)
    for key, value in deferred_exclude.items():
        expr = _legacy_filter_expr(key)
        clauses.append(f"({expr} IS NULL OR {expr} != ?)")
        params.append(value)
    return " AND ".join(clauses), params


def _reservoir_add(
    reservoir: list[dict],
    *,
    item: dict,
    seen: int,
    size: int,
    rng: random.Random,
) -> None:
    if len(reservoir) < size:
        reservoir.append(item)
        return
    index = rng.randrange(seen)
    if index < size:
        reservoir[index] = item


def _select_random_samples(
    store: DataStore,
    *,
    where_sql: str,
    where_params: list[Any],
    n: int,
    rng: random.Random,
) -> tuple[int, list[dict]]:
    conn = store.catalog.conn
    seed_marker = str(rng.random())
    conn.create_function(
        "obtainer_seeded_rank",
        1,
        lambda sample_id: sha256_text(f"{seed_marker}:{sample_id}"),
        deterministic=True,
    )
    where = f" WHERE {where_sql}" if where_sql else ""
    candidate_count = conn.execute(
        f"SELECT COUNT(*) AS n FROM samples{where}",
        list(where_params),
    ).fetchone()["n"]
    rows = conn.execute(
        f"SELECT sample_id FROM samples{where} "
        "ORDER BY obtainer_seeded_rank(sample_id) LIMIT ?",
        [*where_params, int(n)],
    ).fetchall()
    return int(candidate_count), [{"sample_id": row["sample_id"]} for row in rows]


def _select_stratified_samples(
    store: DataStore,
    *,
    where_sql: str,
    where_params: list[Any],
    n: int,
    balance_by: str,
    rng: random.Random,
) -> tuple[int, list[dict]]:
    tag_name = balance_by.split(":", 1)[1]
    group_seen: dict[str, int] = {}
    group_samples: dict[str, list[dict]] = {}
    total_seen = 0
    balance_expr = _legacy_filter_expr(tag_name)
    last: str | None = None
    conn = store.catalog.conn
    while True:
        clauses = [where_sql] if where_sql else []
        params = list(where_params)
        if last is not None:
            clauses.append("sample_id > ?")
            params.append(last)
        sql = f"SELECT sample_id, {balance_expr} AS balance_value FROM samples"
        if clauses:
            sql += " WHERE " + " AND ".join(f"({clause})" for clause in clauses)
        sql += " ORDER BY sample_id LIMIT 4096"
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            break
        for row in rows:
            sample_id = row["sample_id"]
            value = str(row["balance_value"] or "")
            if not value:
                continue
            total_seen += 1
            group_seen[value] = group_seen.get(value, 0) + 1
            group = group_samples.setdefault(value, [])
            _reservoir_add(group, item={"sample_id": sample_id}, seen=group_seen[value], size=n, rng=rng)
        last = rows[-1]["sample_id"]
        if len(rows) < 4096:
            break

    keys = sorted(group_samples)
    if not keys:
        return total_seen, []
    base = n // len(keys)
    extra = n % len(keys)
    selected: list[dict] = []
    leftovers: list[dict] = []
    for i, key in enumerate(keys):
        group = list(group_samples[key])
        rng.shuffle(group)
        quota = base + (1 if i < extra else 0)
        selected.extend(group[:quota])
        leftovers.extend(group[quota:])
    if len(selected) < min(n, total_seen):
        rng.shuffle(leftovers)
        selected.extend(leftovers[: n - len(selected)])
    return total_seen, selected[:n]


def sample_datamixer_records(
    *,
    lake: str | Path,
    output: str | Path,
    domain: str | None = None,
    processing_level: str | None = None,
    source_kind: str | None = None,
    task_type: str | None = None,
    include_tags: Iterable[str] | None = None,
    exclude_tags: Iterable[str] | None = None,
    n: int = 100,
    allow_smaller: bool = False,
    seed: int = 42,
    strategy: str = "random",
    balance_by: str = "",
) -> dict:
    if strategy not in {"random", "stratified"}:
        raise ValueError("First version supports strategy=random|stratified")
    lake_root = resolve_lake_root(lake)
    include = parse_tags(include_tags)
    exclude = parse_tags(exclude_tags)
    store = open_store(lake_root)
    rng = random.Random(seed)
    where_sql, where_params = _sample_where_clause(
        domain=domain,
        source_kind=source_kind,
        processing_level=processing_level,
        task_type=task_type,
        include=include,
        exclude=exclude,
    )
    try:
        if strategy == "stratified":
            if not balance_by:
                raise ValueError("strategy=stratified requires balance_by")
            if not balance_by.startswith("tag:"):
                raise ValueError("First version supports --balance-by tag:<name>")
            candidate_count, selected_samples = _select_stratified_samples(
                store,
                where_sql=where_sql,
                where_params=where_params,
                n=n,
                balance_by=balance_by,
                rng=rng,
            )
        else:
            candidate_count, selected_samples = _select_random_samples(
                store,
                where_sql=where_sql,
                where_params=where_params,
                n=n,
                rng=rng,
            )
        if candidate_count < n and not allow_smaller:
            raise CandidateNotEnoughError(n, candidate_count)
        selected = []
        for sample in selected_samples:
            full_sample = store.catalog.get_sample(sample["sample_id"])
            if full_sample is not None:
                selected.append(_legacy_record_row(store, full_sample))
        write_jsonl(output, selected)
    finally:
        store.close()
    warnings = []
    status = "success"
    if candidate_count < n and allow_smaller:
        status = "success_with_warnings"
        warnings.append(
            {
                "code": "ALLOW_SMALLER_TRIGGERED",
                "message": f"Requested {n} records but exported {len(selected)} because --allow-smaller was set.",
            }
        )
    export_id = "export_" + sha256_text(f"{Path(output).resolve()}:{utc_now()}")[:24]
    append_audit_rows(
        lake_root,
        "exports",
        [
            {
                "export_id": export_id,
                "query": {
                    "domain": domain,
                    "processing_level": processing_level,
                    "source_kind": source_kind,
                    "task_type": task_type,
                    "include_tags": include,
                    "exclude_tags": exclude,
                    "balance_by": balance_by,
                },
                "strategy": strategy,
                "seed": seed,
                "requested_size": n,
                "actual_size": len(selected),
                "output_uri": str(Path(output).resolve()),
                "format": "jsonl",
                "record_ids_sha256": sha256_text(",".join(sorted(row["record_id"] for row in selected))),
                "created_at": utc_now(),
            }
        ],
    )
    return {
        "ok": True,
        "command": "sample",
        "status": status,
        "warnings": warnings,
        "lake_root": str(lake_root),
        "requested_size": n,
        "actual_size": len(selected),
        "output_uri": str(Path(output).resolve()),
    }


def datamixer_argv_from_lake(args: list[str], lake: str | Path | None) -> list[str]:
    if not lake:
        return args
    root = str(warehouse_root(lake))
    has_root = any(item == "--root" or item.startswith("--root=") for item in args)
    return args if has_root else ["--root", root, *args]
