from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import TABLES
from .config import read_lake_config
from .lock import commit_lock

SCHEMA_VERSION = 1
LATEST_LIMIT = 8


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def monitor_state_path(warehouse: str | Path) -> Path:
    return Path(warehouse).expanduser().resolve() / ".loopai" / "monitor_state.json"


def _stat(path: Path) -> dict[str, int | str]:
    if not path.exists():
        return {"path": str(path), "exists": 0, "size": 0, "mtime_ns": 0}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": 1,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def monitor_signature(warehouse: str | Path) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    catalog = root / "catalog.db"
    audit_dir = root / "obtainercli_audit"
    audit: dict[str, dict[str, int | str]] = {}
    for name in ("assets", "ingest_runs", "embeddings", "exports", "record_lineage"):
        audit[name] = _stat(audit_dir / f"{name}.jsonl")
    return {
        "catalog_db": _stat(catalog),
        "catalog_db_wal": _stat(root / "catalog.db-wal"),
        "catalog_db_shm": _stat(root / "catalog.db-shm"),
        "audit": audit,
    }


def _empty_monitor_payload(*, warehouse: Path, lake: str | Path | None = None) -> dict[str, Any]:
    lake_root = warehouse.parent
    table_counts = {name: 0 for name in TABLES}
    tables = {
        name: {
            "name": name,
            "path": "",
            "exists": False,
            "count": count,
            "size_bytes": 0,
            "modified_at": "",
            "warnings": [],
        }
        for name, count in table_counts.items()
    }
    return {
        "ok": True,
        "command": "lake monitor",
        "status": "success",
        "refreshed_at": utc_now(),
        "lake_root": str(lake_root),
        "lake_config": str(lake or ""),
        "config": {"catalog": "datamixer", "namespace": "loopai", "warehouse": str(warehouse)},
        "summary": {
            "datasets": 0,
            "assets": 0,
            "records": 0,
            "record_tags": 0,
            "embeddings": 0,
            "quality_findings": 0,
            "ingest_runs": 0,
            "exports": 0,
            "embedding_coverage": 0,
            "warnings": 0,
            "health_score": 0,
        },
        "tables": tables,
        "embedding": {
            "embedding_provider": "",
            "embedding_model": "",
            "embedding_backend": "",
            "embedding_base_url": "",
            "embedding_text_field": "text",
            "auto_embed": "",
            "api_key_set": False,
            "total_records": 0,
            "indexed_records": 0,
            "pending_records": 0,
            "coverage": 0,
            "probe": {"status": "not_checked"},
        },
        "charts": {
            "ingest_trend": [],
            "composition": {"domain": {}, "processing_level": {}, "source_kind": {}, "task_type": {}},
            "top_tags": [],
            "quality_findings": [],
        },
        "latest": {"records": [], "ingest_runs": [], "quality_findings": [], "exports": []},
        "warnings": [],
    }


def _lake_config_values(lake: str | Path | None) -> dict[str, str]:
    if not lake:
        return {}
    path = Path(lake).expanduser().resolve()
    if not path.is_file():
        path = path / "lake.yaml"
    if not path.is_file():
        return {}
    try:
        return read_lake_config(path)
    except Exception:
        return {}


def _apply_embedding_config(payload: dict[str, Any], config: dict[str, str]) -> None:
    payload["config"].update(
        {
            "catalog": config.get("catalog") or payload["config"].get("catalog") or "datamixer",
            "namespace": config.get("namespace") or payload["config"].get("namespace") or "loopai",
            "warehouse": config.get("warehouse") or payload["config"].get("warehouse") or "",
        }
    )
    payload["embedding"].update(
        {
            "embedding_provider": config.get("embedding_provider", ""),
            "embedding_model": config.get("embedding_model", ""),
            "embedding_backend": config.get("embedding_backend", ""),
            "embedding_base_url": config.get("embedding_base_url", ""),
            "embedding_text_field": config.get("embedding_text_field", "text"),
            "auto_embed": config.get("auto_embed", ""),
            "auto_embed_async": config.get("auto_embed_async", ""),
            "auto_embed_batch_size": config.get("auto_embed_batch_size", ""),
            "api_key_set": bool(config.get("embedding_api_key") or os.getenv("OBTAINERCLI_EMBED_API_KEY")),
        }
    )


