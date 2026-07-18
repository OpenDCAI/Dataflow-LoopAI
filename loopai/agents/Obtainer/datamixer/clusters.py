"""Incremental embedding clusters for dataset-local sampling.

The cluster state is intentionally simple and local to a DataMixer warehouse:
each dataset gets a small online centroid model plus sample assignments. New
embeddings update the nearest centroid, so auto-embedding can keep the model
fresh without rebuilding the whole warehouse.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MAX_CLUSTERS = 32


def _cluster_dir(root: str | Path) -> Path:
    path = Path(root) / "clusters"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_dataset_id(dataset_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in dataset_id) or "dataset"


def _state_path(root: str | Path, dataset_id: str) -> Path:
    return _cluster_dir(root) / f"{_safe_dataset_id(dataset_id)}.json"


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def _normalize(vector: Iterable[float]) -> list[float]:
    values = [float(v) for v in vector]
    n = _norm(values)
    return [v / n for v in values]


def _cosine(a: list[float], b: list[float]) -> float:
    return _dot(a, b) / (_norm(a) * _norm(b))


def _closest(centroids: list[dict[str, Any]], vector: list[float]) -> tuple[int, float]:
    best_index = 0
    best_score = -2.0
    for index, centroid in enumerate(centroids):
        score = _cosine(vector, centroid["vector"])
        if score > best_score:
            best_index = index
            best_score = score
    return best_index, best_score


def _new_cluster_id(index: int) -> str:
    return f"cl_{index:04d}"


def _empty_state(dataset_id: str, *, embedding_model: str, embedding_dim: int, max_clusters: int) -> dict:
    return {
        "schema_version": "datamixer.embedding_clusters.v1",
        "dataset_id": dataset_id,
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "max_clusters": max_clusters,
        "created_at": time.time(),
        "updated_at": time.time(),
        "centroids": [],
        "assignments": {},
    }


def load_cluster_state(root: str | Path, dataset_id: str) -> dict | None:
    path = _state_path(root, dataset_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cluster_assignments(root: str | Path, sample_ids: set[str]) -> dict[str, dict]:
    """Return assignment metadata for the requested sample ids."""
    if not sample_ids:
        return {}
    out: dict[str, dict] = {}
    for path in _cluster_dir(root).glob("*.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for sample_id, assignment in (state.get("assignments") or {}).items():
            if sample_id in sample_ids:
                out[sample_id] = {
                    "dataset_id": state.get("dataset_id", ""),
                    "cluster_id": assignment.get("cluster_id", ""),
                    "cluster_similarity": float(assignment.get("similarity") or 0.0),
                    "embedding_model": state.get("embedding_model", ""),
                }
    return out


def update_dataset_clusters(
    root: str | Path,
    *,
    dataset_id: str,
    embeddings: Iterable[dict[str, Any]],
    max_clusters: int = DEFAULT_MAX_CLUSTERS,
) -> dict:
    """Incrementally assign new embedding rows to dataset-local clusters.

    `embeddings` rows need `record_id`, `vector`, and model metadata. Existing
    assignments are left untouched, making repeated calls idempotent.
    """
    rows = [row for row in embeddings if row.get("record_id") and row.get("vector")]
    if not rows:
        return {"dataset_id": dataset_id, "updated": 0, "clusters": 0}
    first = rows[0]
    dim = int(first.get("embedding_dim") or len(first.get("vector") or []))
    model = str(first.get("embedding_model") or "")
    state = load_cluster_state(root, dataset_id) or _empty_state(
        dataset_id,
        embedding_model=model,
        embedding_dim=dim,
        max_clusters=max_clusters,
    )
    if state.get("embedding_model") != model or int(state.get("embedding_dim") or 0) != dim:
        state = _empty_state(
            dataset_id,
            embedding_model=model,
            embedding_dim=dim,
            max_clusters=max_clusters,
        )
    centroids: list[dict[str, Any]] = state.setdefault("centroids", [])
    assignments: dict[str, dict] = state.setdefault("assignments", {})
    updated = 0
    for row in rows:
        sample_id = str(row["record_id"])
        if sample_id in assignments:
            continue
        vector = _normalize(row["vector"])
        if not centroids or len(centroids) < int(state.get("max_clusters") or max_clusters):
            cluster_id = _new_cluster_id(len(centroids))
            centroids.append({"cluster_id": cluster_id, "vector": vector, "count": 1})
            similarity = 1.0
        else:
            index, similarity = _closest(centroids, vector)
            centroid = centroids[index]
            count = int(centroid.get("count") or 0)
            old = centroid["vector"]
            merged = [(old[i] * count + vector[i]) / (count + 1) for i in range(min(len(old), len(vector)))]
            centroid["vector"] = _normalize(merged)
            centroid["count"] = count + 1
            cluster_id = centroid["cluster_id"]
        assignments[sample_id] = {
            "cluster_id": cluster_id,
            "similarity": float(similarity),
            "created_at": time.time(),
        }
        updated += 1
    state["updated_at"] = time.time()
    _state_path(root, dataset_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "dataset_id": dataset_id,
        "updated": updated,
        "clusters": len(centroids),
        "assigned": len(assignments),
        "embedding_model": state.get("embedding_model", ""),
    }
