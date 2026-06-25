from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

import httpx

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
    def __init__(self, system_config: dict[str, Any], history_store: ResponseProxyHistoryStore | None = None) -> None:
        self.system_config = system_config or {}
        self.history_store = history_store or response_proxy_history_store

    @classmethod
    async def create(cls) -> "ResponseProxyService":
        return cls(system_config=await load_starter_system_config())

    def _resolve_upstream(self, request_body: dict[str, Any]) -> tuple[str, str, str | None]:
        base_url = (
            request_body.get("base_url")
            or self.system_config.get("codex_chat_proxy_url")
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
        return _normalize_upstream_v1(str(base_url)), str(api_key), str(model) if model else None

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

    async def list_models(self, request_body: dict[str, Any] | None = None) -> tuple[int, bytes]:
        request_body = request_body or {}
        upstream_v1, api_key, _ = self._resolve_upstream(request_body)
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

    async def forward_responses_request(
        self,
        body: dict[str, Any],
    ) -> tuple[str, Any, int]:
        upstream_v1, api_key, default_model = self._resolve_upstream(body)
        chat_body = self.responses_to_chat_request(body, default_model=default_model)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth_headers, auth_error = self._build_auth_headers(api_key)
        if auth_error is not None:
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
                        return "error", response.content, response.status_code
                    return "json", self.chat_response_to_response(response.json()), response.status_code
            except httpx.HTTPError as exc:
                return "error", _json_dumps({
                    "error": {
                        "message": f"failed to connect upstream chat completions endpoint: {exc}",
                        "type": "upstream_connection_error",
                        "upstream_base_url": upstream_v1,
                    }
                }).encode("utf-8"), 502

        async def event_stream() -> AsyncIterator[str]:
            state = ChatToResponsesStreamState()
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
                            yield "data: [DONE]\n\n"
                        elif state.has_substantive_output():
                            state.finish_reason = "length"
                            done_events, final_response = state.finalize()
                            for event in done_events:
                                yield event
                            self.history_store.record_response(final_response)
                            yield "data: [DONE]\n\n"
                        else:
                            yield state.failed_event(
                                "Upstream Chat Completions stream ended before sending finish_reason",
                                "stream_truncated",
                            )
                            yield "data: [DONE]\n\n"
            except httpx.HTTPError as exc:
                yield state.failed_event(
                    f"failed to connect upstream chat completions endpoint: {exc}",
                    "upstream_connection_error",
                )
                yield "data: [DONE]\n\n"
            except Exception as exc:
                yield state.failed_event(
                    f"response proxy stream failed: {exc}",
                    "proxy_stream_error",
                )
                yield "data: [DONE]\n\n"

        return "stream", event_stream(), 200
