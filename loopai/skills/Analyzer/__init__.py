from __future__ import annotations

from typing import Any, Dict, Optional


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from loopai.skills.Analyzer.runner import run_analyzer_standalone

    return run_analyzer_standalone(
        state=state,
        thread_id=thread_id,
        resume=resume,
        from_node=from_node,
        baseline_result_path=baseline_result_path,
        **kwargs,
    )


__all__ = ["run"]
