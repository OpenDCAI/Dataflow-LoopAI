# GSM8K-style reasoning SFT case

## Purpose and contract

This example demonstrates the artifact set of a curated skill. It transforms
two source schema families into standalone math SFT records with `instruction`,
`reasoning`, and `output`. The benchmark contract is short grade-school word
problems with a numerically verifiable final answer and concise derivation.
The benchmark rows here are synthetic placeholders identified by an invalid
example URL; a real deposited case must contain authentic official examples
and provenance.

## Inputs and field flow

The audit uses three rows each from `source-a` and `source-b`. Source A exposes
`question` and `answer`; source B exposes `problem` and `final_answer`. A real
pipeline must normalize those branches to canonical `question` and `gold`
fields before generation. The intended flow is:

```text
source schema branch -> canonical question/gold -> structural filters
-> answer-blind reasoning generation -> deterministic gold validation
-> LLM quality evaluation -> instruction/reasoning/output
```

The complete `pipeline.py` illustrates portable DataFlow configuration,
normalizes both schemas, hides gold from reasoning generation, parses the
generated conclusion, rejects gold mismatches, assembles SFT fields, performs
question/reasoning/gold-aware LLM evaluation, and filters its score.

## Results

Both datasets contributed 3 of 3 audit rows, so neither source dominated the
six-row output. All six output records have nonempty SFT fields; independent
inspection found no empty answer, refusal, repetition, or answer mismatch. Five
benchmark examples were compared. The weighted review score is 95 with no
redlines. D4 scored 3 because schema normalization is the most fragile part and
requires dataset-specific verification.

## Selection and limitations

This is the initial recommended case, so there is no predecessor comparison.
It is suitable only when gold answers can be parsed and checked. It does not
establish proof validity, symbolic equivalence for arbitrary expressions, or
coverage of advanced mathematics. LLM generation and evaluation each add a
per-row model call; deterministic filtering should precede them at scale.

The example data are deliberately small and synthetic. Production curation
must retain real review evidence, official benchmark provenance, exact funnel
counts, score distributions, and a fully executable pipeline rather than
claiming that this illustrative case itself passed a production review.
