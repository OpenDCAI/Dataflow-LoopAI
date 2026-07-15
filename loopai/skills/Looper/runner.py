from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import urllib.error
from datetime import date, datetime
from pathlib import Path
import urllib.request
from dataclasses import dataclass
from typing import Any

import psutil

from api.app.utils.monitor.hw_stat import (
    get_cpu_usage,
    get_huawei_npu_usage,
    get_memory_usage,
    get_nvidia_gpu_usage,
)
from loopai.common.event_tool import StreamEvent, get_event_writer
from loopai.common.exception import ErrorCode, emit_error
from loopai.common.prompts import PromptLoader
from loopai.schema.model_pool import (
    StarterModelPool,
    chat_completions_url,
    normalize_wire_api,
    responses_url,
)
from loopai.skills.Configer import (
    get_configer_task_state_config,
    update_configer_task_state_config,
)

MAX_LOOPER_MESSAGES = 12
MAX_CONVERSATION_ITEMS = 30
MAX_TEXT_PREVIEW = 1200
SUMMARY_SYSTEM_PROMPT = """你是 Codex 会话摘要器。你只负责根据给定 conversation 和状态，总结本轮任务实际进展。
输出要求：
1. 输出纯文本，不要输出 JSON。
2. 必须覆盖：当前目标、已完成动作、关键结论、失败/阻塞、最值得继续的下一步。
3. 如果 conversation 信息不足，要明确指出缺口，而不是编造。
4. 总结面向后续 planner 使用，要保留执行细节而不是写空泛摘要。"""


LOOPER_JSON_RETRY_PROMPT = """你刚才的输出不符合 JSON 格式要求，不能被程序解析。
请立刻重新输出，并且严格遵守以下要求：
1. 只输出一个 JSON 对象。
2. 不要输出 markdown 代码块，不要输出解释，不要输出额外文字。
3. 仅允许合法 JSON，所有字符串里的反斜杠和引号都必须正确转义。
4. 保持原本意图，只修正输出格式。"""


@dataclass
class LooperModelConfig:
    model: str
    api_url: str
    api_key: str
    wire_api: str
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 4096
    source: str = ""


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _unwrap_config_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _unwrap_config_dict(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    return {str(key): _unwrap_config_value(value) for key, value in config.items()}


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _extract_text_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _extract_text_content(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "message", "body"):
            if key in value:
                return _extract_text_content(value.get(key))
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)
    return str(value)


