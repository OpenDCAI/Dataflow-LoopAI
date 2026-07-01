"""PII detection & redaction (#13).

Regex-based detectors plus a recursive redactor that rewrites a sample's content
(replacing matches with ``[TYPE]`` placeholders). Used by ``datamixer pii-redact``
to produce a clean new content blob; the catalog repoints the sample at it and,
when the original blob is no longer referenced, it is removed (compliance).
"""
from __future__ import annotations

import re
from typing import Any

# order matters: more specific patterns first so they win over generic ones
PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "IP": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "API_KEY": re.compile(r"\b(?:sk|pk|ghp|xox[baprs])[-_][A-Za-z0-9]{16,}\b"),
    "PHONE": re.compile(
        r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
}

DEFAULT_TYPES = tuple(PATTERNS)


def detect(text: str, types=DEFAULT_TYPES) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in types:
        n = len(PATTERNS[t].findall(text or ""))
        if n:
            counts[t] = n
    return counts


def redact_text(text: str, types=DEFAULT_TYPES) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    out = text or ""
    for t in types:
        pat = PATTERNS[t]
        n = len(pat.findall(out))
        if n:
            counts[t] = counts.get(t, 0) + n
            out = pat.sub(f"[{t}]", out)
    return out, counts


def redact_content(content: Any, types=DEFAULT_TYPES) -> tuple[Any, dict[str, int]]:
    """Recursively redact all string leaves of a sample's content object."""
    total: dict[str, int] = {}

    def walk(obj):
        if isinstance(obj, str):
            red, c = redact_text(obj, types)
            for k, v in c.items():
                total[k] = total.get(k, 0) + v
            return red
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    return walk(content), total
