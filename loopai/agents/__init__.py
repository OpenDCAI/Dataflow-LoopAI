from .BaseAgent import BaseAgent


__all__ = [
    "BaseAgent",
    "StarterAgent",
    "JudgerAgent",
    "AnalyzerAgent",
    "run_analyzer_standalone",
    "ObtainerAgent",
    "TrainerAgent",
]


def __getattr__(name):
    if name == "StarterAgent":
        from .Starter.starter_agent import StarterAgent
        return StarterAgent
    if name == "JudgerAgent":
        from .Judger.judger_agent import JudgerAgent
        return JudgerAgent
    if name == "AnalyzerAgent":
        from .Analyzer.analyzer_agent import AnalyzerAgent
        return AnalyzerAgent
    if name == "run_analyzer_standalone":
        from .Analyzer.standalone import run_analyzer_standalone
        return run_analyzer_standalone
    if name == "ObtainerAgent":
        from .Obtainer.obtainer_agent import ObtainerAgent
        return ObtainerAgent
    if name == "TrainerAgent":
        from .Trainer.trainer_agent import TrainerAgent
        return TrainerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
