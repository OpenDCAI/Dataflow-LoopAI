from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loopai.agents.WebCrawler.nodes.crawl_node import crawl_node
from loopai.agents.WebCrawler.nodes.end_node import end_node
from loopai.agents.WebCrawler.nodes.start_node import start_node
from loopai.agents.WebCrawler.nodes.webcrawler_dataset_node import webcrawler_dataset_node
from loopai.common.event_tool import StreamEvent, get_event_writer
from loopai.logger import get_logger

from .runtime_config import resolve_webcrawler_runtime_config

logger = get_logger()

WEBCRAWLER_PIPELINE_STEPS = (
    "start",
    "crawl",
    "dataset",
    "finish",
)

_STEP_ALIASES = {
    "start_node": "start",
    "crawl_node": "crawl",
    "webcrawler_dataset_node": "dataset",
    "end_node": "finish",
}

_CHECKPOINT_TABLE = "webcrawler_checkpoints_v2"
_LEGACY_CHECKPOINT_TABLE = "webcrawler_checkpoints"
_DEFAULT_CHECKPOINT_VERSION = "__default__"
_CHECKPOINT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {_CHECKPOINT_TABLE} (
    thread_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(thread_id, version_id)
)
"""
_LEGACY_CHECKPOINT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {_LEGACY_CHECKPOINT_TABLE} (
    thread_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_WEBCRAWLER_TASK_STATE_UPDATE_FIELDS = (
    "output_dir",
    "output_run_id",
    "output_result",
    "dataset_summary",
    "dataset_sft_count",
    "dataset_pt_count",
    "dataset_sft_path",
    "dataset_pt_path",
    "dataset_sft_mapped_path",
    "dataset_pt_mapped_path",
    "dataset_mapping_results",
    "runtime_version_id",
    "runtime_version_output_dir",
    "runtime_current_step",
    "runtime_last_completed_step",
)
_SENSITIVE_WEBCRAWLER_FIELDS = ("deepseek_api_key", "tavily_api_key")


def normalize_webcrawler_step(step_name: Optional[str]) -> Optional[str]:
    if not step_name:
        return None
    step_name = str(step_name)
    if step_name in WEBCRAWLER_PIPELINE_STEPS:
        return step_name
    if step_name in _STEP_ALIASES:
        return _STEP_ALIASES[step_name]
    for alias, step in _STEP_ALIASES.items():
        if alias in step_name:
            return step
    for step in WEBCRAWLER_PIPELINE_STEPS:
        if step in step_name:
            return step
    return step_name


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return str(value)


def _sanitize_sensitive_webcrawler_fields(state: Dict[str, Any]) -> Dict[str, Any]:
    webcrawler = state.get("webcrawler")
    if not isinstance(webcrawler, dict):
        return state
    for field in _SENSITIVE_WEBCRAWLER_FIELDS:
        webcrawler.pop(field, None)
    return state


def _connect(checkpoint_path: str) -> sqlite3.Connection:
    checkpoint_dir = os.path.dirname(checkpoint_path)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)
    conn = sqlite3.connect(checkpoint_path)
    conn.execute(_CHECKPOINT_TABLE_DDL)
    conn.execute(_LEGACY_CHECKPOINT_TABLE_DDL)
    conn.commit()
    return conn


def _resolve_checkpoint_version_id(state: Dict[str, Any], version_id: Optional[str] = None) -> str:
    candidate = str(version_id or "").strip()
    if candidate:
        return candidate
    state_version = str((state.get("webcrawler") or {}).get("runtime_version_id") or "").strip()
    return state_version or _DEFAULT_CHECKPOINT_VERSION


def _latest_runtime_version(task_id: str, db_path: Optional[str], node_name: str = "webcrawler") -> Optional[str]:
    if not task_id or not db_path:
        return None
    try:
        from loopai.common.db_tool.runtime import get_latest_task_runtime_sync

        runtime = get_latest_task_runtime_sync(db_path, task_id, node_name)
        if runtime and runtime.get("version"):
            return str(runtime["version"])
    except Exception as exc:
        logger.warning("Failed to resolve latest runtime version for %s: %s", task_id, exc)
    return None


def save_webcrawler_checkpoint(
    state: Dict[str, Any],
    thread_id: str,
    checkpoint_path: str,
    version_id: Optional[str] = None,
) -> None:
    state = _sanitize_sensitive_webcrawler_fields(state)
    payload = json.dumps(_json_safe(state), ensure_ascii=False)
    updated_at = datetime.now(timezone.utc).isoformat()
    checkpoint_version = _resolve_checkpoint_version_id(state, version_id)
    with _connect(checkpoint_path) as conn:
        conn.execute(
            f"""INSERT INTO {_CHECKPOINT_TABLE}(thread_id, version_id, state_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(thread_id, version_id) DO UPDATE SET
                   state_json = excluded.state_json,
                   updated_at = excluded.updated_at""",
            (thread_id, checkpoint_version, payload, updated_at),
        )
        conn.execute(
            f"""INSERT INTO {_LEGACY_CHECKPOINT_TABLE}(thread_id, state_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(thread_id) DO UPDATE SET
                   state_json = excluded.state_json,
                   updated_at = excluded.updated_at""",
            (thread_id, payload, updated_at),
        )
        conn.commit()


def load_webcrawler_checkpoint(
    thread_id: str,
    checkpoint_path: str,
    version_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not os.path.exists(checkpoint_path):
        raise RuntimeError(f"No checkpoint found for thread_id={thread_id} in {checkpoint_path}")
    with _connect(checkpoint_path) as conn:
        normalized_version = str(version_id or "").strip()
        if normalized_version:
            row = conn.execute(
                f"""SELECT state_json
                    FROM {_CHECKPOINT_TABLE}
                    WHERE thread_id = ? AND version_id = ?
                    LIMIT 1""",
                (thread_id, normalized_version),
            ).fetchone()
        else:
            row = conn.execute(
                f"""SELECT state_json
                    FROM {_CHECKPOINT_TABLE}
                    WHERE thread_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 1""",
                (thread_id,),
            ).fetchone()
        if row is None:
            row = conn.execute(
                f"SELECT state_json FROM {_LEGACY_CHECKPOINT_TABLE} WHERE thread_id = ? LIMIT 1",
                (thread_id,),
            ).fetchone()
    if row is None:
        raise RuntimeError(f"No checkpoint found for thread_id={thread_id} in {checkpoint_path}")
    loaded = json.loads(row[0])
    if isinstance(loaded, dict):
        return _sanitize_sensitive_webcrawler_fields(loaded)
    return loaded


def _start_index(step_name: str) -> int:
    normalized = normalize_webcrawler_step(step_name)
    if normalized not in WEBCRAWLER_PIPELINE_STEPS:
        available = ", ".join(WEBCRAWLER_PIPELINE_STEPS)
        raise ValueError(f"Unknown WebCrawler step: {step_name}. Available: {available}")
    return WEBCRAWLER_PIPELINE_STEPS.index(normalized)


def _resume_step_from_state(state: Dict[str, Any]) -> str:
    last_completed = normalize_webcrawler_step(state.get("last_completed"))
    if last_completed in WEBCRAWLER_PIPELINE_STEPS and last_completed != "finish":
        next_index = min(_start_index(last_completed) + 1, len(WEBCRAWLER_PIPELINE_STEPS) - 1)
        return WEBCRAWLER_PIPELINE_STEPS[next_index]
    current = normalize_webcrawler_step(state.get("current"))
    if current in WEBCRAWLER_PIPELINE_STEPS:
        return current
    return WEBCRAWLER_PIPELINE_STEPS[0]


def _run_step(step_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
    step = normalize_webcrawler_step(step_name)
    if step == "start":
        return start_node(state)
    if step == "crawl":
        return crawl_node(state)
    if step == "dataset":
        return webcrawler_dataset_node(state)
    if step == "finish":
        return end_node(state)
    raise ValueError(f"Unknown executable WebCrawler step: {step_name}")


def _safe_update_webcrawler_task_state(
    state: Dict[str, Any],
    runtime: Dict[str, Any],
) -> None:
    task_id = str(runtime.get("thread_id") or "").strip()
    db_path = str(runtime.get("db_path") or "").strip()
    if not task_id or not db_path:
        return

    webcrawler_state = state.get("webcrawler", {})
    if not isinstance(webcrawler_state, dict):
        return

    updates = {
        key: webcrawler_state[key]
        for key in _WEBCRAWLER_TASK_STATE_UPDATE_FIELDS
        if key in webcrawler_state and webcrawler_state.get(key) not in (None, "")
    }
    if not updates:
        return

    try:
        from loopai.skills.Configer import update_configer_task_state_config

        os.environ["DB_PATH"] = db_path
        result = update_configer_task_state_config(
            section_name="webcrawler",
            updates=updates,
            task_id=task_id,
        )
        if not result.get("ok"):
            detail = (result.get("error") or {}).get("detail") or result.get("message")
            logger.warning("Failed to sync WebCrawler task state: %s", detail)
    except Exception as exc:
        logger.warning("Failed to sync WebCrawler task state: %s", exc)


def run_webcrawler_pipeline(
    state: Optional[Dict[str, Any]],
    thread_id: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    if checkpoint_path is None:
        checkpoint_path = os.getenv("WEBCRAWLER_CHECKPOINT_PATH", "outputs/webcrawler_checkpoints.sqlite")

    resume_version_id = str(kwargs.get("version_id") or kwargs.get("run_id") or "").strip()
    writer = kwargs.get("writer")

    if resume:
        if not thread_id:
            thread_id = kwargs.get("task_id") or os.getenv("TASK_ID")
        if not thread_id:
            raise ValueError("task_id/thread_id is required when resume=True")
        db_path = str(kwargs.get("db_path") or os.getenv("DB_PATH") or "").strip()
        resume_version_id = resume_version_id or _latest_runtime_version(str(thread_id), db_path) or ""
        state = load_webcrawler_checkpoint(
            str(thread_id),
            checkpoint_path,
            version_id=resume_version_id or None,
        )
    elif state is None:
        state = {}
    else:
        state = dict(state)

    runtime = resolve_webcrawler_runtime_config(
        state,
        thread_id=thread_id,
        checkpoint_path=checkpoint_path,
        **kwargs,
    )
    _sanitize_sensitive_webcrawler_fields(state)
    thread_id = runtime["thread_id"]
    output_dir = runtime["output_dir"]
    existing_version_id = str((state.get("webcrawler") or {}).get("runtime_version_id") or "").strip()
    resume_version_id = resume_version_id or existing_version_id
    if resume and not resume_version_id:
        resume_version_id = _latest_runtime_version(thread_id, runtime.get("db_path"))

    if writer is None:
        writer = get_event_writer(
            name="webcrawler",
            context_id=thread_id,
            log_file_path=output_dir,
            version_id=resume_version_id or None,
        )
    elif getattr(writer, "version_id", None) is None and resume_version_id:
        writer.version_id = resume_version_id

    writer(
        StreamEvent(
            current="webcrawler",
            progress=0.0,
            message="WebCrawler pipeline started",
            data={"task_id": thread_id, "resume": resume},
        )
    )
    version_id = str(writer.version_id or "")
    version_output_dir = str(Path(output_dir) / str(thread_id) / "webcrawler" / version_id)
    os.makedirs(version_output_dir, exist_ok=True)

    state.setdefault("webcrawler", {})
    state["webcrawler"]["runtime_version_id"] = version_id
    state["webcrawler"]["runtime_version_output_dir"] = version_output_dir
    state["webcrawler"]["output_dir"] = version_output_dir
    _safe_update_webcrawler_task_state(state, runtime)

    if from_step is not None:
        start_step = normalize_webcrawler_step(from_step)
    elif resume:
        start_step = _resume_step_from_state(state)
    else:
        start_step = WEBCRAWLER_PIPELINE_STEPS[0]

    if start_step is None:
        start_step = WEBCRAWLER_PIPELINE_STEPS[0]
    start_at = _start_index(start_step)

    for idx, step_name in enumerate(WEBCRAWLER_PIPELINE_STEPS[start_at:], start_at):
        state["current"] = step_name
        state["webcrawler"]["runtime_current_step"] = step_name
        save_webcrawler_checkpoint(state, thread_id, checkpoint_path, version_id=version_id)
        _safe_update_webcrawler_task_state(state, runtime)
        writer(
            StreamEvent(
                current="webcrawler",
                progress=idx / max(1, len(WEBCRAWLER_PIPELINE_STEPS)),
                message=f"Step started: {step_name}",
            )
        )

        try:
            state = _run_step(step_name, state)
            state["last_completed"] = step_name
            state.setdefault("webcrawler", {})
            state["webcrawler"]["runtime_last_completed_step"] = step_name
            save_webcrawler_checkpoint(state, thread_id, checkpoint_path, version_id=version_id)
            _safe_update_webcrawler_task_state(state, runtime)
        except Exception as exc:
            state.setdefault("webcrawler", {})
            state["exception"] = str(exc)
            state["webcrawler"]["runtime_current_step"] = step_name
            save_webcrawler_checkpoint(state, thread_id, checkpoint_path, version_id=version_id)
            _safe_update_webcrawler_task_state(state, runtime)
            raise

        if state.get("exception"):
            raise RuntimeError(str(state["exception"]))

        writer(
            StreamEvent(
                current="webcrawler",
                progress=(idx + 1) / max(1, len(WEBCRAWLER_PIPELINE_STEPS)),
                message=f"Step completed: {step_name}",
            )
        )

    return state


def run_webcrawler_standalone(
    state: Optional[Dict[str, Any]],
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    return run_webcrawler_pipeline(
        state=state,
        thread_id=thread_id,
        checkpoint_path=checkpoint_path,
        resume=resume,
        from_step=from_step,
        **kwargs,
    )


__all__ = [
    "WEBCRAWLER_PIPELINE_STEPS",
    "normalize_webcrawler_step",
    "run_webcrawler_pipeline",
    "run_webcrawler_standalone",
    "save_webcrawler_checkpoint",
    "load_webcrawler_checkpoint",
]

