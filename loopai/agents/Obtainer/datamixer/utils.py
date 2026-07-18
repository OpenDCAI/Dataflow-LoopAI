"""Low-level helpers: hashing, compression, canonical JSON, token estimation.

Everything here is stdlib-only so the platform runs with zero install steps.
"""
from __future__ import annotations

import hashlib
import json
import re
import zlib
import lzma
from typing import Any

# ---------------------------------------------------------------------------
# Canonical JSON (stable, compact) -- used for content hashing & blob storage.
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> bytes:
    """Deterministic, compact UTF-8 JSON. Stable keys => stable content hash."""
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def loads(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


# ---------------------------------------------------------------------------
# Content addressing.
# ---------------------------------------------------------------------------

def content_id(data: bytes) -> str:
    """Return a content id (CID-like) for raw bytes.

    Format: ``s2-<sha256-hex>``. The ``s2-`` prefix names the hash algorithm so
    the scheme can evolve (e.g. ``b3-`` for blake3) without ambiguity.
    """
    return "s2-" + hashlib.sha256(data).hexdigest()


def short_cid(cid: str, n: int = 12) -> str:
    """Human-friendly truncation of a cid for display/logs."""
    body = cid.split("-", 1)[-1]
    return cid.split("-", 1)[0] + "-" + body[:n]


# ---------------------------------------------------------------------------
# Compression codecs. Extension-tagged so the store is self-describing.
# ---------------------------------------------------------------------------

_CODECS = {
    "zlib": ".zz",
    "lzma": ".xz",
    "raw": ".raw",
}
_EXT_TO_CODEC = {v: k for k, v in _CODECS.items()}


def codec_ext(codec: str) -> str:
    if codec not in _CODECS:
        raise ValueError(f"unknown codec: {codec!r} (choices: {list(_CODECS)})")
    return _CODECS[codec]


def codec_from_ext(ext: str) -> str:
    return _EXT_TO_CODEC[ext]


def compress(data: bytes, codec: str = "zlib") -> bytes:
    if codec == "zlib":
        return zlib.compress(data, 9)
    if codec == "lzma":
        return lzma.compress(data, preset=6)
    if codec == "raw":
        return data
    raise ValueError(f"unknown codec: {codec!r}")


def decompress(data: bytes, codec: str) -> bytes:
    if codec == "zlib":
        return zlib.decompress(data)
    if codec == "lzma":
        return lzma.decompress(data)
    if codec == "raw":
        return data
    raise ValueError(f"unknown codec: {codec!r}")


# ---------------------------------------------------------------------------
# Token estimation -- approximate, dependency-free. Good enough for budgeting.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def estimate_tokens(text: str) -> int:
    """Cheap tokenizer-free estimate. Replace with a real tokenizer operator
    when exact budgets matter; this is fine for configuration and previews."""
    if not text:
        return 0
    return len(_TOKEN_RE.findall(text))


def extract_text(content: Any) -> str:
    """Pull the textual payload out of a sample's content for token counting.

    Understands the common training-stage shapes; falls back to the JSON dump
    for anything else so token counts are never silently zero.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    parts: list[str] = []
    if "text" in content:
        parts.append(str(content["text"]))
    if "instruction" in content:
        parts.append(str(content["instruction"]))
    if "response" in content:
        parts.append(str(content["response"]))
    if "prompt" in content:
        parts.append(str(content["prompt"]))
    for key in ("chosen", "rejected"):
        if key in content:
            parts.append(str(content[key]))
    if "messages" in content and isinstance(content["messages"], list):
        for m in content["messages"]:
            if isinstance(m, dict):
                parts.append(str(m.get("content", "")))
    if "trajectory" in content and isinstance(content["trajectory"], list):
        parts.append(json.dumps(content["trajectory"], ensure_ascii=False))
    if not parts:
        return json.dumps(content, ensure_ascii=False)
    return "\n".join(parts)


def fingerprint(obj: Any) -> str:
    """Stable short fingerprint of a config object (e.g. a recipe)."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()[:16]
