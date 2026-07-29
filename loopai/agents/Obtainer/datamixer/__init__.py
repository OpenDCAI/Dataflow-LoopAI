"""DataMixer -- a decentralized, full-pipeline LLM data platform (MVP).

"Git + data platform for LLM training data": content-addressed compact storage,
a unified tag schema & catalog, pluggable operators, and a recipe-driven recall
& export engine serving pretrain / SFT / preference / agent-trajectory stages
through one abstraction.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .store import DataStore
from .recipe import Recipe, load_recipe, parse_recipe
from . import webagents

__all__ = [
    "DataStore", "Recipe", "load_recipe", "parse_recipe", "webagents",
    "__version__",
]
