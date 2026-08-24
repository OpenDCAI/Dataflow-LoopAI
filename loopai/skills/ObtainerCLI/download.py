from __future__ import annotations

import json
import os
import re
from itertools import islice
from pathlib import Path
from typing import Any, Iterable

from loopai.utils.hf_endpoints import (
    DEFAULT_HF_ENDPOINTS,
    apply_hf_endpoint as _apply_hf_endpoint,
    endpoint_reachable as _endpoint_reachable,
    hf_endpoint_chain as _hf_endpoint_chain,
)

from .errors import ObtainerCliError
from .models import canonical_json, utc_now


MAX_ROWS_PER_DATASET = 100_000
MAX_BYTES_PER_DATASET = 2 * 1024 * 1024 * 1024


def _ensure_hf_mirror_env() -> None:
    endpoint = (
        os.environ.get("HF_ENDPOINT")
        or os.environ.get("HF_HUB_ENDPOINT")
        or DEFAULT_HF_ENDPOINTS[0]
    )
    os.environ.setdefault("HF_ENDPOINT", endpoint)
    os.environ.setdefault("HF_HUB_ENDPOINT", endpoint)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _load_hf_dataset(dataset_id: str, *, split: str, streaming: bool) -> Any:
    """Load a dataset with multi-level endpoint fallback.

    Each endpoint is probed first (short timeout, no retry), then tried in
    order (explicit config, official hub, mirrors, configured extras).  The
    per-endpoint config auto-discovery fallback is kept: when a bare load
    fails, the first config is resolved and retried against the same endpoint
    before moving on.  Only when every endpoint fails is a combined error
    raised, so one dead mirror cannot sink the whole download run.
    """
    errors: list[str] = []
    for endpoint in _hf_endpoint_chain():
        if not _endpoint_reachable(endpoint):
            errors.append(f"{endpoint}: unreachable (probe failed)")
            continue
        _apply_hf_endpoint(endpoint)
        try:
            return _load_hf_dataset_once(dataset_id, split=split, streaming=streaming)
        except Exception as exc:
            errors.append(f"{endpoint}: {type(exc).__name__}: {str(exc)[:300]}")
            continue
    raise ObtainerCliError(
        "HF_DOWNLOAD_ALL_ENDPOINTS_FAILED",
        f"all HuggingFace endpoints failed for dataset {dataset_id!r}",
        hint="\n".join(errors),
    )


def _load_hf_dataset_once(dataset_id: str, *, split: str, streaming: bool) -> Any:
    from datasets import get_dataset_config_names, load_dataset
    from datasets.download.download_config import DownloadConfig

    kwargs: dict[str, Any] = {
        "split": split,
        "streaming": streaming,
        # Fail fast on a bad endpoint so the next mirror in the chain is tried
        # promptly instead of burning huggingface_hub's built-in retries.
        "download_config": DownloadConfig(max_retries=0),
    }
    try:
        return load_dataset(dataset_id, **kwargs)
    except Exception as first_error:
        try:
            configs = get_dataset_config_names(dataset_id)
        except Exception:
            configs = []
        if not configs:
            raise first_error
        return load_dataset(dataset_id, configs[0], **kwargs)


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "__", value.strip())
    return name.strip("._-") or "dataset"


