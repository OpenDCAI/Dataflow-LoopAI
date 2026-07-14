"""DataStore -- the facade that ties the content store (L1) and catalog (L2)
together and implements ingest / materialization.

A store lives in a single directory (the "warehouse")::

    <root>/
      datamixer.toml      # marker + config
      catalog.db          # SQLite sample metadata
      blobs/              # content-addressed, compressed sample bodies
      exports/            # materialized recipe outputs + lineage
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import utils
from .cas import ContentStore
from .catalog import Catalog
from . import schema

MARKER = "datamixer.toml"


class StoreError(RuntimeError):
    pass


def find_root(start: str | os.PathLike | None = None) -> Path | None:
    cur = Path(start or os.getcwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / MARKER).exists():
            return p
    return None


@dataclass
class IngestResult:
    dataset_id: str
    ingested: int        # records read from the input
    written: int         # new sample rows created
    merged: int          # records that collapsed onto an existing sample row
    deduped_blobs: int   # content blobs that were already present (CAS dedup)
    new_blobs: int       # content blobs newly written
    contaminated: int = 0  # records skipped because they overlap benchmark sets
    contam_sources: dict[str, int] = field(default_factory=dict)


class DataStore:
    def __init__(self, root: str | os.PathLike, codec: str = "zlib"):
        self.root = Path(root)
        self.cas = ContentStore(self.root, codec=codec)
        self.catalog = Catalog(self.root / "catalog.db")
        self.exports_dir = self.root / "exports"
        self._index = None

    @property
    def index(self):
        """Lazily-opened L3 index layer (vector + full-text)."""
        if self._index is None:
            from .index import IndexLayer
            self._index = IndexLayer(self.root)
        return self._index

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def init(cls, root: str | os.PathLike, codec: str = "zlib") -> "DataStore":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        marker = root / MARKER
        if not marker.exists():
            marker.write_text(
                f'# DataMixer warehouse\nversion = "0.1.0"\ncodec = "{codec}"\n'
            )
        (root / "exports").mkdir(exist_ok=True)
        return cls(root, codec=codec)

    @classmethod
    def open(cls, start: str | os.PathLike | None = None) -> "DataStore":
        root = find_root(start)
        if root is None:
            raise StoreError(
                "no DataMixer warehouse found (run `datamixer init` first)"
            )
        codec = "zlib"
        try:
            for line in (root / MARKER).read_text().splitlines():
                if line.strip().startswith("codec"):
                    codec = line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return cls(root, codec=codec)

    def close(self) -> None:
        self.catalog.close()
        if self._index is not None:
            self._index.close()

    # -- ingest ------------------------------------------------------------
    def ingest_records(
        self,
        dataset_id: str,
        records: Iterable[dict],
        defaults: dict[str, Any] | None = None,
        content_key: str = "content",
        allow_duplicates: bool = False,
        tokenizer: str | None = None,
        decontaminate: bool = True,
        contamination_threshold: float = 0.8,
    ) -> IngestResult:
        """Ingest an iterable of records.

        Each record is either ``{"content": <obj>, ...metadata}`` or, if no
        ``content`` key is present, the *whole record* is treated as content
        (common for raw JSONL corpora). Metadata is aligned to the schema;
        ``n_tokens`` is auto-filled with the selected ``tokenizer`` (default
        heuristic) when not supplied, and the tokenizer used is recorded.
        """
        from . import tokenizers
        defaults = defaults or {}
        tok = tokenizers.resolve(tokenizer)
        read_rows = accepted_rows = new = dup = written_rows = merged_rows = 0
        contaminated_rows = 0
        contam_sources: Counter[str] = Counter()
        contamination_sets = []
        if decontaminate:
            from . import contam

            contamination_sets = contam.load_sets(self.root)
        row_metadata_keys = set(schema.CORE_FIELD_NAMES) | {
            "bug_type",
            "failure_type",
            "error_type",
            "failure_taxonomy",
            "error_category",
            "processing_level",
            "source_kind",
            "source_uri",
            "source_domain",
            "split",
            "quality_findings",
            "loop_uuid",
            "version_id",
            "idempotency_key",
        }
        for rec in records:
            read_rows += 1
            if content_key in rec:
                content = rec[content_key]
                meta = {k: v for k, v in rec.items() if k != content_key}
            else:
                content = rec
                meta = {k: v for k, v in rec.items() if k in row_metadata_keys}
            if contamination_sets:
                from . import contam

                hit, source = contam.match(
                    utils.extract_text(content),
                    contamination_sets,
                    threshold=contamination_threshold,
                )
                if hit:
                    contaminated_rows += 1
                    contam_sources[source or "unknown"] += 1
                    continue
            merged = {**defaults, **meta}
            if "n_tokens" not in merged:
                merged["n_tokens"] = tok.count(utils.extract_text(content))
                merged.setdefault("tokenizer", tok.name)
            cid, blob_written = self.cas.put_json(content)
            new += int(blob_written)
            dup += int(not blob_written)
            # unique salt per record keeps every occurrence as its own row
            salt = accepted_rows if allow_duplicates else None
            _sid, created = self.catalog.add_sample(dataset_id, cid, merged, salt)
            written_rows += int(created)
            merged_rows += int(not created)
            accepted_rows += 1
            if read_rows % 2000 == 0:
                self.catalog.commit()
        self.catalog.commit()
        return IngestResult(
            dataset_id,
            read_rows,
            written_rows,
            merged_rows,
            dup,
            new,
            contaminated_rows,
            dict(contam_sources),
        )

    def get_content(self, cid: str) -> Any:
        return self.cas.get_json(cid)

    # -- compliance: PII redaction + right-to-erasure (#13) ----------------
    def redact_pii(self, where=None, dataset_id=None, types=None,
                   dry_run=False) -> dict:
        """Rewrite samples' content with PII masked. Repoints each sample at a
        new clean blob and removes the original if no longer referenced."""
        from collections import Counter
        from . import pii
        rows = self.catalog.query(where=where, dataset_id=dataset_id,
                                  columns="sample_id,cid")
        totals: Counter = Counter()
        redacted = 0
        for r in rows:
            try:
                content = self.get_content(r["cid"])
            except KeyError:
                continue
            new_content, counts = pii.redact_content(
                content, types or pii.DEFAULT_TYPES)
            if not counts:
                continue
            redacted += 1
            totals.update(counts)
            if dry_run:
                continue
            old_cid = r["cid"]
            new_cid, _ = self.cas.put_json(new_content)
            if new_cid != old_cid:
                self.catalog.update_cid(r["sample_id"], new_cid)
                self.catalog.update_fields(r["sample_id"], {
                    "pii_redacted": True, "pii_count": sum(counts.values())})
                if not self.catalog.cid_referenced(old_cid):
                    self.cas.delete(old_cid)        # destroy the original PII
        self.catalog.commit()
        return {"scanned": len(rows), "redacted": redacted,
                "by_type": dict(totals), "applied": not dry_run}

    def erase(self, sample_ids=None, where=None, dataset_id=None,
              reason=None) -> dict:
        """Right-to-erasure: tombstone samples from catalog + CAS + indexes,
        with an append-only audit record. After this they are invisible to
        query / recall / export."""
        import json as _json
        import time as _time
        if sample_ids:
            rows = [self.catalog.get_sample(s) for s in sample_ids]
            rows = [{"sample_id": r["sample_id"], "cid": r["cid"]}
                    for r in rows if r]
        else:
            rows = self.catalog.query(where=where, dataset_id=dataset_id,
                                      columns="sample_id,cid")
        ids = [r["sample_id"] for r in rows]
        cids = {r["cid"] for r in rows}
        if not ids:
            return {"erased": 0, "blobs_deleted": 0, "vectors_removed": 0}
        self.catalog.delete_by_ids(ids)
        vec_removed = 0
        try:
            vec_removed = self.index.remove(ids)
        except Exception:  # noqa: BLE001 - index optional
            pass
        blobs = 0
        for cid in cids:
            if not self.catalog.cid_referenced(cid) and self.cas.delete(cid):
                blobs += 1
        audit_dir = self.root / "lineage"
        audit_dir.mkdir(exist_ok=True)
        with open(audit_dir / "erasures.jsonl", "a", encoding="utf-8") as fh:
            fh.write(_json.dumps({"timestamp": _time.time(), "reason": reason,
                                  "count": len(ids), "sample_ids": ids,
                                  "cids": sorted(cids)}) + "\n")
        return {"erased": len(ids), "blobs_deleted": blobs,
                "vectors_removed": vec_removed}

    # -- storage stats (demonstrates the space savings) --------------------
    def storage_stats(self) -> dict:
        """Report content-storage efficiency.

        * ``compression_ratio`` -- raw vs. on-disk for the unique blobs.
        * ``dedup_ratio`` -- how many sample references collapse onto each
          unique blob (content addressing dedups across datasets for free).
        * ``content_savings_ratio`` -- combined content win (dedup x compress).

        The catalog DB size is reported separately (it stores metadata + the
        query indexes, not content) and not folded into the content savings.
        """
        # one pass over blobs: cid -> (raw, stored)
        raw_by_cid: dict[str, int] = {}
        stored = 0
        for p in self.cas.iter_blobs():
            cid = p.stem
            stored += p.stat().st_size
            from . import utils as _u
            raw_by_cid[cid] = len(_u.decompress(p.read_bytes(), _u.codec_from_ext(p.suffix)))
        unique_raw = sum(raw_by_cid.values())
        # reference-level logical size (counts a blob once per referencing sample)
        logical_raw = 0
        ref_samples = 0
        for cid, cnt in self.catalog.cid_counts().items():
            logical_raw += raw_by_cid.get(cid, 0) * cnt
            ref_samples += cnt
        db = self.catalog.db_size()
        return {
            "samples": self.catalog.count(),
            "unique_blobs": len(raw_by_cid),
            "logical_raw_bytes": logical_raw,
            "unique_raw_bytes": unique_raw,
            "stored_blob_bytes": stored,
            "catalog_db_bytes": db,
            "total_on_disk_bytes": stored + db,
            "dedup_ratio": round(logical_raw / unique_raw, 3) if unique_raw else 1.0,
            "compression_ratio": round(unique_raw / stored, 3) if stored else 1.0,
            "content_savings_ratio": round(logical_raw / stored, 3) if stored else 1.0,
            "token_sources": {(d["value"] or "unknown"): d["n"]
                              for d in self.catalog.distribution("tokenizer")},
        }


def read_jsonl(path: str | os.PathLike) -> Iterable[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
