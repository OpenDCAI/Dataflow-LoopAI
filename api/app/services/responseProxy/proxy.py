from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

import asyncio
import httpx

from loopai.utils.model_pool import (
    ModelPoolEntry,
    StarterModelPool,
    chat_completions_url,
    mask_secret,
    normalize_v1_base_url,
    responses_url,
)
from ..starter import load_starter_system_config


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {_json_dumps(payload)}\n\n"


def _take_sse_block(buffer: str) -> tuple[str | None, str]:
    best_pos: int | None = None
    best_len = 0
    for delimiter in ("\r\n\r\n", "\n\n"):
        pos = buffer.find(delimiter)
        if pos >= 0 and (best_pos is None or pos < best_pos):
            best_pos = pos
            best_len = len(delimiter)
    if best_pos is None:
        return None, buffer
    return buffer[:best_pos], buffer[best_pos + best_len:]


def _extract_text_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"input_text", "output_text", "text"}:
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                    continue
            for key in ("text", "content", "value", "output"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate:
                    parts.append(candidate)
                    break
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "output"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        return _json_dumps(value)
    return str(value)


def _normalize_upstream_v1(base_url: str) -> str:
    trimmed = (base_url or "").strip().rstrip("/")
    if not trimmed:
        return "https://api.deepseek.com/v1"
    if trimmed.endswith("/v1"):
        return trimmed
    return f"{trimmed}/v1"


def _response_id_from_chat_id(chat_id: str | None) -> str:
    if chat_id and chat_id.strip():
        return f"resp_{chat_id.strip()}"
    return f"resp_{uuid.uuid4().hex}"


def _response_status_from_finish_reason(finish_reason: str | None) -> str:
    if finish_reason == "length":
        return "incomplete"
    return "completed"


def _is_ascii_header_safe(value: str) -> bool:
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _chat_usage_to_response_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


class ResponseProxyHistoryStore:
    def __init__(self) -> None:
        self.responses: dict[str, dict[str, dict[str, Any]]] = {}
        self.call_index: dict[str, list[dict[str, Any]]] = {}

    def record_response(self, response: dict[str, Any]) -> int:
        response_id = str(response.get("id") or "").strip()
        output = response.get("output")
        if not response_id or not isinstance(output, list):
            return 0
        calls: dict[str, dict[str, Any]] = {}
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call":
                continue
            call_id = str(item.get("call_id") or "").strip()
            if not call_id:
                continue
            calls[call_id] = item
            self.call_index.setdefault(call_id, []).append(item)
            self.call_index[call_id] = self.call_index[call_id][-8:]
        if not calls:
            return 0
        self.responses[response_id] = calls
        if len(self.responses) > 512:
            oldest_key = next(iter(self.responses))
            self.responses.pop(oldest_key, None)
        return len(calls)

    def enrich_request(self, body: dict[str, Any]) -> int:
        previous_response_id = str(body.get("previous_response_id") or "").strip()
        input_value = body.get("input")
        if isinstance(input_value, list):
            items = list(input_value)
            was_object = False
        elif isinstance(input_value, dict):
            items = [input_value]
            was_object = True
        else:
            return 0

        previous_calls = self.responses.get(previous_response_id, {})
        existing_call_ids = {
            str(item.get("call_id") or "").strip()
            for item in items
            if isinstance(item, dict) and item.get("type") == "function_call"
        }

        inserted = 0
        new_items: list[Any] = []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                call_id = str(item.get("call_id") or "").strip()
                cached = None
                if call_id and call_id not in existing_call_ids:
                    cached = previous_calls.get(call_id)
                    if cached is None:
                        fallback = self.call_index.get(call_id) or []
                        if len(fallback) == 1:
                            cached = fallback[0]
                if cached is not None:
                    new_items.append(cached)
                    existing_call_ids.add(call_id)
                    inserted += 1
            new_items.append(item)

        if inserted:
            body["input"] = new_items[0] if was_object and len(new_items) == 1 else new_items
        return inserted


response_proxy_history_store = ResponseProxyHistoryStore()


class ResponseProxyRuntimeStore:
    def __init__(self) -> None:
        self.stats: dict[str, dict[str, Any]] = {}
        self.probes: dict[str, dict[str, Any]] = {}
        self.semaphores: dict[str, asyncio.Semaphore] = {}

    def _key(self, entry: ModelPoolEntry | None, model: str | None = None) -> str:
        if entry is not None:
            return entry.name or entry.tier or entry.model_name
        return str(model or "default")

    def semaphore_for(self, entry: ModelPoolEntry | None) -> asyncio.Semaphore:
        key = self._key(entry)
        limit = int(entry.maxworker if entry is not None else 64)
        current = self.semaphores.get(key)
        if current is None:
            current = asyncio.Semaphore(max(1, limit))
            self.semaphores[key] = current
        return current

    def stats_for(self, entry: ModelPoolEntry | None, model: str | None = None) -> dict[str, Any]:
        key = self._key(entry, model=model)
        return self.stats.setdefault(key, {
            "model": key,
            "requests": 0,
            "success": 0,
            "errors": 0,
            "stream_requests": 0,
            "total_latency_ms": 0.0,
            "last_latency_ms": None,
            "last_error": None,
            "last_status_code": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "recent": [],
        })

    def start(self, entry: ModelPoolEntry | None, *, endpoint: str, stream: bool) -> float:
        stats = self.stats_for(entry)
        stats["requests"] += 1
        if stream:
            stats["stream_requests"] += 1
        stats["recent"].append({
            "started_at": int(time.time()),
            "endpoint": endpoint,
            "stream": bool(stream),
            "status": "running",
        })
        stats["recent"] = stats["recent"][-20:]
        return time.monotonic()

    def finish(
        self,
        entry: ModelPoolEntry | None,
        started: float,
        *,
        ok: bool,
        status_code: int | None = None,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        stats = self.stats_for(entry)
        elapsed = round((time.monotonic() - started) * 1000.0, 2)
        stats["last_latency_ms"] = elapsed
        stats["total_latency_ms"] = round(float(stats.get("total_latency_ms") or 0.0) + elapsed, 2)
        stats["last_status_code"] = status_code
        if ok:
            stats["success"] += 1
            stats["last_error"] = None
        else:
            stats["errors"] += 1
            stats["last_error"] = error or "unknown error"
        usage_obj = _chat_usage_to_response_usage(usage) if usage and "prompt_tokens" in usage else usage
        if isinstance(usage_obj, dict):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                try:
                    stats["usage"][key] += int(usage_obj.get(key) or 0)
                except Exception:
                    pass
        if stats.get("recent"):
            stats["recent"][-1].update({
                "status": "success" if ok else "error",
                "latency_ms": elapsed,
                "status_code": status_code,
                "error": error,
            })

    def status_payload(self, pool: StarterModelPool) -> dict[str, Any]:
        models: list[dict[str, Any]] = []
        for entry in pool.entries:
            key = self._key(entry)
            stats = dict(self.stats_for(entry))
            requests = int(stats.get("requests") or 0)
            avg_latency = None
            if requests:
                avg_latency = round(float(stats.get("total_latency_ms") or 0.0) / requests, 2)
            stats["avg_latency_ms"] = avg_latency
            models.append({
                **entry.public_dict(),
                "proxy_model": entry.name,
                "probe": self.probes.get(key, {}),
                "stats": stats,
                "current_available_workers": self.semaphores.get(key)._value if key in self.semaphores else entry.maxworker,
            })
        return {
            "ok": True,
            "proxy_base_url": pool.proxy_base_url(),
            "proxy_api_key": mask_secret(pool.proxy_api_key()),
            "default_tier": pool.default_tier,
            "models": models,
        }


response_proxy_runtime_store = ResponseProxyRuntimeStore()


class ChatToResponsesStreamState:
    def __init__(self) -> None:
        self.started = False
        self.completed = False
        self.response_id = f"resp_{uuid.uuid4().hex}"
        self.model = ""
        self.created_at = int(time.time())
        self.text = ""
        self.reasoning = ""
        self.message_item_id = ""
        self.reasoning_item_id = ""
        self.message_output_index: int | None = None
        self.reasoning_output_index: int | None = None
        self.next_output_index = 0
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None
        self.tool_calls: dict[int, dict[str, Any]] = {}

    def _allocate_output_index(self) -> int:
        value = self.next_output_index
        self.next_output_index += 1
        return value

    def _base_response(self, status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.model,
            "output": output,
        }
        usage = _chat_usage_to_response_usage(self.usage)
        if usage is not None:
            payload["usage"] = usage
        if status == "incomplete":
            payload["incomplete_details"] = {"reason": "max_output_tokens"}
        return payload

    def has_substantive_output(self) -> bool:
        if self.text.strip() or self.reasoning.strip():
            return True
        for state in self.tool_calls.values():
            if state.get("added") or state.get("call_id") or state.get("name") or state.get("arguments"):
                return True
        return False

    def failed_event(self, message: str, error_type: str | None = None) -> str:
        self.completed = True
        response = self._base_response("failed", self.output_items())
        error_payload: dict[str, Any] = {"message": message}
        if error_type:
            error_payload["type"] = error_type
        response["error"] = error_payload
        return _sse("response.failed", {
            "type": "response.failed",
            "response": response,
        })

    def ensure_started(self) -> list[str]:
        if self.started:
            return []
        self.started = True
        response = self._base_response("in_progress", [])
        return [
            _sse("response.created", {"type": "response.created", "response": response}),
            _sse("response.in_progress", {"type": "response.in_progress", "response": response}),
        ]

    def _reasoning_item(self) -> dict[str, Any]:
        return {
            "id": self.reasoning_item_id,
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": self.reasoning}],
        }

    def _message_item(self) -> dict[str, Any]:
        return {
            "id": self.message_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": self.text, "annotations": []}],
        }

    def _tool_item(self, state: dict[str, Any], status: str = "completed") -> dict[str, Any]:
        return {
            "id": state["item_id"],
            "type": "function_call",
            "call_id": state["call_id"],
            "name": state["name"],
            "arguments": state["arguments"],
            "status": status,
        }

    def handle_chunk(self, chunk: dict[str, Any]) -> list[str]:
        chat_id = chunk.get("id")
        if isinstance(chat_id, str) and chat_id.strip():
            self.response_id = _response_id_from_chat_id(chat_id)
        model = chunk.get("model")
        if isinstance(model, str) and model.strip():
            self.model = model
        created = chunk.get("created")
        if isinstance(created, int):
            self.created_at = created
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
        events = self.ensure_started()

        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return events
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}

        reasoning_delta = delta.get("reasoning_content")
        if isinstance(reasoning_delta, str) and reasoning_delta:
            if self.reasoning_output_index is None:
                self.reasoning_output_index = self._allocate_output_index()
                self.reasoning_item_id = f"{self.response_id}_reasoning"
                events.append(_sse("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": self.reasoning_output_index,
                    "item": {
                        "id": self.reasoning_item_id,
                        "type": "reasoning",
                        "status": "in_progress",
                        "summary": [],
                    },
                }))
                events.append(_sse("response.reasoning_summary_part.added", {
                    "type": "response.reasoning_summary_part.added",
                    "item_id": self.reasoning_item_id,
                    "output_index": self.reasoning_output_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                }))
            self.reasoning += reasoning_delta
            events.append(_sse("response.reasoning_summary_text.delta", {
                "type": "response.reasoning_summary_text.delta",
                "item_id": self.reasoning_item_id,
                "output_index": self.reasoning_output_index,
                "summary_index": 0,
                "delta": reasoning_delta,
            }))

        content_delta = delta.get("content")
        if isinstance(content_delta, str) and content_delta:
            if self.message_output_index is None:
                self.message_output_index = self._allocate_output_index()
                self.message_item_id = f"{self.response_id}_msg"
                events.append(_sse("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": self.message_output_index,
                    "item": {
                        "id": self.message_item_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }))
                events.append(_sse("response.content_part.added", {
                    "type": "response.content_part.added",
                    "item_id": self.message_item_id,
                    "output_index": self.message_output_index,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }))
            self.text += content_delta
            events.append(_sse("response.output_text.delta", {
                "type": "response.output_text.delta",
                "item_id": self.message_item_id,
                "output_index": self.message_output_index,
                "content_index": 0,
                "delta": content_delta,
            }))

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                index = int(tool_call.get("index") or 0)
                state = self.tool_calls.setdefault(index, {
                    "output_index": self._allocate_output_index(),
                    "item_id": "",
                    "call_id": "",
                    "name": "",
                    "arguments": "",
                    "added": False,
                })
                call_id = tool_call.get("id")
                if isinstance(call_id, str) and call_id:
                    state["call_id"] = call_id
                function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                name = function.get("name")
                if isinstance(name, str) and name:
                    state["name"] = name
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments:
                    state["arguments"] += arguments
                if not state["added"] and (state["call_id"] or state["name"]):
                    state["added"] = True
                    if not state["call_id"]:
                        state["call_id"] = f"call_{index}"
                    if not state["name"]:
                        state["name"] = f"tool_{index}"
                    state["item_id"] = f"fc_{state['call_id']}"
                    events.append(_sse("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": state["output_index"],
                        "item": self._tool_item(state, status="in_progress"),
                    }))
                    if state["arguments"]:
                        events.append(_sse("response.function_call_arguments.delta", {
                            "type": "response.function_call_arguments.delta",
                            "item_id": state["item_id"],
                            "output_index": state["output_index"],
                            "delta": state["arguments"],
                        }))
                elif isinstance(arguments, str) and arguments:
                    events.append(_sse("response.function_call_arguments.delta", {
                        "type": "response.function_call_arguments.delta",
                        "item_id": state["item_id"],
                        "output_index": state["output_index"],
                        "delta": arguments,
                    }))

        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            self.finish_reason = finish_reason
        return events

    def finalize(self) -> tuple[list[str], dict[str, Any]]:
        if self.completed:
            response = self._base_response(_response_status_from_finish_reason(self.finish_reason), self.output_items())
            return [], response

        events = self.ensure_started()
        if self.reasoning_output_index is not None:
            events.append(_sse("response.reasoning_summary_text.done", {
                "type": "response.reasoning_summary_text.done",
                "item_id": self.reasoning_item_id,
                "output_index": self.reasoning_output_index,
                "summary_index": 0,
                "text": self.reasoning,
            }))
            events.append(_sse("response.reasoning_summary_part.done", {
                "type": "response.reasoning_summary_part.done",
                "item_id": self.reasoning_item_id,
                "output_index": self.reasoning_output_index,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": self.reasoning},
            }))
            events.append(_sse("response.output_item.done", {
                "type": "response.output_item.done",
                "output_index": self.reasoning_output_index,
                "item": self._reasoning_item(),
            }))

        if self.message_output_index is not None:
            events.append(_sse("response.output_text.done", {
                "type": "response.output_text.done",
                "item_id": self.message_item_id,
                "output_index": self.message_output_index,
                "content_index": 0,
                "text": self.text,
            }))
            events.append(_sse("response.content_part.done", {
                "type": "response.content_part.done",
                "item_id": self.message_item_id,
                "output_index": self.message_output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": self.text, "annotations": []},
            }))
            events.append(_sse("response.output_item.done", {
                "type": "response.output_item.done",
                "output_index": self.message_output_index,
                "item": self._message_item(),
            }))

        for state in self.tool_calls.values():
            if not state.get("added"):
                continue
            events.append(_sse("response.function_call_arguments.done", {
                "type": "response.function_call_arguments.done",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "arguments": state["arguments"],
            }))
            events.append(_sse("response.output_item.done", {
                "type": "response.output_item.done",
                "output_index": state["output_index"],
                "item": self._tool_item(state),
            }))

        response = self._base_response(
            _response_status_from_finish_reason(self.finish_reason),
            self.output_items(),
        )
        events.append(_sse("response.completed", {
            "type": "response.completed",
            "response": response,
        }))
        self.completed = True
        return events, response

    def output_items(self) -> list[dict[str, Any]]:
        items: list[tuple[int, dict[str, Any]]] = []
        if self.reasoning_output_index is not None:
            items.append((self.reasoning_output_index, self._reasoning_item()))
        if self.message_output_index is not None:
            items.append((self.message_output_index, self._message_item()))
        for state in self.tool_calls.values():
            if state.get("added"):
                items.append((state["output_index"], self._tool_item(state)))
        items.sort(key=lambda item: item[0])
        return [item for _, item in items]


