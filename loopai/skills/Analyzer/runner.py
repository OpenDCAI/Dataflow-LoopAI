from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from loopai.common.exception import (
    ErrorCode,
    build_error_payload,
    build_success_payload,
    emit_error,
    emit_success,
)
from loopai.common.event_tool import StreamEvent, get_event_writer

from .pipeline_runner import (
    ANALYZER_PIPELINE_STEPS,
    load_analyzer_checkpoint,
    normalize_analyzer_step,
    run_analyzer_pipeline,
    _resume_step_from_state,
    _is_finished,
)
from .runtime_config import (
    cleanup_old_analyzer_checkpoints,
    find_latest_version_checkpoint,
    get_version_checkpoint_path,
    resolve_analyzer_runtime_config,
)
from .state_bridge import load_analyzer_state_from_configer

ANALYZER_NODE_NAMES = ANALYZER_PIPELINE_STEPS


def _latest_runtime_version(task_id: str, node_name: str = "analyzer") -> Optional[str]:
    try:
        import os
        from loopai.common.db_tool.runtime import get_latest_task_runtime_sync

        db_path = os.getenv("DB_PATH")
        if not db_path or not task_id:
            return None
        runtime = get_latest_task_runtime_sync(db_path, task_id, node_name)
        if runtime and runtime.get("version"):
            return str(runtime["version"])
    except Exception:
        return None
    return None


