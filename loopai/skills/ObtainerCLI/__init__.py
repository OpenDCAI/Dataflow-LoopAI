"""Standalone Obtainer CLI package.

This package is intentionally parallel to ``loopai.agents.Obtainer``. It does
not import LangGraph or the old agent runtime.
"""

from .events import load_events
from .download import download_manifest
from .searchagent import run_searchagent

__all__ = ["__version__", "download_manifest", "load_events", "run_searchagent"]

__version__ = "0.1.0"
