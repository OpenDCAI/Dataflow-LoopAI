from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from ...models.body import StarterLooperRequest, response_body
from ...models.db_models import TaskModel
from ..task import build_initial_task_state, list_latest_task_runtimes
from .codex import CodexStarterService, codex_session_store, load_starter_system_config
from .stream_events import parse_task_state
from loopai.skills.Looper import parse_looper_command, run_looper_once

CURRENT_DIR = Path(__file__).resolve().parent
SERVICES_DIR = CURRENT_DIR.parent
APP_DIR = SERVICES_DIR.parent
API_DIR = APP_DIR.parent
DB_PATH = str(API_DIR / "db" / "db.sqlite3")
PROJECT_ROOT = API_DIR.parent
DEFAULT_OUTPUTS_DIR = str(PROJECT_ROOT / "outputs")


class LooperRunStore:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[Any]] = {}

    def get_task(self, session_id: str) -> asyncio.Task[Any] | None:
        task = self.tasks.get(session_id)
        if task is not None and task.done():
            self.tasks.pop(session_id, None)
            return None
        return task

    def set_task(self, session_id: str, task: asyncio.Task[Any]) -> None:
        self.tasks[session_id] = task

    def clear_task(self, session_id: str, task: asyncio.Task[Any] | None = None) -> asyncio.Task[Any] | None:
        current = self.tasks.get(session_id)
        if current is None:
            return None
        if task is not None and current is not task:
            return current
        return self.tasks.pop(session_id, None)


looper_run_store = LooperRunStore()


def _looper_error_response(exc: Exception) -> dict[str, Any]:
    message = str(exc).strip() or "Looper execution failed"
    data: dict[str, Any] = {"error": message}

    if message.startswith("Looper LLM HTTP "):
        suffix = message[len("Looper LLM HTTP "):]
        code_text, _, detail = suffix.partition(":")
        try:
            status_code = int(code_text.strip())
        except ValueError:
            status_code = 502
        if detail.strip():
            data["upstream_error"] = detail.strip()
        return response_body(
            code=status_code,
            status="error",
            message="Looper upstream model request failed",
            data=data,
        )()

    if message.startswith("Looper LLM connection error:"):
        reason = message.partition(":")[2].strip()
        if reason:
            data["upstream_error"] = reason
        return response_body(
            code=503,
            status="error",
            message="Looper upstream model connection failed",
            data=data,
        )()

    if message == "Looper model configuration is incomplete.":
        return response_body(
            code=400,
            status="error",
            message="Looper model configuration is incomplete",
            data=data,
        )()

    if (
        message.startswith("Looper output JSON is invalid:")
        or message.startswith("No JSON object found in Looper output:")
        or message == "Looper output JSON must be an object."
    ):
        return response_body(
            code=422,
            status="error",
            message="Looper returned invalid JSON",
            data=data,
        )()

    return response_body(
        code=500,
        status="error",
        message="Looper execution failed",
        data=data,
    )()


async def run_looper_for_session(
    session_id: str,
    req: StarterLooperRequest | None = None,
) -> dict[str, Any]:
    task = await TaskModel.get_or_none(task_id=session_id)
    if task is None:
        return response_body(code=404, status="error", message="Task not found")()

    active_looper_task = looper_run_store.get_task(session_id)
    if active_looper_task is not None:
        return response_body(
            code=409,
            status="error",
            message="Looper is still running",
            data={"session_id": session_id},
        )()

    request_payload = req or StarterLooperRequest()
    os.environ["DB_PATH"] = DB_PATH
    os.environ["TASK_ID"] = session_id
    system_config = await load_starter_system_config()
    service = CodexStarterService(system_config=system_config, session_store=codex_session_store)

    session = codex_session_store.get(session_id)
    if session is None:
        session = await service.restore_session_snapshot(session_id)

    state = parse_task_state(task.state)
    if not isinstance(state, dict):
        state = await build_initial_task_state(session_id)
    else:
        state.setdefault("task_id", session_id)
        state.setdefault("messages", [])

    runtime_status = await list_latest_task_runtimes(session_id)
    conversation = []
    if isinstance(session, dict):
        conversation = session.get("conversation") or []

    current_task = asyncio.current_task()
    if current_task is not None:
        looper_run_store.set_task(session_id, current_task)

    try:
        looper_state = await run_looper_once(
            task_id=session_id,
            state=state,
            system_config=system_config,
            conversation=conversation,
            session=session,
            runtime_status=runtime_status,
            output_dir=DEFAULT_OUTPUTS_DIR,
        )
    except asyncio.CancelledError as exc:
        message = str(exc).strip() or "Interrupted by user."
        return response_body(
            code=409,
            status="error",
            message="Looper execution interrupted",
            data={"session_id": session_id, "error": message},
        )()
    except Exception as exc:
        return _looper_error_response(exc)
    finally:
        if current_task is not None:
            looper_run_store.clear_task(session_id, current_task)

    raw_command = (looper_state.get("looper") or {}).get("command") or ""
    try:
        command = parse_looper_command(raw_command)
    except Exception as exc:
        return response_body(
            code=500,
            status="error",
            message=f"Invalid looper command: {exc}",
            data={"state": looper_state, "raw_command": raw_command},
        )()

    action: dict[str, Any] | None = None
    if request_payload.execute_command:
        latest_session = codex_session_store.get(session_id) or session
        latest_status = str((latest_session or {}).get("status") or "")
        if command["op"] == "query":
            if (
                codex_session_store.has_active_execution(session_id)
                or latest_status in {"submitted", "running", "finishing", "terminating"}
            ):
                action = {
                    "type": "deferred_running",
                    "message": "Codex session is still running; looper command was planned but not auto-submitted.",
                    "session_status": latest_status,
                    "command": command,
                }
            else:
                action = await service.submit(
                    prompt=command["message"],
                    workspace=request_payload.workspace or (latest_session or {}).get("workspace"),
                    session_id=session_id,
                    env_overrides={
                        "DB_PATH": DB_PATH,
                        "TASK_ID": session_id,
                    },
                )
        elif command["op"] == "stop":
            action = await service.terminate(session_id)

    return response_body(
        message="Looper finished one planning pass",
        data={
            "session_id": session_id,
            "command": command,
            "action": action,
            "state": looper_state,
        },
    )()


async def terminate_looper_for_session(session_id: str) -> dict[str, Any]:
    task = looper_run_store.get_task(session_id)
    if task is None:
        return response_body(
            code=409,
            status="error",
            message="Looper is not running",
            data={"session_id": session_id},
        )()

    task.cancel("Interrupted by user.")
    return response_body(
        message="Looper termination requested",
        data={"session_id": session_id, "status": "terminating"},
    )()
