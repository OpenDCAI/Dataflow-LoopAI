# -*- coding: utf-8 -*-
from contextvars import ContextVar
import sys
from typing import Callable, Optional


_ANALYZER_STREAM_WRITER: ContextVar[Optional[Callable]] = ContextVar(
    "analyzer_stream_writer",
    default=None,
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
