from __future__ import annotations

import os
from pathlib import Path
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
    version_id: str | None = None,
    trainer_output_dir: str | None = None,
) -> List[Dict[str, Any]]:
    """Load persisted Trainer StreamEvent entries for a task."""
    from loopai.common.event_tool import dump_stream_events_json

    event_dir = trainer_output_dir
    if event_dir is None and version_id:
        event_dir = str(Path(output_dir) / task_id / "trainer" / version_id)
    if event_dir is None:
        event_dir = _find_latest_trainer_event_dir(task_id=task_id, output_dir=output_dir)
    if event_dir is not None:
        return dump_stream_events_json(
            name="trainer",
            context_id=task_id,
            log_file_path=output_dir,
            event_dir=event_dir,
        )
    return dump_stream_events_json(
        name="trainer",
        context_id=task_id,
        log_file_path=output_dir,
    )


def analyze_results(
    task_id: str | None = None,
    output_dir: str = "./outputs",
    trainer_task_id: str | None = None,
    training_output_dir: str | None = None,
    trainer_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyze Trainer artifacts and choose the best checkpoint."""
    from loopai.skills.Trainer.results import analyze_results as _analyze_results

    return _analyze_results(
        task_id=task_id,
        output_dir=output_dir,
        trainer_task_id=trainer_task_id,
        training_output_dir=training_output_dir,
        trainer_state=trainer_state,
    )


def select_best_checkpoint(
    checkpoints: List[Dict[str, Any]],
    metric_records: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Select the best checkpoint from parsed checkpoint and metric records."""
    from loopai.skills.Trainer.results import select_best_checkpoint as _select_best_checkpoint

    return _select_best_checkpoint(checkpoints, metric_records)


def prefill_guide(
    state: Optional[Dict[str, Any]] = None,
    task_type: str = "sft",
) -> Dict[str, Any]:
    """Build Trainer config prefill guidance for Codex or UI."""
    from loopai.skills.Trainer.runtime_config import build_trainer_prefill_guide

    return build_trainer_prefill_guide(state, task_type=task_type)


def _find_latest_trainer_event_dir(task_id: str, output_dir: str) -> str | None:
    version_id = None
    db_path = os.getenv("DB_PATH")
    if db_path:
        try:
            from loopai.common.db_tool.runtime import get_latest_task_runtime_sync

            runtime = get_latest_task_runtime_sync(db_path, task_id, "trainer")
            version_id = runtime.get("version") if runtime else None
        except Exception:
            version_id = None

    if version_id:
        candidate = Path(output_dir) / task_id / "trainer" / str(version_id)
        if (candidate / "trainer.pkl").exists():
            return str(candidate)

    trainer_root = Path(output_dir) / task_id / "trainer"
    if not trainer_root.is_dir():
        return None

    candidates = [
        child
        for child in trainer_root.iterdir()
        if child.is_dir() and (child / "trainer.pkl").exists()
    ]
    if not candidates:
        return None
    return str(max(candidates, key=lambda item: item.stat().st_mtime))


__all__ = [
    "run",
    "load_events",
    "analyze_results",
    "select_best_checkpoint",
    "prefill_guide",
]
