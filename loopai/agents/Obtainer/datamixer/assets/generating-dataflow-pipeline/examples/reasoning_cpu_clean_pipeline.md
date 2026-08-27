# Example: CPU-Only Reasoning Answer Cleaning

## Use Case

Clean already-generated math reasoning without making LLM calls. Inputs require
`instruction`, a reasoning answer field, and `golden_answer`.

## Operator Decision

```json
{
  "ops": ["ReasoningAnswerFormatterFilter", "ReasoningAnswerGroundTruthFilter", "ReasoningAnswerNgramFilter"],
  "field_flow": "instruction+output+golden_answer -> format filter -> mathematical ground-truth filter -> ngram filter",
  "reason": "All required checks are deterministic, so the CPU chain avoids unnecessary generation or LLM judging."
}
```

## Standard Pipeline Core

```python
from dataflow.operators.reasoning import (
    ReasoningAnswerFormatterFilter,
    ReasoningAnswerGroundTruthFilter,
    ReasoningAnswerNgramFilter,
)

format_filter = ReasoningAnswerFormatterFilter()
ground_truth_filter = ReasoningAnswerGroundTruthFilter()
ngram_filter = ReasoningAnswerNgramFilter(min_score=0.1, max_score=1.0, ngrams=5)

format_filter.run(storage=storage.step(), input_key="output")
ground_truth_filter.run(
    storage=storage.step(),
    input_test_answer_key="output",
    input_gt_answer_key="golden_answer",
)
ngram_filter.run(
    storage=storage.step(),
    input_question_key="instruction",
    input_answer_key="output",
)
```

## Key Notes

- This pipeline does not generate missing CoT and does not estimate problem
  difficulty. Use it only for rows that already contain reasoning answers.
- Normalize the source reasoning field to `output`, or change all three operator
  calls consistently.
- `word2number` and `math-verify` are required by the mathematical ground-truth
  filter in the current DataFlow package.
