from __future__ import annotations

from typing import Any

from ..base import mcp
from loopai.skills.Trainer import load_events, run


@mcp.tool(
    name="trainer_run",
    description=(
        "Run LoopAI Trainer skill for a task. "
        "Use this after reading trainer config with configer_get_task or configer_get. "
        "When task_id is provided, Trainer loads that task's trainer state through Configer, "
        "runs the existing TrainerAgent pipeline, emits Trainer stream events, and writes "
        "structured trainer_result/trainer_last_error and training status back to task state."
    ),
)
def trainer_run(
    task_id: str | None = None,
    config_path: str | None = None,
    output_dir: str | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return run(
        state=state,
        thread_id=task_id,
        config_path=config_path,
        output_dir=output_dir,
        emit_result=False,
    )


@mcp.tool(
    name="trainer_load_events",
    description=(
        "Load persisted LoopAI Trainer StreamEvent entries for one task. "
        "Use this to inspect Trainer progress, status, messages, event data, and errors."
    ),
)
def trainer_load_events(task_id: str, output_dir: str = "./outputs") -> list[dict[str, Any]]:
    return load_events(task_id=task_id, output_dir=output_dir)
