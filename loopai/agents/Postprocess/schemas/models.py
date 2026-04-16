"""
Pydantic models for the Postprocess sub-agent system.

Every LLM-facing output and every inter-node data contract is defined here
with strict JSON Schema so that prompts can reference the schema directly.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NextAction(str, Enum):
    FILE_READ = "file_read"
    WEB_SEARCH = "web_search"
    DATA_LOAD = "data_load"
    FINISH = "finish"


class EvidenceSufficiency(str, Enum):
    INSUFFICIENT = "insufficient"
    PARTIAL = "partial"
    SUFFICIENT = "sufficient"


# ---------------------------------------------------------------------------
# Dataset discovery & profiling
# ---------------------------------------------------------------------------

class DatasetSourceInfo(BaseModel):
    """Metadata for a single discovered dataset source directory."""
    source_type: str = Field(..., description="hf_datasets | kaggle_datasets | web_downloads")
    dataset_name: str = Field(..., description="Name of the dataset directory")
    dataset_dir: str = Field(..., description="Absolute path to the dataset directory")
    readme_files: List[str] = Field(default_factory=list, description="Paths of README / doc files found")
    data_files: List[str] = Field(default_factory=list, description="Paths of actual data files found")
    script_files: List[str] = Field(default_factory=list, description="Paths of .py / .sh helper scripts found")
    other_files: List[str] = Field(default_factory=list, description="Any other files found")


class DatasetFolderProfile(BaseModel):
    """Profile produced after scanning a dataset folder's file tree."""
    dataset_name: str
    source_type: str
    total_files: int = 0
    readme_files: List[str] = Field(default_factory=list)
    data_files: List[str] = Field(default_factory=list)
    script_files: List[str] = Field(default_factory=list)
    estimated_size_mb: float = 0.0


# ---------------------------------------------------------------------------
# Knowledge gathered by the agent
# ---------------------------------------------------------------------------

class DatasetKnowledgeSummary(BaseModel):
    """Condensed knowledge about a dataset, written to long-term memory."""
    dataset_name: str = Field(..., description="Dataset identifier")
    description: str = Field("", description="Brief description of the dataset")
    source_url: str = Field("", description="Original URL if known")
    domain: str = Field("", description="Domain / topic area")
    license: str = Field("", description="License information")
    available_fields: List[str] = Field(default_factory=list, description="Column / field names discovered")
    record_count_estimate: Optional[int] = Field(None, description="Estimated number of records")
    format_info: str = Field("", description="File format details (json, csv, parquet, etc.)")
    notes: str = Field("", description="Additional observations")


class FieldMapping(BaseModel):
    """Mapping specification from a source field to a target role."""
    source_field: str = Field(..., description="Field name in the original dataset")
    target_role: str = Field(..., description="Target role: text | user | assistant | system | meta")
    content_fields: Optional[List[str]] = Field(
        None,
        description="For composite fields, list of sub-fields to concatenate",
    )
    loss_mask: Optional[bool] = Field(None, description="Loss mask flag for SFT messages")


class DatasetFieldInventory(BaseModel):
    """Inventory of all fields in a dataset with their proposed mapping."""
    dataset_name: str
    category: str = Field(..., description="PT or SFT")
    available_fields: List[str] = Field(default_factory=list)
    field_descriptions: Dict[str, str] = Field(
        default_factory=dict,
        description="Short description of each field inferred from data / docs",
    )
    proposed_mappings: List[FieldMapping] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Data sampling
# ---------------------------------------------------------------------------

class DatasetSamplePreview(BaseModel):
    """A small sample of actual data records for LLM inspection."""
    dataset_name: str
    file_path: str = ""
    split_name: str = ""
    column_names: List[str] = Field(default_factory=list)
    sample_records: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="1-3 raw records from the dataset",
    )
    total_records: int = 0


# ---------------------------------------------------------------------------
# Relevance verdict (postprocess v2 — no field mapping)
# ---------------------------------------------------------------------------

class DatasetRelevanceVerdict(BaseModel):
    """Relevance to the task plus benchmark-leak guard (exclude eval/benchmark from train export)."""

    related: bool = Field(
        ...,
        description=(
            "True if the dataset is relevant to user_query and benchmark reference "
            "(semantically on-topic, same domain/task, or partially overlapping)."
        ),
    )
    is_benchmark_data: bool = Field(
        default=False,
        description=(
            "True if this dataset is (or is substantially) the same as the evaluation benchmark: "
            "e.g. identical/near-identical samples vs provided benchmark references, README or path "
            "identifies HumanEval/MBPP/official test split, or it is clearly the held-out eval set. "
            "Such data must not be exported as training data to avoid leakage."
        ),
    )
    reason: str = Field(
        "",
        description="Short justification; mention both related and (if applicable) benchmark-leak findings.",
    )


