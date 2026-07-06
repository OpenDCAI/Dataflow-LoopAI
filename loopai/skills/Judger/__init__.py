from __future__ import annotations

from typing import Any, Dict, List, Optional


def run(
    state: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
    writer: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run Judger standalone (Codex / CLI entry point).

    State 存取通过 Configer 走 TaskModel.state，需提前设 ``DB_PATH`` 和 ``TASK_ID``。
    """
    from loopai.skills.Judger.runner import run_judger_pipeline

    return run_judger_pipeline(
        state=state,
        task_id=task_id,
        resume=resume,
        from_step=from_step,
        writer=writer,
        **kwargs,
    )


def load_events(
    task_id: str,
    output_dir: str = "./outputs",
) -> List[Dict[str, Any]]:
    """读取指定任务的 judger 事件列表。

    事件在流水线执行期间实时写入 pickle 文件（``judger.pkl``），
    执行完成后可调用此函数获取完整事件列表，用于前端展示或日志分析。

    Args:
        task_id: 任务 ID。
        output_dir: 输出根目录，默认 ``"./outputs"``。

    Returns:
        StreamEvent dict 列表，按写入时间排序。
    """
    from loopai.common.event_tool import dump_stream_events_json

    return dump_stream_events_json(
        name="judger",
        context_id=task_id,
        log_file_path=output_dir,
    )


__all__ = ["run", "load_events"]
