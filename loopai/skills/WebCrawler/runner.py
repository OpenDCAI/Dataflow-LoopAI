from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
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

_CHECKPOINT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS webcrawler_checkpoints (
    thread_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


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


def _connect(checkpoint_path: str) -> sqlite3.Connection:
    checkpoint_dir = os.path.dirname(checkpoint_path)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)
    conn = sqlite3.connect(checkpoint_path)
    conn.execute(_CHECKPOINT_TABLE_DDL)
    conn.commit()
    return conn


def save_webcrawler_checkpoint(state: Dict[str, Any], thread_id: str, checkpoint_path: str) -> None:
    payload = json.dumps(_json_safe(state), ensure_ascii=False)
    updated_at = datetime.now(timezone.utc).isoformat()
    with _connect(checkpoint_path) as conn:
        conn.execute(
            """INSERT INTO webcrawler_checkpoints(thread_id, state_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(thread_id) DO UPDATE SET
                   state_json = excluded.state_json,
                   updated_at = excluded.updated_at""",
            (thread_id, payload, updated_at),
        )
        conn.commit()


def load_webcrawler_checkpoint(thread_id: str, checkpoint_path: str) -> Dict[str, Any]:
    if not os.path.exists(checkpoint_path):
        raise RuntimeError(f"No checkpoint found for thread_id={thread_id} in {checkpoint_path}")
    with _connect(checkpoint_path) as conn:
        row = conn.execute(
            "SELECT state_json FROM webcrawler_checkpoints WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"No checkpoint found for thread_id={thread_id} in {checkpoint_path}")
    return json.loads(row[0])


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

    if resume:
        if not thread_id:
            thread_id = kwargs.get("task_id") or os.getenv("TASK_ID")
        if not thread_id:
            raise ValueError("task_id/thread_id is required when resume=True")
        state = load_webcrawler_checkpoint(str(thread_id), checkpoint_path)
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
    thread_id = runtime["thread_id"]
    output_dir = runtime["output_dir"]
    writer = get_event_writer(name="webcrawler", context_id=thread_id, log_file_path=output_dir)

    writer(
        StreamEvent(
            current="webcrawler",
            progress=0.0,
            message="WebCrawler pipeline started",
            data={"task_id": thread_id, "resume": resume},
        )
    )

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
        save_webcrawler_checkpoint(state, thread_id, checkpoint_path)
        writer(
            StreamEvent(
                current="webcrawler",
                progress=idx / max(1, len(WEBCRAWLER_PIPELINE_STEPS)),
                message=f"Step started: {step_name}",
            )
        )

        state = _run_step(step_name, state)
        state["last_completed"] = step_name
        save_webcrawler_checkpoint(state, thread_id, checkpoint_path)

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

