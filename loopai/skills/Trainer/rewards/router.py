"""Stable LoopAI entry point for reward implementations provided by Verl.

Verl loads this file directly from the generated training YAML. Keep imports of
Verl lazy so LoopAI can inspect configs without installing the Verl environment
in the API process.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable


REWARD_PRESETS: Dict[str, Dict[str, Any]] = {
    "auto": {
        "description": "Let Verl select its built-in reward from data_source.",
        "ground_truth": "Depends on the selected Verl reward.",
        "data_sources": ["Verl-supported data_source values"],
    },
    "verl_builtin": {
        "description": "Explicit alias for Verl's default data_source router.",
        "ground_truth": "Depends on the selected Verl reward.",
        "data_sources": ["Verl-supported data_source values"],
    },
    "gsm8k_exact": {
        "description": "GSM8K final-answer exact match using Verl's implementation.",
        "ground_truth": "Final numeric answer as a string.",
        "data_sources": ["openai/gsm8k"],
    },
    "math_boxed": {
        "description": "Last boxed-answer equivalence for MATH-style datasets.",
        "ground_truth": "Reference mathematical answer as a string.",
        "data_sources": [
            "lighteval/MATH",
            "DigitalLearningGmbH/MATH-lighteval",
            "HuggingFaceH4/MATH-500",
        ],
    },
    "math_dapo": {
        "description": "DAPO mathematical verification with score and accuracy details.",
        "ground_truth": "Reference mathematical answer as a string.",
        "data_sources": ["math_dapo", "math", "math_dapo_reasoning", "aime*"],
    },
    "prime_math": {
        "description": "PRIME/Numina mathematical answer verification from Verl.",
        "ground_truth": "Reference mathematical answer as a string.",
        "data_sources": ["numina_*"],
    },
    "geometry": {
        "description": "Geometry3K correctness plus boxed-output format reward.",
        "ground_truth": "Reference geometry answer as a string.",
        "data_sources": ["hiyouga/geometry3k"],
    },
    "qa_exact_match": {
        "description": "Search-R1 answer-tag extraction and normalized exact match.",
        "ground_truth": "A mapping containing target, whose value is a string or list.",
        "data_sources": ["searchR1_*"],
    },
}

SUPPORTED_REWARD_PRESETS = tuple(REWARD_PRESETS)

_ALIASES = {
    "builtin": "verl_builtin",
    "default": "auto",
    "gsm8k": "gsm8k_exact",
    "math": "math_boxed",
    "math_reward": "math_boxed",
    "dapo": "math_dapo",
    "geo3k": "geometry",
    "qa_em": "qa_exact_match",
    "search_r1": "qa_exact_match",
}

_VERL_BUILTIN_EXACT_DATA_SOURCES = {
    "openai/gsm8k",
    "lighteval/MATH",
    "DigitalLearningGmbH/MATH-lighteval",
    "HuggingFaceH4/MATH-500",
    "math_dapo",
    "math",
    "math_dapo_reasoning",
    "numina_aops_forum",
    "numina_synthetic_math",
    "numina_amc_aime",
    "numina_synthetic_amc",
    "numina_cn_k12",
    "numina_olympiads",
    "codecontests",
    "apps",
    "codeforces",
    "taco",
    "hiyouga/geometry3k",
    "searchR1_nq",
    "searchR1_triviaqa",
    "searchR1_popqa",
    "searchR1_hotpotqa",
    "searchR1_2wikimultihopqa",
    "searchR1_musique",
    "searchR1_bamboogle",
}


def normalize_reward_preset(value: Any) -> str:
    """Normalize and validate a user-facing reward preset name."""
    preset = str(value or "auto").strip().lower().replace("-", "_")
    preset = _ALIASES.get(preset, preset)
    if preset not in REWARD_PRESETS:
        supported = ", ".join(SUPPORTED_REWARD_PRESETS)
        raise ValueError(f"Unsupported LoopAI reward preset: {preset}; expected one of: {supported}")
    return preset


def get_reward_preset(value: Any = "auto") -> Dict[str, Any]:
    """Return serializable metadata for one normalized preset."""
    preset = normalize_reward_preset(value)
    return {"name": preset, **REWARD_PRESETS[preset]}


def is_verl_builtin_data_source(data_source: Any) -> bool:
    """Whether the pinned Verl default router recognizes this data source."""
    value = str(data_source or "").strip()
    return value in _VERL_BUILTIN_EXACT_DATA_SOURCES or value.startswith("aime")


def _kwargs(source: Dict[str, Any], allowed: Iterable[str]) -> Dict[str, Any]:
    return {key: source[key] for key in allowed if key in source}


def _normalize_result(result: Any) -> float | Dict[str, Any]:
    """Match the scalar/dict contract expected by Verl reward managers."""
    if isinstance(result, dict):
        if "score" not in result:
            raise ValueError("Reward result mappings must contain a 'score' field")
        return result
    if isinstance(result, (list, tuple)):
        if not result:
            raise ValueError("Reward result sequence must not be empty")
        result = result[0]
    if isinstance(result, (bool, int, float)):
        return float(result)
    raise TypeError(f"Unsupported reward result type: {type(result).__name__}")


def compute_score(
    data_source: str,
    solution_str: str,
    ground_truth: Any,
    extra_info: Dict[str, Any] | None = None,
    preset: str = "auto",
    **kwargs: Any,
) -> float | Dict[str, Any]:
    """Compute one reward using a named LoopAI preset backed by Verl.

    The signature intentionally matches Verl's ``NaiveRewardManager``. Named
    presets ignore ``data_source`` for routing, while ``auto`` and
    ``verl_builtin`` delegate routing to Verl.
    """
    resolved = normalize_reward_preset(preset)

    if resolved in {"auto", "verl_builtin"}:
        from verl.utils.reward_score import default_compute_score

        return _normalize_result(
            default_compute_score(
                data_source=data_source,
                solution_str=solution_str,
                ground_truth=ground_truth,
                extra_info=extra_info,
                **kwargs,
            )
        )

    scorer: Callable[..., Any]
    scorer_kwargs: Dict[str, Any]
    if resolved == "gsm8k_exact":
        from verl.utils.reward_score import gsm8k

        scorer = gsm8k.compute_score
        scorer_kwargs = _kwargs(kwargs, ("method", "format_score", "score"))
    elif resolved == "math_boxed":
        from verl.utils.reward_score import math_reward

        scorer = math_reward.compute_score
        scorer_kwargs = {}
    elif resolved == "math_dapo":
        from verl.utils.reward_score import math_dapo

        scorer = math_dapo.compute_score
        scorer_kwargs = _kwargs(kwargs, ("strict_box_verify", "pause_tokens_index"))
    elif resolved == "prime_math":
        from verl.utils.reward_score import prime_math

        scorer = prime_math.compute_score
        scorer_kwargs = {}
    elif resolved == "geometry":
        from verl.utils.reward_score import geo3k

        scorer = geo3k.compute_score
        scorer_kwargs = _kwargs(kwargs, ("use_boxed", "format_score"))
    elif resolved == "qa_exact_match":
        from verl.utils.reward_score import search_r1_like_qa_em

        scorer = search_r1_like_qa_em.compute_score
        scorer_kwargs = _kwargs(kwargs, ("method", "format_score", "score"))
    else:  # pragma: no cover - normalize_reward_preset guards this branch.
        raise AssertionError(f"Unhandled LoopAI reward preset: {resolved}")

    return _normalize_result(scorer(solution_str, ground_truth, **scorer_kwargs))
