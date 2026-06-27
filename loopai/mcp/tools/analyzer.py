from __future__ import annotations

from typing import Any, Optional

from ..base import mcp
from loopai.skills.Analyzer import load_events, run


@mcp.tool(
    name="analyzer_run",
    description="Run the Analyzer skill for one task using Configer-managed state and runtime config.",
)
def analyzer_run(
    task_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
) -> dict[str, Any]:
    return run(
        state=None,
        thread_id=task_id,
        resume=resume,
        from_node=from_node,
        baseline_result_path=baseline_result_path,
    )


@mcp.tool(
    name="analyzer_load_events",
    description="Load persisted Analyzer StreamEvent records for one task.",
)
def analyzer_load_events(task_id: str, output_dir: str = "./outputs") -> list[dict[str, Any]]:
    return load_events(task_id=task_id, output_dir=output_dir)
