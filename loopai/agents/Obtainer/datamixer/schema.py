"""Unified tag schema -- the contract every sample is aligned to (L2).

This is intentionally a *versioned* declaration. The columns listed in
``CORE_FIELDS`` are promoted to real, indexed SQLite columns for fast filtering
and recipe bucketing; anything else a contributor attaches rides along in a
``tags`` JSON blob so the schema can grow without migrations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0"

# Allowed enum-ish vocabularies (kept loose for the MVP; enforced softly).
MODALITIES = ("text", "image", "audio", "video", "interleaved")
STAGES = ("pretrain", "sft", "preference", "agent_trajectory")


@dataclass(frozen=True)
class Field:
    name: str
    sql_type: str          # SQLite column affinity
    dim: str               # which schema dimension this belongs to
    indexed: bool = False
    desc: str = ""


# The promoted, queryable columns. Order matters for table creation.
CORE_FIELDS: tuple[Field, ...] = (
    Field("modality", "TEXT", "modality", True, "text/image/audio/video/interleaved"),
    Field("stage", "TEXT", "stage", True, "pretrain/sft/preference/agent_trajectory"),
    Field("lang", "TEXT", "language", True, "primary language code"),
    Field("lang_conf", "REAL", "language", False, "language id confidence 0..1"),
    Field("domain", "TEXT", "domain", True, "code/math/web/science/..."),
    Field("source", "TEXT", "source", True, "origin dataset / corpus"),
    Field("license", "TEXT", "source", False, "license identifier"),
    Field("quality_score", "REAL", "quality", True, "0..1 quality estimate"),
    Field("toxicity", "REAL", "quality", False, "0..1 toxicity estimate"),
    Field("dedup_cluster_id", "TEXT", "quality", True, "near-dup cluster id"),
    Field("perplexity", "REAL", "quality", False, "model perplexity"),
    Field("resolution", "TEXT", "multimodal", False, "WxH for images/video"),
    Field("duration", "REAL", "multimodal", False, "seconds for audio/video"),
    Field("aspect_ratio", "REAL", "multimodal", False, "width/height"),
    Field("task_type", "TEXT", "agent", True, "agent task category"),
    Field("reward", "REAL", "agent", False, "trajectory reward"),
    Field("trajectory_len", "INTEGER", "agent", False, "steps in trajectory"),
    Field("tool_calls", "INTEGER", "agent", False, "number of tool calls"),
    Field("n_tokens", "INTEGER", "stats", True, "estimated token count"),
    Field("tokenizer", "TEXT", "stats", True,
          "tokenizer that produced n_tokens (heuristic/tiktoken:.../hf:...)"),
    Field("is_contaminated", "INTEGER", "contamination", True,
          "1 if overlapping a registered benchmark (decontaminate)"),
    Field("contam_source", "TEXT", "contamination", False,
          "benchmark set(s) this sample overlaps"),
)

CORE_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in CORE_FIELDS)
_NUMERIC = {"REAL", "INTEGER"}


def coerce(field_name: str, value: Any) -> Any:
    """Best-effort cast of an incoming value to the declared column type."""
    if value is None or value == "":
        return None
    for f in CORE_FIELDS:
        if f.name == field_name:
            try:
                if f.sql_type == "REAL":
                    return float(value)
                if f.sql_type == "INTEGER":
                    return int(value)
                return str(value)
            except (TypeError, ValueError):
                return None
    return value


def split_metadata(meta: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Partition an incoming metadata dict into (core_columns, extra_tags)."""
    core: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for k, v in meta.items():
        if k in CORE_FIELD_NAMES:
            core[k] = coerce(k, v)
        else:
            extra[k] = v
    return core, extra


def describe() -> dict[str, Any]:
    """Machine-readable schema contract (used by ``datamixer schema``)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "modalities": list(MODALITIES),
        "stages": list(STAGES),
        "fields": [
            {
                "name": f.name,
                "type": f.sql_type,
                "dimension": f.dim,
                "indexed": f.indexed,
                "description": f.desc,
            }
            for f in CORE_FIELDS
        ],
        "extensible": True,
        "note": "Unknown fields are preserved in the per-sample 'tags' JSON.",
    }