# ---------------------------------------------------------------------------
# Mapping plan (final structured output of the dataset agent)
# ---------------------------------------------------------------------------

class DatasetMappingPlan(BaseModel):
    """
    The definitive mapping plan produced by the dataset agent.
    This is the equivalent of the old postprocess_node's `annotation_result`
    but richer and strictly typed.
    """
    dataset_name: str
    category: str = Field(..., description="PT or SFT")
    confidence: float = Field(
        1.0, ge=0.0, le=1.0,
        description="Agent confidence in this mapping (0-1)",
    )
    quality_label: str = Field(
        "qualified",
        description=(
            "Mapping quality label: 'qualified' means safe for conversion; "
            "'unqualified' means dataset needs manual handling and should be routed "
            "to the unqualified output folder."
        ),
    )
    quality_reason: str = Field(
        "",
        description="Reason for quality_label decision, especially when unqualified.",
    )

    # PT mapping
    text_field: Optional[Any] = Field(
        None,
        description="For PT: field name(s) to concatenate as text. str or list[str].",
    )

    # SFT mapping
    messages: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "For SFT: message mapping specs. "
            "Each dict has 'role', 'content' (field name or list), optional 'loss_mask'."
        ),
    )
    system: Optional[Any] = Field(
        None,
        description="For SFT: field spec (str or list[str]) to build system prompt",
    )

    # Metadata
    record_path: Optional[str] = Field(
        None,
        description=(
            "Optional path (dot notation) to extract record list from a top-level dict. "
            "Example: 'data' or 'examples.items'."
        ),
    )
    field_joiners: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional joiners for composite field assembly. "
            "Supports per-role keys ('system'/'user'/'assistant') and "
            "per-field keys using 'field:<name>'."
        ),
    )
    field_transforms: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional transform hints for extracted values. "
            "Supports keys like role name or 'field:<name>', values like "
            "'strip' or 'json_dumps'."
        ),
    )
    meta_fields: Dict[str, str] = Field(
        default_factory=dict,
        description="Additional fields to include in meta dict. key=meta_key, value=source_field",
    )
    reasoning: str = Field(
        "",
        description="Brief explanation of why this mapping was chosen",
    )

    @classmethod
    def json_schema_str(cls) -> str:
        return json.dumps(cls.model_json_schema(), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main agent decision (per-step structured output)
# ---------------------------------------------------------------------------

class MainAgentDecision(BaseModel):
    """Structured decision output by the main agent at each reasoning step."""
    current_knowledge_summary: str = Field(
        ..., description="What the agent currently knows about this dataset",
    )
    evidence_sufficiency: EvidenceSufficiency = Field(
        ..., description="Whether gathered evidence is sufficient for mapping",
    )
    next_action: NextAction = Field(
        ..., description="What to do next",
    )
    action_reason: str = Field(
        ..., description="Why this action was chosen",
    )
    is_complete: bool = Field(
        False, description="Whether the dataset task is complete",
    )
    completion_reason: str = Field(
        "", description="If complete, why",
    )

    @classmethod
    def json_schema_str(cls) -> str:
        return json.dumps(cls.model_json_schema(), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Per-dataset agent result
# ---------------------------------------------------------------------------

class DatasetAgentResult(BaseModel):
    """Result produced by a single dataset agent run."""
    dataset_name: str
    source_type: str
    dataset_dir: str
    success: bool = False
    relevance_verdict: Optional[DatasetRelevanceVerdict] = None
    mapping_plan: Optional[DatasetMappingPlan] = None
    knowledge_summary: Optional[DatasetKnowledgeSummary] = None
    records_processed: int = 0
    unqualified_records: int = 0
    output_files: List[str] = Field(default_factory=list)
    unqualified_files: List[str] = Field(default_factory=list)
    log_file: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Final merge result
# ---------------------------------------------------------------------------

class PostprocessMergeResult(BaseModel):
    """Aggregated result across all dataset agents."""
    total_records_processed: int = 0
    processed_sources_count: int = 0
    output_dir: str = ""
    per_dataset_results: List[DatasetAgentResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
