"""Benchmark decontamination support (L4).

Registers evaluation-benchmark text as **contamination sets** and detects
training samples that overlap them, the industry-standard guard against
benchmark leakage (n-gram / substring overlap, cf. GPT-3 / Llama / Dolma /
bigcode decontamination).

A set is stored compactly under ``<root>/contam/<name>``:
  * ``<name>.ngrams`` -- sorted ``uint64`` hashes of the set's word n-grams.
  * ``<name>.json``   -- ``{name, ngram, num_texts, num_ngrams}`` metadata.

Detection: a sample is contaminated when the fraction of its n-grams that appear
in a set exceeds ``overlap_threshold`` (default 0.8). Dependency-free.
"""
from __future__ import annotations

import hashlib
import json
import re
from array import array
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z0-9]+", re.UNICODE)
DEFAULT_NGRAM = 13


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _hash(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(),
                          "big")


def ngram_hashes(text: str, ngram: int) -> set[int]:
    toks = _tokens(text)
    if not toks:
        return set()
    if len(toks) < ngram:
        return {_hash(" ".join(toks))}
    return {_hash(" ".join(toks[i:i + ngram]))
            for i in range(len(toks) - ngram + 1)}


@dataclass
class ContamSet:
    name: str
    ngram: int
    hashes: set[int]

    def overlap(self, text: str) -> float:
        cand = ngram_hashes(text, self.ngram)
        if not cand:
            return 0.0
        hit = sum(1 for h in cand if h in self.hashes)
        return hit / len(cand)


def _dir(root) -> Path:
    d = Path(root) / "contam"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register(root, name: str, texts, ngram: int = DEFAULT_NGRAM) -> dict:
    d = _dir(root)
    hashes: set[int] = set()
    n_texts = 0
    for t in texts:
        if not t:
            continue
        n_texts += 1
        hashes |= ngram_hashes(str(t), ngram)
    buf = array("Q", sorted(hashes))
    (d / f"{name}.ngrams").write_bytes(buf.tobytes())
    meta = {"name": name, "ngram": ngram, "num_texts": n_texts,
            "num_ngrams": len(hashes)}
    (d / f"{name}.json").write_text(json.dumps(meta))
    return meta


def list_sets(root) -> list[dict]:
    d = Path(root) / "contam"
    if not d.is_dir():
        return []
    out = []
    for j in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(j.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def load_set(root, name: str) -> ContamSet:
    d = Path(root) / "contam"
    meta = json.loads((d / f"{name}.json").read_text())
    raw = array("Q")
    raw.frombytes((d / f"{name}.ngrams").read_bytes())
    return ContamSet(name, int(meta["ngram"]), set(raw))


def load_sets(root, against=None) -> list[ContamSet]:
    names = against if against else [m["name"] for m in list_sets(root)]
    return [load_set(root, n) for n in names]


def match(text: str, sets, threshold: float = 0.8) -> tuple[bool, str]:
    """Return (is_contaminated, source) for the first set exceeding threshold."""
    for cs in sets:
        if cs.overlap(text) >= threshold:
            return True, cs.name
    return False, ""
