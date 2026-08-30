---
name: reviewing-dataflow-pipeline
description: Independently audit a generated DataFlow training-data pipeline and its trial artifacts with delegated rubric reviewers before it can be delivered or scaled.
metadata:
  version: 1.0.0
---
# Review a DataFlow Pipeline

Use this skill after a candidate pipeline has produced trial artifacts and
before reporting `mode=trial_run`. This is a release gate, not a substitute for
pipeline generation.

Read [references/rubric.md](references/rubric.md) completely before starting.

## Required evidence

Review the candidate pipeline source, trial input and output, intermediate
operator outputs or counters, logs, target benchmark metadata and evaluation
code, and at least five official benchmark examples. Build a stratified audit
sample of three input records per selected source dataset. Preserve dataset and
`sample_id` provenance in every table and finding.

If the existing trial does not cover three records from every selected dataset,
do not extrapolate from it. Extend the trial input and rerun the same candidate
pipeline on the missing audit records. Do not run the full dataset.

## Delegated review

Dispatch six independent subagents, one for each D1-D6 rubric dimension. Give
each reviewer only the dimension it owns plus paths to the raw artifacts and
the downstream target. Do not give reviewers a proposed score or another
reviewer's conclusions. Run reviewers concurrently when capacity permits.

Each reviewer must return structured JSON containing:

```json
{
  "dimension": "D1",
  "raw_score": 0,
  "weighted_score": 0.0,
  "blocked": false,
  "redlines": [],
  "evidence": [{"claim": "...", "numbers": "...", "sample_ids": ["..."]}],
  "findings": ["..."],
  "required_fixes": ["..."]
}
```

Every raw score is an integer from 0 through 4. Missing evidence scores zero;
reviewers must not assume compliance. Evidence must cite measured counts,
rates, distributions, or concrete sample IDs. A statement without either is
not evidence.

The main agent must not silently change reviewer scores. It checks arithmetic,
deduplicates findings, and applies every redline. Resolve a factual conflict by
reading the cited artifact; if it cannot be resolved, use the lower supported
score and record the conflict.

## Scoring and gate

Calculate each weighted score as `raw_score / 4 * dimension_weight`, retaining
at least two decimal places until the final sum. Weights are D1=15, D2=25,
D3=20, D4=12, D5=16, and D6=12.

Any redline makes the result `Blocked`, regardless of total. Otherwise:

- 85-100: `release`
- 70-84.99: `fix_then_release`
- 50-69.99: `rework`
- below 50: `reject`

Only `release` permits delivery as `mode=trial_run`. For every other decision,
fix the candidate pipeline when feasible, rerun the affected trial stages, and
repeat all six independent reviews. Never relabel an unreviewed repair as
passing. If the gate still does not pass, return `mode=planned_only` with the
review report and blockers.

Write `pipeline_review.json` and a concise `pipeline_review.md` beside the
pipeline. Include the per-dataset funnel, dimension scores, cited evidence,
redlines, total, decision, and review iteration. The final DataFlow agent JSON
summary must name both report paths and the gate decision.
