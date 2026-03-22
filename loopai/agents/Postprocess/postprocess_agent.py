"""
PostprocessAgent — top-level orchestrator.

This module exposes the single entry-point ``run_postprocess_agent_v2``
that can be called from ``Constructor.postprocess_node_wrapper`` when
the version switch is set to ``agent_v2``.

Internally it:
  1. Routes — discovers dataset source directories
  2. Dispatches — runs independent DatasetAgent instances (one per dataset)
  3. Merges — aggregates results into the legacy-compatible contract

The function returns a dict that can be plugged straight into
``state["constructor"]["postprocess_results"]`` and
``state["constructor"]["intermediate_data_path"]``.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from langgraph.config import get_stream_writer

from loopai.logger import get_logger
from loopai.schema.events import StreamEvent

from .memory import PostprocessMemoryManager
from .nodes.dispatch_node import dispatch_node
from .nodes.merge_node import merge_node
from .nodes.router_node import router_node

logger = get_logger()


def _emit(event_name: str, message: str, progress: Optional[float] = None, data: Optional[Dict] = None):
    try:
        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=event_name,
                message=message,
                progress=progress,
                data=data,
            ).json())
    except Exception:
        pass


async def _run_async(
    download_dir: str,
    user_query: str,
    category: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    datasets_background: str,
    tavily_api_key: Optional[str],
    store: Any,
    thread_id: str,
    event_name: str,
    max_concurrent: int = 3,
) -> Dict[str, Any]:
    """Async core of the v2 postprocess pipeline."""

    memory = PostprocessMemoryManager(store, thread_id) if store else None

    # 1. Route
    _emit(event_name, "Postprocess v2: 扫描数据源目录", progress=0.05)
    sources = router_node(download_dir)

    if not sources:
        logger.warning("[PostprocessAgent] No dataset sources found.")
        return {"total_records_processed": 0, "processed_sources_count": 0, "output_dir": ""}

    if memory:
        memory.on_orchestrator_start(download_dir, [s.model_dump() for s in sources])

    # 2. Dispatch
    output_dir = os.path.join(download_dir, "processed_output")
    os.makedirs(output_dir, exist_ok=True)

    _emit(event_name, f"Postprocess v2: 调度 {len(sources)} 个数据集 Agent", progress=0.10)

    per_dataset = await dispatch_node(
        sources=sources,
        category=category,
        user_query=user_query,
        datasets_background=datasets_background,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        tavily_api_key=tavily_api_key,
        output_dir=output_dir,
        memory_manager=memory,
        max_concurrent=max_concurrent,
    )

    # 3. Merge
    _emit(event_name, "Postprocess v2: 汇总结果", progress=0.90)
    merged = merge_node(per_dataset, output_dir)

    if memory:
        memory.on_orchestrator_end(merged.model_dump())

    _emit(
        event_name,
        f"Postprocess v2: 完成 — {merged.total_records_processed} 条记录",
        progress=1.0,
        data={
            "total_records_processed": merged.total_records_processed,
            "processed_sources_count": merged.processed_sources_count,
            "output_dir": merged.output_dir,
        },
    )

    return {
        "total_records_processed": merged.total_records_processed,
        "processed_sources_count": merged.processed_sources_count,
        "output_dir": merged.output_dir,
        "dataset_log_files": [r.log_file for r in per_dataset if getattr(r, "log_file", None)],
        "unqualified_output_dir": os.path.join(output_dir, "unqualified"),
        "total_unqualified_records": sum(getattr(r, "unqualified_records", 0) for r in per_dataset),
        "unqualified_files": [
            fp
            for r in per_dataset
            for fp in (getattr(r, "unqualified_files", None) or [])
        ],
    }


# ---------------------------------------------------------------------------
# Public synchronous entry-point
# ---------------------------------------------------------------------------

def run_postprocess_agent_v2(
    download_dir: str,
    user_query: str,
    category: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.0,
    datasets_background: str = "",
    tavily_api_key: Optional[str] = None,
    store: Any = None,
    thread_id: str = "default",
    event_name: str = "ConstructorAgent.postprocess_node",
    max_concurrent: int = 3,
) -> Dict[str, Any]:
    """Synchronous wrapper suitable for calling from a LangGraph node.

    Returns a dict with the same keys as the legacy postprocess_node:
      - total_records_processed
      - processed_sources_count
      - output_dir

    Or ``{"exception": "..."}`` on failure.
    """
    try:
        return asyncio.run(
            _run_async(
                download_dir=download_dir,
                user_query=user_query,
                category=category,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                datasets_background=datasets_background,
                tavily_api_key=tavily_api_key,
                store=store,
                thread_id=thread_id,
                event_name=event_name,
                max_concurrent=max_concurrent,
            )
        )
    except Exception as e:
        logger.error(f"[PostprocessAgent] run_postprocess_agent_v2 failed: {e}", exc_info=True)
        return {"exception": f"Postprocess agent v2 error: {str(e)}"}
