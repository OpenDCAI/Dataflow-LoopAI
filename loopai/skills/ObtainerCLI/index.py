from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


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
    from .datamixer_adapter import index_datamixer_embeddings

    return index_datamixer_embeddings(
        lake=lake,
        dataset=dataset,
        model=model,
        backend=backend,
        text_field=text_field,
        dim=dim,
        provider=provider,
        base_url=base_url,
        api_key=api_key or os.getenv("OBTAINERCLI_EMBED_API_KEY", ""),
        batch_size=batch_size,
    )
