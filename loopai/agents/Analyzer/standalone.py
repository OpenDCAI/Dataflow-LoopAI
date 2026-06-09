from __future__ import annotations

from typing import Any, Dict, Optional

from .pipeline_runner import (
    ANALYZER_PIPELINE_STEPS,
    load_analyzer_checkpoint,
    normalize_analyzer_step,
    run_analyzer_pipeline,
)


ANALYZER_NODE_NAMES = ANALYZER_PIPELINE_STEPS


def get_analyzer_checkpoint_state(
    thread_id: str = "analyzer-default",
    checkpoint_path: str = "outputs/analyzer_checkpoints.sqlite",
    **_: Any,
) -> Dict[str, Any]:
    return load_analyzer_checkpoint(thread_id, checkpoint_path)


def run_analyzer_standalone(
    state: Optional[Dict[str, Any]],
    thread_id: str = "analyzer-default",
    resume: bool = False,
    from_node: Optional[str] = None,
    checkpoint_path: str = "outputs/analyzer_checkpoints.sqlite",
    baseline_result_path: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    """Run Analyzer through the standalone function pipeline.

    AnalyzerAgent/LangGraph remains available for the original Starter flow.
    This compatibility entry is intentionally function-level so Codex can
    resume at an Analyzer step without re-entering earlier LangGraph nodes.
    """

    return run_analyzer_pipeline(
        state=state,
        thread_id=thread_id,
        checkpoint_path=checkpoint_path,
        resume=resume,
        from_node=normalize_analyzer_step(from_node) if from_node else None,
        baseline_result_path=baseline_result_path,
    )
