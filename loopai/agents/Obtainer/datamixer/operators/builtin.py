"""Built-in starter operators (all implement the Operator contract).

These are intentionally dependency-free heuristics so the MVP runs anywhere.
Each one is a drop-in target for a stronger DataFlow/3rd-party operator later
(e.g. fastText langid, a trained quality classifier, real MinHash LSH).
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterator, Optional

from .. import utils
from .base import Batch, Operator, OperatorContext, OperatorSpec, register

_WS = re.compile(r"\s+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:(?:\+?\d{1,3})?[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}")
_CJK = re.compile(r"[一-鿿]")
_CYR = re.compile(r"[Ѐ-ӿ]")
_ARAB = re.compile(r"[؀-ۿ]")


def _text(row: dict) -> str:
    return utils.extract_text(row.get("content", row))


@register("token_count")
class TokenCount(Operator):
    """Count tokens with a pluggable tokenizer
    (``--arg tokenizer=tiktoken:o200k_base``); records ``n_tokens`` + ``tokenizer``."""
    spec = OperatorSpec("token_count", "1.1", "score",
                        description="Count n_tokens with a pluggable tokenizer.")

    def __init__(self, tokenizer: str | None = None):
        self.tok_spec = tokenizer
        self._tok = None

    def setup(self, ctx: OperatorContext) -> None:
        from .. import tokenizers
        self._tok = tokenizers.resolve(self.tok_spec)

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        from .. import tokenizers
        tok = self._tok or tokenizers.resolve(self.tok_spec)
        for row in batch:
            row["n_tokens"] = tok.count(_text(row))
            row["tokenizer"] = tok.name
        return batch


@register("lang_detect")
class LangDetect(Operator):
    spec = OperatorSpec("lang_detect", "1.0", "score",
                        description="Heuristic language id (script-based).")

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        for row in batch:
            t = _text(row)
            if not t:
                row["lang"], row["lang_conf"] = "und", 0.0
                continue
            cjk = len(_CJK.findall(t))
            cyr = len(_CYR.findall(t))
            arab = len(_ARAB.findall(t))
            total = max(len(t), 1)
            if cjk / total > 0.1:
                row["lang"], row["lang_conf"] = "zh", round(cjk / total + 0.5, 2)
            elif cyr / total > 0.1:
                row["lang"], row["lang_conf"] = "ru", round(cyr / total + 0.5, 2)
            elif arab / total > 0.1:
                row["lang"], row["lang_conf"] = "ar", round(arab / total + 0.5, 2)
            else:
                row["lang"], row["lang_conf"] = "en", 0.8
            row["lang_conf"] = min(row["lang_conf"], 1.0)
        return batch


@register("quality_score")
class QualityScore(Operator):
    """Cheap document-quality heuristic in [0,1].

    Rewards reasonable length and alphabetic density; penalizes excessive symbol
    noise and word repetition. A stand-in for a trained classifier.
    """
    spec = OperatorSpec("quality_score", "1.0", "score",
                        description="Heuristic 0..1 quality estimate.")

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        for row in batch:
            t = _text(row)
            row["quality_score"] = round(self._score(t), 4)
        return batch

    @staticmethod
    def _score(t: str) -> float:
        if not t:
            return 0.0
        n = len(t)
        alpha = sum(c.isalpha() for c in t) / n
        words = t.split()
        if not words:
            return 0.05
        uniq = len(set(words)) / len(words)
        length_factor = min(len(words) / 50.0, 1.0)
        symbol_noise = sum(not (c.isalnum() or c.isspace()) for c in t) / n
        score = (0.4 * alpha + 0.3 * uniq + 0.2 * length_factor
                 + 0.1 * (1 - min(symbol_noise * 3, 1.0)))
        return max(0.0, min(score, 1.0))


@register("pii_detect")
class PIIDetect(Operator):
    spec = OperatorSpec("pii_detect", "1.1", "score",
                        description="Flag PII (email/phone/IP/card/SSN/key) counts.")

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        from .. import pii
        for row in batch:
            counts = pii.detect(_text(row))
            n = sum(counts.values())
            row["pii_count"] = n          # rides in tags
            row["has_pii"] = bool(n)
            row["pii_types"] = sorted(counts) or None
        return batch


_PRIME = (1 << 61) - 1


def _ihash(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


@register("minhash_dedup")
class MinHashDedup(Operator):
    """Near-duplicate detector via MinHash + LSH banding, with persistent,
    cross-run, global state.

    * **MinHash**: ``num_perm`` deterministic permutations over word-``k``-gram
      shingle hashes give each doc a signature.
    * **LSH banding**: the signature is split into ``bands`` bands; two docs that
      collide in *any* band are near-dup candidates and share a
      ``dedup_cluster_id`` (clusters merged by union-find). This recalls local
      rewrites that a single-signature scheme misses.
    * **Persistence**: band-bucket -> cluster mappings are stored under
      ``<root>/dedup/minhash.db`` (when ``ctx.root`` is set), so dedup is global
      across shards, datasets and repeated ``ingest``/``op run`` invocations.
    """
    spec = OperatorSpec("minhash_dedup", "0.3", "dedup", stateful=True,
                        description="MinHash + LSH banded near-dup clustering "
                                    "(persistent, cross-run).")

    def __init__(self, k: int = 5, num_perm: int = 16, bands: int = 4,
                 persist: bool = True):
        self.k = k
        self.num_perm = num_perm
        self.bands = max(1, min(bands, num_perm))
        self.rows = num_perm // self.bands
        self.persist = persist
        self._perms = [( _ihash(f"a{i}") | 1, _ihash(f"b{i}"))
                       for i in range(num_perm)]
        self._buckets: dict[str, str] = {}   # band-bucket -> cluster id (root)
        self._parent: dict[str, str] = {}    # union-find over cluster ids
        self._dirty: set[str] = set()
        self._conn = None

    # -- union-find --------------------------------------------------------
    def _find(self, c: str) -> str:
        while self._parent.get(c, c) != c:
            self._parent[c] = self._parent.get(self._parent[c], self._parent[c])
            c = self._parent[c]
        return c

    def _union(self, a: str, b: str) -> str:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[rb] = ra
        return ra

    # -- minhash + bands ---------------------------------------------------
    def _signature(self, text: str) -> list[int]:
        norm = _WS.sub(" ", text.lower()).strip()
        words = norm.split()
        if len(words) < self.k:
            shingles = {norm} if norm else {""}
        else:
            shingles = {" ".join(words[i:i + self.k])
                        for i in range(len(words) - self.k + 1)}
        hs = [_ihash(s) for s in shingles]
        sig = []
        for a, b in self._perms:
            sig.append(min((a * h + b) % _PRIME for h in hs))
        return sig

    def _band_keys(self, sig: list[int]) -> list[str]:
        keys = []
        for bi in range(self.bands):
            chunk = sig[bi * self.rows:(bi + 1) * self.rows]
            keys.append(f"{bi}:" + hashlib.blake2b(
                ",".join(map(str, chunk)).encode(), digest_size=10).hexdigest())
        return keys

    # -- persistence -------------------------------------------------------
    def setup(self, ctx: OperatorContext) -> None:
        if not (self.persist and ctx.root):
            return
        import sqlite3
        from pathlib import Path
        d = Path(ctx.root) / "dedup"
        d.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(d / "minhash.db")
        self._conn.execute("CREATE TABLE IF NOT EXISTS buckets("
                           "bucket TEXT PRIMARY KEY, cluster TEXT)")
        for bk, cid in self._conn.execute("SELECT bucket, cluster FROM buckets"):
            self._buckets[bk] = cid
            self._parent.setdefault(cid, cid)

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        for row in batch:
            sig = self._signature(_text(row))
            keys = self._band_keys(sig)
            matched = [self._find(self._buckets[k]) for k in keys
                       if k in self._buckets]
            if matched:
                rep = matched[0]
                for m in matched[1:]:
                    rep = self._union(rep, m)
                row["is_duplicate"] = True
            else:
                rep = "dc-" + hashlib.blake2b(
                    ",".join(map(str, sig)).encode(), digest_size=8).hexdigest()
                self._parent[rep] = rep
                row["is_duplicate"] = False
            for k in keys:
                if self._buckets.get(k) != rep:
                    self._buckets[k] = rep
                    self._dirty.add(k)
            row["dedup_cluster_id"] = self._find(rep)
        return batch

    def finalize(self, ctx: OperatorContext) -> Optional[Iterator[Batch]]:
        if self._conn and self._dirty:
            self._conn.executemany(
                "INSERT OR REPLACE INTO buckets(bucket, cluster) VALUES (?,?)",
                [(k, self._find(self._buckets[k])) for k in self._dirty])
            self._conn.commit()
            self._dirty.clear()
        return None

    def teardown(self, ctx: OperatorContext) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


@register("decontaminate")
class Decontaminate(Operator):
    """Benchmark decontamination: flag (or drop) samples overlapping a registered
    evaluation set, via word-n-gram overlap (cf. ``datamixer contam add``).

    Args: ``against`` (set name or list, default all), ``overlap_threshold``
    (0.8), ``mode`` (``score`` annotates, ``filter`` drops hits), ``contam_dir``
    (defaults to ``<warehouse>/contam`` via ``ctx.root``). Sets
    ``is_contaminated`` (0/1) and ``contam_source``.
    """
    spec = OperatorSpec("decontaminate", "1.0", "filter",
                        description="Flag/remove samples overlapping benchmarks.")

    def __init__(self, against=None, overlap_threshold: float = 0.8,
                 mode: str = "score", contam_dir=None):
        self.against = against
        self.threshold = float(overlap_threshold)
        self.mode = mode
        self._dir = contam_dir
        self._sets = []

    def setup(self, ctx: OperatorContext) -> None:
        from .. import contam
        root = self._dir or ctx.root
        if not root:
            raise ValueError("decontaminate needs a warehouse with registered "
                             "contamination sets (run `datamixer contam add`)")
        names = (self.against if isinstance(self.against, list)
                 else [self.against] if self.against else None)
        self._sets = contam.load_sets(root, names)
        if not self._sets:
            raise ValueError("no contamination sets registered; "
                             "run `datamixer contam add` first")

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        from .. import contam
        out = []
        for row in batch:
            hit, src = contam.match(_text(row), self._sets, self.threshold)
            row["is_contaminated"] = 1 if hit else 0
            row["contam_source"] = src or None
            if self.mode == "filter" and hit:
                continue
            out.append(row)
        return out


@register("text_embed")
class TextEmbed(Operator):
    """Attach a dependency-free hashing embedding to each row.

    The pipeline runner detects the ``embedding`` key and pushes the vector into
    the L3 vector index. Replace with a SigLIP/CLIP encoder for production.
    """
    spec = OperatorSpec("text_embed", "1.0", "embed",
                        description="Hashing-trick text embedding (L3).")

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        from ..embedding import embed_text
        for row in batch:
            row["embedding"] = list(embed_text(_text(row)))
        return batch


@register("semantic_dedup")
class SemanticDedup(Operator):
    """Near-duplicate detection by embedding cosine similarity.

    Greedy single-pass clustering: a row joins the first existing cluster whose
    centroid exceeds ``threshold``, else starts a new one. Stateful across
    batches. Sets ``dedup_cluster_id`` and ``is_duplicate`` (semantic).
    """
    spec = OperatorSpec("semantic_dedup", "0.1", "dedup", stateful=True,
                        gpu_required=False,
                        description="Embedding-similarity near-dup clustering.")

    def __init__(self, threshold: float = 0.92):
        self.threshold = float(threshold)
        self._centroids: list = []        # (cluster_id, vector)

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        from ..embedding import embed_text, cosine
        for row in batch:
            vec = embed_text(_text(row))
            best_id, best_sim = None, -1.0
            for cid, cvec in self._centroids:
                s = cosine(vec, cvec)
                if s > best_sim:
                    best_id, best_sim = cid, s
            if best_id is not None and best_sim >= self.threshold:
                row["dedup_cluster_id"] = best_id
                row["is_duplicate"] = True
            else:
                cid = "sc-%04d" % len(self._centroids)
                self._centroids.append((cid, vec))
                row["dedup_cluster_id"] = cid
                row["is_duplicate"] = False
        return batch

    def finalize(self, ctx: OperatorContext) -> Optional[Iterator[Batch]]:
        return None
