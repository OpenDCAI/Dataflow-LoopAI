from __future__ import annotations

import json
import os
import pickle
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import logging

from loopai.common.db_tool.runtime import sync_task_runtime_sync
from loopai.common.db_tool.base import require_db_path
from loopai.common.db_tool.task import get_task_states_config_sync


try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover
    fcntl = None


logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_path_component(value: str, fallback: str) -> str:
    cleaned = (value or "").strip().replace("\\", "_").replace("/", "_")
    return cleaned or fallback


def _normalize_value(value: Any) -> Any:
    if callable(value):
        value = value()

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(item) for item in value]

    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _normalize_value(value.model_dump())

    if hasattr(value, "dict") and callable(value.dict):
        return _normalize_value(value.dict())

    if hasattr(value, "__dict__"):
        return _normalize_value(vars(value))

    return str(value)


@dataclass
class StreamEvent:
    """
    Stream event.
    """

    current: str
    progress: Optional[float] = None
    progress_num: Optional[int] = None
    total: Optional[int] = None
    message: Optional[str] = None
    data: Optional[Any] = None
    time: Optional[str] = None
    version_id: Optional[str] = None
    node: Optional[str] = None
    status: Optional[str] = None
    # i.e. task_id
    context_id: Optional[str] = None
    error: Optional[Any] = None

    def __init__(
        self,
        current: str,
        progress: Optional[float] = None,
        progress_num: Optional[int] = None,
        total: Optional[int] = None,
        message: Optional[str] = None,
        data: Optional[Any] = None,
        time: Optional[str] = None,
        version_id: Optional[str] = None,
        node: Optional[str] = None,
        status: Optional[str] = None,
        context_id: Optional[str] = None,
        error: Optional[Any] = None,
    ):
        self.current = current
        self.progress = progress
        self.progress_num = progress_num
        self.total = total
        self.message = message
        self.data = data
        self.time = time
        self.version_id = version_id
        self.node = node
        self.status = status
        self.context_id = context_id
        self.error = error

    def json(self) -> dict[str, Any]:
        """
        Convert dataclass fields to JSON representation.
        """
        if self.time is None:
            self.time = _utc_now_iso()
        self.data = _normalize_value(self.data)
        self.error = _normalize_value(self.error)
        results = {}
        for f in fields(self):
            results[f.name] = getattr(self, f.name)
        return results


def _coerce_stream_event(payload: StreamEvent | dict[str, Any]) -> StreamEvent:
    if isinstance(payload, StreamEvent):
        event = payload
    elif isinstance(payload, dict):
        event = StreamEvent(**payload)
    else:
        raise TypeError("writer(...) only accepts StreamEvent or dict payload")

    if event.time is None:
        event.time = _utc_now_iso()
    event.data = _normalize_value(event.data)
    event.error = _normalize_value(event.error)
    return event


def _normalize_stream_event_item(item: Any) -> StreamEvent | None:
    if isinstance(item, StreamEvent):
        return item
    if isinstance(item, dict):
        try:
            return StreamEvent(**item)
        except TypeError:
            return None
    return None


def _normalize_event_groups(payload: Any) -> dict[str, list[StreamEvent]]:
    if isinstance(payload, dict):
        normalized_groups: dict[str, list[StreamEvent]] = {}
        for current_key, items in payload.items():
            if not isinstance(items, list):
                continue
            group_key = str(current_key)
            normalized_items: list[StreamEvent] = []
            for item in items:
                event = _normalize_stream_event_item(item)
                if event is not None:
                    normalized_items.append(event)
            if normalized_items:
                normalized_groups[group_key] = normalized_items
        return normalized_groups

    if isinstance(payload, list):
        normalized_groups: dict[str, list[StreamEvent]] = {}
        for item in payload:
            event = _normalize_stream_event_item(item)
            if event is None:
                continue
            normalized_groups.setdefault(event.current, []).append(event)
        return normalized_groups

    return {}


