"""Benchmark decontamination support (L4).

Registers evaluation-benchmark text as **contamination sets** and detects
training samples that overlap them, the industry-standard guard against
benchmark leakage (n-gram / substring overlap, cf. GPT-3 / Llama / Dolma /
bigcode decontamination).

A set is stored compactly under ``<root>/contam/<name>``:
  * ``<name>.ngrams`` -- sorted ``uint64`` hashes of the set's word n-grams.
  * ``<name>.json``   -- metadata, optionally including the associated
    DataMixer benchmark dataset identity.

Detection: a sample is contaminated when the fraction of its n-grams that appear
in a set exceeds ``overlap_threshold`` (default 0.8). Dependency-free.
"""
from __future__ import annotations

import hashlib
import json
import os
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

    def matches(self, text: str, threshold: float) -> bool:
        if threshold <= 0:
            return True
        toks = _tokens(text)
        if not toks:
            return False
        if len(toks) < self.ngram:
            return _hash(" ".join(toks)) in self.hashes
        seen: set[int] = set()
        hit = 0
        # Once candidate unique n-grams exceed this bound, even hitting every
        # benchmark n-gram cannot reach the requested overlap threshold.
        max_unique_for_match = int(len(self.hashes) / threshold)
        for i in range(len(toks) - self.ngram + 1):
            h = _hash(" ".join(toks[i:i + self.ngram]))
            if h in seen:
                continue
            seen.add(h)
            if h in self.hashes:
                hit += 1
            if len(seen) > max_unique_for_match:
                return False
        return bool(seen) and (hit / len(seen)) >= threshold


def _dir(root) -> Path:
    d = Path(root) / "contam"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def _ngram_hash_batch(batch: list[str], ngram: int) -> tuple[int, set[int]]:
    hashes: set[int] = set()
    n_texts = 0
    for text in batch:
        if not text:
            continue
        n_texts += 1
        hashes |= ngram_hashes(text, ngram)
    return n_texts, hashes


def _iter_text_batches(texts, batch_size: int):
    batch = []
    for text in texts:
        if not text:
            continue
        batch.append(str(text))
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def register(root, name: str, texts, ngram: int = DEFAULT_NGRAM,
             workers: int | None = None, batch_size: int = 2048,
             benchmark_dataset: dict | None = None) -> dict:
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

    d = _dir(root)
    dataset_ref = None
    if benchmark_dataset:
        dataset_id = str(benchmark_dataset.get("id") or "").strip()
        dataset_name = str(benchmark_dataset.get("name") or "").strip()
        if not dataset_id or not dataset_name:
            raise ValueError("benchmark_dataset requires non-empty id and name")
        dataset_ref = {"id": dataset_id, "name": dataset_name}
    hashes: set[int] = set()
    n_texts = 0
    workers = max(1, int(workers or 1))
    batch_size = max(1, int(batch_size or 2048))
    if workers == 1:
        for batch in _iter_text_batches(texts, batch_size):
            count, batch_hashes = _ngram_hash_batch(batch, ngram)
            n_texts += count
            hashes |= batch_hashes
    else:
        pending = set()

        def drain(done) -> None:
            nonlocal n_texts, hashes
            for fut in done:
                count, batch_hashes = fut.result()
                n_texts += count
                hashes |= batch_hashes

        with ProcessPoolExecutor(max_workers=workers) as pool:
            for batch in _iter_text_batches(texts, batch_size):
                pending.add(pool.submit(_ngram_hash_batch, batch, ngram))
                if len(pending) >= workers * 2:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    drain(done)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                drain(done)
    buf = array("Q", sorted(hashes))
    (d / f"{name}.ngrams").write_bytes(buf.tobytes())
    meta = {"name": name, "ngram": ngram, "num_texts": n_texts,
            "num_ngrams": len(hashes), "build_workers": workers,
            "build_batch_size": batch_size}
    if dataset_ref:
        meta["benchmark_dataset"] = dataset_ref
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
        if cs.matches(text, threshold):
            return True, cs.name
    return False, ""
