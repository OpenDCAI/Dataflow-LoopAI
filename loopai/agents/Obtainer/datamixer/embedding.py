"""Dependency-free text embedding (the L3 "unified embedding pipeline" stand-in).

Uses the *hashing trick* (feature hashing): tokens are hashed into a fixed-width
vector with signed buckets, then L2-normalized. This is a real, deterministic
bag-of-words embedding in a shared space -- good enough to demonstrate semantic
recall and semantic dedup with zero model downloads. Swap in SigLIP/CLIP-class
encoders behind the same `embed_text` signature for Phase 2.
"""
from __future__ import annotations

import hashlib
import math
import re
from array import array

from . import utils

EMBED_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]", re.UNICODE)


def _tokens(text: str) -> list[str]:
    text = text.lower()
    toks = _TOKEN_RE.findall(text)
    # add char bigrams for short / CJK text to enrich the signal
    if len(toks) < 3:
        toks += [text[i:i + 2] for i in range(max(len(text) - 1, 0))]
    return toks


def embed_text(text: str, dim: int = EMBED_DIM) -> array:
    vec = array("f", [0.0]) * dim
    if not text:
        return vec
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        for i in range(dim):
            vec[i] /= norm
    return vec


def embed_content(content, dim: int = EMBED_DIM) -> array:
    return embed_text(utils.extract_text(content), dim)


def cosine(a: array, b: array) -> float:
    # both are unit vectors -> dot product is cosine similarity
    return sum(x * y for x, y in zip(a, b))
