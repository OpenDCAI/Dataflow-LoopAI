from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import tomlkit

from tortoise.expressions import Q

from ...models.db_models import StarterConfig, TaskModel, ThreadHistory
from ...utils.config.config import check_config_from_db


CURRENT_DIR = Path(__file__).resolve().parent
SERVICES_DIR = CURRENT_DIR.parent
APP_DIR = SERVICES_DIR.parent
API_DIR = APP_DIR.parent
PROJECT_ROOT = API_DIR.parent
CODEX_RUNNER_DIR = PROJECT_ROOT / "codex-runner"
DEFAULT_CODEX_HOME = PROJECT_ROOT / "codex_home"
EXAMPLE_CODEX_HOME = PROJECT_ROOT / "examples" / "codex_home_example"
PROCESS_EXIT_GRACE_SECONDS = 3
FALLBACK_CODEX_HOME = Path.home() / ".codex"
DEFAULT_CODEX_SANDBOX_MODE = "danger-full-access"
ALLOWED_CODEX_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}
CODEX_RUNNER_STREAM_LIMIT = 1024 * 1024
CODEX_CONFIG_ROOT_OVERRIDES = [
    ("codex_model_provider", "model_provider"),
]
CODEX_PROVIDER_OVERRIDES = [
    ("codex_base_url", "base_url"),
    ("codex_api_key_env_key", "env_key"),
    ("codex_provider_name", "name"),
    ("codex_wire_api", "wire_api"),
    ("codex_supports_websockets", "supports_websockets"),
]


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_message_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("value")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_thread_history_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    role = entry.get("role") or entry.get("author") or entry.get("sender") or entry.get("type")
    role = str(role) if role is not None else "unknown"
    if role in {"agent_message", "assistant_message"}:
        role = "assistant"

    content = entry.get("text") or entry.get("message") or entry.get("content") or entry.get("summary") or entry.get("body")
    normalized_content = _extract_message_content(content)
    if not normalized_content and isinstance(entry.get("item"), dict):
        normalized_content = _extract_message_content(entry["item"])
    if not normalized_content:
        return None

    return {
        "id": entry.get("id") or entry.get("uuid") or str(uuid.uuid4()),
        "role": role,
        "content": normalized_content,
        "state": entry.get("state") or entry.get("status") or "completed",
        "created_at": entry.get("created_at") or entry.get("timestamp") or entry.get("time"),
        "source": "codex",
        "raw": entry,
    }


def _extract_thread_history_from_json(value: Any) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            normalized = _normalize_thread_history_entry(item)
            if normalized is not None:
                history.append(normalized)
            elif isinstance(item, (list, dict)):
                history.extend(_extract_thread_history_from_json(item))
        return history
    if isinstance(value, dict):
        normalized = _normalize_thread_history_entry(value)
        if normalized is not None:
            history.append(normalized)
        for key in ("items", "messages", "conversation", "history", "transcript", "entries"):
            nested = value.get(key)
            if nested is not None:
                history.extend(_extract_thread_history_from_json(nested))
        return history
    return history


def _candidate_codex_session_roots() -> list[Path]:
    return [
        DEFAULT_CODEX_HOME / "sessions",
        FALLBACK_CODEX_HOME / "sessions",
    ]


def load_codex_thread_history(thread_id: str) -> dict[str, Any] | None:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return None

    matched_files: list[Path] = []
    for root in _candidate_codex_session_roots():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and thread_id in path.name:
                matched_files.append(path)
        if not matched_files:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    if thread_id in path.read_text(encoding="utf-8"):
                        matched_files.append(path)
                except Exception:
                    continue

    for path in matched_files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        history = _extract_thread_history_from_json(_safe_json_loads(text))
        if not history:
            line_items: list[dict[str, Any]] = []
            for line in text.splitlines():
                obj = _safe_json_loads(line)
                if obj is not None:
                    line_items.extend(_extract_thread_history_from_json(obj))
            history = line_items

        if history:
            return {
                "thread_id": thread_id,
                "source": "codex",
                "path": str(path),
                "conversation": history,
            }
    return None


