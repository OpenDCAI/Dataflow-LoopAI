from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from tortoise.expressions import Q

from ..models.db_models import StarterConfig
from ..utils.config.config import check_config_from_db


CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent
API_DIR = APP_DIR.parent
PROJECT_ROOT = API_DIR.parent
CODEX_RUNNER_DIR = PROJECT_ROOT / "codex-runner"
DEFAULT_CODEX_HOME = PROJECT_ROOT / "codex_home"


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def load_starter_system_config() -> dict[str, Any]:
    await check_config_from_db(str(PROJECT_ROOT))
    config_item = await StarterConfig.filter(Q(name="starter")).first()
    if not config_item or not config_item.config:
        return {}

    try:
        config = json.loads(config_item.config)
    except json.JSONDecodeError:
        return {}

    system_config = config.get("system", {})
    if isinstance(system_config, dict):
        return system_config
    return {}


class CodexSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def create(self, prompt: str, workspace: str | None) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        session = {
            "session_id": session_id,
            "prompt": prompt,
            "workspace": workspace,
            "status": "created",
            "inputs": {},
            "pending_request": None,
            "last_error": None,
            "final_result": None,
            "codex_thread_id": None,
        }
        self.sessions[session_id] = session
        return session

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    def get_or_create(self, session_id: str | None, prompt: str, workspace: str | None) -> dict[str, Any]:
        if session_id:
            session = self.get(session_id)
            if session is not None:
                session["prompt"] = prompt
                session["workspace"] = workspace
                return session
        return self.create(prompt=prompt, workspace=workspace)

    def update(self, session_id: str, **kwargs: Any) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        session.update(kwargs)
        return session

    def merge_inputs(self, session_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        session.setdefault("inputs", {}).update(values)
        return session


codex_session_store = CodexSessionStore()


class CodexStarterService:
    def __init__(self, system_config: dict[str, Any], session_store: CodexSessionStore | None = None):
        self.system_config = system_config
        self.session_store = session_store or codex_session_store

    def _build_env(self, system_config: dict[str, Any], workspace: str | None) -> dict[str, str]:
        env = os.environ.copy()
        resolved_workspace = workspace or system_config.get("codex_workspace") or str(PROJECT_ROOT)

        env["CODEX_WORKSPACE"] = resolved_workspace

        model = system_config.get("codex_model")
        base_url = system_config.get("codex_base_url")
        api_key = system_config.get("codex_api_key")
        timeout_ms = system_config.get("codex_run_timeout_ms", 300000)
        use_project_config = _to_bool(system_config.get("codex_use_project_config"), False)

        if model:
            env["CODEX_MODEL"] = str(model)
        if base_url:
            env["CODEX_BASE_URL"] = str(base_url)
        if api_key:
            env["CODEX_API_KEY"] = str(api_key)
            # Keep provider-specific aliases in sync during the migration period.
            # codex-sdk may still resolve auth from CODEX_HOME provider config.
            env.setdefault("OPENAI_API_KEY", str(api_key))
            env.setdefault("DEEPSEEK_API_KEY", str(api_key))
            env.setdefault("DASHSCOPE_API_KEY", str(api_key))
        env["CODEX_RUN_TIMEOUT_MS"] = str(timeout_ms)
        if use_project_config:
            env["CODEX_USE_PROJECT_CONFIG"] = "1"
        else:
            env.pop("CODEX_USE_PROJECT_CONFIG", None)

        return env

    def _build_runtime_codex_home(self, workspace: str, base_url: str | None) -> tempfile.TemporaryDirectory[str]:
        runtime_home = tempfile.TemporaryDirectory(prefix="loopai-codex-home-")
        home_path = Path(runtime_home.name)
        provider_name = "starter_http"
        provider_base_url = base_url or "https://api.openai.com/v1"
        config_text = "\n".join([
            f'model_provider = "{provider_name}"',
            "",
            f"[model_providers.{provider_name}]",
            'name = "Starter HTTP"',
            f'base_url = "{provider_base_url}"',
            'env_key = "CODEX_API_KEY"',
            'wire_api = "responses"',
            "supports_websockets = false",
            "",
            f'[projects."{workspace}"]',
            'trust_level = "trusted"',
            "",
        ])
        (home_path / "config.toml").write_text(config_text, encoding="utf-8")
        return runtime_home

    async def stream(
        self,
        prompt: str,
        workspace: str | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        prompt = (prompt or "").strip()
        if not prompt:
            yield _sse({
                "type": "error",
                "message": "prompt is required",
            })
            return

        session = self.session_store.get_or_create(
            session_id=session_id,
            prompt=prompt,
            workspace=workspace,
        )
        session_id = session["session_id"]
        merged_system_config = dict(self.system_config)
        merged_system_config.update(session.get("inputs", {}))

        if not CODEX_RUNNER_DIR.exists():
            yield _sse({
                "type": "error",
                "session_id": session_id,
                "message": f"codex-runner not found: {CODEX_RUNNER_DIR}",
            })
            return

        env = self._build_env(merged_system_config, workspace)
        resolved_workspace = env.get("CODEX_WORKSPACE", str(PROJECT_ROOT))
        self.session_store.update(
            session_id,
            workspace=resolved_workspace,
            status="running",
            last_error=None,
            final_result=None,
        )
        runtime_codex_home = self._build_runtime_codex_home(
            workspace=resolved_workspace,
            base_url=env.get("CODEX_BASE_URL"),
        )
        env["CODEX_HOME"] = runtime_codex_home.name

        yield _sse({
            "type": "starter.codex.init",
            "session_id": session_id,
            "workspace": resolved_workspace,
            "runner_dir": str(CODEX_RUNNER_DIR),
            "model": env.get("CODEX_MODEL"),
            "base_url": env.get("CODEX_BASE_URL"),
            "codex_home": env.get("CODEX_HOME"),
        })

        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "yarn",
                    "dev",
                    prompt,
                    cwd=str(CODEX_RUNNER_DIR),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                self.session_store.update(session_id, status="failed", last_error=str(exc))
                yield _sse({
                    "type": "error",
                    "session_id": session_id,
                    "message": f"failed to start codex-runner: {exc}",
                })
                return

            assert proc.stdout is not None
            assert proc.stderr is not None

            event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            final_result: dict[str, Any] | None = None
            error_payload: dict[str, Any] | None = None

            async def consume_stream(stream: asyncio.StreamReader, source: str) -> None:
                nonlocal final_result, error_payload

                while True:
                    line = await stream.readline()
                    if not line:
                        break

                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue

                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        await event_queue.put({
                            "type": f"{source}.raw",
                            "message": text,
                        })
                        continue

                    if source == "stderr" and payload.get("type") == "error":
                        error_payload = payload
                    if source == "stdout" and payload.get("type") == "completed":
                        result = payload.get("result")
                        if isinstance(result, dict):
                            final_result = result
                    if (
                        source == "stdout"
                        and payload.get("type") == "event"
                        and isinstance(payload.get("event"), dict)
                        and payload["event"].get("type") == "thread.started"
                    ):
                        self.session_store.update(
                            session_id,
                            codex_thread_id=payload["event"].get("thread_id"),
                        )

                    payload["session_id"] = session_id
                    payload["_source"] = source
                    await event_queue.put(payload)

            stdout_task = asyncio.create_task(consume_stream(proc.stdout, "stdout"))
            stderr_task = asyncio.create_task(consume_stream(proc.stderr, "stderr"))

            async def finalize_queue() -> None:
                await asyncio.gather(stdout_task, stderr_task)
                await event_queue.put(None)

            finalize_task = asyncio.create_task(finalize_queue())

            try:
                while True:
                    payload = await event_queue.get()
                    if payload is None:
                        break
                    yield _sse(payload)
            finally:
                await finalize_task

            returncode = await proc.wait()
            if returncode != 0:
                self.session_store.update(
                    session_id,
                    status="failed",
                    last_error=error_payload or {"message": "Codex runner failed", "returncode": returncode},
                )
                yield _sse({
                    "type": "error",
                    "session_id": session_id,
                    "message": (error_payload or {}).get("message", "Codex runner failed"),
                    "returncode": returncode,
                    "detail": error_payload,
                })
                return

            self.session_store.update(
                session_id,
                status="completed",
                final_result=final_result,
            )
            yield _sse({
                "type": "done",
                "session_id": session_id,
                "returncode": returncode,
                "result": final_result,
            })
        finally:
            runtime_codex_home.cleanup()
