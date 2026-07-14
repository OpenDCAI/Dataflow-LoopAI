"""Model-based scorers (#20): LLM-as-judge quality / safety scoring.

The built-in `quality_score` / `lang_detect` operators are dependency-free
heuristics, and the schema's `toxicity` column had no writer. This adds a
pluggable LLM-as-judge scorer that reuses the model pool (so quality, toxicity,
educational-value, etc. are all "score this 0..1 with a model" via config),
rather than bundling heavy classifier weights into the zero-dep core.

A scorer is just an ``LLMOperator``; it inherits the model-pool binding and
chunked-concurrent execution (and, once merged, the retry/cache/cost
productionization). Heavy local models (fastText langid, FineWeb-Edu, HF
toxicity) remain an optional, out-of-core extra by design.
"""
from __future__ import annotations

import json

from ..utils import extract_text
from .base import Batch, OperatorContext, OperatorSpec, register
from .llm import LLMOperator, MAX_CHUNK, DEFAULT_CONCURRENCY


@register("llm_score")
class LLMScore(LLMOperator):
    """LLM-as-judge 0..1 scorer. Writes ``field`` (default ``quality_score``);
    use ``--arg field=toxicity`` for the safety dimension.

    Args: ``model`` (pool name, required), ``field`` (target column),
    ``criteria`` (what to rate, free text), ``chunk_size``, ``max_concurrency``,
    ``max_input_chars``.
    """
    spec = OperatorSpec("llm_score", "1.0", "score",
                        description="LLM-as-judge 0..1 scorer "
                                    "(quality/toxicity/edu/...).")

    def __init__(self, model=None, field="quality_score", criteria=None,
                 chunk_size=MAX_CHUNK, max_concurrency=DEFAULT_CONCURRENCY,
                 max_input_chars=2000, **kw):
        super().__init__(model, chunk_size, max_concurrency, **kw)
        self.field = field
        self.criteria = criteria or (
            "overall value as LLM training data (coherence, informativeness, "
            "cleanliness); higher is better")
        self.max_input_chars = int(max_input_chars)

    def build_messages(self, chunk: Batch) -> list[dict]:
        items = [{"index": i,
                  "text": extract_text(r.get("content", r))[:self.max_input_chars]}
                 for i, r in enumerate(chunk)]
        system = ("You are a strict data rater for an LLM training-data platform. "
                  f"Rate each item from 0.0 to 1.0 on: {self.criteria}. "
                  "Use the full range; 0.0 = worst, 1.0 = best.")
        user = (
            f"Items (rate each by index):\n{json.dumps(items, ensure_ascii=False)}"
            '\n\nReturn ONLY this JSON, no prose:\n'
            '{"results": [{"index": <int>, "score": <float between 0 and 1>}]}'
        )
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    def apply(self, chunk: Batch, data: dict) -> None:
        for entry in data.get("results", []):
            idx = entry.get("index")
            score = entry.get("score")
            if (isinstance(idx, int) and 0 <= idx < len(chunk)
                    and isinstance(score, (int, float))):
                chunk[idx][self.field] = max(0.0, min(1.0, float(score)))
