from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from langgraph.errors import ParentCommand

from loopai.memory import checkpointer, store
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


_PREVIOUS_NODE_BY_NODE = {
    "route_eval": "check_required_fields",
    "eval_model": "route_eval",
    "metric_recommend": "route_eval",
    "metric_score": "metric_recommend",
    "analyze_metric_report": "metric_score",
    "analyze_result": "eval_model",
    "draw_conclusion": "analyze_result",
    "finish": "draw_conclusion",
}


def _build_config(thread_id: str) -> Dict[str, Dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _snapshot_next(snapshot: Any) -> Iterable[str]:
    next_nodes = getattr(snapshot, "next", None)
    return next_nodes or ()


def _find_checkpoint_before_node(graph: Any, config: Dict[str, Any], node_name: str) -> Optional[Any]:
    get_state_history = getattr(graph, "get_state_history", None)
    if get_state_history is None:
        return None

    for snapshot in get_state_history(config):
        if node_name in _snapshot_next(snapshot):
            return snapshot
    return None


def _latest_state_values(graph: Any, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    get_state = getattr(graph, "get_state", None)
    if get_state is None:
        return None

    snapshot = get_state(config)
    values = getattr(snapshot, "values", None)
    if values is None:
        return None
    return values


def _invoke_graph(graph: Any, input_state: Optional[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return graph.invoke(input_state, config=config)
    except ParentCommand as exc:
        command = exc.args[0] if exc.args else None
        update = getattr(command, "update", None)
        if isinstance(update, dict):
            return update
        raise


def _invoke_from_node(
    graph: Any,
    state: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    from_node: str,
) -> Dict[str, Any]:
    if from_node not in ANALYZER_NODE_NAMES:
        available = ", ".join(ANALYZER_NODE_NAMES)
        raise ValueError(f"Unknown Analyzer node: {from_node}. Available nodes: {available}")

    checkpoint = _find_checkpoint_before_node(graph, config, from_node)
    if checkpoint is not None:
        return _invoke_graph(graph, None, checkpoint.config)

    base_state = state or _latest_state_values(graph, config)
    if base_state is None:
        raise ValueError(
            "No checkpoint state found. Pass state for the first run, or run once with the same thread_id before resuming."
        )

    if from_node == "check_required_fields":
        return _invoke_graph(graph, base_state, config)

    update_state = getattr(graph, "update_state", None)
    if update_state is None:
        raise RuntimeError("This LangGraph version does not expose update_state; cannot resume from a node fallback.")

    # Fallback for threads without checkpoint history: write the provided/latest state
    # as if the previous node completed, then let LangGraph schedule from_node.
    previous_node = _PREVIOUS_NODE_BY_NODE[from_node]
    update_state(config, base_state, as_node=previous_node)
    return _invoke_graph(graph, None, config)


def run_analyzer_standalone(
    state: Optional[Dict[str, Any]],
    thread_id: str = "analyzer-default",
    resume: bool = False,
    from_node: Optional[str] = None,
) -> Dict[str, Any]:
    """Run AnalyzerAgent directly with LangGraph checkpoint controls.

    The input state is passed through unchanged. This wrapper only controls
    thread_id, checkpoint resume, and optional node-level restart behavior.
    """

    sg = AnalyzerAgent(checkpointer=checkpointer, store=store)
    graph = sg()
    config = _build_config(thread_id)

    if from_node is not None:
        return _invoke_from_node(graph, state, config, from_node)

    if resume:
        return _invoke_graph(graph, None, config)

    if state is None:
        raise ValueError("state is required when resume is false.")

    return _invoke_graph(graph, state, config)
