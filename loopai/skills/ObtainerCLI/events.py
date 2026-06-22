from __future__ import annotations

from typing import Any, Callable


EventWriter = Callable[[Any], Any]


def get_obtainer_event_writer(
    *,
    task_id: str | None = None,
    output_dir: str = "./outputs",
    enabled: bool = True,
) -> EventWriter | None:
    if not enabled or not task_id:
        return None
    from loopai.common.event_tool import get_event_writer

    return get_event_writer(
        name="obtainercli",
        context_id=task_id,
        log_file_path=output_dir,
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

        writer(
            StreamEvent(
                current="obtainercli",
                node=node,
                status=status,
                progress=progress,
                message=message,
                data=data,
                error=error,
            )
        )
    except Exception:
        return


def load_events(task_id: str, output_dir: str = "./outputs") -> list[dict[str, Any]]:
    from loopai.common.event_tool import dump_stream_events_json

    return dump_stream_events_json(
        name="obtainercli",
        context_id=task_id,
        log_file_path=output_dir,
    )
