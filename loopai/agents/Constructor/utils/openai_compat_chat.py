"""
OpenAI-compatible chat completions over HTTP (no LangChain Runnable).

Used by Constructor data-cleaning paths to avoid LangGraph ``messages`` stream
propagation into Starter ``stream_message``.

httpx 默认 trust_env=True 会读取 HTTP(S)_PROXY；本机代理未启动时会导致 Connection refused。
此处对 LLM 直连使用 trust_env=False（与 DataFlow APILLMServing_request 侧 session.trust_env=False 一致）。
若必须通过环境代理访问 API，可设置环境变量 LOOPAI_HTTPX_TRUST_ENV=1 恢复 trust_env。
"""
from __future__ import annotations

import os
import types
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Union

import httpx

from loopai.logger import get_logger

logger = get_logger()

MessageInput = Any


def _httpx_trust_env() -> bool:
    v = (os.getenv("LOOPAI_HTTPX_TRUST_ENV") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class OpenAIChatParams:
    """Endpoint + generation settings (mirrors CleaningSubgraph / BaseAgent LLM fields)."""

    model: str
    base_url: str
    api_key: str
    temperature: float = 0.7
    top_p: float = 0.95
    max_completion_tokens: int = 4096


def chat_completions_url(base_url: str) -> str:
    b = (base_url or "").rstrip("/")
    if not b:
        return "/v1/chat/completions"
    if b.endswith("/v1"):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


def _message_content_to_str(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "".join(parts)
    return str(content)


def lc_messages_to_openai_payload(messages: Sequence[MessageInput]) -> List[dict]:
    """Map LangChain messages (or dicts) to OpenAI ``messages`` JSON."""
    out: List[dict] = []
    for m in messages:
        role: Optional[str] = None
        content: Any = None
        if isinstance(m, dict):
            role = m.get("role") or m.get("type")
            content = m.get("content")
            if role in ("human", "HumanMessage"):
                role = "user"
            elif role in ("ai", "AIMessage", "assistant"):
                role = "assistant"
            elif role in ("system", "SystemMessage"):
                role = "system"
        else:
            t = getattr(m, "type", None)
            content = getattr(m, "content", None)
            if t == "system":
                role = "system"
            elif t == "human":
                role = "user"
            elif t == "ai":
                role = "assistant"
            else:
                cname = m.__class__.__name__
                if cname == "SystemMessage":
                    role = "system"
                elif cname == "HumanMessage":
                    role = "user"
                elif cname == "AIMessage":
                    role = "assistant"
        if not role:
            logger.warning("Skipping unknown message type in lc_messages_to_openai_payload: %r", m)
            continue
        text = _message_content_to_str(content)
        out.append({"role": role, "content": text})
    return out


def _extract_choice_content(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = (choices[0] or {}).get("message") or {}
    raw = msg.get("content")
    return _message_content_to_str(raw)


def chat_completion_sync(
    params: OpenAIChatParams,
    messages: Sequence[MessageInput],
    *,
    timeout_seconds: float,
) -> types.SimpleNamespace:
    """Synchronous non-streaming chat completion."""
    url = chat_completions_url(params.base_url)
    payload = {
        "model": params.model,
        "messages": lc_messages_to_openai_payload(messages),
        "temperature": params.temperature,
        "top_p": params.top_p,
        "max_tokens": params.max_completion_tokens,
        "stream": False,
    }
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "Authorization": f"Bearer {params.api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout, trust_env=_httpx_trust_env()) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    return types.SimpleNamespace(content=_extract_choice_content(data))


async def chat_completion_async(
    params: OpenAIChatParams,
    messages: Sequence[MessageInput],
    *,
    timeout_seconds: float,
) -> types.SimpleNamespace:
    """Async non-streaming chat completion."""
    url = chat_completions_url(params.base_url)
    payload = {
        "model": params.model,
        "messages": lc_messages_to_openai_payload(messages),
        "temperature": params.temperature,
        "top_p": params.top_p,
        "max_tokens": params.max_completion_tokens,
        "stream": False,
    }
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "Authorization": f"Bearer {params.api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout, trust_env=_httpx_trust_env()) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    return types.SimpleNamespace(content=_extract_choice_content(data))