def _sync_incremental_counts(state: dict[str, Any], delta: dict[str, Any] | None = None) -> None:
    summary = state.setdefault("summary", {})
    tables = state.setdefault("tables", {})
    for key, value in (delta or {}).get("summary_delta", {}).items():
        if key in tables and isinstance(tables[key], dict):
            tables[key]["count"] = int(tables[key].get("count") or 0) + int(value or 0)
    records = int(summary.get("records") or 0)
    embeddings = int(summary.get("embeddings") or 0)
    coverage = min(round(embeddings / records, 4), 1) if records else 0
    summary["embedding_coverage"] = coverage
    embedding = state.setdefault("embedding", {})
    embedding.update(
        {
            "total_records": records,
            "indexed_records": min(embeddings, records),
            "pending_records": max(records - embeddings, 0),
            "coverage": coverage,
        }
    )


def _audit_line_count(warehouse: Path, table: str) -> int:
    path = warehouse / "obtainercli_audit" / f"{table}.jsonl"
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _latest_audit_rows(warehouse: Path, table: str, limit: int = LATEST_LIMIT) -> list[dict[str, Any]]:
    path = warehouse / "obtainercli_audit" / f"{table}.jsonl"
    if not path.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(rows)


def build_lightweight_monitor_payload(*, warehouse: str | Path, lake: str | Path | None = None) -> dict[str, Any]:
    import sqlite3

    root = Path(warehouse).expanduser().resolve()
    payload = _empty_monitor_payload(warehouse=root, lake=lake)
    _apply_embedding_config(payload, _lake_config_values(lake))
    warnings: list[dict[str, str]] = []
    catalog = root / "catalog.db"
    counts = {name: 0 for name in TABLES}

    if catalog.exists():
        conn = sqlite3.connect(str(catalog))
        conn.row_factory = sqlite3.Row
        try:
            counts["datasets"] = int(conn.execute("SELECT COUNT(*) AS n FROM datasets").fetchone()["n"])
            counts["records"] = int(conn.execute("SELECT COUNT(*) AS n FROM samples").fetchone()["n"])
            counts["record_tags"] = int(
                conn.execute("SELECT COUNT(*) AS n FROM samples, json_each(samples.tags_json)").fetchone()["n"]
            )
            counts["quality_findings"] = int(
                conn.execute(
                    "SELECT COALESCE(SUM(json_array_length(json_extract(tags_json, '$.quality_findings'))), 0) AS n "
                    "FROM samples"
                ).fetchone()["n"]
            )
            for field in ("domain", "task_type"):
                rows = conn.execute(
                    f"SELECT COALESCE({field}, 'unknown') AS value, COUNT(*) AS n "
                    f"FROM samples GROUP BY {field} ORDER BY n DESC LIMIT 16"
                ).fetchall()
                payload["charts"]["composition"][field] = {str(row["value"]): int(row["n"]) for row in rows}
            for key, chart_key in (("processing_level", "processing_level"), ("source_kind", "source_kind")):
                rows = conn.execute(
                    "SELECT COALESCE(json_extract(tags_json, ?), 'unknown') AS value, COUNT(*) AS n "
                    "FROM samples GROUP BY value ORDER BY n DESC LIMIT 16",
                    (f"$.{key}",),
                ).fetchall()
                payload["charts"]["composition"][chart_key] = {str(row["value"]): int(row["n"]) for row in rows}
            top_tags = conn.execute(
                "SELECT je.key AS tag_name, CAST(je.value AS TEXT) AS tag_value, COUNT(*) AS n "
                "FROM samples, json_each(samples.tags_json) AS je "
                "WHERE je.value IS NOT NULL "
                "AND je.type NOT IN ('object', 'array') "
                "AND je.key NOT IN ("
                "'payload','text','instruction','input','output','messages','quality_findings','parent_record_ids'"
                ") "
                "GROUP BY je.key, tag_value ORDER BY n DESC, je.key ASC LIMIT 16"
            ).fetchall()
            payload["charts"]["top_tags"] = [
                {"tag_name": row["tag_name"], "tag_value": str(row["tag_value"]), "count": int(row["n"])}
                for row in top_tags
            ]
            quality_rows = conn.execute(
                "SELECT "
                "COALESCE(json_extract(q.value, '$.finding_type'), 'quality_signal') AS finding_type, "
                "COALESCE(json_extract(q.value, '$.severity'), 'info') AS severity, "
                "COUNT(*) AS n "
                "FROM samples, json_each(json_extract(samples.tags_json, '$.quality_findings')) AS q "
                "GROUP BY finding_type, severity ORDER BY n DESC LIMIT 32"
            ).fetchall()
            payload["charts"]["quality_findings"] = [
                {
                    "finding_type": str(row["finding_type"]),
                    "severity": str(row["severity"]),
                    "count": int(row["n"]),
                }
                for row in quality_rows
            ]
            latest = conn.execute(
                "SELECT sample_id, dataset_id, domain, task_type, created_at, tags_json "
                "FROM samples ORDER BY created_at DESC LIMIT ?",
                (LATEST_LIMIT,),
            ).fetchall()
            payload["latest"]["records"] = [
                {
                    "record_id": row["sample_id"],
                    "dataset_id": row["dataset_id"],
                    "domain": row["domain"] or "",
                    "task_type": row["task_type"] or "",
                    "created_at": str(row["created_at"] or ""),
                }
                for row in latest
            ]
        except sqlite3.Error as exc:
            warnings.append({"code": "LIGHTWEIGHT_MONITOR_SQL_ERROR", "message": str(exc)})
        finally:
            conn.close()

    for table in ("assets", "ingest_runs", "embeddings", "exports", "record_lineage"):
        counts[table] = _audit_line_count(root, table)

    embedding_coverage = round(counts["embeddings"] / counts["records"], 4) if counts["records"] else 0
    payload["summary"].update(
        {
            "datasets": counts["datasets"],
            "assets": counts["assets"],
            "records": counts["records"],
            "record_tags": counts["record_tags"],
            "embeddings": counts["embeddings"],
            "quality_findings": counts["quality_findings"],
            "ingest_runs": counts["ingest_runs"],
            "exports": counts["exports"],
            "embedding_coverage": min(embedding_coverage, 1),
            "warnings": len(warnings),
            "health_score": 100 if not warnings else 85,
        }
    )
    payload["embedding"].update(
        {
            "total_records": counts["records"],
            "indexed_records": min(counts["embeddings"], counts["records"]),
            "pending_records": max(counts["records"] - counts["embeddings"], 0),
            "coverage": min(embedding_coverage, 1),
        }
    )
    for name, count in counts.items():
        table = payload["tables"][name]
        table["count"] = count
        if name in {"assets", "ingest_runs", "embeddings", "exports", "record_lineage"}:
            path = root / "obtainercli_audit" / f"{name}.jsonl"
            stat = _stat(path)
            table.update({"path": str(path), "exists": bool(stat["exists"]), "size_bytes": stat["size"]})
    payload["latest"]["ingest_runs"] = _latest_audit_rows(root, "ingest_runs")
    payload["latest"]["exports"] = _latest_audit_rows(root, "exports")
    payload["charts"]["ingest_trend"] = list(reversed(payload["latest"]["ingest_runs"]))
    payload["warnings"] = warnings
    return payload


