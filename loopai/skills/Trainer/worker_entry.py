from __future__ import annotations

import argparse
import os
import traceback
from pathlib import Path
from typing import Any, Dict

from loopai.common.exception import ErrorCode, build_error_payload, build_success_payload
from loopai.common.tracking import strip_retired_tracking_fields
from loopai.skills.Trainer.utils.persistent_worker import (
    load_worker_request,
    update_run_state,
    utc_now_iso,
    worker_paths,
    write_worker_result,
)


def _final_event_data(state: Dict[str, Any]) -> Dict[str, Any]:
    trainer = state.setdefault("trainer", {})
    return {
        "task_id": state.get("task_id"),
        "trainer_version_id": trainer.get("trainer_version_id"),
        "trainer_training_success": trainer.get("trainer_training_success"),
        "trainer_training_final_status": trainer.get("trainer_training_final_status"),
        "trainer_result_summary": trainer.get("trainer_result_summary"),
        "trainer_best_checkpoint": trainer.get("trainer_best_checkpoint"),
        "trainer_model_export_error": trainer.get("trainer_model_export_error"),
    }


def run_worker(request_path: str | Path) -> int:
    request = Path(request_path).expanduser().resolve()
    payload = load_worker_request(request)
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("Trainer worker request is missing state")
    state = strip_retired_tracking_fields(state)

    trainer = state.setdefault("trainer", {})
    run_dir = trainer.get("trainer_output_dir") or trainer.get("output_dir")
    if not run_dir:
        raise ValueError("Trainer worker request is missing trainer_output_dir")
    paths = worker_paths(run_dir)

    # The request is transient; remove it as soon as the worker has loaded it.
    try:
        request.unlink()
    except OSError:
        pass

    os.environ["LOOPAI_TRAINER_WORKER_CHILD"] = "1"
    trainer["trainer_worker_pid"] = os.getpid()
    trainer["trainer_run_state_path"] = str(paths["state"])
    trainer["trainer_worker_log_path"] = str(paths["log"])
    update_run_state(
        paths["state"],
        {
            "status": "running",
            "worker_pid": os.getpid(),
            "started_at": utc_now_iso(),
        },
    )

    try:
        from loopai.skills.Trainer.nodes.training_execution_node import _training_execution_inline
        from loopai.skills.Trainer.runner import (
            _update_trainer_task_state,
            finalize_trainer_result_state,
        )
        from loopai.skills.Trainer.utils.stream_events import emit_trainer_event

        result = _training_execution_inline(state, writer=lambda _payload: None)
        result.setdefault("trainer", {}).pop("_trainer_approved_config_yaml", None)
        result.setdefault("trainer", {}).pop("_trainer_approved_config_sha256", None)
        finalize_trainer_result_state(result, task_id=result.get("task_id"))

        result_trainer = result.setdefault("trainer", {})
        success = bool(result_trainer.get("trainer_training_success"))
        final_status = result_trainer.get("trainer_training_final_status") or {}
        reported_status = str(final_status.get("status") or "").lower()
        status = "completed" if success else (
            "cancelled" if reported_status == "cancelled" else "failed"
        )
        final_data = _final_event_data(result)
        if success:
            trainer_payload = build_success_payload(
                data=final_data,
                message="Trainer skill completed.",
            )
        else:
            existing_payload = result_trainer.get("trainer_result")
            if isinstance(existing_payload, dict) and existing_payload.get("ok") is False:
                trainer_payload = existing_payload
            else:
                error_detail = (
                    result_trainer.get("train_output_training_error")
                    or final_status.get("error_message")
                    or f"training finished with status: {status}"
                )
                trainer_payload = build_error_payload(
                    RuntimeError(str(error_detail)),
                    code=(
                        ErrorCode.INTERRUPTED
                        if status == "cancelled"
                        else ErrorCode.UNHANDLED_EXCEPTION
                    ),
                    recoverable=status != "cancelled",
                    message="Trainer skill failed.",
                )
        result_trainer["trainer_result"] = trainer_payload
        result_trainer["trainer_last_error"] = trainer_payload.get("error") or {}

        runtime = result_trainer.pop("_trainer_worker_runtime", None)
        if isinstance(runtime, dict):
            try:
                _update_trainer_task_state(runtime, result_trainer)
            except Exception as state_exc:
                # Training artifacts and pkl events remain authoritative even
                # when the optional database state update is temporarily down.
                result_trainer["trainer_state_update_error"] = str(state_exc)

        emit_trainer_event(
            lambda _payload: None,
            result,
            "trainer.run",
            status,
            progress=1.0,
            message=(
                "Trainer worker completed."
                if success
                else "Trainer worker was cancelled."
                if status == "cancelled"
                else "Trainer worker finished with failure."
            ),
            data=final_data,
            error=trainer_payload.get("error"),
        )

        write_worker_result(
            paths["result"],
            {"ok": True, "state": strip_retired_tracking_fields(result)},
        )
        update_run_state(
            paths["state"],
            {
                "status": status,
                "finished_at": utc_now_iso(),
                "result_path": str(paths["result"]),
            },
        )
        return 0
    except BaseException as exc:
        error = {
            "type": type(exc).__name__,
            "detail": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        print(error["traceback"], flush=True)
        try:
            write_worker_result(paths["result"], {"ok": False, "error": error})
            update_run_state(
                paths["state"],
                {
                    "status": "failed",
                    "finished_at": utc_now_iso(),
                    "error": error,
                    "result_path": str(paths["result"]),
                },
            )
        except Exception:
            traceback.print_exc()
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one persistent LoopAI Trainer task")
    parser.add_argument("--request", required=True, help="Path to the trusted worker request pickle")
    args = parser.parse_args()
    return run_worker(args.request)


if __name__ == "__main__":
    raise SystemExit(main())
