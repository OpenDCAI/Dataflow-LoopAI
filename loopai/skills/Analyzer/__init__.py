from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from loopai.common.exception import ErrorCode, emit_error, emit_success


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
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

    from .event_tool import get_analyzer_event_writer
    from .runtime_config import resolve_analyzer_runtime_config
    from .state_bridge import load_analyzer_state_from_configer
    from loopai.skills.Analyzer.runner import run_analyzer_standalone

    try:
        if state is None:
            state = load_analyzer_state_from_configer(task_id=task_id)
        runtime = resolve_analyzer_runtime_config(
            state,
            thread_id=task_id,
            baseline_result_path=baseline_result_path,
            **kwargs,
        )
        explicit_version = kwargs.get("version_id") or kwargs.get("run_id")
        writer_version_id = runtime["version_id"]
        if writer_version_id == "default" and not explicit_version:
            writer_version_id = None
        writer = get_analyzer_event_writer(
            context_id=runtime["thread_id"],
            log_file_path=runtime["output_dir"],
            state=state,
            version_id=writer_version_id,
        )
        writer.set_running({
            "current": "analyzer.initializing",
            "progress": 0.0,
            "message": "Analyzer initializing.",
        })
        runtime["version_id"] = str(writer.version_id)
        state["version_id"] = runtime["version_id"]
        state.setdefault("analyzer", {})["version_id"] = runtime["version_id"]
        state["analyzer"]["runtime_output_dir"] = str(
            Path(runtime["output_dir"])
            / runtime["thread_id"]
            / "analyzer"
            / runtime["version_id"]
        )
        runner_kwargs = dict(kwargs)
        runner_kwargs.pop("version_id", None)
        runner_kwargs.pop("run_id", None)
        final_state = run_analyzer_standalone(
            state=state,
            thread_id=runtime["thread_id"],
            resume=resume,
            from_node=from_node,
            baseline_result_path=baseline_result_path,
            writer=writer,
            emit_status=False,
            version_id=runtime["version_id"],
            **runner_kwargs,
        )
    except (ValueError, TypeError) as exc:
        emit_error(
            exc,
            code=ErrorCode.INVALID_INPUT,
            recoverable=False,
            message="Analyzer input contract validation failed.",
            stream_writer=locals().get("writer"),
        )
    except RuntimeError as exc:
        emit_error(
            exc,
            code=ErrorCode.CONFIG_ERROR,
            recoverable=True,
            message="Analyzer runtime configuration is incomplete.",
            stream_writer=locals().get("writer"),
        )
    except Exception as exc:
        emit_error(
            exc,
            code=ErrorCode.UNHANDLED_EXCEPTION,
            recoverable=True,
            message="Analyzer crashed with an unhandled exception.",
            stream_writer=locals().get("writer"),
        )

    analyzer = final_state.get("analyzer", {}) if isinstance(final_state, dict) else {}
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
    from .event_tool import load_analyzer_events

    return load_analyzer_events(
        task_id=task_id,
        output_dir=output_dir,
        version_id=version_id,
    )


__all__ = ["run", "load_events"]
