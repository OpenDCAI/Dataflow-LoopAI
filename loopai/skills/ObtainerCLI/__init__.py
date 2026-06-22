"""Standalone Obtainer CLI package.

This package is intentionally parallel to ``loopai.agents.Obtainer``. It does
not import LangGraph or the old agent runtime.
"""

from .events import load_events

__all__ = ["__version__", "load_events"]

__version__ = "0.1.0"
