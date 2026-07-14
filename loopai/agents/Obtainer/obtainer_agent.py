"""Compatibility stub for the retired LangGraph ObtainerAgent.

New data acquisition must use ObtainerCLI/DataMixer:

- skills/obtainer/SKILL.md
- docs/OBTAINERCLI_USAGE.md
- loopai.skills.ObtainerCLI.cli
- loopai.agents.Obtainer.datamixer
"""

from __future__ import annotations


class ObtainerAgent:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "Legacy ObtainerAgent has been retired. Use ObtainerCLI/DataMixer "
            "via skills/obtainer/SKILL.md and loopai.skills.ObtainerCLI.cli."
        )
