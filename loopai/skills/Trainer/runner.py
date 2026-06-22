from __future__ import annotations

from typing import Any, Dict, Optional

from loopai.common.exception import ErrorCode, build_error_payload
from loopai.skills.Trainer.runtime_config import resolve_trainer_runtime_config


def run_trainer_standalone(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    config_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the existing TrainerAgent from the skill layer."""
    runtime = resolve_trainer_runtime_config(
        state=state,
        thread_id=thread_id,
        config_path=config_path,
        **kwargs,
    )
    resolved_state = runtime["state"]
    missing_fields = runtime["missing_fields"]
    if missing_fields:
        raise ValueError(f"missing required Trainer config fields: {', '.join(missing_fields)}")

    from loopai.agents.Trainer.trainer_agent import TrainerAgent
    from loopai.memory import checkpointer, store

    trainer = TrainerAgent(checkpointer=checkpointer, store=store)
    graph = trainer()
    graph_config = {
        "configurable": {
            "thread_id": kwargs.get("graph_thread_id") or f"trainer_{runtime['thread_id']}",
        }
    }

    try:
        result = graph.invoke(resolved_state, config=graph_config)
    except Exception as exc:
        payload = build_error_payload(
            exc,
            code=ErrorCode.UNHANDLED_EXCEPTION,
            recoverable=True,
            message="Trainer skill failed.",
        )
        resolved_state.setdefault("trainer", {})["trainer_result"] = payload
        resolved_state.setdefault("trainer", {})["trainer_last_error"] = payload["error"]
        raise

    return result
