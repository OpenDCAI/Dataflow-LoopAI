from __future__ import annotations

from typing import Any, Dict, Optional


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from loopai.skills.WebCrawler.runner import run_webcrawler_standalone

    return run_webcrawler_standalone(
        state=state,
        thread_id=thread_id,
        resume=resume,
        from_step=from_step,
        checkpoint_path=checkpoint_path,
        **kwargs,
    )


def load_events(task_id: str, output_dir: str = "./outputs"):
    from loopai.common.event_tool import dump_stream_events_json

    return dump_stream_events_json(
        name="webcrawler",
        context_id=task_id,
        log_file_path=output_dir,
    )


__all__ = ["run", "load_events"]

