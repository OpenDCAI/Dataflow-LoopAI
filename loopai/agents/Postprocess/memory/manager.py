"""
Hierarchical memory manager for the Postprocess sub-agent system.

Memory layout (namespace hierarchy):
  ("postprocess", thread_id)                         -> orchestrator-level events
  ("postprocess", thread_id, source_type)            -> source-level summaries
  ("postprocess", thread_id, source_type, ds_name)   -> per-dataset long-term memory

Each dataset agent writes to its own namespace.  The orchestrator reads
from all namespaces when it needs a cross-dataset view.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

from loopai.logger import get_logger

logger = get_logger()


class PostprocessMemoryManager:
    """Thin wrapper over a LangGraph BaseStore for hierarchical memory."""

    def __init__(self, store, thread_id: str = "default"):
        self._store = store
        self._thread_id = thread_id

    # ------------------------------------------------------------------
    # Namespace helpers
    # ------------------------------------------------------------------

    def _orchestrator_ns(self) -> Tuple[str, ...]:
        return ("postprocess", self._thread_id)

    def _source_ns(self, source_type: str) -> Tuple[str, ...]:
        return ("postprocess", self._thread_id, source_type)

    def _dataset_ns(self, source_type: str, dataset_name: str) -> Tuple[str, ...]:
        return ("postprocess", self._thread_id, source_type, dataset_name)

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _put(self, namespace: Tuple[str, ...], key: str, value: Dict[str, Any]) -> None:
        if self._store is None:
            return
        try:
            value.setdefault("timestamp", datetime.datetime.now().isoformat())
            self._store.put(namespace, key, value)
        except Exception as e:
            logger.warning(f"[PostprocessMemory] Failed to write {namespace}/{key}: {e}")

    # ------------------------------------------------------------------
    # Lifecycle events
    # ------------------------------------------------------------------

    def on_orchestrator_start(self, download_dir: str, dataset_sources: List[Dict]) -> None:
        self._put(self._orchestrator_ns(), "start_event", {
            "event": "orchestrator_start",
            "download_dir": download_dir,
            "dataset_sources_count": len(dataset_sources),
        })

    def on_orchestrator_end(self, result: Dict[str, Any]) -> None:
        self._put(self._orchestrator_ns(), "end_event", {
            "event": "orchestrator_end",
            "total_records_processed": result.get("total_records_processed", 0),
            "processed_sources_count": result.get("processed_sources_count", 0),
        })

    def on_orchestrator_error(self, error: str) -> None:
        self._put(self._orchestrator_ns(), "error_event", {
            "event": "orchestrator_error",
            "error": error,
        })

    # ------------------------------------------------------------------
    # Per-dataset events
    # ------------------------------------------------------------------

    def on_dataset_agent_start(self, source_type: str, dataset_name: str) -> None:
        self._put(self._dataset_ns(source_type, dataset_name), "agent_start", {
            "event": "dataset_agent_start",
            "dataset_name": dataset_name,
            "source_type": source_type,
        })

    def on_dataset_agent_end(self, source_type: str, dataset_name: str, result: Dict[str, Any]) -> None:
        self._put(self._dataset_ns(source_type, dataset_name), "agent_end", {
            "event": "dataset_agent_end",
            "success": result.get("success", False),
            "records_processed": result.get("records_processed", 0),
        })

    def on_dataset_agent_error(self, source_type: str, dataset_name: str, error: str) -> None:
        self._put(self._dataset_ns(source_type, dataset_name), "agent_error", {
            "event": "dataset_agent_error",
            "error": error[:500],
        })

    # ------------------------------------------------------------------
    # Tool-level events
    # ------------------------------------------------------------------

    def on_tool_before(self, source_type: str, dataset_name: str, tool_name: str, args: Dict) -> None:
        self._put(self._dataset_ns(source_type, dataset_name), f"tool_before_{tool_name}", {
            "event": "tool_before",
            "tool": tool_name,
            "args_summary": str(args)[:300],
        })

    def on_tool_after(self, source_type: str, dataset_name: str, tool_name: str, result_summary: str) -> None:
        self._put(self._dataset_ns(source_type, dataset_name), f"tool_after_{tool_name}", {
            "event": "tool_after",
            "tool": tool_name,
            "result_summary": result_summary[:500],
        })

    # ------------------------------------------------------------------
    # Knowledge (long-term memory)
    # ------------------------------------------------------------------

    def write_knowledge(self, source_type: str, dataset_name: str, summary: Dict[str, Any]) -> None:
        """Write a refined knowledge summary to long-term memory."""
        self._put(self._dataset_ns(source_type, dataset_name), "knowledge_summary", {
            "event": "knowledge_update",
            "summary": summary,
        })

    def write_mapping_plan(self, source_type: str, dataset_name: str, plan: Dict[str, Any]) -> None:
        self._put(self._dataset_ns(source_type, dataset_name), "mapping_plan", {
            "event": "mapping_plan_finalized",
            "plan": plan,
        })

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_dataset_knowledge(self, source_type: str, dataset_name: str) -> Optional[Dict[str, Any]]:
        if self._store is None:
            return None
        try:
            item = self._store.get(self._dataset_ns(source_type, dataset_name), "knowledge_summary")
            if item and hasattr(item, "value"):
                return item.value
        except Exception:
            pass
        return None
