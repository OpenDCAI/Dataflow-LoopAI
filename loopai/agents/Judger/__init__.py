"""Lightweight Judger exports.

Keep this module cheap to import so Codex/CLI can import Judger submodules
without loading JudgerAgent, LangGraph, or vLLM dependencies.
"""

__all__ = ["JudgerAgent"]


def __getattr__(name):
    if name == "JudgerAgent":
        from .judger_agent import JudgerAgent
        return JudgerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
