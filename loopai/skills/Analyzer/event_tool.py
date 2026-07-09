from __future__ import annotations

import json
import pickle
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from loopai.schema.events import StreamEvent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_sensitive_key(key: Any) -> bool:
    key_name = str(key).lower()
    return (
        key_name == "api_key"
        or key_name.endswith("_api_key")
        or key_name == "token"
        or key_name.endswith("_token")
        or key_name.endswith("_key")
    )


def _normalize_value(value: Any) -> Any:
    if callable(value):
        value = value()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): "***REDACTED***" if _is_sensitive_key(key) else _normalize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _normalize_value(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return _normalize_value(value.dict())
    if is_dataclass(value):
        return _normalize_value(asdict(value))
    if hasattr(value, "__dict__"):
        return _normalize_value(vars(value))
    return str(value)


def _to_event_dict(payload: StreamEvent | dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(payload, StreamEvent):
        raw = payload.json()
    elif isinstance(payload, dict):
        raw = dict(payload)
    elif hasattr(payload, "json") and callable(payload.json):
        raw = payload.json()
    else:
        raise TypeError("Analyzer event writer accepts StreamEvent-like payloads only")
    return _normalize_value(raw)


def append_stream_message(state: dict[str, Any], event: StreamEvent | dict[str, Any] | Any) -> dict[str, Any]:
    state.setdefault("messages", [])
    state["messages"].append(_to_event_dict(event))
    return state


class AnalyzerEventRecord:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = dict(payload)
        self.current = str(self._payload.get("current") or "analyzer.event")

    def json(self) -> dict[str, Any]:
        return _normalize_value(self._payload)


def _safe_path_component(value: Any, fallback: str) -> str:
    cleaned = str(value or "").strip().replace("\\", "_").replace("/", "_")
    return cleaned or fallback


def get_analyzer_run_output_dir(state: dict[str, Any]) -> Path:
    analyzer = state.get("analyzer") if isinstance(state, dict) else {}
    if not isinstance(analyzer, dict):
        analyzer = {}
    configured = analyzer.get("runtime_output_dir")
    if configured:
        return Path(configured)

    base_outdir = Path(analyzer.get("output_dir") or state.get("output_dir") or "./outputs")
    task_id = _safe_path_component(state.get("task_id"), "default_task")
    version_id = _safe_path_component(
        analyzer.get("version_id") or state.get("version_id"),
        "default",
    )
    return base_outdir / task_id / "analyzer" / version_id


class AnalyzerEventWriter:
    """Analyzer-local adapter that keeps common StreamEvent semantics.

    ``emit_success`` / ``emit_error`` pass standard payloads without a
    ``current`` field. This adapter turns those status payloads into Analyzer
    StreamEvents so the runtime status update remains effective.
    """

    def __init__(
        self,
        base_writer: Any,
        *,
        state: Optional[dict[str, Any]] = None,
        stdout: bool = False,
    ) -> None:
        self._base_writer = base_writer
        self._state = state
        self._stdout = stdout

    @property
    def version_id(self) -> Optional[str]:
        return self._base_writer.version_id

    def __call__(self, payload: StreamEvent | dict[str, Any] | Any) -> StreamEvent:
        event_dict = _to_event_dict(payload)
        event = self._base_writer(event_dict)
        self._record(event)
        return event

    def set_running(self, payload: dict[str, Any] | StreamEvent | None = None) -> StreamEvent:
        event = self._coerce_status_payload(
            payload,
            current="analyzer.running",
            message="Analyzer running.",
        )
        result = self._base_writer.set_running(event.json())
        self._record(result)
        return result

    def set_completed(self, payload: dict[str, Any] | StreamEvent | None = None) -> StreamEvent:
        event = self._coerce_status_payload(
            payload,
            current="analyzer.completed",
            message="Analyzer pipeline completed.",
        )
        result = self._base_writer.set_completed(event.json())
        self._record(result)
        return result

    def set_failed(self, payload: dict[str, Any] | StreamEvent | None = None) -> StreamEvent:
        event = self._coerce_status_payload(
            payload,
            current="analyzer.failed",
            message="Analyzer pipeline failed.",
        )
        result = self._base_writer.set_failed(event.json())
        self._record(result)
        return result

    def _coerce_status_payload(
        self,
        payload: dict[str, Any] | StreamEvent | None,
        *,
        current: str,
        message: str,
    ) -> Any:
        if isinstance(payload, StreamEvent):
            return payload
        payload_dict = payload if isinstance(payload, dict) else {}
        return _dict_to_stream_event({
            "current": current,
            "progress": 1.0,
            "message": str(payload_dict.get("message") or message),
            "data": payload_dict.get("data"),
            "error": payload_dict.get("error"),
        })

    def _record(self, event: StreamEvent) -> None:
        event_payload = event.json()
        if self._state is not None:
            self._state.setdefault("messages", [])
            self._state["messages"].append(event_payload)
        if self._stdout:
            print(json.dumps(event_payload, ensure_ascii=False), flush=True)


def get_analyzer_event_writer(
    *,
    context_id: str,
    log_file_path: str = "./outputs",
    stdout: bool = False,
    state: Optional[dict[str, Any]] = None,
    version_id: Optional[str] = None,
) -> Callable[[StreamEvent | dict[str, Any] | Any], StreamEvent]:
    version = _safe_path_component(version_id, "default")
    context = _safe_path_component(context_id, "default")
    event_path = Path(log_file_path) / context / "analyzer" / version / "analyzer.pkl"
    try:
        from loopai.common.event_tool import PickleEventWriter

        base_writer = PickleEventWriter(
            name="analyzer",
            context_id=context,
            event_path=event_path,
            version_id=version,
        )
    except Exception:
        base_writer = LocalAnalyzerPickleEventWriter(
            name="analyzer",
            context_id=context,
            event_path=event_path,
            version_id=version,
        )
    return AnalyzerEventWriter(base_writer, state=state, stdout=stdout)


class LocalAnalyzerPickleEventWriter:
    """Fallback writer used only when common runtime dependencies are absent."""

    def __init__(self, name: str, context_id: str, event_path: Path, version_id: str) -> None:
        self.name = name
        self.context_id = context_id
        self.event_path = event_path
        self.version_id = version_id

    def __call__(self, payload: StreamEvent | dict[str, Any]) -> StreamEvent:
        return self.set_running(payload)

    def set_running(self, payload: StreamEvent | dict[str, Any] | None = None) -> StreamEvent:
        return self._write(payload, "running")

    def set_completed(self, payload: StreamEvent | dict[str, Any] | None = None) -> StreamEvent:
        return self._write(payload, "completed")

    def set_failed(self, payload: StreamEvent | dict[str, Any] | None = None) -> StreamEvent:
        return self._write(payload, "failed")

    def _write(self, payload: StreamEvent | dict[str, Any] | None, status: str) -> StreamEvent:
        event_dict = _to_event_dict(payload or {
            "current": f"analyzer.{status}",
            "message": f"Analyzer {status}.",
        })
        event_dict.setdefault("time", _utc_now_iso())
        event_dict["status"] = status
        event_dict["version_id"] = self.version_id
        event_dict["node"] = self.name
        event_dict["context_id"] = self.context_id
        event = _dict_to_stream_event(event_dict)
        self._append_event(event)
        return event

    def _append_event(self, event: StreamEvent) -> None:
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        if self.event_path.exists():
            with self.event_path.open("rb") as file_obj:
                try:
                    events = pickle.load(file_obj)
                except EOFError:
                    events = {}
        else:
            events = {}
        if not isinstance(events, dict):
            events = {}
        events.setdefault(event.current, []).append(event)
        with self.event_path.open("wb") as file_obj:
            pickle.dump(events, file_obj)


def _dict_to_stream_event(event_dict: dict[str, Any]) -> Any:
    return AnalyzerEventRecord(event_dict)

def load_analyzer_events(
    *,
    task_id: str,
    output_dir: str = "./outputs",
    version_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    if version_id:
        event_path = (
            Path(output_dir)
            / _safe_path_component(task_id, "default")
            / "analyzer"
            / _safe_path_component(version_id, "default")
            / "analyzer.pkl"
        )
        if not event_path.exists():
            return []
        with event_path.open("rb") as file_obj:
            events = pickle.load(file_obj)
        rows: list[dict[str, Any]] = []
        if isinstance(events, dict):
            for group in events.values():
                for event in group or []:
                    rows.append(_to_event_dict(event))
        return rows

    rows: list[dict[str, Any]] = []
    base_dir = Path(output_dir) / _safe_path_component(task_id, "default") / "analyzer"
    for event_path in sorted(base_dir.glob("*/analyzer.pkl")):
        with event_path.open("rb") as file_obj:
            events = pickle.load(file_obj)
        if isinstance(events, dict):
            for group in events.values():
                for event in group or []:
                    rows.append(_to_event_dict(event))
    return rows


__all__ = [
    "StreamEvent",
    "append_stream_message",
    "get_analyzer_run_output_dir",
    "get_analyzer_event_writer",
    "load_analyzer_events",
]
