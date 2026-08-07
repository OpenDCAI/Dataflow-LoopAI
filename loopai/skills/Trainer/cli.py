"""Command-line entry point for the LoopAI Trainer skill."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence


_FAILURE_STATUSES = {"cancelled", "error", "failed", "failure"}
_SAFE_RESULT_KEYS = {
    "approval_required",
    "code",
    "config",
    "config_path",
    "config_sha256",
    "config_yaml",
    "data",
    "error",
    "errors",
    "message",
    "ok",
    "status",
    "success",
    "task_id",
    "trainer_output_dir",
    "trainer_version_id",
    "version_id",
    "warnings",
}
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
_SECRET_SUFFIXES = (
    "_api_key",
    "_credential",
    "_credentials",
    "_password",
    "_secret",
    "_token",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value, key=str)
    return str(value)


def _is_secret_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "***" if _is_secret_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _print_json(payload: Any) -> None:
    print(
        json.dumps(
            _redact(payload),
            ensure_ascii=False,
            sort_keys=True,
            default=_json_default,
        )
    )


def _extract_trainer_result(state: Any) -> dict[str, Any]:
    """Extract the public Trainer response without dumping the complete State."""
    if not isinstance(state, dict):
        return {
            "ok": True,
            "status": "completed",
            "message": "Trainer command completed.",
            "data": None,
            "error": None,
        }

    trainer = state.get("trainer")
    if isinstance(trainer, dict) and isinstance(trainer.get("trainer_result"), dict):
        return trainer["trainer_result"]

    if isinstance(state.get("trainer_result"), dict):
        return state["trainer_result"]

    safe_result = {
        key: value
        for key, value in state.items()
        if key in _SAFE_RESULT_KEYS
    }
    if safe_result:
        return safe_result
    return {
        "ok": True,
        "status": "completed",
        "message": "Trainer command completed.",
        "data": None,
        "error": None,
    }


def _success_payload(message: str, data: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "completed",
        "message": message,
        "data": data,
        "error": None,
    }


def _error_payload(exc: BaseException, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "failed",
        "message": message,
        "data": None,
        "error": {
            "type": type(exc).__name__,
            "code": "UNEXPECTED_ERROR",
            "detail": str(exc),
            "recoverable": not isinstance(exc, KeyboardInterrupt),
        },
    }


def _is_failure(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("ok") is False or result.get("success") is False:
        return True
    if str(result.get("status") or "").strip().lower() in _FAILURE_STATUSES:
        return True
    data = result.get("data")
    return isinstance(data, dict) and _is_failure(data)


def _add_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    require_version: bool = False,
) -> None:
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="LoopAI starter/state YAML used to initialize Trainer.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Optional LoopAI task/thread context ID; takes precedence over --task-id.",
    )
    parser.add_argument(
        "--task-id",
        default=os.getenv("TASK_ID") or None,
        help="Task ID override (defaults to TASK_ID).",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("DB_PATH") or None,
        help="Runtime database path override (defaults to DB_PATH).",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("OUTPUT_DIR") or None,
        help="Output root override (defaults to OUTPUT_DIR or ./outputs).",
    )
    parser.add_argument(
        "--version-id",
        default=os.getenv("VERSION_ID") or None,
        required=require_version and not bool(os.getenv("VERSION_ID")),
        help=(
            "Trainer version ID. For run-prepared, pass trainer_version_id "
            "returned by prepare (defaults to VERSION_ID)."
        ),
    )
    parser.add_argument(
        "--trainer-output-dir",
        default=os.getenv("TRAINER_OUTPUT_DIR") or None,
        help="Explicit Trainer version directory override.",
    )


def _runtime_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "task_id": getattr(args, "task_id", None),
            "db_path": getattr(args, "db_path", None),
            "output_dir": getattr(args, "output_dir", None),
            "version_id": getattr(args, "version_id", None),
            "trainer_output_dir": getattr(args, "trainer_output_dir", None),
        }.items()
        if value is not None
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopai-trainer",
        description=(
            "Prepare, approve, run, and inspect LoopAI Trainer jobs. "
            "Training is deliberately split into prepare and run-prepared steps."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Validate inputs and generate the exact training YAML for approval.",
    )
    _add_runtime_arguments(prepare_parser)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Print a prepared training YAML and its SHA256 approval digest.",
    )
    inspect_parser.add_argument(
        "--prepared-config",
        required=True,
        help="Prepared training YAML path.",
    )

    run_parser = subparsers.add_parser(
        "run-prepared",
        help="Run the exact prepared YAML after verifying its SHA256 digest.",
    )
    run_parser.add_argument(
        "--prepared-config",
        required=True,
        help="Prepared training YAML path.",
    )
    run_parser.add_argument(
        "--sha256",
        required=True,
        help="Expected SHA256 returned by prepare or inspect.",
    )
    _add_runtime_arguments(run_parser, require_version=True)

    events_parser = subparsers.add_parser(
        "events",
        help="Read persisted Trainer events for a task.",
    )
    events_parser.add_argument("--task-id", required=True)
    events_parser.add_argument("--output-dir", default="./outputs")
    events_parser.add_argument("--version-id", default=None)
    events_parser.add_argument("--trainer-output-dir", default=None)

    status_parser = subparsers.add_parser(
        "status",
        help="Read persisted run state and the latest local training metric.",
    )
    status_parser.add_argument("--task-id", default=None)
    status_parser.add_argument("--version-id", default=None)
    status_parser.add_argument("--output-dir", default="./outputs")
    status_parser.add_argument("--trainer-output-dir", default=None)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze Trainer artifacts and select the best checkpoint.",
    )
    analyze_parser.add_argument("--task-id", default=None)
    analyze_parser.add_argument("--output-dir", default="./outputs")
    analyze_parser.add_argument("--trainer-task-id", default=None)
    analyze_parser.add_argument("--training-output-dir", default=None)

    guide_parser = subparsers.add_parser(
        "guide",
        help="Print Trainer configuration prefill guidance.",
    )
    guide_parser.add_argument(
        "--task-type",
        default="sft",
        help="Training task type, for example sft or grpo.",
    )

    return parser


def _dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    # Lazy import keeps `loopai-trainer --help` free of training-side effects.
    from loopai.skills import Trainer

    if args.command == "prepare":
        state = Trainer.prepare(
            thread_id=args.thread_id,
            config_path=args.config_path,
            **_runtime_kwargs(args),
        )
        result = _extract_trainer_result(state)
        return result, 1 if _is_failure(result) else 0

    if args.command == "inspect":
        data = Trainer.inspect_prepared_config(args.prepared_config)
        return _success_payload("Prepared Trainer config inspected.", data), 0

    if args.command == "run-prepared":
        state = Trainer.run_prepared(
            prepared_config_path=args.prepared_config,
            expected_config_sha256=args.sha256,
            thread_id=args.thread_id,
            config_path=args.config_path,
            **_runtime_kwargs(args),
        )
        result = _extract_trainer_result(state)
        return result, 1 if _is_failure(result) else 0

    if args.command == "events":
        data = Trainer.load_events(
            task_id=args.task_id,
            output_dir=args.output_dir,
            version_id=args.version_id,
            trainer_output_dir=args.trainer_output_dir,
        )
        return _success_payload("Trainer events loaded.", data), 0

    if args.command == "status":
        data = _load_status(
            task_id=args.task_id,
            version_id=args.version_id,
            output_dir=args.output_dir,
            trainer_output_dir=args.trainer_output_dir,
        )
        return _success_payload("Trainer status loaded.", data), 0

    if args.command == "analyze":
        result = Trainer.analyze_results(
            task_id=args.task_id,
            output_dir=args.output_dir,
            trainer_task_id=args.trainer_task_id,
            training_output_dir=args.training_output_dir,
        )
        return result, 1 if _is_failure(result) else 0

    if args.command == "guide":
        data = Trainer.prefill_guide(task_type=args.task_type)
        return _success_payload("Trainer prefill guide generated.", data), 0

    raise ValueError(f"Unsupported Trainer command: {args.command}")


def _load_status(
    *,
    task_id: str | None,
    version_id: str | None,
    output_dir: str,
    trainer_output_dir: str | None,
) -> dict[str, Any]:
    if trainer_output_dir:
        run_dir = Path(trainer_output_dir).expanduser().resolve()
    else:
        if not task_id or not version_id:
            raise ValueError(
                "status requires --trainer-output-dir or both --task-id and --version-id"
            )
        run_dir = (
            Path(output_dir).expanduser()
            / str(task_id)
            / "trainer"
            / str(version_id)
        ).resolve()

    from loopai.skills.Trainer.results import load_live_training_metrics
    from loopai.skills.Trainer.utils.persistent_worker import read_run_state, worker_paths

    paths = worker_paths(run_dir)
    run_state = read_run_state(paths["state"])
    if not run_dir.is_dir() and not run_state:
        raise FileNotFoundError(f"Trainer run directory does not exist: {run_dir}")

    metrics_task_info = None
    latest_metric = None
    try:
        live_metrics = load_live_training_metrics(run_dir)
    except FileNotFoundError:
        live_metrics = {}
    if isinstance(live_metrics, dict):
        task_info = live_metrics.get("task_info")
        if isinstance(task_info, dict):
            metrics_task_info = task_info
        records = live_metrics.get("metrics")
        if isinstance(records, list):
            metric_records = [
                item
                for item in records
                if isinstance(item, dict) and "log_line" not in item
            ]
            if metric_records:
                latest_metric = metric_records[-1]

    return {
        "run_dir": str(run_dir),
        "run_state_path": str(paths["state"]),
        "run_state": run_state,
        "metrics_task_info": metrics_task_info,
        "latest_metric": latest_metric,
    }


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result, exit_code = _dispatch(args)
        _print_json(result)
        return exit_code
    except KeyboardInterrupt as exc:
        _print_json(
            _error_payload(
                exc,
                (
                    "Trainer CLI interrupted. A detached worker may still be running; "
                    "inspect persisted events or run_state.json before resubmitting."
                ),
            )
        )
        return 130
    except Exception as exc:
        _print_json(_error_payload(exc, "Trainer CLI command failed."))
        return 1


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
