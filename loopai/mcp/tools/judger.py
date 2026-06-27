from __future__ import annotations

import json
from typing import Any

from ..base import mcp
from loopai.skills.Judger import run


def _build_response(result: dict[str, Any]) -> dict[str, Any]:
    """从 pipeline 返回的 state 提取统一响应。"""
    judger = result.get("judger") or {}
    bench = judger.get("bench") or {}

    metrics = judger.get("metrics") or {}
    if not metrics:
        metrics = (bench.get("meta") or {}).get("eval_result") or {}
    metrics_str = json.dumps(metrics, ensure_ascii=False) if metrics else ""

    return {
        "ok": True,
        "status": "completed",
        "message": "Judger pipeline completed.",
        "data": {
            "task_type": judger.get("eval_task_type"),
            "output_result_path": judger.get("output_result_path", ""),
            "output_case_path": judger.get("output_case_path", ""),
            "output_problem_path": judger.get("output_problem_path", ""),
            "output_pred_path": judger.get("output_pred_path", ""),
            "bench": bench,
            "metrics": metrics_str,
        },
        "error": None,
    }


@mcp.tool(
    name="judger_run",
    description=(
        "Run the Judger evaluation pipeline for one task. "
        "Supports three task types: code (pass@k), text2sql (pass@k), and general_text (accuracy/score via One-Eval). "
        "Requires DB_PATH and TASK_ID environment variables to be set. "
        "Reads task config from Configer (TaskModel.state), writes progress events to outputs/<task_id>/judger.pkl. "
        "Returns result paths and metrics (JSON string) in a unified response. "
        "Use this when you need to evaluate model outputs. "
        "NEVER use loopai.agents.Judger.JudgerAgent — it is deprecated."
    ),
)
def judger_run(
    task_id: str,
    resume: bool = False,
    from_step: str | None = None,
    output_dir: str = "./outputs",
) -> dict[str, Any]:
    try:
        result = run(
            state=None,
            task_id=task_id,
            resume=resume,
            from_step=from_step,
            output_dir=output_dir,
        )
        return _build_response(result)
    except SystemExit:
        # emit_error → print error JSON → sys.exit(1)
        return {
            "ok": False,
            "status": "failed",
            "message": "Judger pipeline failed (SystemExit).",
            "data": None,
            "error": {"code": "UNHANDLED_EXCEPTION", "detail": "Pipeline exited via emit_error. See judger.pkl or Configer for details."},
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "failed",
            "message": str(exc),
            "data": None,
            "error": {
                "type": type(exc).__name__,
                "code": "UNHANDLED_EXCEPTION",
                "detail": str(exc),
            },
        }


