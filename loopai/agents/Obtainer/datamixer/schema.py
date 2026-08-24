"""Unified tag schema -- the contract every sample is aligned to (L2).

This is intentionally a *versioned* declaration. The columns listed in
``CORE_FIELDS`` are promoted to real, indexed SQLite columns for fast filtering
and recipe bucketing; anything else a contributor attaches rides along in a
``tags`` JSON blob so the schema can grow without migrations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1.1.0"

# Allowed enum-ish vocabularies (kept loose for the MVP; enforced softly).
MODALITIES = ("text", "image", "audio", "video", "interleaved")
STAGES = ("pretrain", "sft", "preference", "agent_trajectory")
QUALITY_LEVELS = ("L1", "L2", "L3", "L4")

# A lake starts with a useful, stable vocabulary for broad-content routing.  It
# is deliberately not an enum for ``samples.domain``: each warehouse can add
# its own domains (for example ``text2sql`` or ``robotics``) without a schema
# migration.  ``Catalog`` persists these values in its domain registry and the
# LLM classifier adds the lake's registered values to this baseline at run
# time.
DEFAULT_DOMAIN_CLASSES = (
    "general",
    "code",
    "math",
    "science",
    "medical",
    "finance",
    "law",
    "education",
    "engineering",
    "business",
    "social_science",
    "humanities",
    "web",
    "other",
)


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
    Field(
        "quality_level",
        "TEXT",
        "quality",
        True,
        "required data quality level: L1/L2/L3/L4",
    ),
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


def validate_quality_level(value: Any) -> str:
    """Return a valid explicit quality level or raise a clear error."""
    if value is None or value == "":
        raise ValueError(
            "quality_level is required and must be one of: "
            + ", ".join(QUALITY_LEVELS)
        )
    if not isinstance(value, str) or value not in QUALITY_LEVELS:
        raise ValueError(
            f"invalid quality_level {value!r}; expected one of: "
            + ", ".join(QUALITY_LEVELS)
        )
    return value


def describe() -> dict[str, Any]:
    """Machine-readable schema contract (used by ``datamixer schema``)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "modalities": list(MODALITIES),
        "stages": list(STAGES),
        "quality_levels": list(QUALITY_LEVELS),
        "default_domain_classes": list(DEFAULT_DOMAIN_CLASSES),
        "fields": [
            {
                "name": f.name,
                "type": f.sql_type,
                "dimension": f.dim,
                "indexed": f.indexed,
                "description": f.desc,
                **(
                    {"allowed_values": list(QUALITY_LEVELS)}
                    if f.name == "quality_level"
                    else {}
                ),
            }
            for f in CORE_FIELDS
        ],
        "extensible": True,
        "note": "Unknown fields are preserved in the per-sample 'tags' JSON.",
    }
