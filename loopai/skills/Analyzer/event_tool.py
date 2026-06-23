from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from loopai.common.event_tool import StreamEvent, get_event_writer


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


def get_analyzer_event_writer(
    *,
    context_id: str,
    log_file_path: str = "./outputs",
    stdout: bool = False,
    state: Optional[dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Callable[[StreamEvent | dict[str, Any] | Any], StreamEvent]:
    base_writer = get_event_writer(
        name="analyzer",
        context_id=context_id,
        log_file_path=log_file_path,
        run_id=run_id,
    )

    def write(payload: StreamEvent | dict[str, Any] | Any) -> StreamEvent:
        event_dict = _to_event_dict(payload)
        event = base_writer(event_dict)
        event_payload = event.json()
        if state is not None:
            state.setdefault("messages", [])
            state["messages"].append(event_payload)
        if stdout:
            print(json.dumps(event_payload, ensure_ascii=False), flush=True)
        return event

    return write


__all__ = [
    "StreamEvent",
    "append_stream_message",
    "get_analyzer_event_writer",
]