def find_latest_incomplete_version_checkpoint(
    output_dir: str,
    task_id: str,
) -> Optional[tuple[str, str]]:
    """Return the newest unfinished version checkpoint for a task."""
    analyzer_dir = Path(output_dir) / str(task_id) / "analyzer"
    candidates = []
    for version_dir in analyzer_dir.iterdir() if analyzer_dir.is_dir() else []:
        checkpoint = version_dir / "state_checkpoint.sqlite"
        if version_dir.is_dir() and checkpoint.exists():
            candidates.append((checkpoint.stat().st_mtime, version_dir.name, str(checkpoint)))
    for _, version_id, checkpoint_path in sorted(candidates, reverse=True):
        try:
            with sqlite3.connect(checkpoint_path) as conn:
                row = conn.execute(
                    """
                    SELECT state_json FROM analyzer_checkpoints_v2
                    WHERE thread_id = ? AND version_id = ?
                    LIMIT 1
                    """,
                    (task_id, version_id),
                ).fetchone()
            # Empty, legacy, or incompatible SQLite files must not fall back
            # to Configer state and masquerade as a valid version checkpoint.
            if row is None:
                continue
            candidate_state = json.loads(row[0])
        except (OSError, sqlite3.DatabaseError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if candidate_state and not _is_finished(candidate_state):
            return version_id, checkpoint_path
    return None


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

    if not runtime.get("version_id"):
        latest = find_latest_version_checkpoint(runtime["output_dir"], runtime["thread_id"])
        if latest:
            runtime["version_id"], runtime["checkpoint_path"] = latest
    elif runtime.get("version_id"):
        runtime["checkpoint_path"] = get_version_checkpoint_path(
            runtime["output_dir"], runtime["thread_id"], runtime["version_id"]
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
    analyze_batch_size: Optional[int] = None,
    emit_status: bool = True,
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

    explicit_version = (
        kwargs.get("version_id")
        or kwargs.get("run_id")
        or os.getenv("ANALYZER_VERSION_ID")
        or os.getenv("VERSION_ID")
        or (state.get("version_id") if isinstance(state, dict) else None)
        or ((state.get("analyzer") or {}).get("version_id") if isinstance(state, dict) else None)
    )
    force_new_version = bool(
        kwargs.get("new_version") or kwargs.get("force_new_version")
    )

    if not force_new_version and not runtime.get("version_id"):
        latest_checkpoint = find_latest_incomplete_version_checkpoint(
            runtime["output_dir"], runtime["thread_id"]
        )
        if latest_checkpoint:
            runtime["version_id"], runtime["checkpoint_path"] = latest_checkpoint
            resume = True
        elif resume:
            latest_checkpoint = find_latest_version_checkpoint(
                runtime["output_dir"], runtime["thread_id"]
            )
            if latest_checkpoint:
                runtime["version_id"], runtime["checkpoint_path"] = latest_checkpoint
            else:
                latest_version = _latest_runtime_version(runtime["thread_id"])
                if latest_version:
                    runtime["version_id"] = latest_version
        elif not explicit_version:
            runtime["version_id"] = ""
    elif force_new_version and not explicit_version:
        runtime["version_id"] = ""

    if not force_new_version and runtime.get("version_id"):
        runtime["checkpoint_path"] = get_version_checkpoint_path(
            runtime["output_dir"], runtime["thread_id"], runtime["version_id"]
        )
        if os.path.exists(runtime["checkpoint_path"]):
            candidate_state = load_analyzer_checkpoint(
                runtime["thread_id"],
                runtime["checkpoint_path"],
                version_id=runtime["version_id"],
            )
            if candidate_state and not _is_finished(candidate_state):
                resume = True
                state = candidate_state
            elif candidate_state and _is_finished(candidate_state) and not resume:
                runtime["version_id"] = ""

    if force_new_version:
        resume = False
        if not explicit_version:
            runtime["version_id"] = ""

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
    if analyze_batch_size is not None:
        if int(analyze_batch_size) < 1:
            raise ValueError("analyze_batch_size must be at least 1")
        state.setdefault("analyzer", {})["analyze_batch_size"] = int(analyze_batch_size)

    writer = kwargs.get("writer")
    if writer is None:
        output_dir = runtime["output_dir"] or state.get("output_dir") or (state.get("analyzer") or {}).get("output_dir") or "./outputs"
        writer_version_id = runtime["version_id"]
        if writer_version_id in ("", "default") and not explicit_version:
            writer_version_id = None
        writer = get_event_writer(
            name="analyzer",
            context_id=runtime["thread_id"],
            log_file_path=output_dir,
            version_id=writer_version_id,
        )
        checkpoint_progress = 0.0
        if resume and isinstance(state, dict):
            checkpoint_progress = float(
                (state.get("_analyzer_checkpoint") or {}).get("node_progress", 0.0)
                or 0.0
            )
        writer.set_running({
            "current": "analyzer.initializing",
            "progress": checkpoint_progress,
            "message": (
                "Analyzer resuming."
                if resume and checkpoint_progress
                else "Analyzer initializing."
            ),
        })
        runtime["version_id"] = str(writer.version_id)
        cleanup_old_analyzer_checkpoints(
            output_dir,
            runtime["thread_id"],
            runtime["version_id"],
        )
        runtime["checkpoint_path"] = get_version_checkpoint_path(
            output_dir,
            runtime["thread_id"],
            runtime["version_id"],
        )
        state["version_id"] = runtime["version_id"]
        state.setdefault("analyzer", {})["version_id"] = runtime["version_id"]
        state["analyzer"]["checkpoint_path"] = runtime["checkpoint_path"]
        state["analyzer"]["runtime_output_dir"] = str(
            Path(output_dir)
            / runtime["thread_id"]
            / "analyzer"
            / runtime["version_id"]
        )
    try:
        result = run_analyzer_pipeline(
            state=state,
            thread_id=runtime["thread_id"],
            checkpoint_path=runtime["checkpoint_path"],
            resume=resume,
            from_node=start_node,
            baseline_result_path=baseline_result_path,
            version_id=runtime["version_id"],
            writer=writer,
        )
    except Exception as exc:
        writer.set_failed(StreamEvent(
            current="analyzer.failed",
            progress=1.0,
            message="Analyzer pipeline failed.",
            data={"error": str(exc)},
        ))
        if emit_status:
            emit_error(
                exc,
                code=ErrorCode.UNHANDLED_EXCEPTION,
                recoverable=True,
                message="Analyzer pipeline failed.",
                stream_writer=writer,
                exit_process=False,
                print_payload=False,
            )
        raise

    if emit_status:
        emit_success(
            data={
                "task_id": result.get("task_id") if isinstance(result, dict) else runtime["thread_id"],
                "version_id": runtime["version_id"],
                "current": result.get("current") if isinstance(result, dict) else None,
                "last_completed": result.get("last_completed") if isinstance(result, dict) else None,
            },
            message="Analyzer pipeline completed.",
            stream_writer=writer,
            exit_process=False,
            print_payload=False,
        )
        writer.set_completed(StreamEvent(
            current="analyzer.completed",
            progress=1.0,
            message="Analyzer pipeline completed.",
            data={"task_id": runtime["thread_id"]},
        ))
    return result


def run_analyzer_standalone_payload(
    state: Optional[Dict[str, Any]],
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    analyze_batch_size: Optional[int] = None,
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
            analyze_batch_size=analyze_batch_size,
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


def resume_analyzer_standalone(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    from_node: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    analyze_batch_size: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Explicit in-process continuation entry point."""
    kwargs.pop("resume", None)
    return run_analyzer_standalone(
        state=state,
        thread_id=thread_id,
        resume=True,
        from_node=from_node,
        checkpoint_path=checkpoint_path,
        baseline_result_path=baseline_result_path,
        analyze_batch_size=analyze_batch_size,
        **kwargs,
    )
