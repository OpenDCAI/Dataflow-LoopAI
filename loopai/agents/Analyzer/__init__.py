"""Lightweight Analyzer exports.

Keep this module cheap to import so Codex/CLI can inspect Analyzer metadata
without loading node modules, PromptLoader, OneEval, ChatOpenAI, or models.
"""

ANALYZER_NODE_NAMES = (
    "eval_model",
    "analyze_result",
    "draw_conclusion",
    "finish",
)

__all__ = [
    "ANALYZER_NODE_NAMES",
    "AnalyzerAgent",
    "get_analyzer_checkpoint_state",
    "run_analyzer_standalone",
]


def __getattr__(name):
    if name == "AnalyzerAgent":
        from .analyzer_agent import AnalyzerAgent
        return AnalyzerAgent
    if name in {"get_analyzer_checkpoint_state", "run_analyzer_standalone"}:
        from .standalone import get_analyzer_checkpoint_state, run_analyzer_standalone
        return {
            "get_analyzer_checkpoint_state": get_analyzer_checkpoint_state,
            "run_analyzer_standalone": run_analyzer_standalone,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
