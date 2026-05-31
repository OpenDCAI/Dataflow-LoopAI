from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
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


def _openai_compatible_embeddings(
    *,
    base_url: str,
    api_key: str,
    model: str,
    inputs: list[str],
) -> list[list[float]]:
    endpoint = base_url.rstrip("/") + "/embeddings"
    payload = json.dumps({"model": model, "input": inputs}).encode("utf-8")
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Embedding API HTTP {exc.code}: {detail}") from exc
    data = body.get("data", [])
    vectors = [item.get("embedding", []) for item in sorted(data, key=lambda item: item.get("index", 0))]
    if len(vectors) != len(inputs):
        raise RuntimeError(f"Embedding API returned {len(vectors)} vectors for {len(inputs)} inputs")
    return vectors


def index_embeddings(
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
    lake_root = resolve_lake_root(lake)
    dataset_ids = _dataset_ids_for_name(lake_root, dataset)
    records = read_table(lake_root, "records")
    existing = {
        (row.get("record_id"), row.get("embedding_model"), row.get("text_field"), row.get("index_backend"))
        for row in read_table(lake_root, "embeddings")
    }
    candidate_items = []
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
        candidate_items.append((record, str(text)))

    rows = []
    for offset in range(0, len(candidate_items), batch_size):
        batch = candidate_items[offset : offset + batch_size]
        texts = [text for _, text in batch]
        if provider == "local-hash":
            vectors = [_local_hash_vector(text, dim=dim) for text in texts]
        elif provider == "openai-compatible":
            vectors = _openai_compatible_embeddings(
                base_url=base_url or "http://127.0.0.1:8000/v1",
                api_key=api_key or os.getenv("OBTAINERCLI_EMBED_API_KEY", ""),
                model=model,
                inputs=texts,
            )
        else:
            raise ValueError(f"Unsupported embedding provider: {provider}")
        for (record, text), vector in zip(batch, vectors):
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
                    "embedding_dim": len(vector),
                    "vector": vector,
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
        "embedding_provider": provider,
    }
