from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_tags(tags: Iterable[str] | None) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw in tags or []:
        if not raw:
            continue
        pieces = [piece.strip() for piece in str(raw).split(",") if piece.strip()]
        for piece in pieces:
            if "=" not in piece:
                raise ValueError(f"Tag must be key=value: {piece}")
            key, value = piece.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not value:
                raise ValueError(f"Tag must be key=value: {piece}")
            parsed[key] = value
    return parsed


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def main_text(row: Dict[str, Any]) -> str:
    for key in ["text", "output", "content", "instruction"]:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    messages = row.get("messages")
    if isinstance(messages, list):
        return canonical_json(messages)
    return canonical_json(row)


def load_jsonl(path: str | Path) -> List[dict]:
    rows: List[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError(f"JSONL row {lineno} must be an object")
            rows.append(data)
    return rows


def build_record(
    row: Dict[str, Any],
    *,
    dataset_id: str,
    asset_id: str,
    input_path: str | Path,
    row_index: int,
    stage: str,
    domain: str,
    processing_level: str,
    source_kind: str,
    task_type: str,
    pipeline_run_id: str,
) -> Dict[str, Any]:
    source_uri = str(row.get("source_uri") or f"{Path(input_path).resolve()}#{row_index}")
    payload = dict(row)
    text = str(row.get("text") or main_text(row))
    instruction = row.get("instruction", "")
    input_value = row.get("input", "")
    output = row.get("output", "")
    messages = row.get("messages", [])
    physical_basis = canonical_json(
        {
            "dataset_id": dataset_id,
            "source_uri": source_uri,
            "processing_level": processing_level,
            "payload": payload,
        }
    )
    semantic_basis = normalize_text(main_text(row))
    record_id = "rec_" + sha256_text(physical_basis)
    dedup_key = "dedup_" + sha256_text(semantic_basis)
    return {
        "record_id": record_id,
        "dataset_id": dataset_id,
        "asset_id": asset_id,
        "stage": stage,
        "domain": domain,
        "processing_level": processing_level,
        "source_kind": source_kind,
        "source_domain": str(row.get("source_domain", "")),
        "source_uri": source_uri,
        "task_type": task_type,
        "payload": canonical_json(payload),
        "text": text,
        "instruction": instruction,
        "input": input_value,
        "output": output,
        "messages": messages,
        "tag_names": [],
        "quality_score": row.get("quality_score"),
        "dedup_key": dedup_key,
        "parent_record_ids": row.get("parent_record_ids", []),
        "pipeline_run_id": pipeline_run_id,
        "split": row.get("split", ""),
        "created_at": utc_now(),
        "schema_version": "obtainercli.records.v1",
    }


def build_record_tags(record: Dict[str, Any], tags: Dict[str, str]) -> Tuple[Dict[str, Any], List[dict]]:
    merged = {
        "domain": record["domain"],
        "processing_level": record["processing_level"],
        "source_kind": record["source_kind"],
        "task_type": record["task_type"],
        **tags,
    }
    tag_rows: List[dict] = []
    for name, value in sorted(merged.items()):
        tag_rows.append(
            {
                "record_id": record["record_id"],
                "dataset_id": record["dataset_id"],
                "tag_name": name,
                "tag_value": str(value),
                "tag_source": "cli",
                "confidence": 1.0,
                "created_at": utc_now(),
            }
        )
    record = dict(record)
    record["tag_names"] = sorted(merged.keys())
    return record, tag_rows
