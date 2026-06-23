from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .config import resolve_lake_root
from .lock import commit_lock
from .models import build_record, build_record_tags, load_jsonl, parse_tags, sha256_text, utc_now
from .tables import append_rows, ensure_tables, read_table


def _dataset_id(name: str) -> str:
    return "ds_" + sha256_text(name)[:16]


def _asset_id(dataset_id: str, input_path: Path) -> str:
    stat = input_path.stat()
    basis = f"{dataset_id}:{input_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return "asset_" + sha256_text(basis)[:24]


def ingest_path(
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
    lake_root = resolve_lake_root(lake)
    ensure_tables(lake_root)
    input_file = Path(input_path)
    tag_map = parse_tags(tags)
    dataset_id = _dataset_id(dataset)
    asset_id = _asset_id(dataset_id, input_file)
    ingest_run_id = "ingest_" + sha256_text(idempotency_key or f"{dataset}:{input_file.resolve()}")[:24]

    with commit_lock(lake_root):
        previous_runs = read_table(lake_root, "ingest_runs")
        if idempotency_key and any(
            run.get("idempotency_key") == idempotency_key and run.get("status") == "succeeded"
            for run in previous_runs
        ):
            warning = {
                "code": "DUPLICATE_INGEST_SKIPPED",
                "message": f"Idempotency key already succeeded: {idempotency_key}",
            }
            append_rows(
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

        rows = load_jsonl(input_file)
        existing_record_ids = {row["record_id"] for row in read_table(lake_root, "records")}
        records = []
        tag_rows = []
        quality_finding_rows = []
        rows_seen = len(rows)
        rows_quarantined = 0
        for index, row in enumerate(rows, 1):
            record = build_record(
                row,
                dataset_id=dataset_id,
                asset_id=asset_id,
                input_path=input_file,
                row_index=index,
                stage=stage,
                domain=domain,
                processing_level=processing_level,
                source_kind=source_kind,
                task_type=task_type,
                pipeline_run_id=ingest_run_id,
            )
            if record["record_id"] in existing_record_ids:
                if on_duplicate == "error":
                    raise ValueError(f"Duplicate record_id: {record['record_id']}")
                if on_duplicate == "skip":
                    continue
            record, built_tags = build_record_tags(record, tag_map)
            records.append(record)
            tag_rows.extend(built_tags)
            for finding_index, finding in enumerate(row.get("quality_findings") or [], 1):
                if not isinstance(finding, dict):
                    continue
                quality_finding_rows.append(
                    {
                        "finding_id": "finding_"
                        + sha256_text(f"{record['record_id']}:{finding_index}:{finding}")[:24],
                        "record_id": record["record_id"],
                        "dataset_id": dataset_id,
                        "processing_level": processing_level,
                        "source_kind": source_kind,
                        "finding_type": str(finding.get("finding_type", "quality_signal")),
                        "severity": str(finding.get("severity", "info")),
                        "score": finding.get("score"),
                        "metric_name": str(finding.get("metric_name", "")),
                        "metric_value": finding.get("metric_value"),
                        "detector": str(finding.get("detector", "")),
                        "detector_version": str(finding.get("detector_version", "")),
                        "details": finding.get("details", {}),
                        "pipeline_run_id": ingest_run_id,
                        "created_at": utc_now(),
                    }
                )

        dataset_rows = [
            {
                "dataset_id": dataset_id,
                "name": dataset,
                "stage": stage,
                "domain": domain,
                "task_type": task_type,
                "description": "",
                "owner": "",
                "source_kind": source_kind,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "schema_version": "obtainercli.datasets.v1",
                "default_tags": tag_map,
            }
        ]
        asset_rows = [
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
        ]
        append_rows(lake_root, "datasets", dataset_rows)
        append_rows(lake_root, "assets", asset_rows)
        append_rows(lake_root, "records", records)
        append_rows(lake_root, "record_tags", tag_rows)
        append_rows(lake_root, "quality_findings", quality_finding_rows)
        append_rows(
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
                    "rows_seen": rows_seen,
                    "rows_written": len(records),
                    "rows_quarantined": rows_quarantined,
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
        "rows_seen": rows_seen,
        "rows_written": len(records),
        "rows_quarantined": rows_quarantined,
    }
