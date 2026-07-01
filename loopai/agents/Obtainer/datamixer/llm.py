"""Minimal, dependency-free LLM client supporting two wire formats.

Selected per-model via ``ModelSpec.response_format``:

* ``openaichat`` -- OpenAI **Chat Completions** shape: POST ``{messages: [...]}``
  to the chat-completions URL; read ``choices[0].message.content``.
* ``response``   -- OpenAI **Responses API** shape: POST ``{input: [...]}`` to the
  responses URL; read ``output_text`` (or assemble it from ``output[].content``).

The same ``messages`` list (``[{role, content}, ...]``) is accepted for both, so
an operator is written once and the format is a pure config switch.

Tests and offline runs override :data:`complete` (e.g. monkeypatch) so no network
is required.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .models import ModelSpec


def _post(url: str, payload: dict, api_key: str, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM connection error: {e.reason}") from None


def _chat_payload(spec: ModelSpec, messages, json_mode: bool) -> dict:
    p = {
        "model": spec.model,
        "messages": messages,
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
        "top_p": spec.top_p,
        **spec.extra,
    }
    if json_mode:
        p["response_format"] = {"type": "json_object"}
    return p


def _responses_payload(spec: ModelSpec, messages, json_mode: bool) -> dict:
    p = {
        "model": spec.model,
        "input": messages,
        "temperature": spec.temperature,
        "max_output_tokens": spec.max_tokens,
        "top_p": spec.top_p,
        **spec.extra,
    }
    if json_mode:
        p["text"] = {"format": {"type": "json_object"}}
    return p


def _extract_chat(resp: dict) -> str:
    return resp["choices"][0]["message"]["content"]


def _extract_responses(resp: dict) -> str:
    if resp.get("output_text"):
        return resp["output_text"]
    parts = []
    for item in resp.get("output", []):
        for c in item.get("content", []):
            if isinstance(c, dict) and c.get("text"):
                parts.append(c["text"])
    if parts:
        return "".join(parts)
    raise RuntimeError(f"could not parse responses output: {str(resp)[:200]}")


def _call(spec: ModelSpec, messages, json_mode: bool) -> str:
    if spec.response_format == "openaichat":
        resp = _post(spec.api_url, _chat_payload(spec, messages, json_mode),
                     spec.resolved_key(), spec.timeout)
        return _extract_chat(resp)
    if spec.response_format == "response":
        resp = _post(spec.api_url, _responses_payload(spec, messages, json_mode),
                     spec.resolved_key(), spec.timeout)
        return _extract_responses(resp)
    raise ValueError(f"unknown response_format: {spec.response_format!r}")


def _retryable(msg: str) -> bool:
    return ("connection error" in msg or "timed out" in msg
            or "HTTP 429" in msg or "HTTP 5" in msg)


def complete(spec: ModelSpec, messages, json_mode: bool = True,
             max_retries: int = 3, base_delay: float = 0.5) -> str:
    """Call the model and return the text content (format per ``spec``), with
    exponential backoff on transient errors (429 / 5xx / connection / timeout)."""
    import random
    import time
    last = None
    for attempt in range(max_retries + 1):
        try:
            return _call(spec, messages, json_mode)
        except ValueError:
            raise                      # config error -> don't retry
        except RuntimeError as e:
            last = e
            if attempt < max_retries and _retryable(str(e)):
                time.sleep(base_delay * (2 ** attempt) + random.uniform(0, 0.2))
                continue
            raise
    raise last  # pragma: no cover


def parse_json(text: str):
    """Tolerantly extract a JSON object from a model's text output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"no JSON object in model output: {text[:200]}")
