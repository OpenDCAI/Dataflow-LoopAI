from __future__ import annotations

import json

from loopai.agents.Obtainer.datamixer.cas import ContentStore


def test_cas_cache_serves_repeated_cid_without_re_read(tmp_path, monkeypatch) -> None:
    store = ContentStore(tmp_path, cache_size=8)
    cid, written = store.put_json({"text": "hello world"})
    assert written is True

    # seed cache
    assert store.get_json(cid) == {"text": "hello world"}

    # remove the blob from disk; cache must still serve it
    for p in (tmp_path / "blobs").rglob("*"):
        if p.is_file():
            p.unlink()

    assert store.get_json(cid) == {"text": "hello world"}

    # delete evicts the cache entry even when the blob file is already gone
    assert store.delete(cid) is False
    try:
        store.get_json(cid)
        raise AssertionError("expected KeyError after delete")
    except KeyError:
        pass


def test_cas_cache_is_bounded_and_lru(tmp_path) -> None:
    store = ContentStore(tmp_path, cache_size=3)
    cids = []
    for i in range(5):
        cid, _ = store.put_json({"i": i})
        cids.append(cid)
        store.get_json(cid)  # warm cache
    assert len(store._cache) == 3
    # most recent three are cids[2], cids[3], cids[4]
    assert list(store._cache) == cids[2:]
    # touching cids[1] makes it most-recent
    store.get_json(cids[1])
    assert list(store._cache) == [cids[3], cids[4], cids[1]]


def test_cas_cache_disabled_when_size_zero(tmp_path) -> None:
    store = ContentStore(tmp_path, cache_size=0)
    cid, _ = store.put_json({"text": "x"})
    store.get_json(cid)
    assert len(store._cache) == 0
    store.get_json(cid)  # still works (reads from disk)
    assert json.loads(store.get(cid)) == {"text": "x"}
