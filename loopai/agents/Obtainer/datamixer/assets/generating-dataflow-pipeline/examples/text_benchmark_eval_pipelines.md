# Example: Text Benchmark Evaluation Pipelines

## Use Case

Evaluate generated textual answers against references, either directly or with
the original question available to the semantic judge.

## Direct Semantic Evaluation

```text
model_answer + golden_label
  -> BenchDatasetEvaluator(eval_type="semantic", judger serving)
  -> answer_match_result + evaluation details
```

## Question-Aware Generation and Evaluation

```text
instruction -> ReasoningAnswerGenerator -> generated_cot
generated_cot + golden_answer + instruction
  -> BenchDatasetEvaluatorQuestion(eval_type="semantic")
  -> answer_match_result + evaluation details
```

For deterministic math answers, prefer `eval_type="match"` and the math verifier
where supported. Use semantic judging for free-form answers whose equivalence
cannot be established by normalized matching.

## Key Notes

- Keep generation and judging models logically independent where possible.
- Do not expose held-out benchmark answers to training-side generation.
- Persist raw judge output and parsed result; parsing failures must fail closed.
