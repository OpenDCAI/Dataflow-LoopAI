"""LoopAI reward presets for Verl-backed reinforcement learning."""

from .router import (
    REWARD_PRESETS,
    SUPPORTED_REWARD_PRESETS,
    compute_score,
    get_reward_preset,
    is_verl_builtin_data_source,
    normalize_reward_preset,
)

__all__ = [
    "REWARD_PRESETS",
    "SUPPORTED_REWARD_PRESETS",
    "compute_score",
    "get_reward_preset",
    "is_verl_builtin_data_source",
    "normalize_reward_preset",
]
