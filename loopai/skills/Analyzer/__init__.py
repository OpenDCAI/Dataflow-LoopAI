from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from loopai.common.exception import ErrorCode, emit_error, emit_success
from loopai.common.event_tool import StreamEvent, get_event_writer, load_stream_events


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    analyze_batch_size: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run Analyzer skill (Codex / subprocess entry point).

    ``DB_PATH`` 和 ``TASK_ID`` 从环境变量自动获取，未设置时 emit_error
    退出。事件和产物按 ``output_dir/task_id/analyzer/version_id`` 隔离。
    成功时输出 JSON 到 stdout + sys.exit(0)。不要在进程内直接调用；
    直接调用 ``loopai.skills.Analyzer.runner.run_analyzer_standalone``。
    """
    if not os.getenv("DB_PATH"):
        emit_error(
            ValueError("DB_PATH env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="DB_PATH environment variable is not set.",
        )

    task_id = thread_id or os.getenv("TASK_ID")
    if not task_id:
        emit_error(
            ValueError("TASK_ID env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="TASK_ID environment variable is not set.",
        )

    from .runtime_config import resolve_analyzer_runtime_config
    from .runtime_config import get_version_checkpoint_path
    from .runtime_config import find_latest_version_checkpoint
    from .runtime_config import cleanup_old_analyzer_checkpoints
    from .pipeline_runner import load_analyzer_checkpoint, _is_finished
    from .state_bridge import load_analyzer_state_from_configer
    from loopai.skills.Analyzer.runner import (
        find_latest_incomplete_version_checkpoint,
        run_analyzer_standalone,
    )

    try:
        if state is None:
            state = load_analyzer_state_from_configer(task_id=task_id)
        runtime = resolve_analyzer_runtime_config(
            state,
            thread_id=task_id,
            baseline_result_path=baseline_result_path,
            analyze_batch_size=analyze_batch_size,
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
        if resume and not explicit_version:
            latest_checkpoint = find_latest_version_checkpoint(
                runtime["output_dir"], runtime["thread_id"]
            )
            if latest_checkpoint:
                runtime["version_id"], runtime["checkpoint_path"] = latest_checkpoint
            else:
                runtime["version_id"] = ""
        elif not explicit_version:
            if not kwargs.get("new_version") and not kwargs.get("force_new_version"):
                latest_checkpoint = find_latest_incomplete_version_checkpoint(
                    runtime["output_dir"], runtime["thread_id"]
                )
                if latest_checkpoint:
                    runtime["version_id"], runtime["checkpoint_path"] = latest_checkpoint
                    resume = True
                else:
                    runtime["version_id"] = ""
            else:
                runtime["version_id"] = ""
        if runtime.get("version_id"):
            runtime["checkpoint_path"] = get_version_checkpoint_path(
                runtime["output_dir"], runtime["thread_id"], runtime["version_id"]
            )
            if not resume and os.path.exists(runtime["checkpoint_path"]):
                candidate_state = load_analyzer_checkpoint(
                    runtime["thread_id"],
                    runtime["checkpoint_path"],
                    version_id=runtime["version_id"],
                )
                if candidate_state and not _is_finished(candidate_state):
                    resume = True
                elif candidate_state and _is_finished(candidate_state):
                    runtime["version_id"] = ""
        writer_version_id = runtime["version_id"]
        if writer_version_id in ("", "default") and not explicit_version:
            writer_version_id = None
        writer = get_event_writer(
            name="analyzer",
            context_id=runtime["thread_id"],
            log_file_path=runtime["output_dir"],
            version_id=writer_version_id,
        )
        resume_progress = 0.0
        if resume and runtime.get("version_id") and os.path.exists(runtime["checkpoint_path"]):
            checkpoint_state = load_analyzer_checkpoint(
                runtime["thread_id"],
                runtime["checkpoint_path"],
                version_id=runtime["version_id"],
            )
            resume_progress = float(
                (checkpoint_state.get("_analyzer_checkpoint") or {}).get(
                    "node_progress", 0.0
                )
                or 0.0
            )
        writer.set_running({
            "current": "analyzer.initializing",
            "progress": resume_progress,
            "message": "Analyzer resuming." if resume and resume_progress else "Analyzer initializing.",
        })
        runtime["version_id"] = str(writer.version_id)
        cleanup_old_analyzer_checkpoints(
            runtime["output_dir"],
            runtime["thread_id"],
            runtime["version_id"],
        )
        runtime["checkpoint_path"] = get_version_checkpoint_path(
            runtime["output_dir"],
            runtime["thread_id"],
            runtime["version_id"],
        )
        state["version_id"] = runtime["version_id"]
        state.setdefault("analyzer", {})["version_id"] = runtime["version_id"]
        state["analyzer"]["checkpoint_path"] = runtime["checkpoint_path"]
        state["analyzer"]["runtime_output_dir"] = str(
            Path(runtime["output_dir"])
            / runtime["thread_id"]
            / "analyzer"
            / runtime["version_id"]
        )
        runner_kwargs = dict(kwargs)
        for control_key in (
            "version_id",
            "run_id",
            "resume",
            "from_node",
            "checkpoint_path",
            "baseline_result_path",
            "analyze_batch_size",
            "writer",
            "emit_status",
        ):
            runner_kwargs.pop(control_key, None)
        runner_kwargs["version_id"] = runtime["version_id"]
        final_state = run_analyzer_standalone(
            state=state,
            thread_id=runtime["thread_id"],
            resume=resume,
            from_node=from_node,
            baseline_result_path=baseline_result_path,
            analyze_batch_size=analyze_batch_size,
            writer=writer,
            emit_status=False,
            **runner_kwargs,
        )
    except (ValueError, TypeError) as exc:
        if locals().get("writer") is not None:
            writer.set_failed(StreamEvent(
                current="analyzer.failed",
                progress=1.0,
                message="Analyzer input contract validation failed.",
                data={"error": str(exc)},
            ))
        emit_error(
            exc,
            code=ErrorCode.INVALID_INPUT,
            recoverable=False,
            message="Analyzer input contract validation failed.",
            stream_writer=locals().get("writer"),
        )
    except RuntimeError as exc:
        if locals().get("writer") is not None:
            writer.set_failed(StreamEvent(
                current="analyzer.failed",
                progress=1.0,
                message="Analyzer runtime configuration is incomplete.",
                data={"error": str(exc)},
            ))
        emit_error(
            exc,
            code=ErrorCode.CONFIG_ERROR,
            recoverable=True,
            message="Analyzer runtime configuration is incomplete.",
            stream_writer=locals().get("writer"),
        )
    except Exception as exc:
        if locals().get("writer") is not None:
            writer.set_failed(StreamEvent(
                current="analyzer.failed",
                progress=1.0,
                message="Analyzer crashed with an unhandled exception.",
                data={"error": str(exc)},
            ))
        emit_error(
            exc,
            code=ErrorCode.UNHANDLED_EXCEPTION,
            recoverable=True,
            message="Analyzer crashed with an unhandled exception.",
            stream_writer=locals().get("writer"),
        )

    analyzer = final_state.get("analyzer", {}) if isinstance(final_state, dict) else {}
    # common.emit_success passes a response envelope without ``current``.
    # Emit the terminal StreamEvent explicitly so the shared writer records it.
    writer.set_completed(StreamEvent(
        current="analyzer.completed",
        progress=1.0,
        message="Analyzer pipeline completed.",
        data={"task_id": task_id},
    ))
    emit_success(
        data={
            "task_id": final_state.get("task_id") if isinstance(final_state, dict) else task_id,
            "version_id": final_state.get("version_id") if isinstance(final_state, dict) else None,
            "current": final_state.get("current") if isinstance(final_state, dict) else None,
            "last_completed": final_state.get("last_completed") if isinstance(final_state, dict) else None,
            "output_dir": analyzer.get("runtime_output_dir") or analyzer.get("output_dir"),
            "historical_comparison": analyzer.get("historical_comparison", {}),
            "state": final_state,
        },
        stream_writer=writer,
        message="Analyzer pipeline completed.",
    )


def load_events(
    task_id: str,
    output_dir: str = "./outputs",
    version_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """读取指定任务的 analyzer 事件列表。

    事件在流水线执行期间实时写入 pickle 文件（``analyzer.pkl``），
    执行完成后可调用此函数获取完整事件列表，用于前端展示或日志分析。
    """
    return [event.json() for event in load_stream_events(
        name="analyzer",
        context_id=task_id,
        log_file_path=output_dir,
    ) if version_id is None or event.version_id == version_id]


def resume_run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    analyze_batch_size: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Explicit continuation entry point; always resumes the latest checkpoint."""
    kwargs.pop("resume", None)
    return run(
        state=state,
        thread_id=thread_id,
        resume=True,
        from_node=from_node,
        baseline_result_path=baseline_result_path,
        analyze_batch_size=analyze_batch_size,
        **kwargs,
    )


__all__ = ["run", "resume_run", "load_events"]