def _with_cache_metadata(
    payload: dict[str, Any],
    *,
    warehouse: str | Path,
    cache_status: str,
    stale_reason: str = "",
    rebuild: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    signature = monitor_signature(root)
    now = utc_now()
    out = dict(payload)
    out.setdefault("ok", True)
    out.setdefault("command", "lake monitor")
    out.setdefault("status", "success")
    out["schema_version"] = SCHEMA_VERSION
    out["warehouse"] = str(root)
    out["updated_at"] = now
    out["refreshed_at"] = out.get("refreshed_at") or now
    out["cache_status"] = cache_status
    out["stale"] = cache_status in {"stale", "cache_missing", "error"}
    out["stale_reason"] = stale_reason
    out["signature"] = signature
    out["catalog_db_mtime"] = signature["catalog_db"]["mtime_ns"]
    out["catalog_db_size"] = signature["catalog_db"]["size"]
    out["catalog_db_wal_mtime"] = signature["catalog_db_wal"]["mtime_ns"]
    out["catalog_db_wal_size"] = signature["catalog_db_wal"]["size"]
    out["audit_signature"] = signature["audit"]
    out["rebuild"] = rebuild or out.get("rebuild") or {
        "status": "idle",
        "job_id": "",
        "started_at": "",
        "finished_at": "",
        "message": "",
    }
    return out


def write_monitor_state(warehouse: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    path = monitor_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with commit_lock(root.parent):
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    return state


def _read_raw_state(warehouse: str | Path) -> dict[str, Any] | None:
    path = monitor_state_path(warehouse)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def is_monitor_state_fresh(warehouse: str | Path, state: dict[str, Any]) -> tuple[bool, str]:
    if int(state.get("schema_version") or 0) != SCHEMA_VERSION:
        return False, "schema_version_changed"
    if state.get("cache_status") == "rebuilding":
        return False, "rebuilding"
    if state.get("cache_status") in {"cache_missing", "stale", "error"}:
        return False, str(state.get("stale_reason") or state.get("cache_status"))
    if state.get("signature") != monitor_signature(warehouse):
        return False, "warehouse_signature_changed"
    return True, ""


def read_monitor_state(warehouse: str | Path, *, lake: str | Path | None = None) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    config = _lake_config_values(lake)
    try:
        state = _read_raw_state(root)
    except (OSError, json.JSONDecodeError) as exc:
        payload = _empty_monitor_payload(warehouse=root, lake=lake)
        _apply_embedding_config(payload, config)
        return _with_cache_metadata(payload, warehouse=root, cache_status="error", stale_reason=str(exc))
    if state is None:
        payload = _empty_monitor_payload(warehouse=root, lake=lake)
        _apply_embedding_config(payload, config)
        return _with_cache_metadata(payload, warehouse=root, cache_status="cache_missing", stale_reason="cache_missing")
    _apply_embedding_config(state, config)
    fresh, reason = is_monitor_state_fresh(root, state)
    if fresh:
        state["cache_status"] = "fresh"
        state["stale"] = False
        state["stale_reason"] = ""
        return state
    out = dict(state)
    _apply_embedding_config(out, config)
    if out.get("cache_status") != "rebuilding":
        out["cache_status"] = "stale"
    out["stale"] = True
    out["stale_reason"] = reason
    return out


def mark_monitor_stale(warehouse: str | Path, reason: str) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    state = _read_raw_state(root) or _empty_monitor_payload(warehouse=root)
    state = _with_cache_metadata(state, warehouse=root, cache_status="stale", stale_reason=reason)
    return write_monitor_state(root, state)


def update_monitor_delta(
    warehouse: str | Path,
    delta: dict[str, Any] | None = None,
    *,
    operation_id: str = "",
    trigger_background: bool = False,
    lake: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    existing_state = _read_raw_state(root)
    state = existing_state or build_lightweight_monitor_payload(warehouse=root, lake=lake)
    summary = dict(state.get("summary") or {})
    effective_delta = delta if existing_state is not None else {}
    for key, value in (effective_delta or {}).get("summary_delta", {}).items():
        summary[key] = int(summary.get(key) or 0) + int(value or 0)
    latest_delta = (effective_delta or {}).get("latest", {})
    latest = dict(state.get("latest") or {})
    for key, rows in latest_delta.items():
        merged = list(rows or []) + list(latest.get(key) or [])
        latest[key] = merged[:LATEST_LIMIT]
    state["summary"] = summary
    state["latest"] = latest
    _sync_incremental_counts(state, effective_delta)
    state["last_operation_id"] = operation_id
    cache_status = "rebuilding" if trigger_background else "fresh"
    rebuild = state.get("rebuild") if isinstance(state.get("rebuild"), dict) else {}
    if trigger_background:
        rebuild = {
            "status": "queued",
            "job_id": operation_id or f"monitor-{int(time.time() * 1000)}",
            "started_at": utc_now(),
            "finished_at": "",
            "message": "Background monitor update queued",
        }
    state = _with_cache_metadata(state, warehouse=root, cache_status=cache_status, rebuild=rebuild)
    written = write_monitor_state(root, state)
    if trigger_background:
        start_background_rebuild(root, lake=lake, mark_running=False)
    return written


def rebuild_monitor_state(warehouse: str | Path, *, lake: str | Path | None = None, deep: bool = False) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    running = _with_cache_metadata(
        _read_raw_state(root) or _empty_monitor_payload(warehouse=root, lake=lake),
        warehouse=root,
        cache_status="rebuilding",
        rebuild={
            "status": "running",
            "job_id": f"monitor-{int(time.time() * 1000)}",
            "started_at": utc_now(),
            "finished_at": "",
            "message": "Rebuilding monitor state",
        },
    )
    write_monitor_state(root, running)
    try:
        if deep and lake:
            from api.app.utils.obtainer.monitor import build_lake_monitor

            payload = build_lake_monitor(lake=lake)
        else:
            payload = build_lightweight_monitor_payload(warehouse=root, lake=lake)
        state = _with_cache_metadata(
            payload,
            warehouse=root,
            cache_status="fresh",
            rebuild={
                "status": "idle",
                "job_id": running["rebuild"]["job_id"],
                "started_at": running["rebuild"]["started_at"],
                "finished_at": utc_now(),
                "message": "Monitor state rebuilt",
            },
        )
    except Exception as exc:
        state = _with_cache_metadata(
            running,
            warehouse=root,
            cache_status="error",
            stale_reason=str(exc),
            rebuild={
                **running["rebuild"],
                "status": "error",
                "finished_at": utc_now(),
                "message": str(exc),
            },
        )
    return write_monitor_state(root, state)


def start_background_rebuild(
    warehouse: str | Path,
    *,
    lake: str | Path | None = None,
    mark_running: bool = True,
) -> dict[str, Any]:
    root = Path(warehouse).expanduser().resolve()
    job_id = f"monitor-{int(time.time() * 1000)}"
    if mark_running:
        state = _with_cache_metadata(
            _read_raw_state(root) or _empty_monitor_payload(warehouse=root, lake=lake),
            warehouse=root,
            cache_status="rebuilding",
            rebuild={
                "status": "queued",
                "job_id": job_id,
                "started_at": utc_now(),
                "finished_at": "",
                "message": "Background monitor update queued",
            },
        )
        write_monitor_state(root, state)
    cmd = [sys.executable, "-m", "loopai.skills.ObtainerCLI.monitor_state", "rebuild", "--warehouse", str(root)]
    if lake:
        cmd.extend(["--lake", str(lake)])
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {
        "ok": True,
        "command": "lake monitor rebuild",
        "status": "queued",
        "warehouse": str(root),
        "job_id": job_id,
        "warnings": [],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m loopai.skills.ObtainerCLI.monitor_state")
    sub = parser.add_subparsers(dest="command", required=True)
    rebuild = sub.add_parser("rebuild")
    rebuild.add_argument("--warehouse", required=True)
    rebuild.add_argument("--lake", default="")
    rebuild.add_argument("--deep", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "rebuild":
        state = rebuild_monitor_state(args.warehouse, lake=args.lake or None, deep=args.deep)
        print(json.dumps({"ok": state.get("cache_status") == "fresh", "cache_status": state.get("cache_status")}))
        return 0 if state.get("cache_status") == "fresh" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