def _read_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise ObtainerCliError(
            "MANIFEST_NOT_FOUND",
            f"download manifest not found: {manifest_path}",
            hint="Run searchagent first or pass an existing searchagent_manifest.json.",
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ObtainerCliError(
            "INVALID_MANIFEST",
            "download manifest must be a JSON object",
            hint="Expected a SearchAgent manifest with a download_list field.",
        )
    return payload


def _iter_download_items(manifest: dict[str, Any], *, limit: int = 0) -> list[dict[str, Any]]:
    raw_items = manifest.get("download_list") or manifest.get("candidates") or []
    if not isinstance(raw_items, list):
        raise ObtainerCliError(
            "INVALID_MANIFEST",
            "manifest download_list must be a list",
            hint="Re-run searchagent to generate a valid manifest.",
        )
    items = [item for item in raw_items if isinstance(item, dict)]
    if limit > 0:
        return items[:limit]
    return items


def _row_text(row: dict[str, Any]) -> str:
    for key in ("text", "content", "question", "problem", "query", "instruction"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return canonical_json(row)


def _normalize_row(row: dict[str, Any], *, dataset_id: str, split: str, row_index: int) -> dict[str, Any]:
    text = _row_text(row)
    question = row.get("question") or row.get("problem") or row.get("query") or row.get("instruction") or ""
    answer = row.get("answer") or row.get("target") or row.get("output") or row.get("response") or ""
    normalized = dict(row)
    normalized.setdefault("text", text)
    normalized.setdefault("instruction", question if isinstance(question, str) else "")
    normalized.setdefault("input", question if isinstance(question, str) else "")
    normalized.setdefault("output", answer if isinstance(answer, str) else canonical_json(answer))
    normalized.setdefault("source_domain", "huggingface")
    normalized.setdefault("source_uri", f"hf://datasets/{dataset_id}/{split}#{row_index}")
    normalized.setdefault("split", split)
    return normalized


def _write_jsonl(
    path: Path,
    rows: Iterable[dict[str, Any]],
    *,
    max_bytes: int,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    bytes_written = 0
    truncated = False
    with path.open("wb") as handle:
        for row in rows:
            line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            if max_bytes > 0 and bytes_written + len(line) > max_bytes:
                truncated = True
                break
            handle.write(line)
            count += 1
            bytes_written += len(line)
    return {
        "rows_written": count,
        "bytes_written": bytes_written,
        "byte_cap_reached": truncated,
    }


def _export_huggingface_jsonl(
    *,
    item: dict[str, Any],
    output_root: Path,
    split: str,
    max_rows: int,
    max_bytes_per_dataset: int,
    streaming: bool,
) -> dict[str, Any]:
    dataset_id = str(
        item.get("dataset_id")
        or (item.get("download") or {}).get("dataset_id")
        or ""
    ).strip()
    if not dataset_id:
        return {
            "ok": False,
            "status": "failed",
            "source": "huggingface",
            "error": "missing dataset_id",
            "candidate": item,
        }

    _ensure_hf_mirror_env()
    dataset = _load_hf_dataset(dataset_id, split=split, streaming=streaming)
    endpoint_used = os.environ.get("HF_ENDPOINT") or ""
    effective_max_rows = _effective_max_rows(max_rows)
    effective_max_bytes = _effective_max_bytes(max_bytes_per_dataset)
    selected_rows = islice(dataset, effective_max_rows)
    rows_iter = (
        _normalize_row(dict(row), dataset_id=dataset_id, split=split, row_index=index)
        for index, row in enumerate(selected_rows, 1)
    )
    dataset_name = _safe_name(dataset_id)
    records_path = output_root / "records" / f"{dataset_name}.{split}.jsonl"
    write_result = _write_jsonl(records_path, rows_iter, max_bytes=effective_max_bytes)
    rows_written = int(write_result["rows_written"])
    return {
        "ok": rows_written > 0,
        "status": "completed" if rows_written > 0 else "empty",
        "source": "huggingface",
        "dataset_id": dataset_id,
        "split": split,
        "endpoint_used": endpoint_used,
        "rows_written": rows_written,
        "bytes_written": int(write_result["bytes_written"]),
        "max_rows_requested": max_rows,
        "max_rows_effective": effective_max_rows,
        "row_cap_applied": max_rows == 0 or max_rows > effective_max_rows,
        "max_bytes_requested": max_bytes_per_dataset,
        "max_bytes_effective": effective_max_bytes,
        "byte_cap_applied": bool(write_result["byte_cap_reached"]),
        "truncated": bool(write_result["byte_cap_reached"]),
        "truncated_reason": "max_bytes_per_dataset" if write_result["byte_cap_reached"] else "",
        "records_jsonl": str(records_path.resolve()),
        "candidate": item,
    }


def _effective_max_rows(max_rows: int) -> int:
    if max_rows <= 0:
        return MAX_ROWS_PER_DATASET
    return min(max_rows, MAX_ROWS_PER_DATASET)


def _effective_max_bytes(max_bytes: int) -> int:
    if max_bytes <= 0:
        return MAX_BYTES_PER_DATASET
    return min(max_bytes, MAX_BYTES_PER_DATASET)


def _write_download_progress(
    path: Path,
    *,
    total: int,
    index: int,
    item: dict[str, Any] | None,
    results: list[dict[str, Any]],
    state: str,
) -> None:
    completed = [row for row in results if row.get("ok")]
    failed = [row for row in results if not row.get("ok")]
    current_dataset = None
    if item:
        current_dataset = str(
            item.get("dataset_id")
            or (item.get("download") or {}).get("dataset_id")
            or ""
        ) or None
    payload = {
        "schema_version": "obtainercli.download.progress.v1",
        "updated_at": utc_now(),
        "state": state,
        "total": total,
        "processed": len(results),
        "current_index": index,
        "current_dataset": current_dataset,
        "completed": len(completed),
        "failed": len(failed),
        "percent": int((len(results) / total) * 100) if total else 100,
        "last_result": results[-1] if results else None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def download_manifest(
    *,
    manifest: str | Path,
    output_root: str | Path,
    limit: int = 0,
    split: str = "train",
    max_rows: int = MAX_ROWS_PER_DATASET,
    max_bytes_per_dataset: int = MAX_BYTES_PER_DATASET,
    streaming: bool = True,
) -> dict[str, Any]:
    if max_rows < 0:
        raise ObtainerCliError(
            "INVALID_MAX_ROWS",
            "download max rows must be zero or positive",
            hint=f"Use a value from 1 to {MAX_ROWS_PER_DATASET}; 0 is capped to the default per-dataset limit.",
        )
    if max_bytes_per_dataset < 0:
        raise ObtainerCliError(
            "INVALID_MAX_BYTES",
            "download max bytes per dataset must be zero or positive",
            hint=f"Use a value from 1 to {MAX_BYTES_PER_DATASET}; 0 is capped to the default per-dataset byte limit.",
        )

    payload = _read_manifest(manifest)
    output_path = Path(output_root)
    output_path.mkdir(parents=True, exist_ok=True)
    items = _iter_download_items(payload, limit=limit)
    progress_path = output_path / "download_progress.json"

    results: list[dict[str, Any]] = []
    _write_download_progress(progress_path, total=len(items), index=0, item=None, results=results, state="starting")
    for index, item in enumerate(items, 1):
        _write_download_progress(progress_path, total=len(items), index=index, item=item, results=results, state="running")
        source = str(item.get("source") or (item.get("download") or {}).get("method") or "").lower()
        if source == "huggingface":
            try:
                results.append(
                    _export_huggingface_jsonl(
                        item=item,
                        output_root=output_path,
                        split=split,
                        max_rows=max_rows,
                        max_bytes_per_dataset=max_bytes_per_dataset,
                        streaming=streaming,
                    )
                )
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "status": "failed",
                        "source": "huggingface",
                        "dataset_id": item.get("dataset_id"),
                        "error": str(exc),
                        "candidate": item,
                    }
                )
        elif source == "kaggle":
            results.append(
                {
                    "ok": False,
                    "status": "skipped",
                    "source": "kaggle",
                    "dataset_id": item.get("dataset_id"),
                    "error": "kaggle download is not implemented in ObtainerCLI minimal downloader",
                    "candidate": item,
                }
            )
        else:
            results.append(
                {
                    "ok": False,
                    "status": "skipped",
                    "source": source or "unknown",
                    "dataset_id": item.get("dataset_id"),
                    "error": "unsupported download source",
                    "candidate": item,
                    }
                )
        _write_download_progress(progress_path, total=len(items), index=index, item=item, results=results, state="running")

    result_path = output_path / "download_results.json"
    completed = [row for row in results if row.get("ok")]
    response = {
        "ok": bool(completed),
        "status": "completed" if completed else "failed",
        "schema_version": "obtainercli.download.v1",
        "created_at": utc_now(),
        "manifest": str(Path(manifest).resolve()),
        "output_root": str(output_path.resolve()),
        "max_rows_requested": max_rows,
        "max_rows_effective": _effective_max_rows(max_rows),
        "max_rows_per_dataset": MAX_ROWS_PER_DATASET,
        "max_bytes_requested": max_bytes_per_dataset,
        "max_bytes_effective": _effective_max_bytes(max_bytes_per_dataset),
        "max_bytes_per_dataset": MAX_BYTES_PER_DATASET,
        "requested": len(items),
        "completed": len(completed),
        "results": results,
        "records_jsonl": [row["records_jsonl"] for row in completed if row.get("records_jsonl")],
    }
    result_path.write_text(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_download_progress(
        progress_path,
        total=len(items),
        index=len(items),
        item=items[-1] if items else None,
        results=results,
        state=response["status"],
    )
    return {
        **response,
        "result_path": str(result_path.resolve()),
        "progress_path": str(progress_path.resolve()),
    }
