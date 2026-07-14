from __future__ import annotations

import os
from typing import Any, Dict, Optional

def _build_result_data(final_state: Dict[str, Any]) -> Dict[str, Any]:
    webcrawler_state = final_state.get("webcrawler", {}) or {}
    return {
        "task_id": final_state.get("task_id"),
        "output_dir": final_state.get("output_dir"),
        "version_id": webcrawler_state.get("runtime_version_id", ""),
        "run_id": webcrawler_state.get("output_run_id"),
        "crawl_output_dir": webcrawler_state.get("output_dir"),
        "total_pages": (webcrawler_state.get("output_result") or {}).get("total_pages", 0),
        "dataset_sft_count": webcrawler_state.get("dataset_sft_count", 0),
        "dataset_pt_count": webcrawler_state.get("dataset_pt_count", 0),
        "dataset_sft_path": webcrawler_state.get("dataset_sft_path", ""),
        "dataset_pt_path": webcrawler_state.get("dataset_pt_path", ""),
        "dataset_sft_mapped_path": webcrawler_state.get("dataset_sft_mapped_path", ""),
        "dataset_pt_mapped_path": webcrawler_state.get("dataset_pt_mapped_path", ""),
    }


def _resolve_resume_version_id(task_id: str, db_path: str, state: Optional[Dict[str, Any]]) -> Optional[str]:
    if isinstance(state, dict):
        state_version_id = str((state.get("webcrawler") or {}).get("runtime_version_id") or "").strip()
        if state_version_id:
            return state_version_id

    try:
        from loopai.common.db_tool.runtime import get_latest_task_runtime_sync

        runtime = get_latest_task_runtime_sync(db_path, task_id, "webcrawler")
        if runtime and runtime.get("version"):
            return str(runtime["version"])
    except Exception:
        return None
    return None


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from loopai.skills.WebCrawler.runner import run_webcrawler_standalone
    from loopai.common.event_tool import get_event_writer
    from loopai.common.exception import ErrorCode, emit_error, emit_success

    _ = thread_id
    db_path = str(os.getenv("DB_PATH") or "").strip()
    if not db_path:
        emit_error(
            ValueError("DB_PATH env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="DB_PATH environment variable is not set.",
        )

    task_id = str(os.getenv("TASK_ID") or "").strip()
    if not task_id:
        emit_error(
            ValueError("TASK_ID env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="TASK_ID environment variable is not set.",
        )

    run_kwargs = dict(kwargs)
    output_dir = str(
        run_kwargs.get("output_dir")
        or os.getenv("OUTPUT_DIR")
        or ((state or {}).get("output_dir") if isinstance(state, dict) else None)
        or "./outputs"
    ).strip()
    writer_version_id = _resolve_resume_version_id(task_id, db_path, state) if resume else None
    writer = run_kwargs.pop("writer", None) or get_event_writer(
        name="webcrawler",
        context_id=task_id,
        log_file_path=output_dir,
        version_id=writer_version_id,
    )
    run_kwargs.pop("db_path", None)
    writer.set_running(
        {
            "current": "webcrawler.initializing",
            "progress": 0.0,
            "message": "WebCrawler initializing.",
            "data": {"task_id": task_id, "resume": resume},
        }
    )

    try:
        result = run_webcrawler_standalone(
            state=state,
            thread_id=task_id,
            resume=resume,
            from_step=from_step,
            checkpoint_path=checkpoint_path,
            db_path=db_path,
            writer=writer,
            **run_kwargs,
        )
    except Exception as exc:
        code = ErrorCode.UNHANDLED_EXCEPTION
        if isinstance(exc, ValueError):
            code = ErrorCode.CONFIG_ERROR
        elif isinstance(exc, FileNotFoundError):
            code = ErrorCode.NOT_FOUND
        emit_error(
            exc,
            code=code,
            recoverable=True,
            message="WebCrawler pipeline failed.",
            stream_writer=writer,
        )
        raise

    emit_success(
        data=_build_result_data(result),
        message="WebCrawler pipeline completed.",
        stream_writer=writer,
    )
    return result


def load_events(task_id: str, output_dir: str = "./outputs"):
    from loopai.common.event_tool import dump_stream_events_json

    return dump_stream_events_json(
        name="webcrawler",
        context_id=task_id,
        log_file_path=output_dir,
    )


__all__ = ["run", "load_events"]

