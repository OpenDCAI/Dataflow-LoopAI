__all__ = ["datamixer"]


def __getattr__(name):
    if name == "ObtainerAgent":
        raise AttributeError(
            "Legacy ObtainerAgent has been retired. Use skills/obtainer/SKILL.md "
            "and loopai.skills.ObtainerCLI.cli DataMixer workflows."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
