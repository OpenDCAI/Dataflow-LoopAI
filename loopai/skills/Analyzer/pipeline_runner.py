from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from loopai.schema.events import StreamEvent
from .state_bridge import load_analyzer_state_from_configer, update_analyzer_state_via_configer


ANALYZER_PIPELINE_STEPS = (
    "eval_model",
    "analyze_result",
    "draw_conclusion",
    "finish",
)

_STEP_PROGRESS_RANGES = {
    "eval_model": (0.00, 0.45),
    "analyze_result": (0.45, 0.75),
    "draw_conclusion": (0.75, 0.95),
    "finish": (0.95, 1.00),
}


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


def _event_to_dict(payload: StreamEvent | Dict[str, Any] | Any) -> Dict[str, Any]:
    if isinstance(payload, StreamEvent):
        return payload.json()
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "json") and callable(payload.json):
        raw = payload.json()
        return dict(raw) if isinstance(raw, dict) else {"message": str(raw)}
    return {"message": str(payload)}


def _progress_percent(progress: float) -> int:
    return max(0, min(100, int(round(progress * 100))))


def _map_step_progress(step_name: str, step_progress: Optional[float]) -> float:
    start, end = _STEP_PROGRESS_RANGES.get(step_name, (0.0, 1.0))
    if step_progress is None:
        step_progress = 0.0
    step_progress = max(0.0, min(1.0, float(step_progress)))
    return start + (end - start) * step_progress


def _emit_pipeline_progress(
    writer: Optional[Callable],
    progress: float,
    message: str,
    *,
    data: Optional[Dict[str, Any]] = None,
    progress_state: Optional[Dict[str, int]] = None,
) -> None:
    if writer is None:
        return

    progress = max(0.0, min(1.0, float(progress)))
    target_percent = _progress_percent(progress)
    last_percent = progress_state.get("last_percent", -1) if progress_state is not None else -1

    if target_percent >= last_percent:
        writer(StreamEvent(
            current="analyzer.pipeline",
            progress=round(progress, 4),
            progress_num=target_percent,
            total=100,
            message=f"整体进度 {target_percent}%：{message}",
            data={
                **(data or {}),
                "progress_percent": target_percent,
            },
        ))
        if progress_state is not None:
            progress_state["last_percent"] = target_percent


class _AnalyzerProgressWriter:
    """Forward node events and update pipeline progress from real work counts."""

    def __init__(
        self,
        writer: Optional[Callable],
        step_name: str,
        progress_state: Optional[Dict[str, int]] = None,
    ) -> None:
        self._writer = writer
        self._step_name = step_name
        self._progress_state = progress_state

    def __call__(self, payload: StreamEvent | Dict[str, Any] | Any):
        if self._writer is None:
            return None

        event_dict = _event_to_dict(payload)
        result = self._writer(payload)

        if str(event_dict.get("current") or "") == "analyzer.pipeline":
            return result

        data = event_dict.get("data") if isinstance(event_dict.get("data"), dict) else {}
        processed_samples = data.get("processed_samples")
        total_failed_samples = data.get("total_failed_samples")
        is_heartbeat = bool(data.get("heartbeat"))

        # Do not convert arbitrary local node progress into global progress.
        # It caused jumps such as 4% -> 63% when a node reported "local 60%".
        # Only real completed work units should move the global bar.
        if (
            not is_heartbeat
            and isinstance(processed_samples, (int, float))
            and isinstance(total_failed_samples, (int, float))
            and total_failed_samples >= 0
        ):
            ratio = 1.0 if total_failed_samples == 0 else processed_samples / max(total_failed_samples, 1)
            overall_progress = _map_step_progress(self._step_name, ratio)
            percent = _progress_percent(overall_progress)
            _emit_pipeline_progress(
                self._writer,
                overall_progress,
                event_dict.get("message") or self._step_name,
                data={
                    "step": self._step_name,
                    "step_current": event_dict.get("current"),
                    "processed_samples": processed_samples,
                    "total_failed_samples": total_failed_samples,
                    "progress_percent": percent,
                },
                progress_state=self._progress_state,
            )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)


def _checkpoint_dir(checkpoint_path: str) -> None:
    checkpoint_dir = os.path.dirname(checkpoint_path)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)


