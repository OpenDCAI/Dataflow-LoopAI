from __future__ import annotations

from typing import Any, Dict, List, Optional


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run Analyzer skill (Codex / MCP entry point).

    事件会在流水线执行期间实时写入
    ``<output_dir>/<task_id>/analyzer.pkl``。

    返回统一 success/error payload；如需旧的 final state 返回值，
    直接调用 ``loopai.skills.Analyzer.runner.run_analyzer_standalone``。
    """
    from loopai.skills.Analyzer.runner import run_analyzer_standalone_payload

    return run_analyzer_standalone_payload(
        state=state,
        thread_id=thread_id,
        resume=resume,
        from_node=from_node,
        baseline_result_path=baseline_result_path,
        **kwargs,
    )


def load_events(
    task_id: str,
    output_dir: str = "./outputs",
) -> List[Dict[str, Any]]:
    """读取指定任务的 analyzer 事件列表。

    事件在流水线执行期间实时写入 pickle 文件（``analyzer.pkl``），
    执行完成后可调用此函数获取完整事件列表，用于前端展示或日志分析。
    """
    from loopai.common.event_tool import dump_stream_events_json

    return dump_stream_events_json(
        name="analyzer",
        context_id=task_id,
        log_file_path=output_dir,
    )


__all__ = ["run", "load_events"]
