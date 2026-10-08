# Data Generation Pipeline Review Rubric

## Common rules

Score each dimension from 0 to 4, then apply its weight. No evidence means zero
and must never be treated as a pass. Every score must cite measured numbers or
sample IDs. Select three trial records per selected source dataset. Any redline
immediately blocks release and cannot be offset by other scores.

## D1: Data fit and dataset use (weight 15)

First construct a funnel for every dataset: input count, count after every
operator, output count, yield, and principal rejection reasons.

- Determine whether every dataset has nonzero output. Zero is acceptable only
  with evidence that the dataset does not match the target.
- Explain both unusually low and complete retention; low yield can indicate a
  fit failure, while complete retention can indicate ineffective filtering.
- Measure whether one dataset contributes more than 90% of output.
- Check whether heterogeneous schemas have appropriate branches instead of one
  hard-coded path.
- Confirm rejection reasons are counted in logs and not swallowed by exception
  handling.

Redline: more than two thirds of datasets have unexplained zero output, or no
stage-level counts exist and the pipeline is not auditable.

## D2: Quality first (weight 25)

- Measure correctness and quality using task-appropriate validators.
- Examine validation-pass distributions. A pass rate above 98% requires proof
  that the validator adds value; otherwise treat it as ineffective/removable.
- Require distributional evidence for thresholds; reject unexplained magic
  numbers.
- Determine whether failed records are discarded or are rewritten by an LLM
  and mixed back into output without revalidation.
- Measure empty answers, repetition, truncation, meta-talk, and refusals;
  combined degradation must be below 1%.
- Independently inspect at least one output record per dataset.

Redline: independent review error rate exceeds 20%, or a verifiable task has no
correctness validation.

## D3: Benchmark style fit (weight 20)

Read at least five official benchmark examples before comparison.

- Task-type agreement with the benchmark must be at least 95%. Contextual
  multi-hop QA and closed-book short QA are different task types.
- When evaluation uses strict matching, answer-format agreement must be at
  least 98%. Check option letters, `\\boxed{}`, JSON, code fences, system
  prompts, and single-turn versus multi-turn shape as applicable.
- Compare difficulty distributions and detect collapse to tasks that are too
  easy or too hard.
- Check coverage of the benchmark's measured capability dimensions.
- Measure unrelated chat, refusal, translation, and role-play contamination;
  it must be below 2%.

Redline: answer format is incompatible with the evaluation script, or the
generated task type is fundamentally different.

## D4: LLM operator input assembly (weight 12)

For every LLM operator, compare template placeholders, declared input fields,
and fields actually available from upstream.

- Include every field genuinely required by the operation.
- Verify semantic alignment; for example, a question slot must not receive an
  answer field after cross-dataset renaming.
- Question-generation inputs must not leak target answers.
- Required context must be present and not silently truncated. Long inputs need
  an explicit truncation strategy.

Redline: unresolved placeholders, empty critical fields, or target-answer
leakage into a question-generation operator.

## D5: Reasoning regeneration and filtering (weight 16)

First decide whether the benchmark requires reasoning, citing its official
description, performance evidence with and without CoT when available, and
step complexity in official examples.

When reasoning is required:

- Verify reasoning is regenerated rather than copied from the source.
- Reasoning conclusions and gold answers must agree at least 90%.
- Independently inspect multiple records for a wrong process that guesses the
  right answer; error rate must be below 10%.
- Compare retained and rejected difficulty distributions to detect systematic
  removal of hard tasks.
- Ensure reasoning format matches the target training template.

When reasoning is not required, check that long CoT was not forcibly injected
and does not contaminate the expected answer style.

Redline: required reasoning was not regenerated or reasoning-answer mismatch
exceeds 5%.

## D6: Post-training SFT field completeness (weight 12)

Derive the required SFT schema from the downstream training template. Verify
that every output can be deterministically assembled into a complete training
record, required fields are nonempty, roles are unambiguous, and source
datasets converge to a consistent schema.

Redline: required fields are missing or empty, output fields cannot be composed
into the SFT training format, or schemas remain inconsistent across sources.
