#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopai.common.db_tool.runtime import list_task_runtime_history_sync, upsert_task_runtime_sync


SUPPORTED_NODES = {
    "configer",
    "judger",
    "analyzer",
    "trainer",
    "webcrawler",
    "obtainer",
    "constructor",
}

def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else {}


def _repo_root() -> Path:
    return REPO_ROOT


def _resolve_db_path() -> str:
    return os.getenv("DB_PATH", "").strip()


def _resolve_task_id(payload: dict[str, Any]) -> str:
    return os.getenv("TASK_ID", "").strip()


def _resolve_node_name(tool_name: str) -> str:
    tool_name = (tool_name or "").strip().lower()
    if not tool_name.startswith("mcp__"):
        return ""

    parts = tool_name.split("__", 2)
    if len(parts) < 3:
        return ""

    server_name = parts[1].replace("-", "_")
    if server_name.startswith("loopai_"):
        server_name = server_name[len("loopai_") :]
    node_name = server_name.split("_", 1)[0].strip()
    return node_name if node_name in SUPPORTED_NODES else ""


def _extract_status_from_result(payload: dict[str, Any]) -> str:
    for error_key in ("tool_error", "error", "exception"):
        if payload.get(error_key):
            return "failed"

    for result_key in ("tool_output", "tool_result", "tool_response", "result", "response", "output"):
        result = payload.get(result_key)
        if not result:
            continue
        if isinstance(result, dict):
            if result.get("ok") is False:
                return "failed"
            status = str(result.get("status", "")).strip().lower()
            if status == "completed":
                return "completed"
            if status in {"failed", "error", "cancelled", "denied"}:
                return "failed"
            if result.get("error"):
                return "failed"
            if result.get("ok") is True:
                return "completed"
        if isinstance(result, str):
            lowered = result.strip().lower()
            if any(marker in lowered for marker in ('"ok": false', '"status": "failed"', '"status":"failed"')):
                return "failed"
            if any(marker in lowered for marker in ('"status": "completed"', '"status":"completed"')):
                return "completed"
            if any(marker in lowered for marker in ('"error"', '"exception"')):
                return "failed"

    return "completed"


def _update_runtime(status: str, payload: dict[str, Any], mode: str) -> None:
    tool_name = str(payload.get("tool_name", "")).strip()
    node_name = _resolve_node_name(tool_name)
    if node_name not in SUPPORTED_NODES:
        return

    task_id = _resolve_task_id(payload)
    if not task_id:
        return

    db_path = _resolve_db_path()
    if not db_path or not Path(db_path).exists():
        return

    if mode == "pre":
        version = ""
        for item in list_task_runtime_history_sync(db_path, task_id, node_name):
            if str(item.get("status", "")).strip().lower() == "running":
                version = str(item.get("version", "")).strip()
                if version:
                    break
        if not version:
            version = str(uuid.uuid4())
    else:
        version = ""
        for item in list_task_runtime_history_sync(db_path, task_id, node_name):
            if str(item.get("status", "")).strip().lower() == "running":
                version = str(item.get("version", "")).strip()
                if version:
                    break
    if not version:
        return

    upsert_task_runtime_sync(
        db_path=db_path,
        task_id=task_id,
        node_name=node_name,
        version=version,
        status=status,
    )


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if mode not in {"pre", "post"}:
        return 0

    try:
        payload = _read_payload()
        status = "running" if mode == "pre" else _extract_status_from_result(payload)
        _update_runtime(status, payload, mode)
    except Exception:
        # Hooks should not block tool execution if runtime tracking fails.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
