# -*- coding: utf-8 -*-
import sys
from typing import Callable, Optional


def get_safe_stream_writer() -> Optional[Callable]:
    """Return LangGraph stream writer when available, otherwise no-op.

    Analyzer standalone can run outside LangGraph runtime, where
    get_stream_writer() raises because there is no runnable context.
    """
    try:
        config_module = sys.modules.get("langgraph.config")
        if config_module is None:
            return lambda *args, **kwargs: None
        return config_module.get_stream_writer()
    except Exception:
        return lambda *args, **kwargs: None
