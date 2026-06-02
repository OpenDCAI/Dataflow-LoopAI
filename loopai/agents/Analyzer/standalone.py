from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from langgraph.errors import ParentCommand

from loopai.memory import checkpointer as default_checkpointer
from loopai.memory import store as default_store
from .analyzer_agent import AnalyzerAgent


ANALYZER_NODE_NAMES = (
    "check_required_fields",
    "route_eval",
    "eval_model",
    "metric_recommend",
    "metric_score",
    "analyze_metric_report",
    "analyze_result",
    "draw_conclusion",
    "finish",
)


_NODE_ALIASES = {
    "check_required_fields_node": "check_required_fields",
    "route_eval_node": "route_eval",
    "eval_model_node": "eval_model",
    "metric_recommend_node": "metric_recommend",
    "metric_score_node": "metric_score",
    "analyze_metric_report_node": "analyze_metric_report",
    "analyze_result_node": "analyze_result",
    "draw_conclusion_node": "draw_conclusion",
    "finish_node": "finish",
}


def _build_config(thread_id: str) -> Dict[str, Dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _state_is_finished(state: Dict[str, Any]) -> bool:
    current = _normalize_node_name(str(state.get("current", "")))
    return current == "finish"


def _normalize_node_name(node_name: str) -> str:
    node_name = str(node_name or "")
    if node_name in ANALYZER_NODE_NAMES:
        return node_name
    if node_name in _NODE_ALIASES:
        return _NODE_ALIASES[node_name]

    for alias, graph_node in _NODE_ALIASES.items():
        if alias in node_name:
            return graph_node
    for graph_node in sorted(ANALYZER_NODE_NAMES, key=len, reverse=True):
        if graph_node in node_name:
            return graph_node
    return node_name


def _resume_node_from_state(state: Dict[str, Any]) -> str:
    resume_node = _normalize_node_name(state.get("current") or state.get("next_to") or "")
    if resume_node not in ANALYZER_NODE_NAMES:
        available = ", ".join(ANALYZER_NODE_NAMES)
        raise RuntimeError(
            f"Cannot resume Analyzer from current={state.get('current')!r}, "
            f"next_to={state.get('next_to')!r}. Available nodes: {available}"
        )
    return resume_node


def _build_graph(checkpointer: Any, store: Any, entry_point: Optional[str] = None) -> Any:
    sg = AnalyzerAgent(checkpointer=checkpointer or default_checkpointer, store=store or default_store)
    return sg(entry_point=entry_point)


def _checkpoint_values(
    graph: Any,
    config: Dict[str, Any],
    thread_id: str,
    checkpoint_path: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot = graph.get_state(config)
    values = getattr(snapshot, "values", None)
    if not values:
        if checkpoint_path:
            raise RuntimeError(f"No checkpoint found for thread_id={thread_id} in checkpoint_path={checkpoint_path}")
        raise RuntimeError(f"No checkpoint found for thread_id={thread_id}")
    next_nodes = tuple(node for node in _snapshot_next(snapshot) if node in ANALYZER_NODE_NAMES)
    if next_nodes:
        values = values.copy()
        values["current"] = next_nodes[0]
        values["next_to"] = next_nodes[0]
    return values


def _snapshot_next(snapshot: Any) -> Iterable[str]:
    next_nodes = getattr(snapshot, "next", None)
    return next_nodes or ()


def get_analyzer_checkpoint_state(
    thread_id: str = "analyzer-default",
    checkpointer: Any = None,
    checkpoint_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the latest Analyzer checkpoint values for a thread_id."""
    graph = _build_graph(checkpointer, default_store)
    config = _build_config(thread_id)
    return _checkpoint_values(graph, config, thread_id, checkpoint_path)


def _invoke_graph(graph: Any, input_state: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return graph.invoke(input_state, config=config)
    except ParentCommand as exc:
        command = exc.args[0] if exc.args else None
        update = getattr(command, "update", None)
        if isinstance(update, dict):
            return update
        raise


def run_analyzer_standalone(
    state: Optional[Dict[str, Any]],
    thread_id: str = "analyzer-default",
    resume: bool = False,
    from_node: Optional[str] = None,
    checkpointer: Any = None,
    store: Any = None,
    checkpoint_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run AnalyzerAgent directly with LangGraph checkpoint controls.

    The input state is passed through unchanged. This wrapper only controls
    thread_id, checkpoint resume, and optional node-level restart behavior.
    """

    checkpointer = checkpointer or default_checkpointer
    store = store or default_store
    graph = _build_graph(checkpointer, store)
    config = _build_config(thread_id)

    if resume or from_node is not None:
        checkpoint_state = _checkpoint_values(graph, config, thread_id, checkpoint_path)
        resume_node = _normalize_node_name(from_node) if from_node is not None else _resume_node_from_state(checkpoint_state)
        if resume_node not in ANALYZER_NODE_NAMES:
            available = ", ".join(ANALYZER_NODE_NAMES)
            raise ValueError(f"Unknown Analyzer node: {resume_node}. Available nodes: {available}")

        checkpoint_state = checkpoint_state.copy()
        checkpoint_state["current"] = resume_node
        checkpoint_state["next_to"] = resume_node
        if _state_is_finished(checkpoint_state):
            return checkpoint_state
        resume_graph = _build_graph(checkpointer, store, entry_point=resume_node)
        return _invoke_graph(resume_graph, checkpoint_state, config)

    if state is None:
        raise ValueError("state is required when resume is false.")

    return _invoke_graph(graph, state, config)
