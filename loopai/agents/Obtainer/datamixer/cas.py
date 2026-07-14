"""Content-Addressed Store (L1, local) -- compact, deduplicated blob storage.

Design goals (the brief's #1 priority: save space / store efficiently):

* **Dedup for free** -- identical content hashes to the same cid and is written
  once, no matter how many samples or datasets reference it.
* **Compressed at rest** -- every blob is compressed (zlib by default, lzma
  available) before it touches disk.
* **Self-describing** -- the codec is encoded in the file extension, so a store
  can be read back with no side-car metadata.
* **Sharded** -- blobs fan out by hash prefix to keep directories shallow even
  with hundreds of millions of objects.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import utils


@dataclass
class CASStats:
    num_blobs: int
    stored_bytes: int        # bytes actually on disk (compressed)
    raw_bytes: int           # bytes after decompression (logical size of unique data)


class ContentStore:
    def __init__(self, root: str | os.PathLike, codec: str = "zlib"):
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.codec = codec

    def _path_for(self, cid: str, codec: str) -> Path:
        # cid looks like "s2-<hex>"; shard on the hex body.
        body = cid.split("-", 1)[-1]
        return self.blobs / body[:2] / body[2:4] / (cid + utils.codec_ext(codec))

    def _find(self, cid: str) -> Path | None:
        body = cid.split("-", 1)[-1]
        d = self.blobs / body[:2] / body[2:4]
        if not d.is_dir():
            return None
        for ext in (".zz", ".xz", ".raw"):
            p = d / (cid + ext)
            if p.exists():
                return p
        return None

    # -- write -------------------------------------------------------------
    def put(self, data: bytes, codec: str | None = None) -> tuple[str, bool]:
        """Store raw bytes. Returns ``(cid, written)`` where ``written`` is
        False if the content already existed (dedup hit)."""
        codec = codec or self.codec
        cid = utils.content_id(data)
        existing = self._find(cid)
        if existing is not None:
            return cid, False
        path = self._path_for(cid, codec)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = utils.compress(data, codec)
        # atomic write
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)
        return cid, True

    def put_json(self, obj, codec: str | None = None) -> tuple[str, bool]:
        return self.put(utils.canonical_json(obj), codec)

    # -- read --------------------------------------------------------------
    def get(self, cid: str) -> bytes:
        path = self._find(cid)
        if path is None:
            raise KeyError(f"cid not found: {cid}")
        codec = utils.codec_from_ext(path.suffix)
        return utils.decompress(path.read_bytes(), codec)

    def get_json(self, cid: str):
        return utils.loads(self.get(cid))

    def has(self, cid: str) -> bool:
        return self._find(cid) is not None

    def delete(self, cid: str) -> bool:
        """Unpin/remove a blob (supports the tombstone/right-to-erasure flow)."""
        path = self._find(cid)
        if path is None:
            return False
        path.unlink()
        return True

    # -- introspection -----------------------------------------------------
    def iter_blobs(self) -> Iterator[Path]:
        if not self.blobs.is_dir():
            return
        for p in self.blobs.rglob("*"):
            if p.is_file() and p.suffix in (".zz", ".xz", ".raw"):
                yield p

    def stats(self) -> CASStats:
        num = 0
        stored = 0
        raw = 0
        for p in self.iter_blobs():
            num += 1
            stored += p.stat().st_size
            codec = utils.codec_from_ext(p.suffix)
            raw += len(utils.decompress(p.read_bytes(), codec))
        return CASStats(num_blobs=num, stored_bytes=stored, raw_bytes=raw)
