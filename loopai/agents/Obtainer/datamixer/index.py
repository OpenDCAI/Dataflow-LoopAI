"""L3 Index Layer -- vector recall + full-text search (node-local).

Per the roadmap: storage is decentralized, the index is local. Two indexes:

* ``VectorIndex`` -- compact float32 vectors persisted with the stdlib ``array``
  module; brute-force cosine top-k. Stands in for Lance/Milvus behind the same
  ``add`` / ``search`` surface.
* ``FullTextIndex`` -- SQLite FTS5 (ships with Python) for keyword recall/debug.

Both live under ``<root>/index/`` and are rebuildable from the catalog + CAS at
any time via ``IndexLayer.rebuild``.
"""
from __future__ import annotations

import sqlite3
from array import array
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
        d = self.dim
        for i, sid in enumerate(ids):
            self._ids.append(sid)
            self._vecs.append(raw[i * d:(i + 1) * d])
            self._pos[sid] = i

    def add(self, sample_id: str, vec: array) -> None:
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

    def __init__(self, root: Path):
        self.dir = Path(root) / "index"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.vectors = VectorIndex(self.dir)
        self.fulltext = FullTextIndex(self.dir)

    def rebuild(self, store, vector: bool = True, fulltext: bool = True) -> dict:
        """(Re)build indexes from every sample in the catalog."""
        if vector:
            self.vectors = VectorIndex(self.dir)
            self.vectors._ids, self.vectors._vecs, self.vectors._pos = [], [], {}
        if fulltext:
            self.fulltext.clear()
        n = 0
        for row in store.catalog.query(columns="sample_id,cid"):
            try:
                content = store.get_content(row["cid"])
            except KeyError:
                continue
            text = store_text(content)
            if vector:
                self.vectors.add(row["sample_id"], embedding.embed_text(text))
            if fulltext:
                self.fulltext.add(row["sample_id"], text)
            n += 1
        if vector:
            self.vectors.flush()
        if fulltext:
            self.fulltext.commit()
        return {"indexed": n, "vectors": len(self.vectors),
                "fulltext_docs": self.fulltext.count()}

    def semantic_recall(self, query: str, top_k: int = 10,
                        restrict: set[str] | None = None,
                        min_sim: float = -1.0) -> list[tuple[str, float]]:
        return self.vectors.search(
            embedding.embed_text(query), top_k=top_k, restrict=restrict,
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
        self.fulltext.remove(ids)
        return n

    def stats(self) -> dict:
        return {"vectors": len(self.vectors),
                "fulltext_docs": self.fulltext.count(),
                "dim": self.vectors.dim}

    def close(self) -> None:
        self.fulltext.close()


def store_text(content) -> str:
    from . import utils
    return utils.extract_text(content)
