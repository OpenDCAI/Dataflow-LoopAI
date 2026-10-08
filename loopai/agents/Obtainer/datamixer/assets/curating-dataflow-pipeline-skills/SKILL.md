---
name: curating-dataflow-pipeline-skills
description: Promote exceptionally reviewed DataFlow pipelines into reusable task-pattern skills, or update an inferior same-task skill while preserving comparison evidence and history.
metadata:
  version: 1.0.0
---
# Curate Proven DataFlow Pipeline Skills

Use this skill only after `reviewing-dataflow-pipeline` has produced a complete
review with decision `release`. Ordinary passing pipelines are not
automatically worth preserving.

Before writing a skill, read the installed `skill-creator` skill completely and
follow it. If `skill-creator` is unavailable, do not improvise the skill
structure; report the missing dependency. Read the complete reference example
at [example/complete-math-reasoning-sft-skill](example/complete-math-reasoning-sft-skill)
when creating the first curated skill or when the required evidence layout is
unclear.

## Promotion gate

A candidate is eligible only when all conditions hold:

- review total is at least 90 and no redline is present;
- D2, D3, D4, and D6 raw scores are each at least 3;
- review evidence includes numbers and sample IDs;
- trial artifacts cover at least three records per selected source dataset;
- at least five official benchmark examples were used;
- pipeline source, trial input/output, benchmark samples, and review reports
  are complete and contain no secrets.

If any condition fails, record `not_promoted` in the DataFlow run summary and
do not create or update a skill.

## Match the task type

Derive a task fingerprint from benchmark/task family, required capabilities,
training record shape, reasoning policy, source schema families, answer format,
and major operator-chain purpose. Dataset names alone do not define a type.

Search only curated skills named `dataflow-pattern-*`. Never rewrite
`generating-dataflow-pipeline`, `reviewing-dataflow-pipeline`, this curator, or
`skill-creator`.

- No matching task fingerprint: create a new `dataflow-pattern-<type>` skill.
- Matching skill exists: compare the candidate with its currently recommended
  case using the same rubric and comparable evidence.
- Incomparable evidence: append nothing and report `comparison_inconclusive`.

## Replacement rule

Do not replace a recommendation merely because the total score is higher. The
candidate may become recommended only when it has no regression in redlines,
D2, D3, D4, or D6; supports at least the same task/schema coverage; and has a
strictly better supported tradeoff in quality, coverage, robustness, or cost.
Quality takes precedence over runtime cost.

When the candidate is eligible but not better, it may be appended as a
non-recommended case only if it adds meaningful schema, domain, or operating
coverage. Otherwise report `duplicate_not_added`.

## Required skill contents

Use `skill-creator` to create or update this layout:

```text
dataflow-pattern-<type>/
|-- SKILL.md
`-- examples/
    `-- <case-id>/
        |-- trial_input.jsonl
        |-- trial_output.jsonl
        |-- benchmark_samples.jsonl
        |-- pipeline.py
        |-- pipeline_review.json
        `-- case_report.md
```

`SKILL.md` must define the task fingerprint, applicability and exclusions,
recommended case, field-flow and operator-selection guidance, known schema
branches, quality invariants, and links to retained cases. It must explain the
pattern rather than blindly require one pipeline for every dataset.

Each case must contain small representative artifacts, not the full dataset.
Preserve `sample_id`, dataset provenance, and benchmark source metadata. Replace
API keys with environment-variable references and replace machine-specific
absolute paths with portable parameters. `case_report.md` must document purpose,
inputs, benchmark contract, operator chain, funnel and quality results,
limitations, cost/runtime characteristics, review score, and why it was or was
not selected as recommended.

Never overwrite an old case directory. Use a stable, unique case ID and record
the old-versus-new comparison in the new case report. Update the recommendation
pointer only after all files are written and the resulting skill passes the
installed `skill-creator/scripts/quick_validate.py` validator.

## Result

Return the curation status (`created`, `updated`, `appended`, `not_promoted`,
`duplicate_not_added`, or `comparison_inconclusive`), skill path, case ID,
previous and current recommended cases, and the evidence-backed reason. Add
this result to the DataFlow agent's final summary; curation failure must not
retroactively change an already valid pipeline review decision.
