# Example: Text-to-QA Pipeline

## Use Case

Select diverse source texts, generate question-answer pairs, and score their
quality, alignment, and verifiability.

## Operator Decision

```text
text -> embeddings
  -> KCenterGreedyFilter (diverse source selection)
  -> Text2QAGenerator
  -> generated_prompt + generated_question + generated_answer
  -> Text2QASampleEvaluator
  -> question quality + answer alignment + answer verifiability grades/feedback
```

## Key Fields

`Text2QAGenerator` writes `generated_prompt`, `generated_question`, and
`generated_answer`. `Text2QASampleEvaluator` must consume the generated question
and answer and write all requested grade and feedback columns before filtering.

## Key Notes

- Use `Text2MultiHopQAGenerator` instead when the target explicitly requires
  multi-hop QA over long cleaned chunks.
- Diversity selection requires embeddings; do not replace it with random sampling
  when coverage is part of the target.
- Filter only after evaluator output fields exist.
