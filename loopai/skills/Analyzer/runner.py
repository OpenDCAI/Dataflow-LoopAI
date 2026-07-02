from __future__ import annotations

from typing import Any, Dict, Optional

from loopai.common.exception import ErrorCode, build_error_payload, build_success_payload

from .event_tool import StreamEvent, get_analyzer_event_writer
from .pipeline_runner import (
    ANALYZER_PIPELINE_STEPS,
    load_analyzer_checkpoint,
    normalize_analyzer_step,
    run_analyzer_pipeline,
    _resume_step_from_state,
)
from .runtime_config import resolve_analyzer_runtime_config
from .state_bridge import load_analyzer_state_from_configer

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
    return load_analyzer_checkpoint(
        runtime["thread_id"],
        runtime["checkpoint_path"],
        version_id=runtime["version_id"],
    )


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
        state = load_analyzer_checkpoint(
            runtime["thread_id"],
            runtime["checkpoint_path"],
            version_id=runtime["version_id"],
        )
        resolve_analyzer_runtime_config(
            state,
            thread_id=runtime["thread_id"],
            checkpoint_path=runtime["checkpoint_path"],
            version_id=runtime["version_id"],
            **kwargs,
        )
        if start_node is None:
            start_node = _resume_step_from_state(state)

    if state is None:
        state = load_analyzer_state_from_configer(task_id=runtime["thread_id"])

    output_dir = state.get("output_dir") or (state.get("analyzer") or {}).get("output_dir") or "./outputs"
    writer = get_analyzer_event_writer(
        context_id=runtime["thread_id"],
        log_file_path=output_dir,
        stdout=kwargs.get("stream_stdout"),
        state=state,
        version_id=runtime["version_id"],
    )
    try:
        result = run_analyzer_pipeline(
            state=state,
            thread_id=runtime["thread_id"],
            checkpoint_path=runtime["checkpoint_path"],
            resume=False,
            from_node=start_node,
            baseline_result_path=baseline_result_path,
            version_id=runtime["version_id"],
            writer=writer,
        )
    except Exception as exc:
        writer(StreamEvent(
            current="analyzer.failed",
            progress=1.0,
            message="Analyzer failed",
            status="failed",
            error={"type": type(exc).__name__, "detail": str(exc)},
        ))
        if hasattr(writer, "set_failed"):
            writer.set_failed()
        raise

    writer(StreamEvent(
        current="analyzer.completed",
        progress=1.0,
        message="Analyzer completed",
        status="completed",
        data={
            "task_id": result.get("task_id") if isinstance(result, dict) else runtime["thread_id"],
            "version_id": runtime["version_id"],
            "current": result.get("current") if isinstance(result, dict) else None,
        },
    ))
    if hasattr(writer, "set_completed"):
        writer.set_completed()
    return result


def run_analyzer_standalone_payload(
    state: Optional[Dict[str, Any]],
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run Analyzer and return the unified success/error payload."""
    try:
        final_state = run_analyzer_standalone(
            state=state,
            thread_id=thread_id,
            resume=resume,
            from_node=from_node,
            checkpoint_path=checkpoint_path,
            baseline_result_path=baseline_result_path,
            **kwargs,
        )
    except (ValueError, TypeError) as exc:
        return build_error_payload(
            exc,
            code=ErrorCode.INVALID_INPUT,
            recoverable=False,
            message="Analyzer input contract validation failed.",
        )
    except RuntimeError as exc:
        return build_error_payload(
            exc,
            code=ErrorCode.CONFIG_ERROR,
            recoverable=True,
            message="Analyzer runtime configuration is incomplete.",
        )
    except Exception as exc:
        return build_error_payload(
            exc,
            code=ErrorCode.UNHANDLED_EXCEPTION,
            recoverable=True,
            message="Analyzer crashed with an unhandled exception.",
        )

    analyzer_state = final_state.get("analyzer", {}) if isinstance(final_state, dict) else {}
    return build_success_payload(
        data={
            "result": {
                "task_id": final_state.get("task_id") if isinstance(final_state, dict) else thread_id,
                "version_id": final_state.get("version_id") if isinstance(final_state, dict) else None,
                "current": final_state.get("current") if isinstance(final_state, dict) else None,
                "last_completed": final_state.get("last_completed") if isinstance(final_state, dict) else None,
                "insights": analyzer_state.get("analysis_summary") or analyzer_state.get("historical_comparison") or {},
                "error_patterns": analyzer_state.get("historical_comparison", {}).get("error_distribution_diff", {})
                if isinstance(analyzer_state.get("historical_comparison"), dict)
                else {},
            },
            "state": final_state,
        },
        message="Analyzer completed.",
    )
