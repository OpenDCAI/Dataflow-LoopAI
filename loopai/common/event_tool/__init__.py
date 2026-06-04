from __future__ import annotations

import os
import pickle
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover
    fcntl = None


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
    run_id: Optional[str] = None

    def __init__(
        self,
        current: str,
        progress: Optional[float] = None,
        progress_num: Optional[int] = None,
        total: Optional[int] = None,
        message: Optional[str] = None,
        data: Optional[Any] = None,
        time: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        self.current = current
        self.progress = progress
        self.progress_num = progress_num
        self.total = total
        self.message = message
        self.data = data
        self.time = time
        self.run_id = run_id

    def json(self) -> dict[str, Any]:
        """
        Convert dataclass fields to JSON representation.
        """
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
    return event


class PickleEventWriter:
    def __init__(self, name: str, context_id: str, event_path: Path, run_id: str | None = None):
        self.name = name
        self.context_id = context_id
        self.event_path = event_path
        self.run_id = run_id

    def __call__(self, payload: StreamEvent | dict[str, Any]) -> StreamEvent:
        event = _coerce_stream_event(payload)
        if event.run_id is None:
            event.run_id = self.run_id
        self._append_event(event)
        return event

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
                    events = []

                if not isinstance(events, list):
                    events = []

                events.append(event)

                file_obj.seek(0)
                file_obj.truncate()
                pickle.dump(events, file_obj)
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
    run_id: str | None = None,
) -> PickleEventWriter:
    agent_name = _sanitize_path_component(name, "agent")
    context_value = _sanitize_path_component(context_id, "default")
    base_path = Path(log_file_path)
    event_path = base_path / context_value / agent_name / f"{agent_name}.pkl"
    return PickleEventWriter(
        name=agent_name,
        context_id=context_value,
        event_path=event_path,
        run_id=run_id,
    )


def load_stream_events(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
) -> list[StreamEvent]:
    agent_name = _sanitize_path_component(name, "agent")
    context_value = _sanitize_path_component(context_id, "default")
    event_path = Path(log_file_path) / context_value / agent_name / f"{agent_name}.pkl"
    if not event_path.exists():
        return []

    with event_path.open("rb") as file_obj:
        events = pickle.load(file_obj)

    if not isinstance(events, list):
        return []

    normalized_events: list[StreamEvent] = []
    for item in events:
        if isinstance(item, StreamEvent):
            normalized_events.append(item)
        elif isinstance(item, dict):
            normalized_events.append(StreamEvent(**item))
    return normalized_events


def dump_stream_events_json(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
) -> list[dict[str, Any]]:
    return [asdict(item) for item in load_stream_events(name, context_id, log_file_path)]


__all__ = [
    "PickleEventWriter",
    "StreamEvent",
    "dump_stream_events_json",
    "get_event_writer",
    "load_stream_events",
]