class ResponseProxyService:
    def __init__(
        self,
        system_config: dict[str, Any],
        history_store: ResponseProxyHistoryStore | None = None,
        runtime_store: ResponseProxyRuntimeStore | None = None,
    ) -> None:
        self.system_config = system_config or {}
        self.model_pool = StarterModelPool(self.system_config)
        self.history_store = history_store or response_proxy_history_store
        self.runtime_store = runtime_store or response_proxy_runtime_store

    @classmethod
    async def create(cls) -> "ResponseProxyService":
        return cls(system_config=await load_starter_system_config())

    def _resolve_pool_entry(self, request_body: dict[str, Any]) -> ModelPoolEntry | None:
        requested = str(request_body.get("model") or "").strip()
        return self.model_pool.find_entry(requested or None)

    def _resolve_upstream(self, request_body: dict[str, Any]) -> tuple[str, str, str | None, ModelPoolEntry | None]:
        entry = self._resolve_pool_entry(request_body)
        if entry is not None:
            return (
                normalize_v1_base_url(entry.base_url),
                entry.resolved_api_key(),
                entry.model_name,
                entry,
            )
        base_url = (
            request_body.get("base_url")
            or self.system_config.get("codex_chat_proxy_url")
            or self.system_config.get("codex_base_url")
            or self.system_config.get("base_url")
            or "https://api.deepseek.com"
        )
        api_key = (
            request_body.get("api_key")
            or self.system_config.get("codex_api_key")
            or self.system_config.get("api_key")
            or ""
        )
        model = (
            request_body.get("model")
            or self.system_config.get("codex_model")
            or self.system_config.get("model")
        )
        return _normalize_upstream_v1(str(base_url)), str(api_key), str(model) if model else None, None

    def _resolve_wire_api(self, request_body: dict[str, Any], entry: ModelPoolEntry | None = None) -> str:
        configured = str(
            request_body.get("wire_api")
            or (entry.wire_api if entry is not None else None)
            or self.system_config.get("codex_wire_api")
            or "chat"
        ).strip().lower()
        if configured == "auto" and entry is not None:
            probe = self.runtime_store.probes.get(entry.name) or {}
            selected = probe.get("selected_wire_api")
            if selected in {"responses", "chat"}:
                return str(selected)
            if probe.get("responses", {}).get("ok"):
                return "responses"
            if probe.get("chat", {}).get("ok"):
                return "chat"
        if configured in {"response", "responses"}:
            return "responses"
        return "chat"

    def _build_auth_headers(self, api_key: str) -> tuple[dict[str, str], dict[str, Any] | None]:
        headers: dict[str, str] = {}
        normalized_key = str(api_key or "")
        if not normalized_key:
            return headers, None
        if not _is_ascii_header_safe(normalized_key):
            return {}, {
                "error": {
                    "message": "api_key contains non-ASCII characters and cannot be sent in HTTP Authorization headers",
                    "type": "invalid_api_key",
                }
            }
        headers["Authorization"] = f"Bearer {normalized_key}"
        return headers, None

    def _tool_choice_to_chat(self, value: Any) -> Any:
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and value.get("type") == "function":
            return {"type": "function", "function": {"name": value.get("name")}}
        return value

    def _response_tools_to_chat_tools(self, tools: Any) -> list[dict[str, Any]]:
        if not isinstance(tools, list):
            return []
        chat_tools: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            chat_tools.append({
                "type": "function",
                "function": {
                    "name": name.strip(),
                    "description": tool.get("description") or "",
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            })
        return chat_tools

    def _collapse_system_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system_parts: list[str] = []
        rest: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "developer":
                message = dict(message)
                message["role"] = "system"
                role = "system"
            if role == "system":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    system_parts.append(content)
                    continue
            rest.append(message)
        if system_parts:
            return [{"role": "system", "content": "\n\n".join(system_parts)}] + rest
        return rest

    def _append_input_item(
        self,
        item: Any,
        messages: list[dict[str, Any]],
        pending_tool_calls: list[dict[str, Any]],
        pending_reasoning: list[str],
    ) -> None:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            return
        if not isinstance(item, dict):
            return

        item_type = item.get("type")
        if item_type == "function_call":
            name = str(item.get("name") or "").strip()
            call_id = str(item.get("call_id") or "").strip() or f"call_{uuid.uuid4().hex[:8]}"
            arguments = item.get("arguments")
            if isinstance(arguments, dict):
                arguments = _json_dumps(arguments)
            elif arguments is None:
                arguments = ""
            else:
                arguments = str(arguments)
            pending_tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name or "unknown_tool",
                    "arguments": arguments,
                },
            })
            reasoning = item.get("reasoning")
            if isinstance(reasoning, dict):
                for summary in reasoning.get("summary") or []:
                    if isinstance(summary, dict):
                        text = summary.get("text")
                        if isinstance(text, str) and text.strip():
                            pending_reasoning.append(text.strip())
            return

        if item_type == "function_call_output":
            if pending_tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": "\n".join(part for part in pending_reasoning if part),
                    "tool_calls": pending_tool_calls.copy(),
                })
                pending_tool_calls.clear()
                pending_reasoning.clear()
            messages.append({
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or "").strip(),
                "content": _extract_text_content(item.get("output") or item.get("content") or ""),
            })
            return

        if item_type == "reasoning":
            for summary in item.get("summary") or []:
                if isinstance(summary, dict):
                    text = summary.get("text")
                    if isinstance(text, str) and text.strip():
                        pending_reasoning.append(text.strip())
            return

        role = str(item.get("role") or item.get("type") or "user")
        if role in {"input_text", "message"}:
            role = str(item.get("role") or "user")
        if role == "developer":
            role = "system"
        if role in {"assistant_message", "agent_message"}:
            role = "assistant"
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        content = _extract_text_content(item.get("content") if "content" in item else item)
        if role == "assistant" and pending_tool_calls:
            messages.append({
                "role": "assistant",
                "content": "\n".join(part for part in pending_reasoning if part),
                "tool_calls": pending_tool_calls.copy(),
            })
            pending_tool_calls.clear()
            pending_reasoning.clear()
        if content:
            messages.append({"role": role, "content": content})

    def responses_to_chat_request(self, body: dict[str, Any], default_model: str | None = None) -> dict[str, Any]:
        self.history_store.enrich_request(body)
        chat_body: dict[str, Any] = {}
        resolved_model = body.get("model") or default_model
        if resolved_model:
            chat_body["model"] = resolved_model

        messages: list[dict[str, Any]] = []
        instructions = body.get("instructions")
        if instructions is not None:
            text = _extract_text_content(instructions)
            if text:
                messages.append({"role": "system", "content": text})

        input_value = body.get("input")
        pending_tool_calls: list[dict[str, Any]] = []
        pending_reasoning: list[str] = []
        if isinstance(input_value, list):
            for item in input_value:
                self._append_input_item(item, messages, pending_tool_calls, pending_reasoning)
        elif input_value is not None:
            self._append_input_item(input_value, messages, pending_tool_calls, pending_reasoning)
        if pending_tool_calls:
            messages.append({
                "role": "assistant",
                "content": "\n".join(part for part in pending_reasoning if part),
                "tool_calls": pending_tool_calls,
            })

        chat_body["messages"] = self._collapse_system_messages(messages)

        for key in ("temperature", "top_p", "stream", "presence_penalty", "frequency_penalty", "seed", "user"):
            if key in body:
                chat_body[key] = body[key]
        if "max_output_tokens" in body:
            chat_body["max_tokens"] = body["max_output_tokens"]
        elif "max_tokens" in body:
            chat_body["max_tokens"] = body["max_tokens"]

        tools = self._response_tools_to_chat_tools(body.get("tools"))
        if tools:
            chat_body["tools"] = tools
            if "tool_choice" in body:
                chat_body["tool_choice"] = self._tool_choice_to_chat(body["tool_choice"])

        return chat_body

    def chat_to_responses_request(self, body: dict[str, Any], default_model: str | None = None) -> dict[str, Any]:
        responses_body: dict[str, Any] = {}
        resolved_model = body.get("model") or default_model
        if resolved_model:
            responses_body["model"] = resolved_model

        messages = body.get("messages")
        input_items: list[dict[str, Any]] = []
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "user")
                content = message.get("content")
                if role == "system" and not responses_body.get("instructions"):
                    responses_body["instructions"] = _extract_text_content(content)
                    continue
                if role == "tool":
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": str(message.get("tool_call_id") or ""),
                        "output": _extract_text_content(content),
                    })
                    continue
                item_role = role if role in {"user", "assistant", "developer"} else "user"
                input_items.append({
                    "role": item_role,
                    "content": _extract_text_content(content),
                })
        if input_items:
            responses_body["input"] = input_items
        else:
            responses_body["input"] = _extract_text_content(body.get("input") or "")

        for key in ("temperature", "top_p", "stream", "presence_penalty", "frequency_penalty", "seed", "user"):
            if key in body:
                responses_body[key] = body[key]
        if "max_tokens" in body:
            responses_body["max_output_tokens"] = body["max_tokens"]
        elif "max_completion_tokens" in body:
            responses_body["max_output_tokens"] = body["max_completion_tokens"]

        tools = body.get("tools")
        if isinstance(tools, list):
            response_tools = []
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                    function = tool["function"]
                    response_tools.append({
                        "type": "function",
                        "name": function.get("name"),
                        "description": function.get("description") or "",
                        "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                    })
            if response_tools:
                responses_body["tools"] = response_tools
        return responses_body

    def chat_response_to_response(self, chat_response: dict[str, Any]) -> dict[str, Any]:
        response_id = _response_id_from_chat_id(chat_response.get("id"))
        created_at = int(chat_response.get("created") or time.time())
        model = str(chat_response.get("model") or "")
        choices = chat_response.get("choices") if isinstance(chat_response.get("choices"), list) else []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        finish_reason = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
        output: list[dict[str, Any]] = []

        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            output.append({
                "id": f"{response_id}_reasoning",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": reasoning_content}],
            })

        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            output.append({
                "id": f"fc_{tool_call.get('id') or uuid.uuid4().hex[:8]}",
                "type": "function_call",
                "call_id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "name": function.get("name") or "unknown_tool",
                "arguments": function.get("arguments") or "",
                "status": "completed",
            })

        content = message.get("content")
        if isinstance(content, str) and content:
            output.append({
                "id": f"{response_id}_msg",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            })

        payload: dict[str, Any] = {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "status": _response_status_from_finish_reason(finish_reason),
            "model": model,
            "output": output,
        }
        usage = _chat_usage_to_response_usage(chat_response.get("usage"))
        if usage is not None:
            payload["usage"] = usage
        if payload["status"] == "incomplete":
            payload["incomplete_details"] = {"reason": "max_output_tokens"}
        self.history_store.record_response(payload)
        return payload

    def response_to_chat_response(self, response_payload: dict[str, Any]) -> dict[str, Any]:
        response_id = str(response_payload.get("id") or f"resp_{uuid.uuid4().hex}")
        created_at = int(response_payload.get("created_at") or time.time())
        model = str(response_payload.get("model") or "")
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in response_payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                for content in item.get("content") or []:
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        content_parts.append(content["text"])
            elif item_type == "function_call":
                tool_calls.append({
                    "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "unknown_tool",
                        "arguments": item.get("arguments") or "",
                    },
                })
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        status = response_payload.get("status")
        finish_reason = "length" if status == "incomplete" else "stop"
        usage = response_payload.get("usage") if isinstance(response_payload.get("usage"), dict) else {}
        return {
            "id": response_id.replace("resp_", "chatcmpl_", 1),
            "object": "chat.completion",
            "created": created_at,
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": int(usage.get("input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
        }

    async def list_models(self, request_body: dict[str, Any] | None = None) -> tuple[int, bytes]:
        request_body = request_body or {}
        if self.model_pool.entries and not request_body.get("upstream"):
            payload = {
                "object": "list",
                "data": [
                    {
                        "id": entry.name,
                        "object": "model",
                        "owned_by": "loopai-response-proxy",
                        "tier": entry.tier,
                        "upstream_model": entry.model_name,
                    }
                    for entry in self.model_pool.entries
                    if entry.enabled
                ],
            }
            return 200, _json_dumps(payload).encode("utf-8")
        upstream_v1, api_key, _, _ = self._resolve_upstream(request_body)
        headers = {"Accept": "application/json"}
        auth_headers, auth_error = self._build_auth_headers(api_key)
        if auth_error is not None:
            return 400, _json_dumps(auth_error).encode("utf-8")
        headers.update(auth_headers)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{upstream_v1}/models", headers=headers)
                return response.status_code, response.content
        except httpx.HTTPError as exc:
            payload = {
                "error": {
                    "message": f"failed to connect upstream models endpoint: {exc}",
                    "type": "upstream_connection_error",
                    "upstream_base_url": upstream_v1,
                }
            }
            return 502, _json_dumps(payload).encode("utf-8")

    async def _forward_responses_api_request(
        self,
        body: dict[str, Any],
        upstream_v1: str,
        api_key: str,
    ) -> tuple[str, Any, int]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth_headers, auth_error = self._build_auth_headers(api_key)
        if auth_error is not None:
            return "error", _json_dumps(auth_error).encode("utf-8"), 400
        headers.update(auth_headers)

        if not bool(body.get("stream")):
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    response = await client.post(
                        f"{upstream_v1}/responses",
                        headers=headers,
                        json=body,
                    )
                    if response.status_code >= 400:
                        return "error", response.content, response.status_code
                    return "json", response.json(), response.status_code
            except httpx.HTTPError as exc:
                return "error", _json_dumps({
                    "error": {
                        "message": f"failed to connect upstream responses endpoint: {exc}",
                        "type": "upstream_connection_error",
                        "upstream_base_url": upstream_v1,
                    }
                }).encode("utf-8"), 502

        client = httpx.AsyncClient(timeout=None)
        try:
            request = client.build_request(
                "POST",
                f"{upstream_v1}/responses",
                headers=headers,
                json=body,
            )
            response = await client.send(request, stream=True)
            status_code = response.status_code
            if status_code >= 400:
                content = await response.aread()
                await response.aclose()
                await client.aclose()
                return "error", content, status_code
        except httpx.HTTPError as exc:
            await client.aclose()
            return "error", _json_dumps({
                "error": {
                    "message": f"failed to connect upstream responses stream endpoint: {exc}",
                    "type": "upstream_connection_error",
                    "upstream_base_url": upstream_v1,
                }
            }).encode("utf-8"), 502

        async def stream_generator() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return "stream", stream_generator(), status_code

    async def forward_responses_request(
        self,
        body: dict[str, Any],
    ) -> tuple[str, Any, int]:
        upstream_v1, api_key, default_model, entry = self._resolve_upstream(body)
        started = self.runtime_store.start(entry, endpoint="responses", stream=bool(body.get("stream")))
        semaphore = self.runtime_store.semaphore_for(entry)
        body = dict(body)
        if default_model:
            body["model"] = default_model
        async with semaphore:
            if self._resolve_wire_api(body, entry) == "responses":
                mode, payload, status_code = await self._forward_responses_api_request(body, upstream_v1, api_key)
                usage = payload.get("usage") if isinstance(payload, dict) else None
                self.runtime_store.finish(
                    entry,
                    started,
                    ok=status_code < 400 and mode != "error",
                    status_code=status_code,
                    error=payload.decode("utf-8", errors="ignore")[:300] if mode == "error" and isinstance(payload, bytes) else None,
                    usage=usage,
                )
                return mode, payload, status_code

            chat_body = self.responses_to_chat_request(body, default_model=default_model)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth_headers, auth_error = self._build_auth_headers(api_key)
        if auth_error is not None:
            self.runtime_store.finish(entry, started, ok=False, status_code=400, error=auth_error["error"]["message"])
            return "error", _json_dumps(auth_error).encode("utf-8"), 400
        headers.update(auth_headers)

        if not bool(chat_body.get("stream")):
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    response = await client.post(
                        f"{upstream_v1}/chat/completions",
                        headers=headers,
                        json=chat_body,
                    )
                    if response.status_code >= 400:
                        self.runtime_store.finish(entry, started, ok=False, status_code=response.status_code, error=response.text[:300])
                        return "error", response.content, response.status_code
                    chat_json = response.json()
                    payload = self.chat_response_to_response(chat_json)
                    self.runtime_store.finish(entry, started, ok=True, status_code=response.status_code, usage=payload.get("usage"))
                    return "json", payload, response.status_code
            except httpx.HTTPError as exc:
                self.runtime_store.finish(entry, started, ok=False, status_code=502, error=str(exc))
                return "error", _json_dumps({
                    "error": {
                        "message": f"failed to connect upstream chat completions endpoint: {exc}",
                        "type": "upstream_connection_error",
                        "upstream_base_url": upstream_v1,
                    }
                }).encode("utf-8"), 502

        async def event_stream() -> AsyncIterator[str]:
            state = ChatToResponsesStreamState()
            final_ok = False
            final_error: str | None = None
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "POST",
                        f"{upstream_v1}/chat/completions",
                        headers=headers,
                        json=chat_body,
                    ) as stream_response:
                        if stream_response.status_code >= 400:
                            error_body = await stream_response.aread()
                            final_error = error_body.decode("utf-8", errors="ignore")[:300]
                            yield state.failed_event(
                                error_body.decode("utf-8", errors="ignore") or "upstream error",
                                "upstream_http_error",
                            )
                            return

                        buffer = ""
                        async for text in stream_response.aiter_text():
                            buffer += text
                        while True:
                            block, buffer = _take_sse_block(buffer)
                            if block is None:
                                break
                            data_lines = []
                            for line in block.splitlines():
                                if line.startswith("data:"):
                                    data_lines.append(line[5:].strip())
                                if not data_lines:
                                    continue
                                data = "\n".join(data_lines)
                                if data == "[DONE]":
                                    done_events, final_response = state.finalize()
                                    for event in done_events:
                                        yield event
                                    self.history_store.record_response(final_response)
                                    self.runtime_store.finish(entry, started, ok=True, status_code=stream_response.status_code, usage=final_response.get("usage"))
                                    final_ok = True
                                    yield "data: [DONE]\n\n"
                                    return
                                try:
                                    chunk = json.loads(data)
                                except json.JSONDecodeError:
                                    continue
                                for event in state.handle_chunk(chunk):
                                    yield event

                        if state.completed or state.finish_reason is not None:
                            done_events, final_response = state.finalize()
                            for event in done_events:
                                yield event
                            self.history_store.record_response(final_response)
                            self.runtime_store.finish(entry, started, ok=True, status_code=stream_response.status_code, usage=final_response.get("usage"))
                            final_ok = True
                            yield "data: [DONE]\n\n"
                        elif state.has_substantive_output():
                            state.finish_reason = "length"
                            done_events, final_response = state.finalize()
                            for event in done_events:
                                yield event
                            self.history_store.record_response(final_response)
                            self.runtime_store.finish(entry, started, ok=True, status_code=stream_response.status_code, usage=final_response.get("usage"))
                            final_ok = True
                            yield "data: [DONE]\n\n"
                        else:
                            final_error = "Upstream Chat Completions stream ended before sending finish_reason"
                            yield state.failed_event(
                                "Upstream Chat Completions stream ended before sending finish_reason",
                                "stream_truncated",
                            )
                            yield "data: [DONE]\n\n"
            except httpx.HTTPError as exc:
                final_error = str(exc)
                yield state.failed_event(
                    f"failed to connect upstream chat completions endpoint: {exc}",
                    "upstream_connection_error",
                )
                yield "data: [DONE]\n\n"
            except Exception as exc:
                final_error = str(exc)
                yield state.failed_event(
                    f"response proxy stream failed: {exc}",
                    "proxy_stream_error",
                )
                yield "data: [DONE]\n\n"
            finally:
                if not final_ok:
                    self.runtime_store.finish(entry, started, ok=False, status_code=502, error=final_error)

        return "stream", event_stream(), 200

    async def forward_chat_request(self, body: dict[str, Any]) -> tuple[str, Any, int]:
        upstream_v1, api_key, default_model, entry = self._resolve_upstream(body)
        started = self.runtime_store.start(entry, endpoint="chat/completions", stream=bool(body.get("stream")))
        semaphore = self.runtime_store.semaphore_for(entry)
        body = dict(body)
        if default_model:
            body["model"] = default_model
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth_headers, auth_error = self._build_auth_headers(api_key)
        if auth_error is not None:
            self.runtime_store.finish(entry, started, ok=False, status_code=400, error=auth_error["error"]["message"])
            return "error", _json_dumps(auth_error).encode("utf-8"), 400
        headers.update(auth_headers)

        async with semaphore:
            if self._resolve_wire_api(body, entry) == "chat":
                if not bool(body.get("stream")):
                    try:
                        async with httpx.AsyncClient(timeout=None) as client:
                            response = await client.post(
                                f"{upstream_v1}/chat/completions",
                                headers=headers,
                                json=body,
                            )
                            if response.status_code >= 400:
                                self.runtime_store.finish(entry, started, ok=False, status_code=response.status_code, error=response.text[:300])
                                return "error", response.content, response.status_code
                            payload = response.json()
                            self.runtime_store.finish(entry, started, ok=True, status_code=response.status_code, usage=payload.get("usage"))
                            return "json", payload, response.status_code
                    except httpx.HTTPError as exc:
                        self.runtime_store.finish(entry, started, ok=False, status_code=502, error=str(exc))
                        return "error", _json_dumps({
                            "error": {
                                "message": f"failed to connect upstream chat completions endpoint: {exc}",
                                "type": "upstream_connection_error",
                                "upstream_base_url": upstream_v1,
                            }
                        }).encode("utf-8"), 502

                client = httpx.AsyncClient(timeout=None)
                try:
                    request = client.build_request(
                        "POST",
                        f"{upstream_v1}/chat/completions",
                        headers=headers,
                        json=body,
                    )
                    response = await client.send(request, stream=True)
                    status_code = response.status_code
                    if status_code >= 400:
                        content = await response.aread()
                        await response.aclose()
                        await client.aclose()
                        self.runtime_store.finish(entry, started, ok=False, status_code=status_code, error=content.decode("utf-8", errors="ignore")[:300])
                        return "error", content, status_code
                except httpx.HTTPError as exc:
                    await client.aclose()
                    self.runtime_store.finish(entry, started, ok=False, status_code=502, error=str(exc))
                    return "error", _json_dumps({
                        "error": {
                            "message": f"failed to connect upstream chat completions stream endpoint: {exc}",
                            "type": "upstream_connection_error",
                            "upstream_base_url": upstream_v1,
                        }
                    }).encode("utf-8"), 502

                async def stream_generator() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
                        self.runtime_store.finish(entry, started, ok=True, status_code=status_code)
                    except Exception as exc:
                        self.runtime_store.finish(entry, started, ok=False, status_code=502, error=str(exc))
                        raise
                    finally:
                        await response.aclose()
                        await client.aclose()

                return "stream", stream_generator(), status_code

            responses_body = self.chat_to_responses_request(body, default_model=default_model)
            mode, payload, status_code = await self._forward_responses_api_request(responses_body, upstream_v1, api_key)
            if mode == "json":
                chat_payload = self.response_to_chat_response(payload)
                self.runtime_store.finish(entry, started, ok=True, status_code=status_code, usage=chat_payload.get("usage"))
                return "json", chat_payload, status_code
            if mode == "error":
                self.runtime_store.finish(
                    entry,
                    started,
                    ok=False,
                    status_code=status_code,
                    error=payload.decode("utf-8", errors="ignore")[:300] if isinstance(payload, bytes) else None,
                )
            return mode, payload, status_code

    async def probe_pool(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        async with httpx.AsyncClient(timeout=20.0) as client:
            for entry in self.model_pool.entries:
                cached = self.runtime_store.probes.get(entry.name)
                if cached and not force and now - float(cached.get("checked_at") or 0) < 60:
                    continue
                headers = {"Content-Type": "application/json", "Accept": "application/json"}
                auth_headers, auth_error = self._build_auth_headers(entry.resolved_api_key())
                headers.update(auth_headers)
                probe_payload: dict[str, Any] = {
                    "checked_at": now,
                    "responses": {"ok": False},
                    "chat": {"ok": False},
                    "selected_wire_api": None,
                }
                if auth_error is not None:
                    probe_payload["error"] = auth_error["error"]["message"]
                    self.runtime_store.probes[entry.name] = probe_payload
                    continue
                chat_body = {
                    "model": entry.model_name,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "stream": False,
                }
                responses_body = {
                    "model": entry.model_name,
                    "input": "ping",
                    "max_output_tokens": 1,
                    "stream": False,
                }
                for key, url, body in (
                    ("chat", chat_completions_url(entry.base_url), chat_body),
                    ("responses", responses_url(entry.base_url), responses_body),
                ):
                    started = time.monotonic()
                    try:
                        response = await client.post(url, headers=headers, json=body)
                        probe_payload[key] = {
                            "ok": response.status_code < 400,
                            "status_code": response.status_code,
                            "latency_ms": round((time.monotonic() - started) * 1000.0, 2),
                            "error": response.text[:300] if response.status_code >= 400 else None,
                        }
                    except Exception as exc:
                        probe_payload[key] = {
                            "ok": False,
                            "latency_ms": round((time.monotonic() - started) * 1000.0, 2),
                            "error": str(exc),
                        }
                if entry.wire_api in {"chat", "responses"} and probe_payload.get(entry.wire_api, {}).get("ok"):
                    probe_payload["selected_wire_api"] = entry.wire_api
                elif probe_payload["responses"].get("ok"):
                    probe_payload["selected_wire_api"] = "responses"
                elif probe_payload["chat"].get("ok"):
                    probe_payload["selected_wire_api"] = "chat"
                self.runtime_store.probes[entry.name] = probe_payload
        return self.runtime_store.status_payload(self.model_pool)

    def pool_status(self) -> dict[str, Any]:
        return self.runtime_store.status_payload(self.model_pool)
