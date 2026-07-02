"""Lightweight Judger exports.

JudgerAgent is deprecated — use loopai.skills.Judger.run() (CLI) or MCP
tool 'judger_run' instead. This module will raise if you try to access
JudgerAgent, preventing accidental use of the old LangGraph path.
"""

__all__ = ["JudgerAgent"]


def __getattr__(name):
    if name == "JudgerAgent":
        raise RuntimeError(
            "JudgerAgent is deprecated. Use:\n"
            "  - MCP tool:  judger_run\n"
            "  - CLI:       python examples/scripts/run_judger_standalone.py\n"
            "  - Python:    loopai.skills.Judger.run()\n"
            "The old LangGraph-based JudgerAgent no longer produces judger.pkl events."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