def _preview_text(value: Any, limit: int = MAX_TEXT_PREVIEW) -> str:
    text = _extract_text_content(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _json_safe(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return _json_safe(value.dict())
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except TypeError:
            pass
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def _normalize_conversation_message(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    role = item.get("role") or item.get("author") or item.get("sender") or item.get("type") or "unknown"
    role = str(role)
    if role in {"agent_message", "assistant_message"}:
        role = "assistant"
    content = _extract_text_content(
        item.get("content")
        or item.get("text")
        or item.get("message")
        or item.get("summary")
        or item.get("body")
        or item.get("item")
    ).strip()
    if not content:
        return None
    return {
        "id": item.get("id") or item.get("uuid") or "",
        "role": role,
        "content": content,
        "state": item.get("state") or item.get("status") or "completed",
        "created_at": item.get("created_at") or item.get("timestamp") or item.get("time") or "",
    }


def _normalize_conversation(conversation: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in conversation or []:
        message = _normalize_conversation_message(item)
        if message is not None:
            normalized.append(message)
    return normalized


def _trim_internal_messages(messages: list[Any] | None, *, keep: int = MAX_LOOPER_MESSAGES) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        content = _extract_text_content(item.get("content")).strip()
        if content:
            normalized.append({"role": role, "content": content})
    if len(normalized) <= keep:
        return normalized
    return normalized[-keep:]


def _truncate_for_prompt(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                result["..."] = "truncated"
                break
            if depth == 0 and key == "messages":
                continue
            result[str(key)] = _truncate_for_prompt(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        if len(value) > 8:
            value = value[:8] + ["<truncated>"]
        return [_truncate_for_prompt(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > 500:
            return value[:500] + "..."
        return value
    return value


def _bytes_to_gib(value: Any) -> float:
    try:
        return round(float(value) / (1024 ** 3), 2)
    except (TypeError, ValueError):
        return 0.0


def _looper_error_code(exc: Exception) -> ErrorCode:
    message = str(exc).strip()
    if message == "Looper model configuration is incomplete.":
        return ErrorCode.CONFIG_ERROR
    if message.startswith("Looper LLM HTTP ") or message.startswith("Looper LLM connection error:"):
        return ErrorCode.EXTERNAL_SERVICE_ERROR
    if (
        message.startswith("Looper output JSON is invalid:")
        or message.startswith("No JSON object found in Looper output:")
        or message == "Looper output JSON must be an object."
    ):
        return ErrorCode.INVALID_INPUT
    return ErrorCode.UNHANDLED_EXCEPTION


def collect_machine_environment() -> dict[str, Any]:
    gpu_usage: list[Any] = []
    accelerator = "none"
    gpu_result = get_nvidia_gpu_usage()
    if gpu_result[0]:
        accelerator = "nvidia"
        gpu_usage = gpu_result[1]
    else:
        npu_result = get_huawei_npu_usage()
        if npu_result[0]:
            accelerator = "huawei_npu"
            gpu_usage = npu_result[1]

    memory_usage = get_memory_usage()
    cpu_usage = get_cpu_usage()
    return {
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "cpu": {
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "usage": cpu_usage,
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else [],
        },
        "memory": {
            **memory_usage,
            "total_GiB": _bytes_to_gib(memory_usage.get("total")),
            "available_GiB": _bytes_to_gib(memory_usage.get("available")),
            "used_GiB": _bytes_to_gib(memory_usage.get("used")),
            "free_GiB": _bytes_to_gib(memory_usage.get("free")),
        },
        "accelerator": {
            "type": accelerator,
            "devices": gpu_usage,
        },
    }


def load_looper_state_from_configer(task_id: str) -> dict[str, Any]:
    result = get_configer_task_state_config("looper", task_id=task_id)
    if not result.get("ok"):
        return {"task_id": task_id, "looper": {}}
    data = result.get("data") or {}
    return {
        "task_id": data.get("task_id") or task_id,
        "looper": _unwrap_config_dict(data.get("config")),
    }


def update_looper_state_via_configer(state: dict[str, Any], *, task_id: str | None = None) -> dict[str, Any]:
    looper = state.get("looper") if isinstance(state, dict) else {}
    if not isinstance(looper, dict):
        looper = {}
    updates = {
        "messages": looper.get("messages") if isinstance(looper.get("messages"), list) else [],
        "historySummary": str(looper.get("historySummary") or ""),
        "last_conv_id": str(looper.get("last_conv_id") or ""),
        "command": str(looper.get("command") or ""),
    }
    return update_configer_task_state_config("looper", updates, task_id=task_id or state.get("task_id"))


def _resolve_looper_model_config(system_config: dict[str, Any], state: dict[str, Any]) -> LooperModelConfig:
    system = _unwrap_config_dict(system_config)
    looper = state.get("looper") if isinstance(state.get("looper"), dict) else {}

    requested_model = str(_first_non_empty(
        looper.get("model_path"),
        system.get("looper_model"),
        system.get("starter_model_path"),
        system.get("starter_model_name"),
        system.get("codex_model"),
        "",
    ) or "").strip()

    model_value = system.get("model")
    has_explicit_pool = (
        isinstance(model_value, list)
        or (isinstance(model_value, dict) and isinstance(model_value.get("pool") or model_value.get("models"), list))
    )
    if has_explicit_pool:
        pool = StarterModelPool(system)
        provider = pool.resolve_proxy_provider(requested_model or None, tier=pool.default_tier)
        if provider is not None:
            return LooperModelConfig(
                model=provider.model,
                api_url=responses_url(provider.base_url),
                api_key=provider.api_key,
                wire_api="responses",
                temperature=_float_or_default(looper.get("temperature"), 0.2),
                top_p=_float_or_default(looper.get("top_p"), 0.95),
                max_tokens=_int_or_default(looper.get("max_completion_tokens"), 4096),
                source=provider.source,
            )

    base_url = str(_first_non_empty(
        looper.get("base_url"),
        system.get("codex_base_url"),
        "",
    ) or "").strip()
    api_key = str(_first_non_empty(
        looper.get("api_key"),
        system.get("codex_api_key"),
        "",
    ) or "").strip()
    if not requested_model or not base_url:
        raise RuntimeError("Looper model configuration is incomplete.")

    wire_api = normalize_wire_api(_first_non_empty(
        looper.get("wire_api"),
        system.get("codex_wire_api"),
        "chat",
    ))
    if wire_api == "auto":
        wire_api = "chat"
    api_url = responses_url(base_url) if wire_api == "responses" else chat_completions_url(base_url)
    return LooperModelConfig(
        model=requested_model,
        api_url=api_url,
        api_key=api_key,
        wire_api=wire_api,
        temperature=_float_or_default(looper.get("temperature"), 0.2),
        top_p=_float_or_default(looper.get("top_p"), 0.95),
        max_tokens=_int_or_default(looper.get("max_completion_tokens"), 4096),
        source="system",
    )


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"Looper LLM HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Looper LLM connection error: {exc.reason}") from None


def _build_chat_payload(config: LooperModelConfig, messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _build_responses_payload(config: LooperModelConfig, messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
    payload = {
        "model": config.model,
        "input": messages,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_output_tokens": config.max_tokens,
    }
    if json_mode:
        payload["text"] = {"format": {"type": "json_object"}}
    return payload


def _extract_chat_text(response: dict[str, Any]) -> str:
    return _extract_text_content(response.get("choices", [{}])[0].get("message", {}).get("content"))


def _extract_responses_text(response: dict[str, Any]) -> str:
    if response.get("output_text"):
        return str(response["output_text"])
    parts: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("text"):
                parts.append(str(content["text"]))
    return "".join(parts)


def _call_llm_text(config: LooperModelConfig, messages: list[dict[str, str]], *, json_mode: bool) -> str:
    if config.wire_api == "responses":
        response = _post_json(config.api_url, _build_responses_payload(config, messages, json_mode), config.api_key)
        text = _extract_responses_text(response)
    else:
        response = _post_json(config.api_url, _build_chat_payload(config, messages, json_mode), config.api_key)
        text = _extract_chat_text(response)
    if not text.strip():
        raise RuntimeError("Looper LLM returned empty content.")
    return text.strip()


async def _llm_text(config: LooperModelConfig, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
    return await asyncio.to_thread(_call_llm_text, config, messages, json_mode=json_mode)


async def _llm_json(config: LooperModelConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    text = await _llm_text(config, messages, json_mode=True)
    try:
        return _parse_json_object(text)
    except ValueError as exc:
        retry_messages = [
            *messages,
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    f"上一次输出解析失败：{exc}\n\n"
                    f"原始输出如下：\n{text}\n\n{LOOPER_JSON_RETRY_PROMPT}"
                ),
            },
        ]
        retry_text = await _llm_text(config, retry_messages, json_mode=True)
        try:
            return _parse_json_object(retry_text)
        except ValueError as retry_exc:
            raise ValueError(
                f"Looper JSON retry failed after invalid first response. First error: {exc}. "
                f"Retry error: {retry_exc}"
            ) from None


def _parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"No JSON object found in Looper output: {text[:200]}")
        candidate = text[start:end + 1]
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as inner_exc:
            snippet = candidate[max(0, inner_exc.pos - 80):inner_exc.pos + 80]
            raise ValueError(
                f"Looper output JSON is invalid: {inner_exc.msg} at line {inner_exc.lineno} "
                f"column {inner_exc.colno}. Snippet: {snippet}"
            ) from None
    if not isinstance(payload, dict):
        raise ValueError("Looper output JSON must be an object.")
    return payload


def parse_looper_command(command: str | dict[str, Any]) -> dict[str, Any]:
    payload = _parse_json_object(command) if isinstance(command, str) else dict(command)
    op = str(payload.get("op") or "").strip().lower()
    if op not in {"query", "stop"}:
        raise ValueError("Looper command op must be 'query' or 'stop'.")
    result = {"op": op}
    if op == "query":
        message = _extract_text_content(payload.get("message")).strip()
        if not message:
            raise ValueError("Looper query command requires a non-empty message.")
        result["message"] = message
    return result


def _fallback_history_summary(conversation: list[dict[str, Any]], session: dict[str, Any] | None) -> str:
    parts: list[str] = []
    status = str((session or {}).get("status") or "unknown")
    parts.append(f"当前 Codex 会话状态：{status}")
    if conversation:
        last_user = next((item for item in reversed(conversation) if item.get("role") == "user"), None)
        last_assistant = next((item for item in reversed(conversation) if item.get("role") == "assistant"), None)
        if last_user:
            parts.append("最近一次用户/系统推动：" + _preview_text(last_user.get("content"), 300))
        if last_assistant:
            parts.append("最近一次 Codex 输出：" + _preview_text(last_assistant.get("content"), 400))
    else:
        parts.append("尚未读取到可用的 Codex conversation。")
    return "\n".join(parts)


def _format_conversation_excerpt(conversation: list[dict[str, Any]]) -> str:
    items = conversation[-MAX_CONVERSATION_ITEMS:]
    lines: list[str] = []
    for index, item in enumerate(items, 1):
        role = item.get("role") or "unknown"
        state = item.get("state") or "completed"
        created_at = item.get("created_at") or ""
        prefix = f"[{index}] role={role} state={state}"
        if created_at:
            prefix += f" time={created_at}"
        lines.append(prefix)
        lines.append(_preview_text(item.get("content"), 1200) or "<empty>")
    return "\n".join(lines).strip()


def _latest_conversation_id(conversation: list[dict[str, Any]]) -> str:
    for item in reversed(conversation):
        conv_id = str(item.get("id") or "").strip()
        if conv_id:
            return conv_id
    return ""


def _conversation_since_last_id(
    conversation: list[dict[str, Any]],
    last_conv_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    normalized_last_id = str(last_conv_id or "").strip()
    if not normalized_last_id:
        return conversation, False

    for index, item in enumerate(conversation):
        if str(item.get("id") or "").strip() == normalized_last_id:
            return conversation[index + 1 :], True
    return conversation, False


async def summarize_codex_progress(
    *,
    config: LooperModelConfig,
    task_id: str,
    state: dict[str, Any],
    session: dict[str, Any] | None,
    conversation: list[dict[str, Any]],
    runtime_status: list[dict[str, Any]] | None,
) -> tuple[str, str]:
    current_last_conv_id = _latest_conversation_id(conversation)
    previous_summary = str((state.get("looper") or {}).get("historySummary") or "")
    previous_last_conv_id = str((state.get("looper") or {}).get("last_conv_id") or "")

    if not conversation:
        fallback = previous_summary or _fallback_history_summary(conversation, session)
        return fallback, ""

    new_conversation, found_last_conv_id = _conversation_since_last_id(conversation, previous_last_conv_id)
    if found_last_conv_id and not new_conversation and previous_summary:
        return previous_summary, current_last_conv_id

    is_incremental = found_last_conv_id and bool(previous_summary)
    conversation_for_summary = new_conversation if is_incremental else conversation

    user_prompt = json.dumps(
        _json_safe({
            "task_id": task_id,
            "summary_mode": "incremental" if is_incremental else "full",
            "previous_history_summary": previous_summary if is_incremental else "",
            "previous_last_conv_id": previous_last_conv_id if is_incremental else "",
            "current_last_conv_id": current_last_conv_id,
            "session_status": (session or {}).get("status"),
            "active_prompt": (session or {}).get("active_prompt"),
            "pending_prompts": (session or {}).get("pending_prompts"),
            "final_result": (session or {}).get("final_result"),
            "last_error": (session or {}).get("last_error"),
            "runtime_status": runtime_status or [],
            "state_snapshot": _truncate_for_prompt(state),
            "conversation_excerpt": _format_conversation_excerpt(conversation_for_summary),
        }),
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    try:
        return await _llm_text(config, messages, json_mode=False), current_last_conv_id
    except Exception:
        fallback = previous_summary if is_incremental and previous_summary else _fallback_history_summary(conversation_for_summary, session)
        return fallback, current_last_conv_id


def _build_planner_user_prompt(
    *,
    task_id: str,
    state: dict[str, Any],
    session: dict[str, Any] | None,
    conversation: list[dict[str, Any]],
    runtime_status: list[dict[str, Any]] | None,
    machine_env: dict[str, Any],
    latest_history_summary: str,
) -> str:
    payload = {
        "task_id": task_id,
        "session_status": (session or {}).get("status") or "not_started",
        "active_prompt": (session or {}).get("active_prompt"),
        "pending_prompts": (session or {}).get("pending_prompts"),
        "final_result": (session or {}).get("final_result"),
        "last_error": (session or {}).get("last_error"),
        "runtime_status": runtime_status or [],
        "previous_looper_history_summary": str((state.get("looper") or {}).get("historySummary") or ""),
        "latest_codex_history_summary": latest_history_summary,
        "state_snapshot": _truncate_for_prompt(state),
        "machine_environment": machine_env,
        "latest_conversation_excerpt": _format_conversation_excerpt(conversation),
    }
    return json.dumps(_json_safe(payload), ensure_ascii=False)


async def run_looper_once(
    *,
    task_id: str,
    state: dict[str, Any] | None,
    system_config: dict[str, Any],
    conversation: list[dict[str, Any]] | None = None,
    session: dict[str, Any] | None = None,
    runtime_status: list[dict[str, Any]] | None = None,
    output_dir: str = "./outputs",
) -> dict[str, Any]:
    state = dict(state or {})
    state["task_id"] = task_id

    persisted = load_looper_state_from_configer(task_id)
    merged_looper = dict(persisted.get("looper") or {})
    merged_looper.update(state.get("looper") or {})
    state["looper"] = merged_looper

    writer = get_event_writer("looper", task_id, log_file_path=output_dir)
    try:
        writer.set_running(
            StreamEvent(
                current="looper.status",
                message="Looper is collecting Codex context.",
                progress=0.1,
                data={"task_id": task_id},
            )
        )

        model_config = _resolve_looper_model_config(system_config, state)
        normalized_conversation = _normalize_conversation(conversation)
        machine_env = collect_machine_environment()

        writer.set_running(
            StreamEvent(
                current="looper.status",
                message="Looper is summarizing the latest Codex progress.",
                progress=0.45,
            )
        )
        history_summary, latest_conv_id = await summarize_codex_progress(
            config=model_config,
            task_id=task_id,
            state=state,
            session=session,
            conversation=normalized_conversation,
            runtime_status=runtime_status,
        )

        prompt_loader = PromptLoader()
        system_prompt = prompt_loader("system", "looper_prompt")
        planner_prompt = _build_planner_user_prompt(
            task_id=task_id,
            state=state,
            session=session,
            conversation=normalized_conversation,
            runtime_status=runtime_status,
            machine_env=machine_env,
            latest_history_summary=history_summary,
        )
        internal_messages = _trim_internal_messages(merged_looper.get("messages"))

        writer.set_running(
            StreamEvent(
                current="looper.status",
                message="Looper is deciding the next Codex action.",
                progress=0.8,
            )
        )
        planner_output = await _llm_json(
            model_config,
            [
                {"role": "system", "content": system_prompt},
                *internal_messages,
                {"role": "user", "content": planner_prompt},
            ],
        )
        command = parse_looper_command(planner_output)

        updated_messages = _trim_internal_messages(
            [
                *internal_messages,
                {"role": "user", "content": planner_prompt},
                {"role": "assistant", "content": json.dumps(planner_output, ensure_ascii=False)},
            ]
        )
        state["looper"] = {
            **merged_looper,
            "messages": updated_messages,
            "historySummary": history_summary,
            "last_conv_id": latest_conv_id,
            "command": json.dumps(command, ensure_ascii=False),
        }
        update_looper_state_via_configer(state, task_id=task_id)

        writer.set_completed(
            StreamEvent(
                current="looper.status",
                message="Looper finished one planning pass.",
                progress=1.0,
                data={
                    "command": command,
                    "historySummary": history_summary,
                    "model_source": model_config.source,
                },
            )
        )
        return state
    except Exception as exc:
        emit_error(
            exc,
            code=_looper_error_code(exc),
            recoverable=True,
            message="Looper planning pass failed.",
            stream_writer=writer,
            exit_process=False,
            print_payload=False,
        )
        raise