def _resolved_codex_home(system_config: dict[str, Any]) -> Path:
    configured = str(system_config.get("codex_home") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CODEX_HOME


def _apply_config_overrides(
    source: dict[str, Any],
    target: dict[str, Any],
    override_keys: list[tuple[str, str]],
) -> None:
    for source_key, target_key in override_keys:
        if source_key not in source:
            continue
        value = source.get(source_key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        if source_key == "codex_supports_websockets":
            value = _to_bool(value)
        target[target_key] = value


def _sync_codex_home_config(
    system_config: dict[str, Any],
    env_overrides: dict[str, Any] | None = None,
) -> Path:
    codex_home = _resolved_codex_home(system_config)
    codex_home.mkdir(parents=True, exist_ok=True)

    config_path = codex_home / "config.toml"
    workspace = str(system_config.get("codex_workspace") or PROJECT_ROOT).strip() or str(PROJECT_ROOT)
    example_config_path = EXAMPLE_CODEX_HOME / "config.toml"
    if example_config_path.exists():
        parsed = tomlkit.parse(example_config_path.read_text(encoding="utf-8"))
        template = parsed if isinstance(parsed, dict) else {}
    else:
        template = {}
    _apply_config_overrides(system_config, template, CODEX_CONFIG_ROOT_OVERRIDES)
    provider_name = str(template.get("model_provider") or "dashscope_http").strip()
    template["model_provider"] = provider_name

    model_providers = template.setdefault("model_providers", {})
    provider_config = model_providers.setdefault(provider_name, {})
    if not isinstance(provider_config, dict):
        provider_config = {}
        model_providers[provider_name] = provider_config

    _apply_config_overrides(system_config, provider_config, CODEX_PROVIDER_OVERRIDES)
    if system_config.get("codex_api_key") and not provider_config.get("env_key"):
        provider_config["env_key"] = "CODEX_API_KEY"

    projects = template.get("projects")
    project_config: dict[str, Any] | None = None
    if isinstance(projects, dict):
        current_project = projects.get(workspace)
        if isinstance(current_project, dict):
            project_config = dict(current_project)
        else:
            for value in projects.values():
                if isinstance(value, dict):
                    project_config = dict(value)
                    break
    if project_config is None:
        project_config = {"trust_level": "trusted"}
    project_config.setdefault("trust_level", "trusted")
    template["projects"] = {workspace: project_config}

    mcp_servers = template.setdefault("mcp_servers", {})
    if isinstance(mcp_servers, dict):
        loopai_configer = mcp_servers.get("loopai_configer")
        if isinstance(loopai_configer, dict):
            mcp_env = loopai_configer.setdefault("env", {})
            if isinstance(mcp_env, dict):
                for key, value in (env_overrides or {}).items():
                    key = str(key)
                    if value is None:
                        mcp_env.pop(key, None)
                    else:
                        mcp_env[key] = str(value)

    config_path.write_text(tomlkit.dumps(template), encoding="utf-8")
    return codex_home


def _resolved_codex_sandbox_mode(system_config: dict[str, Any]) -> str:
    configured = str(system_config.get("codex_sandbox_mode") or "").strip()
    if configured in ALLOWED_CODEX_SANDBOX_MODES:
        return configured
    return DEFAULT_CODEX_SANDBOX_MODE


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

    def _new_session(
        self,
        session_id: str,
        prompt: str,
        workspace: str | None,
        env_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "prompt": prompt,
            "workspace": workspace,
            "env_overrides": dict(env_overrides or {}),
            "status": "created",
            "inputs": {},
            "pending_request": None,
            "pending_prompts": [],
            "last_error": None,
            "final_result": None,
            "codex_thread_id": None,
            "active_prompt": None,
            "conversation": [],
            "events": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }

    def create(
        self,
        prompt: str,
        workspace: str | None,
        session_id: str | None = None,
        env_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = session_id or str(uuid.uuid4())
        session = self._new_session(
            session_id=resolved_session_id,
            prompt=prompt,
            workspace=workspace,
            env_overrides=env_overrides,
        )
        self.sessions[resolved_session_id] = session
        return session

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    def get_or_create(
        self,
        session_id: str | None,
        prompt: str,
        workspace: str | None,
        env_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if session_id:
            session = self.get(session_id)
            if session is not None:
                session["prompt"] = prompt
                session["workspace"] = workspace
                if env_overrides is not None:
                    session["env_overrides"] = dict(env_overrides)
                session["updated_at"] = _utc_now()
                return session
        return self.create(
            prompt=prompt,
            workspace=workspace,
            session_id=session_id,
            env_overrides=env_overrides,
        )

    def update(self, session_id: str, **kwargs: Any) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        kwargs["updated_at"] = _utc_now()
        session.update(kwargs)
        return session

    def merge_inputs(self, session_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        session.setdefault("inputs", {}).update(values)
        session["updated_at"] = _utc_now()
        return session

    def add_user_prompt(self, session_id: str, prompt: str, state: str) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        message = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": prompt,
            "state": state,
            "created_at": _utc_now(),
        }
        session.setdefault("conversation", []).append(message)
        session["updated_at"] = _utc_now()
        return message

    def set_message_state(self, session_id: str, message_id: str, state: str) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        for message in session.get("conversation", []):
            if message.get("id") == message_id:
                message["state"] = state
                message["updated_at"] = _utc_now()
                session["updated_at"] = _utc_now()
                return message
        return None

    def add_assistant_message(self, session_id: str, content: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        message = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": content,
            "state": "completed",
            "created_at": _utc_now(),
            "result": result,
        }
        session.setdefault("conversation", []).append(message)
        session["updated_at"] = _utc_now()
        return message

    def queue_prompt(self, session_id: str, prompt: str) -> dict[str, Any] | None:
        message = self.add_user_prompt(session_id, prompt, state="queued")
        session = self.get(session_id)
        if session is None or message is None:
            return None
        queued_item = {
            "message_id": message["id"],
            "prompt": prompt,
            "created_at": message["created_at"],
        }
        session.setdefault("pending_prompts", []).append(queued_item)
        session["pending_request"] = queued_item
        session["updated_at"] = _utc_now()
        return queued_item

    def record_event(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        event = {
            "created_at": _utc_now(),
            "payload": payload,
        }
        session.setdefault("events", []).append(event)
        session["updated_at"] = _utc_now()
        return event

    def start_prompt(self, session_id: str, prompt: str) -> dict[str, Any] | None:
        message = self.add_user_prompt(session_id, prompt, state="running")
        session = self.get(session_id)
        if session is None or message is None:
            return None
        active_prompt = {
            "message_id": message["id"],
            "prompt": prompt,
            "created_at": message["created_at"],
        }
        session["active_prompt"] = active_prompt
        session["prompt"] = prompt
        session["updated_at"] = _utc_now()
        return active_prompt

    def reset(self, session_id: str) -> dict[str, Any] | None:
        session = self.get(session_id)
        if session is None:
            return None
        preserved_workspace = session.get("workspace")
        preserved_env = dict(session.get("env_overrides") or {})
        preserved_inputs = dict(session.get("inputs") or {})
        session.update(
            {
                "prompt": "",
                "workspace": preserved_workspace,
                "env_overrides": preserved_env,
                "status": "created",
                "inputs": preserved_inputs,
                "pending_request": None,
                "pending_prompts": [],
                "last_error": None,
                "final_result": None,
                "codex_thread_id": None,
                "active_prompt": None,
                "conversation": [],
                "events": [],
                "updated_at": _utc_now(),
            }
        )
        return session


codex_session_store = CodexSessionStore()


class CodexStarterService:
    def __init__(self, system_config: dict[str, Any], session_store: CodexSessionStore | None = None):
        self.system_config = system_config
        self.session_store = session_store or codex_session_store

    async def load_persisted_thread_id(self, task_id: str | None) -> str | None:
        task_id = (task_id or "").strip()
        if not task_id:
            return None
        task = await TaskModel.get_or_none(task_id=task_id)
        if task and task.ai_thread_id:
            return str(task.ai_thread_id)
        return None

    async def _find_thread_history(self, task_id: str, thread_id: str | None = None) -> ThreadHistory | None:
        task_id = (task_id or "").strip()
        thread_id = (thread_id or "").strip()
        if not task_id:
            return None
        if thread_id:
            record = await ThreadHistory.filter(task_id=task_id, thread_id=thread_id).order_by("-updatedAt").first()
            if record is not None:
                return record
        return await ThreadHistory.filter(task_id=task_id).order_by("-updatedAt").first()

    def _serialize_session(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "prompt": session.get("prompt") or "",
            "workspace": session.get("workspace"),
            "status": session.get("status") or "created",
            "env_overrides": _json_dumps(session.get("env_overrides") or {}),
            "inputs": _json_dumps(session.get("inputs") or {}),
            "pending_request": _json_dumps(session.get("pending_request")),
            "pending_prompts": _json_dumps(session.get("pending_prompts") or []),
            "last_error": _json_dumps(session.get("last_error")),
            "final_result": _json_dumps(session.get("final_result")),
            "active_prompt": _json_dumps(session.get("active_prompt")),
            "conversation": _json_dumps(session.get("conversation") or []),
            "events": _json_dumps(session.get("events") or []),
        }

    def _history_to_session(self, record: ThreadHistory) -> dict[str, Any]:
        return {
            "session_id": record.session_id or record.task_id,
            "prompt": record.prompt or "",
            "workspace": record.workspace,
            "env_overrides": _json_loads(record.env_overrides, {}),
            "status": record.status or "created",
            "inputs": _json_loads(record.inputs, {}),
            "pending_request": _json_loads(record.pending_request, None),
            "pending_prompts": _json_loads(record.pending_prompts, []),
            "last_error": _json_loads(record.last_error, None),
            "final_result": _json_loads(record.final_result, None),
            "codex_thread_id": record.thread_id,
            "active_prompt": _json_loads(record.active_prompt, None),
            "conversation": _json_loads(record.conversation, []),
            "events": _json_loads(record.events, []),
            "created_at": record.createdAt.isoformat() if record.createdAt else _utc_now(),
            "updated_at": record.updatedAt.isoformat() if record.updatedAt else _utc_now(),
        }

    async def restore_session_snapshot(self, task_id: str) -> dict[str, Any] | None:
        existing = self.session_store.get(task_id)
        if existing is not None:
            return existing
        thread_id = await self.load_persisted_thread_id(task_id)
        if not thread_id:
            return None
        record = await self._find_thread_history(task_id, thread_id)
        if record is None:
            return None
        session = self._history_to_session(record)
        session["session_id"] = task_id
        self.session_store.sessions[task_id] = session
        return session

    async def persist_session_snapshot(self, session_id: str) -> ThreadHistory | None:
        session = self.session_store.get(session_id)
        if session is None:
            return None

        thread_id = str(session.get("codex_thread_id") or "").strip() or None
        if thread_id:
            record = await ThreadHistory.filter(task_id=session_id, thread_id=thread_id).order_by("-updatedAt").first()
            if record is None:
                record = await ThreadHistory.filter(task_id=session_id, thread_id__isnull=True).order_by("-updatedAt").first()
        else:
            record = await ThreadHistory.filter(task_id=session_id, thread_id__isnull=True).order_by("-updatedAt").first()
        payload = {
            "task_id": session_id,
            "session_id": session_id,
            "thread_id": thread_id,
            **self._serialize_session(session),
        }
        if record is None:
            return await ThreadHistory.create(**payload)
        await ThreadHistory.filter(id=record.id).update(**payload)
        return await ThreadHistory.get(id=record.id)

    async def sync_task_thread_id(self, task_id: str, thread_id: str | None) -> None:
        task_id = (task_id or "").strip()
        if not task_id:
            return
        await TaskModel.filter(task_id=task_id).update(ai_thread_id=thread_id)

    async def submit(
        self,
        prompt: str,
        workspace: str | None = None,
        session_id: str | None = None,
        env_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if session_id:
            await self.restore_session_snapshot(session_id)
        existing_session = self.session_store.get(session_id) if session_id else None
        if existing_session is not None and existing_session.get("status") in {"submitted", "running", "finishing"}:
            queued_item = self.session_store.queue_prompt(session_id, prompt)
            self.session_store.update(session_id, status=existing_session.get("status") or "running")
            payload = {
                "type": "queued",
                "session_id": session_id,
                "message": "Codex session is already running; prompt queued",
                "pending_prompts": existing_session.get("pending_prompts", []),
                "queued_item": queued_item,
            }
            self.session_store.record_event(session_id, payload)
            await self.persist_session_snapshot(session_id)
            return payload

        session = self.session_store.get_or_create(
            session_id=session_id,
            prompt=prompt,
            workspace=workspace,
            env_overrides=env_overrides,
        )
        resolved_session_id = session["session_id"]
        self.session_store.update(
            resolved_session_id,
            prompt=prompt,
            workspace=workspace,
            env_overrides=dict(env_overrides or session.get("env_overrides") or {}),
            status="submitted",
            last_error=None,
        )
        persisted_thread_id = await self.load_persisted_thread_id(resolved_session_id)
        if persisted_thread_id and not session.get("codex_thread_id"):
            self.session_store.update(resolved_session_id, codex_thread_id=persisted_thread_id)
        await self.persist_session_snapshot(resolved_session_id)

        async def run_in_background() -> None:
            async for _ in self.stream(
                prompt=prompt,
                workspace=workspace,
                session_id=resolved_session_id,
                env_overrides=env_overrides,
            ):
                pass

        asyncio.create_task(run_in_background())
        payload = {
            "type": "submitted",
            "session_id": resolved_session_id,
            "message": "Codex prompt submitted",
            "status": "submitted",
        }
        self.session_store.record_event(resolved_session_id, payload)
        await self.persist_session_snapshot(resolved_session_id)
        return payload

    def _build_env(
        self,
        system_config: dict[str, Any],
        workspace: str | None,
        env_overrides: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        resolved_workspace = workspace or system_config.get("codex_workspace") or str(PROJECT_ROOT)

        for key, value in system_config.items():
            if value is None or isinstance(value, (dict, list)):
                continue
            env[str(key)] = str(value)

        env["CODEX_WORKSPACE"] = resolved_workspace
        existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        pythonpath_parts = [resolved_workspace]
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

        model = system_config.get("codex_model")
        base_url = system_config.get("codex_base_url")
        api_key = system_config.get("codex_api_key")
        timeout_ms = system_config.get("codex_run_timeout_ms", 300000)
        use_project_config = _to_bool(system_config.get("codex_use_project_config"), False)
        codex_home = str(_resolved_codex_home(system_config))
        sandbox_mode = _resolved_codex_sandbox_mode(system_config)

        if model:
            env["CODEX_MODEL"] = str(model)
        if base_url:
            env["CODEX_BASE_URL"] = str(base_url)
        if api_key:
            env["CODEX_API_KEY"] = str(api_key)
            env.setdefault("OPENAI_API_KEY", str(api_key))
            env.setdefault("DEEPSEEK_API_KEY", str(api_key))
            env.setdefault("DASHSCOPE_API_KEY", str(api_key))
        env["CODEX_HOME"] = codex_home
        env["CODEX_SANDBOX_MODE"] = sandbox_mode
        env["CODEX_RUN_TIMEOUT_MS"] = str(timeout_ms)
        if use_project_config:
            env["CODEX_USE_PROJECT_CONFIG"] = "1"
        else:
            env.pop("CODEX_USE_PROJECT_CONFIG", None)

        for key, value in (env_overrides or {}).items():
            key = str(key)
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)

        return env

    async def stream(
        self,
        prompt: str,
        workspace: str | None = None,
        session_id: str | None = None,
        env_overrides: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        prompt = (prompt or "").strip()
        if not prompt:
            yield _sse({
                "type": "error",
                "message": "prompt is required",
            })
            return

        if session_id:
            await self.restore_session_snapshot(session_id)
        existing_session = self.session_store.get(session_id) if session_id else None
        if existing_session is not None and existing_session.get("status") in {"running", "finishing"}:
            queued_item = self.session_store.queue_prompt(session_id, prompt)
            self.session_store.update(session_id, status=existing_session.get("status") or "running")
            payload = {
                "type": "queued",
                "session_id": session_id,
                "message": "Codex session is already running; prompt queued",
                "pending_prompts": existing_session.get("pending_prompts", []),
                "queued_item": queued_item,
            }
            self.session_store.record_event(session_id, payload)
            await self.persist_session_snapshot(session_id)
            yield _sse(payload)
            return

        session = self.session_store.get_or_create(
            session_id=session_id,
            prompt=prompt,
            workspace=workspace,
            env_overrides=env_overrides,
        )
        session_id = session["session_id"]
        persisted_thread_id = await self.load_persisted_thread_id(session_id)
        if persisted_thread_id and not session.get("codex_thread_id"):
            self.session_store.update(session_id, codex_thread_id=persisted_thread_id)
            session = self.session_store.get(session_id) or session

        active_prompt = self.session_store.start_prompt(session_id, prompt)
        await self.persist_session_snapshot(session_id)
        merged_system_config = dict(self.system_config)
        merged_system_config.update(session.get("inputs", {}))
        merged_env_overrides = dict(session.get("env_overrides") or {})
        if env_overrides is not None:
            merged_env_overrides = dict(env_overrides)
            self.session_store.update(session_id, env_overrides=merged_env_overrides)
            await self.persist_session_snapshot(session_id)

        if not CODEX_RUNNER_DIR.exists():
            yield _sse({
                "type": "error",
                "session_id": session_id,
                "message": f"codex-runner not found: {CODEX_RUNNER_DIR}",
            })
            return

        env = self._build_env(
            merged_system_config,
            workspace,
            env_overrides=merged_env_overrides,
        )
        resolved_workspace = env.get("CODEX_WORKSPACE", str(PROJECT_ROOT))
        use_project_config = _to_bool(merged_system_config.get("codex_use_project_config"), False)
        merged_system_config = dict(merged_system_config)
        merged_system_config["codex_workspace"] = resolved_workspace
        resolved_codex_home = _sync_codex_home_config(
            merged_system_config,
            env_overrides=merged_env_overrides,
        )
        resumed_thread_id = str(session.get("codex_thread_id") or "").strip()

        self.session_store.update(
            session_id,
            workspace=resolved_workspace,
            env_overrides=merged_env_overrides,
            status="running",
            last_error=None,
            final_result=None,
        )
        env["CODEX_HOME"] = str(resolved_codex_home)
        if resumed_thread_id:
            env["CODEX_THREAD_ID"] = resumed_thread_id
        else:
            env.pop("CODEX_THREAD_ID", None)

        init_payload = {
            "type": "starter.codex.init",
            "session_id": session_id,
            "workspace": resolved_workspace,
            "runner_dir": str(CODEX_RUNNER_DIR),
            "model": env.get("CODEX_MODEL"),
            "base_url": env.get("CODEX_BASE_URL"),
            "codex_home": env.get("CODEX_HOME"),
            "sandbox_mode": env.get("CODEX_SANDBOX_MODE"),
            "use_project_config": use_project_config,
            "resume_thread_id": resumed_thread_id or None,
            "env_keys": sorted(merged_env_overrides.keys()),
        }
        self.session_store.record_event(session_id, init_payload)
        await self.persist_session_snapshot(session_id)
        yield _sse(init_payload)

        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "corepack",
                    "yarn",
                    "dev",
                    prompt,
                    cwd=str(CODEX_RUNNER_DIR),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=CODEX_RUNNER_STREAM_LIMIT,
                )
            except OSError as exc:
                self.session_store.update(session_id, status="failed", last_error=str(exc))
                error_payload = {
                    "type": "error",
                    "session_id": session_id,
                    "message": f"failed to start codex-runner: {exc}",
                }
                if active_prompt:
                    self.session_store.set_message_state(session_id, active_prompt["message_id"], "failed")
                self.session_store.record_event(session_id, error_payload)
                await self.persist_session_snapshot(session_id)
                yield _sse(error_payload)
                return

            assert proc.stdout is not None
            assert proc.stderr is not None

            event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            final_result: dict[str, Any] | None = None
            error_payload: dict[str, Any] | None = None
            completed_seen = False
            cleanup_forced = False

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
                        thread_id = payload["event"].get("thread_id")
                        self.session_store.update(
                            session_id,
                            codex_thread_id=thread_id,
                        )
                        await self.sync_task_thread_id(session_id, thread_id)

                    payload["session_id"] = session_id
                    payload["_source"] = source
                    self.session_store.record_event(session_id, payload)
                    await self.persist_session_snapshot(session_id)
                    await event_queue.put(payload)

            stdout_task = asyncio.create_task(consume_stream(proc.stdout, "stdout"))
            stderr_task = asyncio.create_task(consume_stream(proc.stderr, "stderr"))

            async def finalize_queue() -> None:
                try:
                    await asyncio.gather(stdout_task, stderr_task)
                finally:
                    await event_queue.put(None)

            finalize_task = asyncio.create_task(finalize_queue())

            try:
                while True:
                    payload = await event_queue.get()
                    if payload is None:
                        break
                    yield _sse(payload)
                    if payload.get("type") == "completed" and payload.get("_source") == "stdout":
                        completed_seen = True
                        break
            finally:
                if completed_seen:
                    finishing_payload = {
                        "type": "finishing",
                        "session_id": session_id,
                        "message": "Codex result completed; waiting for runner cleanup",
                        "grace_seconds": PROCESS_EXIT_GRACE_SECONDS,
                    }
                    self.session_store.update(
                        session_id,
                        status="finishing",
                        final_result=final_result,
                    )
                    self.session_store.record_event(session_id, finishing_payload)
                    await self.persist_session_snapshot(session_id)
                    yield _sse(finishing_payload)

                    try:
                        returncode = await asyncio.wait_for(proc.wait(), timeout=PROCESS_EXIT_GRACE_SECONDS)
                    except asyncio.TimeoutError:
                        cleanup_forced = True
                        proc.terminate()
                        try:
                            returncode = await asyncio.wait_for(proc.wait(), timeout=1)
                        except asyncio.TimeoutError:
                            proc.kill()
                            returncode = await proc.wait()
                        cleanup_payload = {
                            "type": "runner.cleanup",
                            "session_id": session_id,
                            "message": "Codex runner exceeded cleanup grace period and was terminated",
                            "returncode": returncode,
                        }
                        self.session_store.record_event(session_id, cleanup_payload)
                        await self.persist_session_snapshot(session_id)
                        yield _sse(cleanup_payload)
                else:
                    await finalize_task
                    returncode = await proc.wait()

                try:
                    await asyncio.wait_for(finalize_task, timeout=1)
                except asyncio.TimeoutError:
                    finalize_task.cancel()
                    await asyncio.gather(finalize_task, return_exceptions=True)

            if returncode != 0 and not (completed_seen and final_result is not None):
                self.session_store.update(
                    session_id,
                    status="failed",
                    last_error=error_payload or {"message": "Codex runner failed", "returncode": returncode},
                    active_prompt=None,
                )
                if active_prompt:
                    self.session_store.set_message_state(session_id, active_prompt["message_id"], "failed")
                final_error_payload = {
                    "type": "error",
                    "session_id": session_id,
                    "message": (error_payload or {}).get("message", "Codex runner failed"),
                    "returncode": returncode,
                    "detail": error_payload,
                }
                self.session_store.record_event(session_id, final_error_payload)
                await self.persist_session_snapshot(session_id)
                yield _sse(final_error_payload)
                return

            self.session_store.update(
                session_id,
                status="completed",
                final_result=final_result,
                active_prompt=None,
            )
            if active_prompt:
                self.session_store.set_message_state(session_id, active_prompt["message_id"], "completed")
            final_response = ""
            if isinstance(final_result, dict):
                final_response = str(final_result.get("finalResponse") or "")
            if final_response:
                self.session_store.add_assistant_message(session_id, final_response, result=final_result)
            done_payload = {
                "type": "done",
                "session_id": session_id,
                "returncode": returncode,
                "result": final_result,
            }
            if cleanup_forced:
                done_payload["cleanup_forced"] = True
            self.session_store.record_event(session_id, done_payload)
            await self.persist_session_snapshot(session_id)
            yield _sse(done_payload)
        except Exception as exc:
            self.session_store.update(session_id, status="failed", last_error=str(exc), active_prompt=None)
            if active_prompt:
                self.session_store.set_message_state(session_id, active_prompt["message_id"], "failed")
            error_payload = {
                "type": "error",
                "session_id": session_id,
                "message": f"unexpected starter stream error: {exc}",
            }
            self.session_store.record_event(session_id, error_payload)
            await self.persist_session_snapshot(session_id)
            yield _sse(error_payload)
