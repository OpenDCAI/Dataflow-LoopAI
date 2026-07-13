"""Pluggable tokenizer seam (#8 / #18).

The recipe engine's headline feature is **token-budget mixing**
(``total_tokens`` / ``weighted_token``). That is only meaningful if ``n_tokens``
reflects the *real* tokenizer of the target model — the default regex heuristic
is systematically off (CJK/code/multilingual) and would distort the mix.

Specs (string form, so they are recordable in recipes/manifests):
  * ``heuristic``           -- regex word/punct count (default, zero-dep, *estimated*)
  * ``whitespace`` / ``char`` -- trivial deterministic counters (estimated)
  * ``tiktoken:<encoding>`` -- e.g. ``tiktoken:o200k_base`` (exact; needs tiktoken)
  * ``hf:<model>``          -- e.g. ``hf:Qwen/Qwen2.5-7B`` (exact; needs transformers)

Real tokenizers are optional extras (``pip install datamixer[tokenizers]``); the
MVP stays zero-dependency by defaulting to ``heuristic`` and clearly marking
counts as *estimated* vs *exact*. Per-record tokenizer failures also fall back
to the heuristic estimator so token accounting does not block ingest/export on
unexpected text.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable

from . import utils

DEFAULT = "heuristic"


_HEUR_RE = __import__("re").compile(r"[A-Za-z0-9]+|\S", __import__("re").UNICODE)
_HEUR_VOCAB = 1 << 31


def _heuristic_encode(text: str) -> list[int]:
    """Deterministic placeholder token ids (no real vocab). Lets the binary
    export produce a valid .bin/.idx with the heuristic tokenizer; use a real
    tokenizer (tiktoken/hf) for training-ready ids."""
    import hashlib
    return [int.from_bytes(hashlib.blake2b(t.encode(), digest_size=4).digest(),
                           "big") % _HEUR_VOCAB
            for t in _HEUR_RE.findall(text or "")]


@dataclass
class Tokenizer:
    name: str
    _count: Callable[[str], int]
    exact: bool
    _encode: Callable[[str], list] | None = None

    def count(self, text: str) -> int:
        try:
            return self._count(text or "")
        except Exception:
            return utils.estimate_tokens(text or "")

    def encode(self, text: str) -> list:
        if self._encode is not None:
            try:
                return self._encode(text or "")
            except Exception:
                return _heuristic_encode(text or "")
        return _heuristic_encode(text)        # fallback for count-only tokenizers


# tests / embedders can inject a tokenizer without the heavy deps
_CUSTOM: dict[str, Tokenizer] = {}


def register(name: str, count_fn: Callable[[str], int], exact: bool = True,
             encode_fn: Callable[[str], list] | None = None) -> None:
    _CUSTOM[name] = Tokenizer(name, count_fn, exact, encode_fn)
    resolve.cache_clear()


@functools.lru_cache(maxsize=16)
def resolve(spec: str | None) -> Tokenizer:
    spec = (spec or DEFAULT).strip()
    if spec in _CUSTOM:
        return _CUSTOM[spec]
    if spec in ("heuristic", "estimate"):
        return Tokenizer("heuristic", utils.estimate_tokens, False)
    if spec == "char":
        return Tokenizer("char", len, False)
    if spec == "whitespace":
        return Tokenizer("whitespace", lambda t: len(t.split()), False)
    if spec.startswith("tiktoken:"):
        enc_name = spec.split(":", 1)[1]
        try:
            import tiktoken
        except ImportError as e:
            raise RuntimeError("tiktoken not installed; "
                               "pip install datamixer[tokenizers]") from e
        enc = tiktoken.get_encoding(enc_name)
        return Tokenizer(spec, lambda t: len(enc.encode(t)), True,
                         lambda t: enc.encode(t))
    if spec.startswith("hf:"):
        model = spec.split(":", 1)[1]
        try:
            from transformers import AutoTokenizer
        except ImportError as e:
            raise RuntimeError("transformers not installed; "
                               "pip install datamixer[tokenizers]") from e
        tok = AutoTokenizer.from_pretrained(model)
        return Tokenizer(spec, lambda t: len(tok.encode(t)), True,
                         lambda t: tok.encode(t))
    raise ValueError(f"unknown tokenizer spec: {spec!r}")


def count_tokens(text: str, spec: str | None = None) -> int:
    return resolve(spec).count(text)


def is_exact(spec: str | None = None) -> bool:
    return resolve(spec).exact
