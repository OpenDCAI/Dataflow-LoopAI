from __future__ import annotations

import pickle
import uuid
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


EventWriter = Callable[[Any], Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _LocalStreamEvent:
    current: str
    progress: float | None = None
    progress_num: int | None = None
    total: int | None = None
    message: str | None = None
    data: Any = None
    time: str | None = None
    version_id: str | None = None
    node: str | None = None
    status: str | None = None
    context_id: str | None = None
    error: Any = None

    def json(self) -> dict[str, Any]:
        if self.time is None:
            self.time = _utc_now_iso()
        return {field.name: getattr(self, field.name) for field in fields(self)}


class _LocalPickleEventWriter:
    def __init__(self, *, name: str, context_id: str, output_dir: str, version_id: str | None = None) -> None:
        self.name = name
        self.context_id = context_id
        self.version_id = version_id or str(uuid.uuid4())
        self.event_path = Path(output_dir) / context_id / name / self.version_id / f"{name}.pkl"

    def __call__(self, payload: Any) -> Any:
        event = payload if isinstance(payload, _LocalStreamEvent) else _LocalStreamEvent(**payload)
        event.current = event.current or self.name
        event.node = event.node or self.name
        event.context_id = event.context_id or self.context_id
        event.version_id = self.version_id
        event.status = event.status or "running"
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.event_path.open("rb") as handle:
                groups = pickle.load(handle)
        except (FileNotFoundError, EOFError):
            groups = {}
        groups.setdefault(event.current, []).append(event)
        with self.event_path.open("wb") as handle:
            pickle.dump(groups, handle)
        return event


def _load_local_events(task_id: str, output_dir: str, version_id: str | None = None) -> list[dict[str, Any]]:
    base = Path(output_dir) / task_id / "obtainercli"
    paths = [base / version_id / "obtainercli.pkl"] if version_id else sorted(base.glob("*/obtainercli.pkl"))
    events: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            with path.open("rb") as handle:
                groups = pickle.load(handle)
        except Exception:
            continue
        for items in groups.values():
            for item in items:
                if hasattr(item, "json"):
                    events.append(item.json())
                elif isinstance(item, dict):
                    events.append(item)
    return events


def get_obtainer_event_writer(
    *,
    task_id: str | None = None,
    output_dir: str = "./outputs",
    version_id: str | None = None,
    enabled: bool = True,
) -> EventWriter | None:
    if not enabled or not task_id:
        return None
    try:
        from loopai.common.event_tool import get_event_writer

        return get_event_writer(
            name="obtainercli",
            context_id=task_id,
            log_file_path=output_dir,
            version_id=version_id,
        )
    except Exception:
        return _LocalPickleEventWriter(
            name="obtainercli",
            context_id=task_id,
            output_dir=output_dir,
            version_id=version_id,
        )


def emit_obtainer_event(
    writer: EventWriter | None,
    *,
    node: str,
    status: str,
    message: str,
    progress: float | None = None,
    data: Any = None,
    error: Any = None,
) -> None:
    if writer is None:
        return
    try:
        from loopai.common.event_tool import StreamEvent
    except Exception:
        StreamEvent = _LocalStreamEvent
    try:
        event = StreamEvent(
            current="obtainercli",
            node=node,
            status=status,
            progress=progress,
            message=message,
            data=data,
            error=error,
        )
        normalized_status = str(status or "").lower()
        method_name = (
            "set_completed" if normalized_status in {"completed", "succeeded", "success"}
            else "set_failed" if normalized_status in {"failed", "error", "interrupted"}
            else "set_running"
        )
        method = getattr(writer, method_name, None)
        method(event) if callable(method) else writer(event)
    except Exception:
        return


def load_events(task_id: str, output_dir: str = "./outputs", version_id: str | None = None) -> list[dict[str, Any]]:
    try:
        from loopai.common.event_tool import dump_stream_events_json

        return dump_stream_events_json(
            name="obtainercli",
            context_id=task_id,
            log_file_path=output_dir,
            version_id=version_id,
        )
    except Exception:
        return _load_local_events(task_id, output_dir, version_id=version_id)
