__all__ = [
    "BaseAgent",
    "StarterAgent",
    "JudgerAgent",
    "AnalyzerAgent",
    "run_analyzer_standalone",
    "TrainerAgent",
    "ObtainerAgent",
]


def __getattr__(name):
    if name == "BaseAgent":
        from .BaseAgent.base_agent import BaseAgent
        return BaseAgent
    if name == "StarterAgent":
        from .Starter.starter_agent import StarterAgent
        return StarterAgent
    if name == "JudgerAgent":
        from .Judger.judger_agent import JudgerAgent
        return JudgerAgent
    if name == "AnalyzerAgent":
        from loopai.skills.Analyzer.analyzer_agent import AnalyzerAgent
        return AnalyzerAgent
    if name == "run_analyzer_standalone":
        from loopai.skills.Analyzer.runner import run_analyzer_standalone
        return run_analyzer_standalone
    if name == "TrainerAgent":
        from .Trainer.trainer_agent import TrainerAgent
        return TrainerAgent
    if name == "ObtainerAgent":
        from .Obtainer.obtainer_agent import ObtainerAgent
        return ObtainerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
