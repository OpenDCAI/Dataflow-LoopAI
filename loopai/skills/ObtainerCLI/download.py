from __future__ import annotations

import json
import os
import re
from itertools import islice
from pathlib import Path
from typing import Any, Iterable

from .errors import ObtainerCliError
from .models import canonical_json, utc_now


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


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _load_hf_dataset(dataset_id: str, *, split: str, streaming: bool) -> Any:
    from datasets import get_dataset_config_names, load_dataset

    try:
        return load_dataset(dataset_id, split=split, streaming=streaming)
    except Exception as first_error:
        try:
            configs = get_dataset_config_names(dataset_id)
        except Exception:
            configs = []
        if not configs:
            raise first_error
        return load_dataset(dataset_id, configs[0], split=split, streaming=streaming)


def _export_huggingface_jsonl(
    *,
    item: dict[str, Any],
    output_root: Path,
    split: str,
    max_rows: int,
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

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    dataset = _load_hf_dataset(dataset_id, split=split, streaming=streaming)
    rows_iter = (
        _normalize_row(dict(row), dataset_id=dataset_id, split=split, row_index=index)
        for index, row in enumerate(islice(dataset, max_rows), 1)
    )
    dataset_name = _safe_name(dataset_id)
    records_path = output_root / "records" / f"{dataset_name}.{split}.jsonl"
    rows_written = _write_jsonl(records_path, rows_iter)
    return {
        "ok": rows_written > 0,
        "status": "completed" if rows_written > 0 else "empty",
        "source": "huggingface",
        "dataset_id": dataset_id,
        "split": split,
        "rows_written": rows_written,
        "records_jsonl": str(records_path.resolve()),
        "candidate": item,
    }


def download_manifest(
    *,
    manifest: str | Path,
    output_root: str | Path,
    limit: int = 0,
    split: str = "train",
    max_rows: int = 200,
    streaming: bool = True,
) -> dict[str, Any]:
    if max_rows <= 0:
        raise ObtainerCliError(
            "INVALID_MAX_ROWS",
            "download max rows must be positive",
            hint="Pass --max-rows with a positive integer.",
        )

    payload = _read_manifest(manifest)
    output_path = Path(output_root)
    output_path.mkdir(parents=True, exist_ok=True)
    items = _iter_download_items(payload, limit=limit)

    results: list[dict[str, Any]] = []
    for item in items:
        source = str(item.get("source") or (item.get("download") or {}).get("method") or "").lower()
        if source == "huggingface":
            try:
                results.append(
                    _export_huggingface_jsonl(
                        item=item,
                        output_root=output_path,
                        split=split,
                        max_rows=max_rows,
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

    result_path = output_path / "download_results.json"
    completed = [row for row in results if row.get("ok")]
    response = {
        "ok": bool(completed),
        "status": "completed" if completed else "failed",
        "schema_version": "obtainercli.download.v1",
        "created_at": utc_now(),
        "manifest": str(Path(manifest).resolve()),
        "output_root": str(output_path.resolve()),
        "requested": len(items),
        "completed": len(completed),
        "results": results,
        "records_jsonl": [row["records_jsonl"] for row in completed if row.get("records_jsonl")],
    }
    result_path.write_text(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        **response,
        "result_path": str(result_path.resolve()),
    }
