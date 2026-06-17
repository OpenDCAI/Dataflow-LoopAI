from __future__ import annotations

from typing import Any, Dict, Optional

from .pipeline_runner import (
    ANALYZER_PIPELINE_STEPS,
    load_analyzer_checkpoint,
    normalize_analyzer_step,
    run_analyzer_pipeline,
    _resume_step_from_state,
)
from .runtime_config import resolve_analyzer_runtime_config

ANALYZER_NODE_NAMES = ANALYZER_PIPELINE_STEPS


def get_analyzer_checkpoint_state(
    thread_id: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    runtime = resolve_analyzer_runtime_config(
        {},
        thread_id=thread_id,
        checkpoint_path=checkpoint_path,
        require_api_key=False,
        **kwargs,
    )
    return load_analyzer_checkpoint(runtime["thread_id"], runtime["checkpoint_path"])


def run_analyzer_standalone(
    state: Optional[Dict[str, Any]],
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run Analyzer through the standalone function pipeline.

    Original AnalyzerAgent/LangGraph flow remains available for Starter.
    Runtime config is resolved in the skill layer so Codex/CLI can inject
    environment values before executing or resuming Analyzer steps.
    """
    runtime = resolve_analyzer_runtime_config(
        state,
        thread_id=thread_id,
        checkpoint_path=checkpoint_path,
        **kwargs,
    )

    start_node = normalize_analyzer_step(from_node) if from_node else None
    if resume:
        state = load_analyzer_checkpoint(runtime["thread_id"], runtime["checkpoint_path"])
        resolve_analyzer_runtime_config(
            state,
            thread_id=runtime["thread_id"],
            checkpoint_path=runtime["checkpoint_path"],
            **kwargs,
        )
        if start_node is None:
            start_node = _resume_step_from_state(state)

    return run_analyzer_pipeline(
        state=state,
        thread_id=runtime["thread_id"],
        checkpoint_path=runtime["checkpoint_path"],
        resume=False,
        from_node=start_node,
        baseline_result_path=baseline_result_path,
    )
