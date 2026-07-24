from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loopai.common.tracking import (
    strip_retired_tracking_environment,
    strip_retired_tracking_fields,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows compatibility
    fcntl = None


RUN_STATE_FILENAME = "run_state.json"
WORKER_REQUEST_FILENAME = "worker_request.pkl"
WORKER_RESULT_FILENAME = "worker_result.pkl"
WORKER_LOG_FILENAME = "worker.log"
WORKER_LAUNCH_LOCK_FILENAME = "worker_launch.lock"


class PersistentTrainerWorkerError(RuntimeError):
    """Raised when a detached Trainer worker cannot finish its request."""


@dataclass
class PersistentWorkerHandle:
    run_dir: Path
    state_path: Path
    result_path: Path
    process: Optional[subprocess.Popen] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def worker_paths(run_dir: str | Path) -> Dict[str, Path]:
    root = Path(run_dir).expanduser().resolve()
    return {
        "run_dir": root,
        "state": root / RUN_STATE_FILENAME,
        "request": root / WORKER_REQUEST_FILENAME,
        "result": root / WORKER_RESULT_FILENAME,
        "log": root / WORKER_LOG_FILENAME,
        "launch_lock": root / WORKER_LAUNCH_LOCK_FILENAME,
    }


@contextmanager
def _exclusive_launch_lock(path: Path):
    """Serialize attach/launch decisions for one Trainer version directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def resolve_run_state_path(state: Dict[str, Any]) -> Optional[Path]:
    trainer = state.get("trainer") if isinstance(state, dict) else None
    if not isinstance(trainer, dict):
        return None
    run_dir = trainer.get("trainer_output_dir") or trainer.get("output_dir")
    if not run_dir:
        return None
    return worker_paths(str(run_dir))["state"]


def _atomic_pickle_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp_path.open("wb") as file:
        pickle.dump(value, file, protocol=pickle.HIGHEST_PROTOCOL)
        file.flush()
        os.fsync(file.fileno())
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def load_worker_result(path: str | Path) -> Dict[str, Any]:
    result_path = Path(path)
    with result_path.open("rb") as file:
        payload = pickle.load(file)
    if not isinstance(payload, dict):
        raise PersistentTrainerWorkerError("Trainer worker result is not a mapping")
    cleaned = strip_retired_tracking_fields(payload)
    if cleaned != payload:
        _atomic_pickle_dump(result_path, cleaned)
    return cleaned


def load_worker_request(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("rb") as file:
        payload = pickle.load(file)
    if not isinstance(payload, dict):
        raise PersistentTrainerWorkerError("Trainer worker request is not a mapping")
    return strip_retired_tracking_fields(payload)


def read_run_state(path: str | Path) -> Dict[str, Any]:
    state_path = Path(path)
    if not state_path.is_file():
        return {}
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def update_run_state(
    path: str | Path,
    updates: Dict[str, Any],
    *,
    increment_event_sequence: bool = False,
) -> Dict[str, Any]:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            current = read_run_state(state_path)
            current.update(updates)
            if increment_event_sequence:
                current["event_sequence"] = int(current.get("event_sequence") or 0) + 1
            current["updated_at"] = utc_now_iso()

            temp_path = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(current, file, ensure_ascii=False, indent=2, default=str)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, state_path)
            return current
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def record_worker_event(state: Dict[str, Any], event_payload: Dict[str, Any]) -> None:
    state_path = resolve_run_state_path(state)
    if state_path is None or not state_path.exists():
        return
    data = event_payload.get("data") if isinstance(event_payload, dict) else None
    updates: Dict[str, Any] = {
        "phase": event_payload.get("node"),
        "event_status": event_payload.get("status"),
        "progress": event_payload.get("progress"),
        "message": event_payload.get("message"),
        "last_event": event_payload,
    }
    if isinstance(data, dict):
        for source, target in (
            ("current_step", "current_step"),
            ("total_steps", "total_steps"),
            ("elapsed_time", "elapsed_time"),
            ("training_pid", "training_pid"),
            ("process_group_id", "training_process_group_id"),
        ):
            if data.get(source) is not None:
                updates[target] = data[source]
    update_run_state(state_path, updates, increment_event_sequence=True)


def _pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def launch_or_attach_persistent_worker(state: Dict[str, Any]) -> PersistentWorkerHandle:
    cleaned_state = strip_retired_tracking_fields(state)
    state.clear()
    state.update(cleaned_state)
    trainer = state.get("trainer") if isinstance(state, dict) else None
    if not isinstance(trainer, dict) or not trainer.get("trainer_output_dir"):
        raise PersistentTrainerWorkerError("trainer_output_dir is required before launching the worker")

    paths = worker_paths(trainer["trainer_output_dir"])
    paths["run_dir"].mkdir(parents=True, exist_ok=True)
    handle = PersistentWorkerHandle(
        run_dir=paths["run_dir"],
        state_path=paths["state"],
        result_path=paths["result"],
    )

    with _exclusive_launch_lock(paths["launch_lock"]):
        if paths["result"].is_file():
            return handle

        existing = read_run_state(paths["state"])
        existing_worker_pid = existing.get("worker_pid")
        if existing.get("status") in {"queued", "running"} and _pid_is_alive(existing_worker_pid):
            return handle
        if existing.get("status") in {"queued", "running"} and _pid_is_alive(existing.get("training_pid")):
            raise PersistentTrainerWorkerError(
                "Trainer worker exited while its training process is still alive; refusing to start a duplicate run"
            )

        trainer["trainer_run_state_path"] = str(paths["state"])
        trainer["trainer_worker_log_path"] = str(paths["log"])
        _atomic_pickle_dump(paths["request"], {"state": state})
        update_run_state(
            paths["state"],
            {
                "status": "queued",
                "task_id": state.get("task_id"),
                "version_id": trainer.get("trainer_version_id"),
                "framework": trainer.get("train_framework"),
                "run_dir": str(paths["run_dir"]),
                "request_path": str(paths["request"]),
                "result_path": str(paths["result"]),
                "worker_log_path": str(paths["log"]),
                "submitted_at": utc_now_iso(),
            },
        )

        project_root = Path(__file__).resolve().parents[4]
        env = strip_retired_tracking_environment(os.environ)
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(project_root), current_pythonpath) if item
        )
        env["LOOPAI_TRAINER_WORKER_CHILD"] = "1"

        with paths["log"].open("ab", buffering=0) as worker_log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "loopai.skills.Trainer.worker_entry",
                    "--request",
                    str(paths["request"]),
                ],
                stdin=subprocess.DEVNULL,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
                cwd=str(project_root),
                env=env,
                start_new_session=(os.name == "posix"),
                close_fds=True,
            )
        handle.process = process
        trainer["trainer_worker_pid"] = process.pid
        update_run_state(
            paths["state"],
            {
                "worker_pid": process.pid,
                "worker_process_group_id": process.pid if os.name == "posix" else None,
            },
        )
    return handle


def wait_for_persistent_worker(
    handle: PersistentWorkerHandle,
    *,
    stream_writer: Optional[Callable[[Dict[str, Any]], Any]] = None,
    poll_interval: float = 1.0,
) -> Dict[str, Any]:
    last_sequence = -1
    dead_since: Optional[float] = None

    while True:
        if handle.result_path.is_file():
            payload = load_worker_result(handle.result_path)
            if handle.process is not None:
                try:
                    handle.process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    pass
            if payload.get("ok") is True and isinstance(payload.get("state"), dict):
                return strip_retired_tracking_fields(payload["state"])
            error = payload.get("error") or {}
            detail = error.get("detail") if isinstance(error, dict) else str(error)
            raise PersistentTrainerWorkerError(detail or "Trainer worker failed")

        run_state = read_run_state(handle.state_path)
        sequence = int(run_state.get("event_sequence") or 0)
        if sequence != last_sequence:
            last_sequence = sequence
            last_event = run_state.get("last_event")
            if stream_writer is not None and isinstance(last_event, dict):
                try:
                    stream_writer(last_event)
                except Exception:
                    pass

        worker_pid = run_state.get("worker_pid")
        process_alive = (
            handle.process.poll() is None
            if handle.process is not None
            else _pid_is_alive(worker_pid)
        )
        if process_alive:
            dead_since = None
        else:
            dead_since = dead_since or time.monotonic()
            if time.monotonic() - dead_since >= max(2.0, poll_interval * 2):
                status = run_state.get("status") or "unknown"
                raise PersistentTrainerWorkerError(
                    f"Trainer worker exited without a result (status={status}, log={handle.run_dir / WORKER_LOG_FILENAME})"
                )

        time.sleep(poll_interval)


def write_worker_result(path: str | Path, payload: Dict[str, Any]) -> None:
    _atomic_pickle_dump(Path(path), payload)
