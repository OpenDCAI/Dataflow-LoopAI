"""Megatron-style indexed binary export (#12).

Real pretraining loaders read **pre-tokenized, memory-mapped binaries**, not
JSON. This writes the Megatron-LM ``MMapIndexedDataset`` pair so an export drops
straight into Megatron/NeMo-style training without re-tokenizing (which would
also desync the token-budget mix):

* ``<prefix>.bin`` -- concatenated token ids (int32, little-endian).
* ``<prefix>.idx`` -- the Megatron index (magic ``MMIDIDX\\x00\\x00``, version 1,
  dtype code, per-document sizes (int32), byte pointers (int64), and the
  document index (int64)). One sample = one document.

Stdlib-only (``struct`` + ``array``); token ids come from the platform tokenizer
seam (``tiktoken``/``hf`` for real ids, heuristic placeholder otherwise).
"""
from __future__ import annotations

import struct
import sys
from array import array
from pathlib import Path

_MAGIC = b"MMIDIDX\x00\x00"
_DTYPE_CODE = 4          # Megatron code for int32
_DTYPE_SIZE = 4


def _le_i32(values) -> bytes:
    a = array("i", values)
    assert a.itemsize == 4, "platform int is not 4 bytes"
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def write_indexed(prefix: Path, docs) -> dict:
    """Write ``<prefix>.bin`` / ``<prefix>.idx`` for an iterable of token-id lists
    (one list per document). Returns ``{documents, total_tokens, dtype}``."""
    sizes: list[int] = []
    total = 0
    with open(f"{prefix}.bin", "wb") as fh:
        for ids in docs:
            ids = list(ids)
            sizes.append(len(ids))
            total += len(ids)
            if ids:
                fh.write(_le_i32(ids))
    # byte pointers = prefix sums of size*itemsize
    pointers, addr = [], 0
    for s in sizes:
        pointers.append(addr)
        addr += s * _DTYPE_SIZE
    doc_idx = list(range(len(sizes) + 1))   # one sequence per document
    with open(f"{prefix}.idx", "wb") as fh:
        fh.write(_MAGIC)
        fh.write(struct.pack("<Q", 1))                 # version
        fh.write(struct.pack("<B", _DTYPE_CODE))       # int32
        fh.write(struct.pack("<Q", len(sizes)))        # sequence count
        fh.write(struct.pack("<Q", len(doc_idx)))      # doc_idx length
        fh.write(struct.pack(f"<{len(sizes)}i", *sizes))
        fh.write(struct.pack(f"<{len(pointers)}q", *pointers))
        fh.write(struct.pack(f"<{len(doc_idx)}q", *doc_idx))
    return {"documents": len(sizes), "total_tokens": total, "dtype": "int32"}


def read_index(prefix: Path) -> dict:
    """Minimal reader (for verification/tests): parse the .idx header + sizes."""
    data = Path(f"{prefix}.idx").read_bytes()
    assert data[:9] == _MAGIC, "bad magic"
    off = 9
    (version,) = struct.unpack_from("<Q", data, off); off += 8
    (code,) = struct.unpack_from("<B", data, off); off += 1
    (n,) = struct.unpack_from("<Q", data, off); off += 8
    (ndoc,) = struct.unpack_from("<Q", data, off); off += 8
    sizes = list(struct.unpack_from(f"<{n}i", data, off)); off += 4 * n
    pointers = list(struct.unpack_from(f"<{n}q", data, off)); off += 8 * n
    doc_idx = list(struct.unpack_from(f"<{ndoc}q", data, off))
    return {"version": version, "dtype_code": code, "count": n,
            "sizes": sizes, "pointers": pointers, "doc_idx": doc_idx}
