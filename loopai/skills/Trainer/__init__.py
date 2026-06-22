from __future__ import annotations

from typing import Any, Dict, List, Optional


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    config_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run TrainerAgent through the skill entry point."""
    from loopai.skills.Trainer.runner import run_trainer_standalone

    return run_trainer_standalone(
        state=state,
        thread_id=thread_id,
        config_path=config_path,
        **kwargs,
    )


def load_events(
    task_id: str,
    output_dir: str = "./outputs",
) -> List[Dict[str, Any]]:
    """Load persisted Trainer StreamEvent entries for a task."""
    from loopai.common.event_tool import dump_stream_events_json

    return dump_stream_events_json(
        name="trainer",
        context_id=task_id,
        log_file_path=output_dir,
    )


__all__ = ["run", "load_events"]
