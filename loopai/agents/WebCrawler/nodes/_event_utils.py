from __future__ import annotations

from typing import Any, Callable, Dict

from langgraph.config import get_stream_writer

from loopai.common.event_tool import StreamEvent as PersistentStreamEvent
from loopai.common.event_tool import get_event_writer
from loopai.logger import get_logger
from loopai.schema.events import StreamEvent as GraphStreamEvent

logger = get_logger()


def _normalize_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, GraphStreamEvent):
        event = payload.json()
    elif isinstance(payload, dict):
        event = dict(payload)
    else:
        raise TypeError("WebCrawler event payload must be dict or StreamEvent")
    event.setdefault("current", "webcrawler")
    return event


def get_webcrawler_event_writer(state: Dict[str, Any]) -> Callable[[Any], None]:
    """Return a writer that mirrors LangGraph custom events and persistent stream events."""
    task_id = str(state.get("task_id") or "").strip()
    output_dir = str(state.get("output_dir") or "./outputs")
    persistent_writer = None

    if task_id:
        try:
            persistent_writer = get_event_writer(
                name="webcrawler",
                context_id=task_id,
                log_file_path=output_dir,
            )
        except Exception as exc:  # pragma: no cover - best-effort fallback
            logger.warning("Failed to initialize persistent WebCrawler event writer: %s", exc)

    def _writer(payload: Any) -> None:
        event = _normalize_payload(payload)

        # 1) Keep original LangGraph custom stream behavior.
        try:
            graph_writer = get_stream_writer()
            graph_writer(event)
        except Exception:
            # Standalone mode may not have LangGraph runtime context.
            pass

        # 2) Persist event for standalone/CLI realtime polling.
        if persistent_writer is not None:
            try:
                persistent_writer(
                    PersistentStreamEvent(
                        current=str(event.get("current") or "webcrawler"),
                        progress=event.get("progress"),
                        progress_num=event.get("progress_num"),
                        total=event.get("total"),
                        message=event.get("message"),
                        data=event.get("data"),
                    )
                )
            except Exception as exc:  # pragma: no cover - best-effort fallback
                logger.warning("Failed to persist WebCrawler stream event: %s", exc)

    return _writer