class PickleEventWriter:
    def __init__(self, name: str, context_id: str, event_path: Path, version_id: str | None = None):
        self.name = name
        self.context_id = context_id
        self.event_path = event_path
        self.version_id = version_id or str(uuid.uuid4())

    def __call__(self, payload: StreamEvent | dict[str, Any]) -> StreamEvent:
        event = self.set_running(payload)
        return event

    def set_event(self, payload: StreamEvent | dict[str, Any], status: str = 'running') -> StreamEvent:
        if self.version_id is None:
            self.version_id = self.new_version_id()
        payload = payload or {}
        if isinstance(payload, dict) and "current" not in payload:
            event = StreamEvent(
                current=self.name,
                progress=payload.get("progress", 1.0 if status in {"failed", "completed"} else None),
                progress_num=payload.get("progress_num"),
                total=payload.get("total"),
                message=payload.get("message"),
                data=payload.get("data", payload if status == "completed" else None),
                error=payload.get("error", payload if status == "failed" else None),
            )
            event = _coerce_stream_event(event)
        else:
            event = _coerce_stream_event(payload)
        if event.version_id and event.version_id != self.version_id:
            logger.warning(
                "Ignoring mismatched event version_id for %s/%s: event=%s writer=%s",
                self.context_id,
                self.name,
                event.version_id,
                self.version_id,
            )
        if event.message is None and status == "failed":
            event.message = "Sub-agent failed."
        elif event.message is None and status == "completed":
            event.message = "Sub-agent completed."
        event.status = status
        event.version_id = self.version_id
        event.node = self.name
        event.context_id = self.context_id
        self._sync_runtime(status=event.status)
        self._append_event(event)
        return event

    def set_running(self, payload: dict[str, Any] | None = None):
        return self.set_event(payload, status="running")

    def set_failed(self, payload: dict[str, Any] | None = None):
        return self.set_event(payload, status="failed")

    def set_completed(self, payload: dict[str, Any] | None = None):
        return self.set_event(payload, status="completed")

    def refresh_version_id(self):
        self.version_id = self.new_version_id()
        return self.version_id

    def new_version_id(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    def _sync_runtime(self, *, status: str) -> None:
        try:
            db_path = require_db_path()
            task_state = self._load_task_state(db_path)
            sync_task_runtime_sync(
                db_path=db_path,
                task_id=self.context_id,
                node_name=self.name,
                version=self.version_id,
                status=status,
                state=task_state,
            )
        except Exception as exc:  # pragma: no cover - runtime sync is best effort
            logger.warning("Failed to sync task runtime for %s/%s: %s",
                           self.context_id, self.name, exc)

    def _load_task_state(self, db_path: str) -> str | None:
        task_state = get_task_states_config_sync(db_path, self.context_id)
        return json.dumps(task_state.get("config", {}), ensure_ascii=False)

    def _append_event(self, event: StreamEvent) -> None:
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_path.open("a+b") as file_obj:
            if fcntl is not None:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX)
            try:
                file_obj.seek(0)
                try:
                    events = pickle.load(file_obj)
                except EOFError:
                    events = {}

                event_groups = _normalize_event_groups(events)
                event_groups.setdefault(event.current, []).append(event)

                file_obj.seek(0)
                file_obj.truncate()
                pickle.dump(event_groups, file_obj)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)


def get_event_writer(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
    *,
    version_id: str | None = None,
    event_dir: str | os.PathLike[str] | None = None,
) -> PickleEventWriter:
    agent_name = _sanitize_path_component(name, "agent")
    context_value = _sanitize_path_component(context_id, "default")
    version_value = _sanitize_path_component(version_id or str(uuid.uuid4()), "version")
    base_path = Path(log_file_path)
    if event_dir is not None:
        event_path = Path(event_dir) / f"{agent_name}.pkl"
    else:
        event_path = base_path / context_value / agent_name / version_value / f"{agent_name}.pkl"
    return PickleEventWriter(
        name=agent_name,
        context_id=context_value,
        event_path=event_path,
        version_id=version_value,
    )


def load_stream_events(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
    *,
    version_id: str | None = None,
    event_dir: str | os.PathLike[str] | None = None,
) -> list[StreamEvent]:
    grouped_events = load_stream_event_groups(
        name,
        context_id,
        log_file_path,
        version_id=version_id,
        event_dir=event_dir,
    )
    flattened_events: list[StreamEvent] = []
    for events in grouped_events.values():
        flattened_events.extend(events)
    flattened_events.sort(key=lambda item: item.time or "")
    return flattened_events


def load_stream_event_groups(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
    *,
    version_id: str | None = None,
    event_dir: str | os.PathLike[str] | None = None,
) -> dict[str, list[StreamEvent]]:
    agent_name = _sanitize_path_component(name, "agent")
    context_value = _sanitize_path_component(context_id, "default")
    base_path = Path(log_file_path) / context_value
    event_paths: list[Path] = []
    if event_dir is not None:
        event_paths.append(Path(event_dir) / f"{agent_name}.pkl")
    elif version_id:
        version_value = _sanitize_path_component(version_id, "version")
        event_paths.append(base_path / agent_name / version_value / f"{agent_name}.pkl")
    else:
        event_paths.extend(sorted((base_path / agent_name).glob(f"*/{agent_name}.pkl")))
        event_paths.append(base_path / f"{agent_name}.pkl")

    merged: dict[str, list[StreamEvent]] = {}
    for event_path in event_paths:
        if not event_path.exists():
            continue
        with event_path.open("rb") as file_obj:
            events = pickle.load(file_obj)
        for current_key, group_events in _normalize_event_groups(events).items():
            merged.setdefault(current_key, []).extend(group_events)

    return merged


def dump_stream_events_json(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
    *,
    version_id: str | None = None,
    event_dir: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    return [
        item.json()
        for item in load_stream_events(
            name,
            context_id,
            log_file_path,
            version_id=version_id,
            event_dir=event_dir,
        )
    ]


__all__ = [
    "PickleEventWriter",
    "StreamEvent",
    "load_stream_event_groups",
    "dump_stream_events_json",
    "get_event_writer",
    "load_stream_events",
]
