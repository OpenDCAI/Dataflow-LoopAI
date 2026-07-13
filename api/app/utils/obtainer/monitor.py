from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loopai.skills.ObtainerCLI.config import read_lake_config_for_lake, resolve_lake_root
from loopai.skills.ObtainerCLI.monitor_state import benchmark_monitor_state
from loopai.skills.ObtainerCLI.tables import TABLES, read_table, table_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _modified_at(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _short_text(value: Any, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _read_table_safe(lake_root: Path, table: str) -> tuple[list[dict], dict]:
    path = table_path(lake_root, table)
    warnings: list[dict] = []
    rows: list[dict] = []
    try:
        rows = read_table(lake_root, table)
    except Exception as exc:
        warnings.append(
            {
                "code": "TABLE_READ_ERROR",
                "table": table,
                "message": str(exc),
            }
        )
    return rows, {
        "name": table,
        "path": str(path),
        "exists": path.exists() or bool(rows),
        "count": len(rows),
        "size_bytes": _path_size(path),
        "modified_at": _modified_at(path),
        "warnings": warnings,
    }


def _latest(rows: list[dict], *, limit: int, time_fields: tuple[str, ...]) -> list[dict]:
    def sort_key(row: dict) -> str:
        for field in time_fields:
            value = row.get(field)
            if value:
                return str(value)
        return ""

    return sorted(rows, key=sort_key, reverse=True)[:limit]


def _record_preview(row: dict) -> dict:
    return {
        "record_id": row.get("record_id", ""),
        "dataset_id": row.get("dataset_id", ""),
        "stage": row.get("stage", ""),
        "domain": row.get("domain", ""),
        "processing_level": row.get("processing_level", ""),
        "source_kind": row.get("source_kind", ""),
        "task_type": row.get("task_type", ""),
        "text": _short_text(row.get("text", "")),
        "quality_score": row.get("quality_score"),
        "created_at": row.get("created_at", ""),
    }


def _quality_preview(row: dict) -> dict:
    return {
        "finding_id": row.get("finding_id", ""),
        "record_id": row.get("record_id", ""),
        "dataset_id": row.get("dataset_id", ""),
        "finding_type": row.get("finding_type", ""),
        "severity": row.get("severity", ""),
        "score": row.get("score"),
        "metric_name": row.get("metric_name", ""),
        "metric_value": row.get("metric_value"),
        "detector": row.get("detector", ""),
        "created_at": row.get("created_at", ""),
    }


def _export_preview(row: dict) -> dict:
    return {
        "export_id": row.get("export_id", ""),
        "strategy": row.get("strategy", ""),
        "requested_size": row.get("requested_size", 0),
        "actual_size": row.get("actual_size", 0),
        "output_uri": row.get("output_uri", ""),
        "format": row.get("format", ""),
        "created_at": row.get("created_at", ""),
    }


def _count_by(rows: list[dict], field: str) -> dict[str, int]:
    counts = Counter(str(row.get(field) or "unknown") for row in rows)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _top_tags(tag_rows: list[dict], limit: int = 16) -> list[dict]:
    counts = Counter(
        (str(row.get("tag_name", "")), str(row.get("tag_value", "")))
        for row in tag_rows
        if row.get("tag_name") and row.get("tag_value")
    )
    return [
        {"tag_name": name, "tag_value": value, "count": count}
        for (name, value), count in counts.most_common(limit)
    ]


def _quality_chart(rows: list[dict]) -> list[dict]:
    counts = Counter(
        (str(row.get("finding_type") or "quality_signal"), str(row.get("severity") or "info"))
        for row in rows
    )
    return [
        {"finding_type": finding_type, "severity": severity, "count": count}
        for (finding_type, severity), count in counts.most_common()
    ]


def _ingest_trend(rows: list[dict], limit: int = 14) -> list[dict]:
    latest = _latest(rows, limit=limit, time_fields=("finished_at", "started_at"))
    return [
        {
            "ingest_run_id": row.get("ingest_run_id", ""),
            "label": _short_text(row.get("ingest_run_id", ""), 18),
            "status": row.get("status", ""),
            "started_at": row.get("started_at", ""),
            "finished_at": row.get("finished_at", ""),
            "rows_seen": int(row.get("rows_seen") or 0),
            "rows_written": int(row.get("rows_written") or 0),
            "rows_quarantined": int(row.get("rows_quarantined") or 0),
        }
        for row in reversed(latest)
    ]


def _embedding_summary(records: list[dict], embeddings: list[dict], config: dict[str, str]) -> dict:
    record_ids = {row.get("record_id") for row in records if row.get("record_id")}
    indexed_ids = {
        row.get("record_id")
        for row in embeddings
        if row.get("record_id") and str(row.get("index_status", "indexed")) == "indexed"
    }
    total_records = len(record_ids)
    indexed_records = len(indexed_ids & record_ids)
    coverage = round(indexed_records / total_records, 4) if total_records else 0
    return {
        "embedding_provider": config.get("embedding_provider", ""),
        "embedding_model": config.get("embedding_model", ""),
        "embedding_backend": config.get("embedding_backend", ""),
        "embedding_base_url": config.get("embedding_base_url", ""),
        "embedding_text_field": config.get("embedding_text_field", "text"),
        "auto_embed": config.get("auto_embed", ""),
        "api_key_set": bool(config.get("embedding_api_key") or os.getenv("OBTAINERCLI_EMBED_API_KEY")),
        "total_records": total_records,
        "indexed_records": indexed_records,
        "pending_records": max(total_records - indexed_records, 0),
        "coverage": coverage,
        "probe": {"status": "not_checked"},
    }


def _health_score(warnings: list[dict], records: list[dict], embedding: dict, quality_findings: list[dict]) -> int:
    score = 100
    score -= min(len(warnings) * 5, 35)
    if records and embedding["coverage"] < 1:
        score -= min(int((1 - embedding["coverage"]) * 30), 30)
    if quality_findings:
        score -= min(len(quality_findings) * 3, 25)
    return max(score, 0)


def build_lake_monitor(*, lake: str | Path, latest_limit: int = 8) -> dict:
    lake_path = Path(lake).expanduser()
    lake_root = resolve_lake_root(lake_path)
    config = read_lake_config_for_lake(lake_path)
    tables: dict[str, dict] = {}
    rows_by_table: dict[str, list[dict]] = {}
    warnings: list[dict] = []

    for table in TABLES:
        rows, meta = _read_table_safe(lake_root, table)
        rows_by_table[table] = rows
        tables[table] = meta
        warnings.extend(meta["warnings"])

    records = rows_by_table["records"]
    ingest_runs = rows_by_table["ingest_runs"]
    embeddings = rows_by_table["embeddings"]
    quality_findings = rows_by_table["quality_findings"]
    exports = rows_by_table["exports"]
    tag_rows = rows_by_table["record_tags"]
    embedding = _embedding_summary(records, embeddings, config)
    benchmarks = benchmark_monitor_state(lake_root)

    if records and embedding["coverage"] < 1:
        warnings.append(
            {
                "code": "EMBEDDING_PENDING",
                "message": f"{embedding['pending_records']} records are not indexed",
            }
        )
    failed_runs = [row for row in ingest_runs if str(row.get("status", "")).lower() not in {"succeeded", "success"}]
    if failed_runs:
        warnings.append(
            {
                "code": "INGEST_RUN_WARNINGS",
                "message": f"{len(failed_runs)} ingest runs are not marked as succeeded",
            }
        )

    summary = {
        "datasets": len(rows_by_table["datasets"]),
        "assets": len(rows_by_table["assets"]),
        "records": len(records),
        "record_tags": len(tag_rows),
        "embeddings": len(embeddings),
        "quality_findings": len(quality_findings),
        "ingest_runs": len(ingest_runs),
        "exports": len(exports),
        "benchmarks": int(benchmarks.get("count") or 0),
        "benchmark_rows": int(benchmarks.get("total_rows") or 0),
        "embedding_coverage": embedding["coverage"],
        "warnings": len(warnings),
        "health_score": _health_score(warnings, records, embedding, quality_findings),
    }

    return {
        "ok": True,
        "command": "lake monitor",
        "status": "success",
        "refreshed_at": _utc_now(),
        "lake_root": str(lake_root),
        "lake_config": str(lake_path),
        "config": {
            "catalog": config.get("catalog", ""),
            "namespace": config.get("namespace", ""),
            "warehouse": config.get("warehouse", ""),
        },
        "summary": summary,
        "tables": tables,
        "embedding": embedding,
        "benchmarks": benchmarks,
        "charts": {
            "ingest_trend": _ingest_trend(ingest_runs),
            "composition": {
                "domain": _count_by(records, "domain"),
                "processing_level": _count_by(records, "processing_level"),
                "source_kind": _count_by(records, "source_kind"),
                "task_type": _count_by(records, "task_type"),
            },
            "top_tags": _top_tags(tag_rows),
            "quality_findings": _quality_chart(quality_findings),
        },
        "latest": {
            "records": [_record_preview(row) for row in _latest(records, limit=latest_limit, time_fields=("created_at",))],
            "ingest_runs": _latest(ingest_runs, limit=latest_limit, time_fields=("finished_at", "started_at")),
            "quality_findings": [
                _quality_preview(row)
                for row in _latest(quality_findings, limit=latest_limit, time_fields=("created_at",))
            ],
            "exports": [_export_preview(row) for row in _latest(exports, limit=latest_limit, time_fields=("created_at",))],
        },
        "warnings": warnings,
    }


def probe_embedding_health(*, lake: str | Path, timeout_seconds: float = 3.0) -> dict:
    lake_path = Path(lake).expanduser()
    config = read_lake_config_for_lake(lake_path)
    provider = config.get("embedding_provider", "")
    model = config.get("embedding_model", "")
    base_url = config.get("embedding_base_url", "")
    result = {
        "status": "not_configured",
        "embedding_provider": provider,
        "embedding_model": model,
        "embedding_backend": config.get("embedding_backend", ""),
        "embedding_base_url": base_url,
        "embedding_dim": 0,
        "message": "",
    }
    if provider != "openai-compatible" or not base_url or not model:
        result["status"] = "unsupported_probe" if provider else "not_configured"
        result["message"] = "Only openai-compatible embedding endpoints can be probed"
        return result

    payload = json.dumps({"model": model, "input": ["LoopAI embedding monitor probe"]}).encode("utf-8")
    headers = {"content-type": "application/json"}
    api_key = config.get("embedding_api_key") or os.getenv("OBTAINERCLI_EMBED_API_KEY", "")
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    endpoint = base_url.rstrip("/") + "/embeddings"
    request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        result["status"] = "offline"
        result["message"] = str(exc)
        return result

    vectors = [item.get("embedding", []) for item in body.get("data", [])]
    if not vectors or not isinstance(vectors[0], list):
        result["status"] = "error"
        result["message"] = "Embedding endpoint returned no vector"
        return result
    result["status"] = "online"
    result["embedding_dim"] = len(vectors[0])
    result["message"] = "Embedding endpoint responded"
    return result
