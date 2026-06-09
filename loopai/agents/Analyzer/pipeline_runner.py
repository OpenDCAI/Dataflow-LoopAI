from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional


ANALYZER_PIPELINE_STEPS = (
    "eval_model",
    "analyze_result",
    "draw_conclusion",
    "finish",
)


_STEP_ALIASES = {
    "eval_model_node": "eval_model",
    "AnalyzerAgent.eval_model_node": "eval_model",
    "analyze_result_node": "analyze_result",
    "AnalyzerAgent.analyze_result_node": "analyze_result",
    "draw_conclusion_node": "draw_conclusion",
    "AnalyzerAgent.draw_conclusion_node": "draw_conclusion",
    "finish_node": "finish",
    "AnalyzerAgent.finish_node": "finish",
}


def normalize_analyzer_step(step_name: Optional[str]) -> Optional[str]:
    if not step_name:
        return None
    step_name = str(step_name)
    if step_name in ANALYZER_PIPELINE_STEPS:
        return step_name
    if step_name in _STEP_ALIASES:
        return _STEP_ALIASES[step_name]
    for alias, step in _STEP_ALIASES.items():
        if alias in step_name:
            return step
    for step in ANALYZER_PIPELINE_STEPS:
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


def _checkpoint_dir(checkpoint_path: str) -> None:
    checkpoint_dir = os.path.dirname(checkpoint_path)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)


def _connect(checkpoint_path: str) -> sqlite3.Connection:
    _checkpoint_dir(checkpoint_path)
    conn = sqlite3.connect(checkpoint_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyzer_checkpoints (
            thread_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_analyzer_checkpoint(state: Dict[str, Any], thread_id: str, checkpoint_path: str) -> None:
    payload = json.dumps(_json_safe(state), ensure_ascii=False)
    updated_at = datetime.now(timezone.utc).isoformat()
    with _connect(checkpoint_path) as conn:
        conn.execute(
            """
            INSERT INTO analyzer_checkpoints(thread_id, state_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (thread_id, payload, updated_at),
        )
        conn.commit()


def load_analyzer_checkpoint(thread_id: str, checkpoint_path: str) -> Dict[str, Any]:
    if not os.path.exists(checkpoint_path):
        raise RuntimeError(f"No checkpoint found for thread_id={thread_id} in checkpoint_path={checkpoint_path}")
    with _connect(checkpoint_path) as conn:
        row = conn.execute(
            "SELECT state_json FROM analyzer_checkpoints WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"No checkpoint found for thread_id={thread_id} in checkpoint_path={checkpoint_path}")
    return json.loads(row[0])


def _start_index(step_name: str) -> int:
    step_name = normalize_analyzer_step(step_name)
    if step_name not in ANALYZER_PIPELINE_STEPS:
        available = ", ".join(ANALYZER_PIPELINE_STEPS)
        raise ValueError(f"Unknown Analyzer step: {step_name}. Available steps: {available}")
    return ANALYZER_PIPELINE_STEPS.index(step_name)


def _resume_step_from_state(state: Dict[str, Any]) -> str:
    current = normalize_analyzer_step(state.get("current"))
    last_completed = normalize_analyzer_step(state.get("last_completed"))

    if last_completed in ANALYZER_PIPELINE_STEPS:
        if current == last_completed or current not in ANALYZER_PIPELINE_STEPS:
            next_index = min(_start_index(last_completed) + 1, len(ANALYZER_PIPELINE_STEPS) - 1)
            return ANALYZER_PIPELINE_STEPS[next_index]
    if current in ANALYZER_PIPELINE_STEPS:
        return current
    return ANALYZER_PIPELINE_STEPS[0]


def _is_finished(state: Dict[str, Any]) -> bool:
    return (
        normalize_analyzer_step(state.get("current")) == "finish"
        or normalize_analyzer_step(state.get("last_completed")) == "finish"
    )


def _run_step(step_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
    if step_name == "eval_model":
        from .nodes.eval_model import eval_model_node
        return eval_model_node(state)
    if step_name == "analyze_result":
        from .nodes.analyze_result import analyze_result_node
        return analyze_result_node(state)
    if step_name == "draw_conclusion":
        from .nodes.draw_conclusion import draw_conclusion_node
        return draw_conclusion_node(state)
    raise ValueError(f"Unknown executable Analyzer step: {step_name}")


def run_analyzer_pipeline(
    state: Optional[Dict[str, Any]],
    thread_id: str = "analyzer-default",
    checkpoint_path: str = "outputs/analyzer_checkpoints.sqlite",
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
) -> Dict[str, Any]:
    if resume:
        state = load_analyzer_checkpoint(thread_id, checkpoint_path)
    elif state is None:
        raise ValueError("state is required when resume is false.")

    if baseline_result_path:
        state.setdefault("analyzer", {})["baseline_result_path"] = baseline_result_path

    if from_node is not None:
        start_step = normalize_analyzer_step(from_node)
    elif resume:
        start_step = _resume_step_from_state(state)
    else:
        start_step = ANALYZER_PIPELINE_STEPS[0]

    if start_step is None:
        start_step = ANALYZER_PIPELINE_STEPS[0]
    start_at = _start_index(start_step)

    if _is_finished(state):
        return state

    for step_name in ANALYZER_PIPELINE_STEPS[start_at:]:
        state["current"] = step_name
        save_analyzer_checkpoint(state, thread_id, checkpoint_path)

        if step_name == "finish":
            state["last_completed"] = "finish"
            save_analyzer_checkpoint(state, thread_id, checkpoint_path)
            return state

        state = _run_step(step_name, state)
        state["last_completed"] = step_name
        save_analyzer_checkpoint(state, thread_id, checkpoint_path)

    return state
