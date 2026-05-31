from __future__ import annotations

import json
from pathlib import Path

from .config import resolve_lake_root
from .lock import commit_lock
from .models import sha256_text, utc_now
from .tables import append_rows, read_table


def _dataset_ids_for_name(lake_root: Path, dataset: str | None) -> set[str] | None:
    if not dataset:
        return None
    return {row["dataset_id"] for row in read_table(lake_root, "datasets") if row.get("name") == dataset}


def _local_hash_vector(text: str, dim: int = 16) -> list[float]:
    digest = bytes.fromhex(sha256_text(text))
    return [round(digest[i] / 255.0, 6) for i in range(dim)]


def index_embeddings(
    *,
    lake: str | Path,
    dataset: str | None = None,
    model: str = "local-hash-v1",
    backend: str = "local-jsonl",
    text_field: str = "text",
    dim: int = 16,
) -> dict:
    lake_root = resolve_lake_root(lake)
    dataset_ids = _dataset_ids_for_name(lake_root, dataset)
    records = read_table(lake_root, "records")
    existing = {
        (row.get("record_id"), row.get("embedding_model"), row.get("text_field"), row.get("index_backend"))
        for row in read_table(lake_root, "embeddings")
    }
    rows = []
    for record in records:
        if dataset_ids is not None and record.get("dataset_id") not in dataset_ids:
            continue
        text = record.get(text_field)
        if text is None:
            payload = record.get("payload") or "{}"
            try:
                text = json.dumps(json.loads(payload), ensure_ascii=False, sort_keys=True)
            except Exception:
                text = str(payload)
        key = (record["record_id"], model, text_field, backend)
        if key in existing:
            continue
        chunk_hash = sha256_text(str(text))
        rows.append(
            {
                "record_id": record["record_id"],
                "dataset_id": record["dataset_id"],
                "source_snapshot_id": "",
                "text_field": text_field,
                "chunk_id": "chunk_000000",
                "chunk_text_sha256": chunk_hash,
                "embedding_model": model,
                "embedding_dim": dim,
                "vector": _local_hash_vector(str(text), dim=dim),
                "vector_uri": "",
                "index_backend": backend,
                "index_status": "indexed",
                "created_at": utc_now(),
            }
        )
    with commit_lock(lake_root):
        append_rows(lake_root, "embeddings", rows)
    return {
        "ok": True,
        "command": "index embed",
        "status": "success",
        "warnings": [],
        "lake_root": str(lake_root),
        "rows_indexed": len(rows),
        "embedding_model": model,
        "index_backend": backend,
    }
