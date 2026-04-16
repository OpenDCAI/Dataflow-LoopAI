"""
PostprocessAgent — top-level orchestrator.

``run_postprocess_agent_v2`` (Constructor ``agent_v2``) does:

  1. **Route** — discover dataset folders under the download directory
  2. **Dispatch** — per-dataset sub-agent: only ``file_read`` + ``data_load``,
     then LLM outputs ``{related, reason}`` vs user_query + benchmark samples
  3. **Export** — if ``related``, stream all rows from that source’s data files
     into ``processed_output/related_jsonl/<source_type>__<dataset>.jsonl``
     as raw dicts (original keys preserved, ``default=str`` for odd types)
  4. **Merge** — aggregate counts for the legacy postprocess contract

No field mapping or PT/SFT intermediate conversion.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from langgraph.config import get_stream_writer

from loopai.logger import get_logger
from loopai.schema.events import StreamEvent

from .hf_datasets_cache import ensure_postprocess_hf_cache_env
from .memory import PostprocessMemoryManager
from .nodes.dispatch_node import RELATED_JSONL_SUBDIR, dispatch_node
from .nodes.merge_node import merge_node
from .nodes.router_node import router_node
from .tools import discover_benchmark_sources, sample_benchmark_sources, sample_benchmark_sources_agent_v2

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


def _emit_postprocess_v2(
    event_name: str,
    message: str,
    *,
    progress: float,
    phase: str,
    stage: str,
    category: str,
    download_dir: str = "",
    output_dir: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Structured custom stream payload aligned with ConstructorAgent._emit_stream_event style."""
    data: Dict[str, Any] = {
        "agent": "constructor",
        "pipeline": "postprocess_v2",
        "phase": phase,
        "stage": stage,
        "category": category,
    }
    if download_dir:
        data["download_dir"] = download_dir
    if output_dir:
        data["output_dir"] = output_dir
    if extra:
        data.update(extra)
    _emit(event_name, message, progress, data)


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
    benchmark_dir: str = "",
    enable_benchmark_reference: bool = True,
) -> Dict[str, Any]:
    """Async core of the v2 postprocess pipeline."""

    memory = PostprocessMemoryManager(store, thread_id) if store else None

    # 1. Route
    sources = router_node(download_dir)
    benchmark_sources = [s for s in sources if s.source_type == "benchmark_datasets"]
    normal_sources = [s for s in sources if s.source_type != "benchmark_datasets"]

    # Allow benchmark data to come from an explicit directory, while staying backward compatible.
    if benchmark_dir and os.path.isdir(benchmark_dir):
        external_benchmark = discover_benchmark_sources(benchmark_dir)
        if external_benchmark:
            benchmark_sources = external_benchmark

    output_dir = os.path.join(download_dir, "processed_output")

    if not normal_sources and not benchmark_sources:
        logger.warning("[PostprocessAgent] No dataset sources found.")
        _emit_postprocess_v2(
            event_name,
            "Postprocess v2: 未发现数据源",
            progress=1.0,
            phase="route",
            stage="no_sources",
            category=category,
            download_dir=download_dir,
            output_dir=output_dir if os.path.isdir(output_dir) else "",
            extra={
                "normal_source_count": 0,
                "benchmark_source_count": 0,
            },
        )
        return {"total_records_processed": 0, "processed_sources_count": 0, "output_dir": ""}

    _emit_postprocess_v2(
        event_name,
        "Postprocess v2: 路由完成",
        progress=0.08,
        phase="route",
        stage="completed",
        category=category,
        download_dir=download_dir,
        output_dir=output_dir,
        extra={
            "normal_source_count": len(normal_sources),
            "benchmark_source_count": len(benchmark_sources),
            "total_source_count": len(sources),
        },
    )

    if memory:
        memory.on_orchestrator_start(download_dir, [s.model_dump() for s in normal_sources])

    # 2. Dispatch
    os.makedirs(output_dir, exist_ok=True)
    hf_datasets_cache = ensure_postprocess_hf_cache_env(output_dir)
    benchmark_samples: list[dict] = []
    benchmark_samples_file = ""
    benchmark_source_count = len(benchmark_sources)
    benchmark_sampled_count = 0
    benchmark_sampling_failures = 0

    if enable_benchmark_reference and benchmark_sources:
        # Prefer constrained agent_v2 sampler for benchmark references.
        # Fall back to legacy sampler to preserve backward compatibility.
        try:
            benchmark_sample_result = await sample_benchmark_sources_agent_v2(
                benchmark_sources=benchmark_sources,
                output_dir=output_dir,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                max_concurrent=max_concurrent,
                hf_datasets_cache_dir=hf_datasets_cache,
            )
        except Exception:
            logger.warning("[PostprocessAgent] benchmark agent_v2 sampler failed, fallback to legacy sampler", exc_info=True)
            benchmark_sample_result = sample_benchmark_sources(
                benchmark_sources=benchmark_sources,
                output_dir=output_dir,
                max_per_dataset=1,
                hf_datasets_cache_dir=hf_datasets_cache,
            )
        benchmark_samples = benchmark_sample_result.get("benchmark_samples", [])
        benchmark_samples_file = benchmark_sample_result.get("benchmark_samples_file", "")
        benchmark_source_count = int(benchmark_sample_result.get("benchmark_source_count", benchmark_source_count))
        benchmark_sampled_count = int(benchmark_sample_result.get("benchmark_sampled_count", 0))
        benchmark_sampling_failures = int(benchmark_sample_result.get("benchmark_sampling_failures", 0))
        logger.info(
            f"[PostprocessAgent] benchmark reference sampled: "
            f"sources={benchmark_source_count}, sampled={benchmark_sampled_count}, "
            f"failures={benchmark_sampling_failures}, file={benchmark_samples_file}"
        )

    dispatch_extra: Dict[str, Any] = {
        "normal_source_count": len(normal_sources),
        "max_concurrent": max_concurrent,
        "benchmark_reference_enabled": bool(enable_benchmark_reference and benchmark_sources),
        "benchmark_source_count": benchmark_source_count,
        "benchmark_sampled_count": benchmark_sampled_count,
        "benchmark_sampling_failures": benchmark_sampling_failures,
    }
    if benchmark_samples_file:
        dispatch_extra["benchmark_samples_file"] = benchmark_samples_file

    _emit_postprocess_v2(
        event_name,
        f"Postprocess v2: 相关性判定 {len(normal_sources)} 个数据集",
        progress=0.18,
        phase="dispatch",
        stage="start",
        category=category,
        download_dir=download_dir,
        output_dir=output_dir,
        extra=dispatch_extra,
    )

    per_dataset = await dispatch_node(
        sources=normal_sources,
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
        benchmark_reference_samples=benchmark_samples,
        hf_datasets_cache_dir=hf_datasets_cache,
    )

    # 3. Merge
    _emit_postprocess_v2(
        event_name,
        "Postprocess v2: 汇总结果",
        progress=0.90,
        phase="merge",
        stage="start",
        category=category,
        download_dir=download_dir,
        output_dir=output_dir,
        extra={"dataset_results_count": len(per_dataset)},
    )
    merged = merge_node(per_dataset, output_dir)

    if memory:
        memory.on_orchestrator_end(merged.model_dump())

    _emit_postprocess_v2(
        event_name,
        f"Postprocess v2: 完成 — {merged.total_records_processed} 条记录",
        progress=1.0,
        phase="completed",
        stage="done",
        category=category,
        download_dir=download_dir,
        output_dir=merged.output_dir,
        extra={
            "total_records_processed": merged.total_records_processed,
            "processed_sources_count": merged.processed_sources_count,
            "benchmark_source_count": benchmark_source_count,
            "benchmark_sampled_count": benchmark_sampled_count,
            "benchmark_sampling_failures": benchmark_sampling_failures,
            "benchmark_samples_file": benchmark_samples_file,
        },
    )

    related_jsonl_dir = os.path.join(output_dir, RELATED_JSONL_SUBDIR) if output_dir else ""
    return {
        "total_records_processed": merged.total_records_processed,
        "processed_sources_count": merged.processed_sources_count,
        "output_dir": merged.output_dir,
        "related_jsonl_dir": related_jsonl_dir,
        "dataset_log_files": [r.log_file for r in per_dataset if getattr(r, "log_file", None)],
        "unqualified_output_dir": "",
        "total_unqualified_records": 0,
        "unqualified_files": [],
        "benchmark_source_count": benchmark_source_count,
        "benchmark_sampled_count": benchmark_sampled_count,
        "benchmark_sampling_failures": benchmark_sampling_failures,
        "benchmark_samples_file": benchmark_samples_file,
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
    benchmark_dir: str = "",
    enable_benchmark_reference: bool = True,
) -> Dict[str, Any]:
    """Synchronous wrapper suitable for calling from a LangGraph node.

    On success, includes at least:
      - ``total_records_processed``, ``processed_sources_count``, ``output_dir``
      - ``related_jsonl_dir``: directory where related datasets are written as raw JSONL
      - benchmark sampling fields when enabled

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
                benchmark_dir=benchmark_dir,
                enable_benchmark_reference=enable_benchmark_reference,
            )
        )
    except Exception as e:
        logger.error(f"[PostprocessAgent] run_postprocess_agent_v2 failed: {e}", exc_info=True)
        return {"exception": f"Postprocess agent v2 error: {str(e)}"}
