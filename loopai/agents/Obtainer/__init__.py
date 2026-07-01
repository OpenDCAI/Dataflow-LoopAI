__all__ = ["ObtainerAgent"]


def __getattr__(name):
    if name == "ObtainerAgent":
        from .obtainer_agent import ObtainerAgent
        return ObtainerAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
