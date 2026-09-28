"""L3 index facade; backends are selected per warehouse.

Model-backed vector workflows use ``vector_db.LanceVectorIndex`` with persisted
encoder metadata and never create/open an FTS database. ``VectorIndex`` and
``FullTextIndex`` remain available to existing legacy warehouses. All derived
indexes live below ``<root>/index/``; real-model builds use the DataMixer CLI's
``index build --backend lancedb --vector-only`` path.
"""
from __future__ import annotations

import json
import sqlite3
from array import array
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path

from . import embedding
from .embedding import EMBED_DIM


class VectorIndex:
    """Flat float32 vector store with brute-force cosine search."""

    def __init__(self, root: Path, dim: int = EMBED_DIM):
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.vec_path = self.dir / "vectors.f32"
        self.ids_path = self.dir / "vectors.ids"
        self.dim = dim
        self._ids: list[str] = []
        self._vecs: list[array] = []
        self._pos: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self.vec_path.exists() or not self.ids_path.exists():
            return
        ids = self.ids_path.read_text().splitlines()
        raw = array("f")
        raw.frombytes(self.vec_path.read_bytes())
        # Infer the persisted dimensionality from the data so a real encoder's
        # width (e.g. bge-small = 512d) is honored instead of a stale default.
        if ids and len(raw) % len(ids) == 0:
            self.dim = len(raw) // len(ids)
        d = self.dim
        for i, sid in enumerate(ids):
            self._ids.append(sid)
            self._vecs.append(raw[i * d:(i + 1) * d])
            self._pos[sid] = i

    def add(self, sample_id: str, vec: array) -> None:
        # Adopt the dimensionality of the configured embedding model on first
        # write so a real encoder (e.g. bge-small = 512d) is not silently
        # dropped by a stale default dim. The flat store is single-model, so a
        # consistent non-empty dim is all that matters for cosine search.
        if not self._ids and len(vec) and len(vec) != self.dim:
            self.dim = len(vec)
        if sample_id in self._pos:
            self._vecs[self._pos[sample_id]] = vec
            return
        self._pos[sample_id] = len(self._ids)
        self._ids.append(sample_id)
        self._vecs.append(vec)

    def remove(self, sample_ids) -> int:
        drop = set(sample_ids)
        if not drop:
            return 0
        kept_ids, kept_vecs = [], []
        for sid, vec in zip(self._ids, self._vecs):
            if sid not in drop:
                kept_ids.append(sid)
                kept_vecs.append(vec)
        removed = len(self._ids) - len(kept_ids)
        self._ids, self._vecs = kept_ids, kept_vecs
        self._pos = {sid: i for i, sid in enumerate(self._ids)}
        return removed

    def flush(self) -> None:
        buf = array("f")
        for v in self._vecs:
            buf.extend(v)
        self.vec_path.write_bytes(buf.tobytes())
        self.ids_path.write_text("\n".join(self._ids))

    def search(
        self, query_vec: array, top_k: int = 10,
        restrict: set[str] | None = None, min_sim: float = -1.0,
    ) -> list[tuple[str, float]]:
        scored = []
        for sid, vec in zip(self._ids, self._vecs):
            if restrict is not None and sid not in restrict:
                continue
            s = embedding.cosine(query_vec, vec)
            if s >= min_sim:
                scored.append((sid, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k] if top_k else scored

    def get(self, sample_id: str) -> array | None:
        i = self._pos.get(sample_id)
        return self._vecs[i] if i is not None else None

    def __len__(self) -> int:
        return len(self._ids)


class FullTextIndex:
    """SQLite FTS5 keyword index."""

    def __init__(self, root: Path):
        self.path = Path(root) / "fulltext.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS docs "
            "USING fts5(sample_id UNINDEXED, body)"
        )

    def add(self, sample_id: str, text: str) -> None:
        self.conn.execute("DELETE FROM docs WHERE sample_id=?", (sample_id,))
        self.conn.execute(
            "INSERT INTO docs(sample_id, body) VALUES (?,?)", (sample_id, text)
        )

    def clear(self) -> None:
        self.conn.execute("DELETE FROM docs")

    def remove(self, sample_ids) -> None:
        self.conn.executemany("DELETE FROM docs WHERE sample_id=?",
                             [(s,) for s in sample_ids])
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def search(self, query: str, top_k: int = 10,
               restrict: set[str] | None = None) -> list[tuple[str, float]]:
        # FTS5 'rank' is ascending (more negative = better); flip for a score.
        rows = self.conn.execute(
            "SELECT sample_id, rank FROM docs WHERE docs MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query, top_k * 5 if restrict else top_k),
        ).fetchall()
        out = []
        for sid, rank in rows:
            if restrict is not None and sid not in restrict:
                continue
            out.append((sid, -float(rank)))
            if len(out) >= top_k:
                break
        return out

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