def _connect(checkpoint_path: str) -> sqlite3.Connection:
    _checkpoint_dir(checkpoint_path)
    conn = sqlite3.connect(checkpoint_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyzer_checkpoints_v2 (
            thread_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(thread_id, version_id)
        )
        """
    )
    conn.commit()
    return conn


def save_analyzer_checkpoint(
    state: Dict[str, Any],
    thread_id: str,
    checkpoint_path: str,
    version_id: str = "default",
) -> None:
    """Persist a version-scoped Analyzer checkpoint.

    The version dimension prevents a finished run for the same task_id from
    causing a later version run to be skipped.
    """
    state["version_id"] = version_id
    state.setdefault("analyzer", {})["version_id"] = version_id
    payload = json.dumps(_json_safe(state), ensure_ascii=False)
    updated_at = datetime.now(timezone.utc).isoformat()
    with _connect(checkpoint_path) as conn:
        conn.execute(
            """
            INSERT INTO analyzer_checkpoints_v2(thread_id, version_id, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(thread_id, version_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (thread_id, version_id, payload, updated_at),
        )
        conn.commit()
    update_analyzer_state_via_configer(state, task_id=thread_id)


def load_analyzer_checkpoint(
    thread_id: str,
    checkpoint_path: str,
    version_id: str = "default",
) -> Dict[str, Any]:
    if os.path.exists(checkpoint_path):
        with _connect(checkpoint_path) as conn:
            row = conn.execute(
                """
                SELECT state_json FROM analyzer_checkpoints_v2
                WHERE thread_id = ? AND version_id = ?
                LIMIT 1
                """,
                (thread_id, version_id),
            ).fetchone()
        if row is not None:
            return json.loads(row[0])
    return load_analyzer_state_from_configer(task_id=thread_id)


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


def _run_step(step_name: str, state: Dict[str, Any], writer: Optional[Callable] = None) -> Dict[str, Any]:
    from loopai.skills.Analyzer.utils.stream import (
        reset_analyzer_stream_writer,
        set_analyzer_stream_writer,
    )

    token = set_analyzer_stream_writer(writer)
    try:
        if step_name == "eval_model":
            from loopai.skills.Analyzer.nodes.eval_model import eval_model_node
            return eval_model_node(state)
        if step_name == "analyze_result":
            from loopai.skills.Analyzer.nodes.analyze_result import analyze_result_node
            return analyze_result_node(state)
        if step_name == "draw_conclusion":
            from loopai.skills.Analyzer.nodes.draw_conclusion import draw_conclusion_node
            return draw_conclusion_node(state)
        raise ValueError(f"Unknown executable Analyzer step: {step_name}")
    finally:
        reset_analyzer_stream_writer(token)


def run_analyzer_pipeline(
    state: Optional[Dict[str, Any]],
    thread_id: str = "analyzer-default",
    checkpoint_path: str = "outputs/analyzer_checkpoints.sqlite",
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    version_id: str = "default",
    writer: Optional[Callable] = None,
) -> Dict[str, Any]:
    if resume:
        state = load_analyzer_checkpoint(thread_id, checkpoint_path, version_id=version_id)
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
    progress_state = {"last_percent": -1}

    if writer:
        _emit_pipeline_progress(
            writer,
            0.0,
            "Analyzer pipeline started",
            data={
                "task_id": thread_id,
                "resume": resume,
                "analyze_task_type": (state.get("analyzer") or {}).get("analyze_task_type"),
            },
            progress_state=progress_state,
        )

    if resume and from_node is None and _is_finished(state):
        if writer:
            _emit_pipeline_progress(
                writer,
                1.0,
                "Analyzer pipeline already finished",
                progress_state=progress_state,
            )
        return state

    for step_name in ANALYZER_PIPELINE_STEPS[start_at:]:
        state["current"] = step_name
        save_analyzer_checkpoint(state, thread_id, checkpoint_path, version_id=version_id)

        if writer:
            writer(StreamEvent(
                current=f"analyzer.{step_name}",
                progress=_STEP_PROGRESS_RANGES.get(step_name, (0.0, 1.0))[0],
                progress_num=_progress_percent(_STEP_PROGRESS_RANGES.get(step_name, (0.0, 1.0))[0]),
                total=100,
                message=f"步骤开始: {step_name}",
                data={"step": step_name},
            ))
            _emit_pipeline_progress(
                writer,
                _STEP_PROGRESS_RANGES.get(step_name, (0.0, 1.0))[0],
                f"开始 {step_name}",
                data={"step": step_name},
                progress_state=progress_state,
            )

        if step_name == "finish":
            state["last_completed"] = "finish"
            save_analyzer_checkpoint(state, thread_id, checkpoint_path, version_id=version_id)
            if writer:
                writer(StreamEvent(
                    current="analyzer.finish",
                    progress=1.0,
                    progress_num=100,
                    total=100,
                    message="流水线完成",
                ))
                _emit_pipeline_progress(
                    writer,
                    1.0,
                    "Analyzer 完成",
                    data={"step": "finish"},
                    progress_state=progress_state,
                )
            return state

        state = _run_step(
            step_name,
            state,
            writer=_AnalyzerProgressWriter(writer, step_name, progress_state),
        )
        state["last_completed"] = step_name
        save_analyzer_checkpoint(state, thread_id, checkpoint_path, version_id=version_id)

        if writer:
            step_end = _STEP_PROGRESS_RANGES.get(step_name, (0.0, 1.0))[1]
            step_percent = _progress_percent(step_end)
            writer(StreamEvent(
                current=f"analyzer.{step_name}",
                progress=round(step_end, 4),
                progress_num=step_percent,
                total=100,
                message=f"步骤完成: {step_name}",
                data={"step": step_name, "progress_percent": step_percent},
            ))
            _emit_pipeline_progress(
                writer,
                step_end,
                f"完成 {step_name}",
                data={"step": step_name},
                progress_state=progress_state,
            )

    return state
