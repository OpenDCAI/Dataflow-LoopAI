from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loopai.common.event_tool import load_stream_event_groups


STREAM_EVENT_AGENT_NAMES = (
    "looper",
    "judger",
    "obtainer",
    "constructor",
    "analyzer",
    "trainer",
    "webcrawler",
)


def parse_task_state(task_state: str | None) -> dict[str, Any] | None:
    if not task_state:
        return None
    try:
        state = json.loads(task_state)
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def resolve_output_dir(
    state: dict[str, Any] | None,
    default_output_dir: str | Path,
    project_root_dir: str | Path,
) -> str:
    default_output_path = Path(default_output_dir)
    project_root_path = Path(project_root_dir)
    if isinstance(state, dict):
        output_dir = state.get("output_dir")
        if isinstance(output_dir, str) and output_dir.strip():
            output_path = Path(output_dir).expanduser()
            if output_path.is_absolute():
                return str(output_path)
            return str((project_root_path / output_path).resolve())
    return str(default_output_path)


def serialize_stream_event(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump") and callable(event.model_dump):
        return event.model_dump()
    if hasattr(event, "dict") and callable(event.dict):
        return event.dict()
    if hasattr(event, "__dict__"):
        return dict(vars(event))
    if isinstance(event, dict):
        return dict(event)
    return {}


def _load_legacy_trainer_event_groups(
    task_id: str,
    state: dict[str, Any] | None,
    output_dir: str,
) -> dict[str, list[Any]]:
    trainer_state = state.get("trainer", {}) if isinstance(state, dict) else {}
    candidates: list[Path] = []
    event_log_path = trainer_state.get("trainer_event_log_path")
    if event_log_path:
        candidates.append(Path(str(event_log_path)).expanduser().parent)
    for field in ("trainer_output_dir", "output_dir"):
        value = trainer_state.get(field)
        if value:
            candidates.append(Path(str(value)).expanduser())

    visited: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in visited or not (candidate / "trainer.pkl").exists():
            continue
        visited.add(candidate_key)
        try:
            groups = load_stream_event_groups(
                "trainer",
                task_id,
                log_file_path=output_dir,
                event_dir=candidate,
            )
        except Exception:
            continue
        if groups:
            return groups
    return {}


def build_custom_info(
    task_id: str,
    state: dict[str, Any] | None,
    default_output_dir: str | Path,
    project_root_dir: str | Path,
) -> dict[str, dict[str, Any]]:
    custom_info: dict[str, dict[str, Any]] = {}
    output_dir = resolve_output_dir(state, default_output_dir, project_root_dir)

    for agent_name in STREAM_EVENT_AGENT_NAMES:
        try:
            event_groups = load_stream_event_groups(agent_name, task_id, log_file_path=output_dir)
        except Exception:
            continue
        if agent_name == "trainer" and not event_groups:
            event_groups = _load_legacy_trainer_event_groups(task_id, state, output_dir)

        for current_key, events in event_groups.items():
            if not events:
                continue
            payload = serialize_stream_event(events[-1])
            if payload.get("current"):
                custom_info[f"{agent_name}.{current_key}"] = payload

    return custom_info