class IndexLayer:
    """Facade over the vector + full-text indexes for a warehouse."""

    def __init__(self, root: Path, query_embedder=None):
        self.dir = Path(root) / "index"
        self.dir.mkdir(parents=True, exist_ok=True)
        config_path = self.dir / "vector_config.json"
        self.vector_config = json.loads(config_path.read_text()) if config_path.exists() else {}
        if self.vector_config.get("backend") == "lancedb":
            from .vector_db import LanceVectorIndex
            self.vectors = LanceVectorIndex(self.dir, self.vector_config)
        else:
            self.vectors = VectorIndex(self.dir)
        # Vector-only workflows must never open or create a SQLite FTS index.
        self._fulltext = None
        # Optional callable(text) -> sequence[float] used to embed *queries* with
        # the same model that built the index (e.g. a remote bge server). When
        # None, fall back to the dependency-free hashing embedder. This keeps
        # query and document vectors in the same space regardless of dim.
        self.query_embedder = query_embedder

    @property
    def fulltext(self):
        if (self.dir / "fulltext_disabled.json").exists() or self.vector_config.get("backend") == "lancedb":
            raise ValueError("full-text indexing/keyword recall is disabled in this warehouse")
        if self._fulltext is None:
            self._fulltext = FullTextIndex(self.dir)
        return self._fulltext

    def _embed_query(self, query: str) -> array:
        if self.vector_config.get("backend") == "lancedb":
            # Persisted model + query preprocessing are authoritative. Never
            # silently fall back to hashing or an incompatible caller embedder.
            return self.vectors.embed_query(query)
        if self.query_embedder is not None:
            vec = self.query_embedder(query)
            return vec if isinstance(vec, array) else array("f", [float(v) for v in vec])
        return embedding.embed_text(query)

    def rebuild(self, store, vector: bool = True, fulltext: bool = True,
                io_workers: int = 1) -> dict:
        """(Re)build indexes from every sample in the catalog."""
        if self.vector_config.get("backend") == "lancedb":
            raise ValueError("use index build --backend lancedb --vector-only for this warehouse")
        if fulltext and (self.dir / "fulltext_disabled.json").exists():
            raise ValueError("full-text indexing is disabled in this warehouse")
        if io_workers < 1:
            raise ValueError("io_workers must be positive")
        if vector:
            self.vectors = VectorIndex(self.dir)
            self.vectors._ids, self.vectors._vecs, self.vectors._pos = [], [], {}
        if fulltext:
            self.fulltext.clear()
        n = 0
        def load_text(row):
            try:
                return row["sample_id"], store_text(store.get_content(row["cid"]))
            except KeyError:
                return row["sample_id"], None

        rows = iter(store.catalog.query(columns="sample_id,cid"))
        # Read immutable blobs concurrently, but preserve index insertion order
        # and keep all SQLite/index writes on the coordinator thread.
        with ThreadPoolExecutor(max_workers=io_workers) as pool:
            while batch := list(islice(rows, io_workers * 4)):
                for sid, text in pool.map(load_text, batch):
                    if text is None:
                        continue
                    if vector:
                        self.vectors.add(sid, embedding.embed_text(text))
                    if fulltext:
                        self.fulltext.add(sid, text)
                    n += 1
        if vector:
            self.vectors.flush()
        if fulltext:
            self.fulltext.commit()
        return {"indexed": n, "vectors": len(self.vectors),
                "fulltext_docs": self._fulltext.count() if self._fulltext else 0}

    def semantic_recall(self, query: str, top_k: int = 10,
                        restrict: set[str] | None = None,
                        min_sim: float = -1.0) -> list[tuple[str, float]]:
        return self.vectors.search(
            self._embed_query(query), top_k=top_k, restrict=restrict,
            min_sim=min_sim,
        )

    def keyword_recall(self, query: str, top_k: int = 10,
                       restrict: set[str] | None = None) -> list[tuple[str, float]]:
        return self.fulltext.search(query, top_k=top_k, restrict=restrict)

    def remove(self, sample_ids) -> int:
        """Remove samples from both indexes (erasure). Returns vectors removed."""
        ids = list(sample_ids)
        n = self.vectors.remove(ids)
        self.vectors.flush()
        if (self.dir / "fulltext.db").exists() and not (self.dir / "fulltext_disabled.json").exists():
            self.fulltext.remove(ids)
        return n

    def stats(self) -> dict:
        return {"vectors": len(self.vectors),
                "fulltext_docs": self.fulltext.count() if (self.dir / "fulltext.db").exists()
                    and not (self.dir / "fulltext_disabled.json").exists()
                    and self.vector_config.get("backend") != "lancedb" else 0,
                "dim": self.vectors.dim,
                "backend": self.vector_config.get("backend", "legacy-flat"),
                "model": self.vector_config.get("model"),
                "state": self.vector_config.get("state"),
                "fulltext_enabled": not (self.dir / "fulltext_disabled.json").exists()
                    and self.vector_config.get("backend") != "lancedb"}

    def close(self) -> None:
        if self._fulltext is not None:
            self._fulltext.close()


def store_text(content) -> str:
    from . import utils
    return utils.extract_text(content)
