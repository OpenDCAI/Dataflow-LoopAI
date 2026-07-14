"""Dataset snapshots / content-addressed data state (#7 / #21).

The project's slogan is "Git for LLM training data", but the recipe fingerprint
only pins the *config* — re-running the same recipe after the catalog changes
yields a different dataset. A **snapshot** pins the *data*: an immutable, content
-addressed manifest of an exact sample set, so an export is reproducible as the
pair ``(recipe_fingerprint, dataset_digest)``.

Stored under ``<root>/snapshots/<id>.json`` with the member list inline so a
snapshot can be re-materialized later (``recipe export --from-snapshot``).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from . import utils

_MEMBER_COLS = "sample_id,cid,n_tokens"


def digest(members: list[dict]) -> str:
    """Order-independent content digest of a sample set (sample_id + cid)."""
    h = hashlib.sha256()
    for m in sorted(members, key=lambda r: r["sample_id"]):
        h.update(f"{m['sample_id']}\t{m['cid']}\n".encode("utf-8"))
    return h.hexdigest()


def selection_digest(selected) -> str:
    """Digest for a list of recipe ``Selected`` items (for export manifests)."""
    return digest([{"sample_id": s.sample_id, "cid": s.cid} for s in selected])


def _members(store, where=None, dataset_id=None) -> list[dict]:
    rows = store.catalog.query(where=where, dataset_id=dataset_id,
                               columns=_MEMBER_COLS)
    rows.sort(key=lambda r: r["sample_id"])
    return rows


def create(store, name=None, where=None, dataset=None) -> dict:
    ds_id = store.catalog.resolve_dataset(dataset) if dataset else None
    members = _members(store, where, ds_id)
    dg = digest(members)
    sid = "snap-" + utils.fingerprint({"d": dg, "ts": time.time()})
    d = Path(store.root) / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "id": sid, "name": name, "created_at": time.time(),
        "count": len(members),
        "tokens": sum(m.get("n_tokens") or 0 for m in members),
        "digest": dg, "filter": where, "dataset": dataset,
        "members": members,
    }
    (d / f"{sid}.json").write_text(utils.canonical_json(doc).decode())
    return {k: v for k, v in doc.items() if k != "members"}


def _path(store, sid: str) -> Path:
    return Path(store.root) / "snapshots" / f"{sid}.json"


def get(store, sid: str) -> dict:
    p = _path(store, sid)
    if not p.exists():
        raise KeyError(f"snapshot not found: {sid}")
    return utils.loads(p.read_bytes())


def list_snapshots(store) -> list[dict]:
    d = Path(store.root) / "snapshots"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("snap-*.json")):
        try:
            doc = utils.loads(p.read_bytes())
            out.append({k: v for k, v in doc.items() if k != "members"})
        except (ValueError, OSError):
            continue
    return out


def diff(store, a: str, b: str) -> dict:
    da, db = get(store, a), get(store, b)
    sa = {m["sample_id"] for m in da["members"]}
    sb = {m["sample_id"] for m in db["members"]}
    return {
        "a": {"id": a, "count": da["count"], "digest": da["digest"]},
        "b": {"id": b, "count": db["count"], "digest": db["digest"]},
        "identical": da["digest"] == db["digest"],
        "added": sorted(sb - sa)[:1000],
        "removed": sorted(sa - sb)[:1000],
        "n_added": len(sb - sa), "n_removed": len(sa - sb),
    }
