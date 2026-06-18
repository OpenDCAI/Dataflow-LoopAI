from __future__ import annotations

from typing import Any, Dict, List, Optional


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run Judger standalone (Codex / CLI entry point).

    事件会在流水线执行期间实时写入
    ``<output_dir>/<task_id>/judger.pkl``。

    Args:
        state: Full state dict with ``state["judger"]`` config fields.
            Required unless ``resume=True``.
        thread_id: Checkpoint thread id (default: ``"judger-default"``).
        resume: If True, load state from checkpoint and resume.
        from_step: Force start from a specific pipeline step name.
        checkpoint_path: Path to sqlite checkpoint file.
        **kwargs: Runtime overrides.

    Returns:
        Final state dict.
    """
    from loopai.skills.Judger.runner import run_judger_pipeline

    return run_judger_pipeline(
        state=state,
        thread_id=thread_id,
        checkpoint_path=checkpoint_path,
        resume=resume,
        from_step=from_step,
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
        task_id: 任务 ID（对应 run() 的 thread_id）。
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
