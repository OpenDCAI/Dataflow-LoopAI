---
name: dataflow-pattern-math-reasoning-sft
description: Reuse a reviewed pipeline pattern for constructing benchmark-aligned math reasoning SFT records from question-and-answer datasets with verifiable final answers.
metadata:
  version: 1.0.0
---
# Math Reasoning SFT Pattern

Use this pattern for short, verifiable math problems whose training output
requires a standalone instruction, regenerated reasoning, and a final answer
compatible with strict benchmark evaluation. Do not use it for proof-only
tasks, open-ended tutoring, or records without a trustworthy answer signal.

The recommended case is
[gsm8k-reasoning-v1](examples/gsm8k-reasoning-v1/case_report.md). It accepts
`question`/`answer` and `problem`/`final_answer` schema branches, normalizes them
to canonical fields, regenerates reasoning without exposing the gold answer to
the generator, validates the derived final answer against gold, and applies an
LLM quality evaluation after deterministic correctness checks.

Preserve these invariants when adapting the case:

- `sample_id` and source dataset provenance survive every stage.
- The reasoning generator sees the problem but not the gold answer.
- Generated reasoning is accepted only when its parsed conclusion matches gold.
- Quality thresholds are justified by the trial score distribution.
- Output always provides nonempty `instruction`, `reasoning`, and `output`.
- Benchmark answer formatting is handled explicitly rather than left to chance.

For a new schema or benchmark, retain the staged validation pattern but adapt
normalization, correctness parsing, difficulty coverage, and final formatting
to the actual task.
