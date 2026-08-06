"""Operator layer (L4): pluggable data-processing operators + pipeline runner."""
from .base import (
    Batch,
    DataFlowAdapter,
    Operator,
    OperatorContext,
    OperatorSpec,
    Row,
    available,
    create,
    is_registered,
    param_info,
    register,
)
from . import builtin  # noqa: F401 - side effect: register built-ins
from . import llm  # noqa: F401 - side effect: register LLM operators
from . import posttrain  # noqa: F401 - side effect: register post-training ops
from . import scorers  # noqa: F401 - side effect: register model-based scorers
from . import dataflow  # noqa: F401 - side effect: register the DataFlow bridge
from . import webpage  # noqa: F401 - side effect: register webpage/PT/SFT ops
from . import domain  # noqa: F401 - side effect: register domain-specific L3 ops
from .dataflow import (
    DataFlowBridge,
    DataFlowStorage,
    dataflow_available,
    load_dataflow_operator,
)
from .llm import DomainClassify, LLMOperator
from .webpage import PTToSFTQA, WebpageToPT
from .pipeline import PipelineResult, load_pipeline, run_pipeline
from .streaming import PersistentPipelineQueue, StreamingPipelineRunner

__all__ = [
    "Batch",
    "Row",
    "Operator",
    "OperatorContext",
    "OperatorSpec",
    "DataFlowAdapter",
    "DataFlowBridge",
    "DataFlowStorage",
    "dataflow_available",
    "load_dataflow_operator",
    "register",
    "create",
    "available",
    "is_registered",
    "load_pipeline",
    "run_pipeline",
    "PipelineResult",
    "PersistentPipelineQueue",
    "StreamingPipelineRunner",
    "LLMOperator",
    "DomainClassify",
    "WebpageToPT",
    "PTToSFTQA",
]
