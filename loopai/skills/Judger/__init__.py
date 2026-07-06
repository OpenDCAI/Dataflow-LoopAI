from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from loopai.common.event_tool import get_event_writer
from loopai.common.exception import emit_success
from loopai.skills.Judger.runner import _load_task_state, run_judger_pipeline


def run(
    state: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Judger standalone (Codex / CLI / subprocess entry point).

    ``DB_PATH`` 和 ``TASK_ID`` 从环境变量自动获取，未设置时 sys.exit(1)。
    成功时输出 JSON 到 stdout + sys.exit(0)，失败时 emit_error 已处理。
    不要在进程内直接调用——内部会 sys.exit()。
    """
    
    if not os.getenv("DB_PATH"):
        print(json.dumps({"ok": False, "message": "DB_PATH env is required"}), file=sys.stderr)
        sys.exit(1)

    resolved_task_id = task_id or os.getenv("TASK_ID")
    if not resolved_task_id:
        print(json.dumps({"ok": False, "message": "TASK_ID env or task_id param is required"}), file=sys.stderr)
        sys.exit(1)

    # 提前读 state 获取 output_dir，建 writer
    if state is None:
        state = _load_task_state(resolved_task_id)
    output_dir = state.get("output_dir", "./outputs")
    writer = get_event_writer(name="judger", context_id=resolved_task_id, log_file_path=output_dir)

    result = run_judger_pipeline(
        state=state,
        task_id=resolved_task_id,
        resume=resume,
        from_step=from_step,
        writer=writer,
    )

    # 成功——标准 payload 输出到 stdout（Codex 消费）
    judger = result.get("judger", {})
    bench = judger.get("bench") or {}
    metrics = judger.get("metrics") or {}
    if not metrics:
        metrics = (bench.get("meta") or {}).get("eval_result") or {}
    metrics_str = json.dumps(metrics, ensure_ascii=False) if metrics else ""

    emit_success(
        data={
            "task_type": judger.get("eval_task_type"),
            "output_result_path": judger.get("output_result_path", ""),
            "output_case_path": judger.get("output_case_path", ""),
            "output_problem_path": judger.get("output_problem_path", ""),
            "output_pred_path": judger.get("output_pred_path", ""),
            "bench": bench,
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
