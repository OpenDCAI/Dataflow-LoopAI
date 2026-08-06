__all__ = [
    "BaseAgent",
    "ObtainerAgent",
]


def __getattr__(name):
    if name == "BaseAgent":
        from .BaseAgent.base_agent import BaseAgent
        return BaseAgent
    if name == "ObtainerAgent":
        from .Obtainer.obtainer_agent import ObtainerAgent
        return ObtainerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
