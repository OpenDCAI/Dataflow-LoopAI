__all__ = [
    "BaseAgent",
    "StarterAgent",
    "run_analyzer_standalone",
    "ObtainerAgent",
]


def __getattr__(name):
    if name == "BaseAgent":
        from .BaseAgent.base_agent import BaseAgent
        return BaseAgent
    if name == "StarterAgent":
        from .Starter.starter_agent import StarterAgent
        return StarterAgent
    if name == "run_analyzer_standalone":
        from loopai.skills.Analyzer.runner import run_analyzer_standalone
        return run_analyzer_standalone
    if name == "ObtainerAgent":
        from .Obtainer.obtainer_agent import ObtainerAgent
        return ObtainerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
