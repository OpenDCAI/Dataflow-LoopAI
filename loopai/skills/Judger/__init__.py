from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from loopai.common.event_tool import get_event_writer
from loopai.common.exception import emit_error, emit_success, ErrorCode
from loopai.skills.Judger.runner import _load_task_state, run_judger_pipeline


def run(
    state: Optional[Dict[str, Any]] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Judger standalone (Codex / CLI / subprocess entry point).

    ``DB_PATH`` 和 ``TASK_ID`` 从环境变量自动获取，未设置时 emit_error 退出。
    成功时输出 JSON 到 stdout + sys.exit(0)，失败时 emit_error 已处理。
    不要在进程内直接调用——内部会 sys.exit()。
    """

    if not os.getenv("DB_PATH"):
        emit_error(
            ValueError("DB_PATH env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="DB_PATH environment variable is not set.",
        )

    task_id = os.getenv("TASK_ID")
    if not task_id:
        emit_error(
            ValueError("TASK_ID env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="TASK_ID environment variable is not set.",
        )

    # 提前读 state 获取 output_dir，建 writer
    if state is None:
        state = _load_task_state(task_id)
    output_dir = state.get("output_dir", "./outputs")

    # resume 时读取上次运行的 version_id，复用输出目录
    version_id = None
    if resume:
        try:
            from loopai.common.db_tool.runtime import get_latest_task_runtime_sync
            rt = get_latest_task_runtime_sync(os.getenv("DB_PATH"), task_id, "judger")
            if rt and rt.get("version"):
                version_id = rt["version"]
        except Exception:
            pass

    writer = get_event_writer(name="judger", context_id=task_id, log_file_path=output_dir, version_id=version_id)

    result = run_judger_pipeline(
        state=state,
        task_id=task_id,
        resume=resume,
        from_step=from_step,
        writer=writer,
    )

    # 成功——标准 payload 输出到 stdout（Codex 消费）
    judger = result.get("judger", {})
    bench_list = judger.get("bench_result") or []
    secondary_list = judger.get("extra_bench_result") or []

    # 聚合 metrics：按 bench_name 索引
    metrics = {}
    for b in bench_list + secondary_list:
        m = b.get("metrics") or {}
        if m:
            metrics[b.get("bench_name", "unknown")] = m
        else:
            meta = b.get("meta") or {}
            m = meta.get("eval_result") or {}
            if m:
                metrics[b.get("bench_name", "unknown")] = m

    metrics_str = json.dumps(metrics, ensure_ascii=False) if metrics else ""

    emit_success(
        data={
            "bench_result": bench_list,
            "extra_bench_result": secondary_list,
            "metrics": metrics_str,
        },
        stream_writer=writer,
        message="Judger pipeline completed.",
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
