# -*- coding: utf-8 -*-
from contextvars import ContextVar
import sys
from typing import Callable, Optional


_ANALYZER_STREAM_WRITER: ContextVar[Optional[Callable]] = ContextVar(
    "analyzer_stream_writer",
    default=None,
)

_ANALYZER_CHECKPOINT_CALLBACK: ContextVar[Optional[Callable]] = ContextVar(
    "analyzer_checkpoint_callback",
    default=None,
)

_ANALYZER_RESUME_PROGRESS: ContextVar[float] = ContextVar(
    "analyzer_resume_progress",
    default=0.0,
)


def set_analyzer_stream_writer(writer: Optional[Callable]):
    return _ANALYZER_STREAM_WRITER.set(writer)


def reset_analyzer_stream_writer(token) -> None:
    _ANALYZER_STREAM_WRITER.reset(token)


def get_safe_stream_writer() -> Optional[Callable]:
    """Return LangGraph stream writer when available, otherwise no-op.

    Analyzer standalone can run outside LangGraph runtime, where
    get_stream_writer() raises because there is no runnable context.
    """
    injected_writer = _ANALYZER_STREAM_WRITER.get()
    if injected_writer is not None:
        return injected_writer

    try:
        config_module = sys.modules.get("langgraph.config")
        if config_module is None:
            return lambda *args, **kwargs: None
        return config_module.get_stream_writer()
    except Exception:
        return lambda *args, **kwargs: None


def set_analyzer_checkpoint_context(
    callback: Optional[Callable],
    resume_progress: float = 0.0,
):
    return (
        _ANALYZER_CHECKPOINT_CALLBACK.set(callback),
        _ANALYZER_RESUME_PROGRESS.set(float(resume_progress or 0.0)),
    )


def reset_analyzer_checkpoint_context(tokens) -> None:
    callback_token, progress_token = tokens
    _ANALYZER_CHECKPOINT_CALLBACK.reset(callback_token)
    _ANALYZER_RESUME_PROGRESS.reset(progress_token)


def checkpoint_analyzer_progress(
    state: dict,
    progress: float,
    *,
    stage: Optional[str] = None,
    data: Optional[dict] = None,
) -> None:
    callback = _ANALYZER_CHECKPOINT_CALLBACK.get()
    if callback is not None:
        callback(state, float(progress), stage=stage, data=data)


def get_analyzer_resume_progress() -> float:
    return float(_ANALYZER_RESUME_PROGRESS.get() or 0.0)
