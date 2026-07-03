---
name: obtainer
description: Use this skill when LoopAI needs dataset discovery, acquisition, DataMixer lakehouse operations, data processing, indexing, recipe planning, or production training-data export. In long-running Codex SDK loops, when Analyzer produces an analysis report or failure taxonomy that implies new training data is needed, Codex must activate this Obtainer skill, interpret the data need, run SearchAgent for source discovery, download selected sources, and perform all lake operations through the integrated DataMixer command surface.
---

# Obtainer Skill

## Purpose

Obtainer is the agent-facing workflow for turning a data need into a production
training-data artifact. SearchAgent handles dataset discovery. DataMixer is the
only data-lake command surface for storage, ingest, processing, indexing,
sampling, recipe planning, export, snapshots, and lineage.

When a long-running Codex SDK loop receives an Analyzer report, failure taxonomy,
training recipe, or next-iteration data request, treat it as an Obtainer input,
not a generic coding task:

1. Identify whether the report needs dataset acquisition, production export, or both.
2. For acquisition/download/ingest, start the managed
   `dataset-acquisition-agent` worker instead of manually driving
   SearchAgent/download/ingest from the outer Codex context.
3. Poll worker status and decide whether to resume the same worker or start a
   fresh worker.
4. Run DataMixer processing, quality, decontamination, deduplication, indexing,
   and recall operations required by the recipe.
5. For production SFT outflow, start the managed `sft-export-agent` worker.
6. Report warehouse path, datasets, record counts, recipe/export artifacts,
   lineage, manifests, and snapshots.

Do not introduce a separate DataMixer skill. DataMixer is already integrated into
Obtainer and must be invoked through `loopai-obtainercli dm ...`.

## Hard Constraints

- **DataMixer-only lakehouse.** Do not use non-DataMixer lake logic, standalone
  table sampling, compatibility shims, or hand-written tiny fixtures for lake
  operations. If a DataMixer command cannot satisfy the request, stop and report
  the blocker.
- **DataMixer is the only lake command surface.** Use
  `loopai-obtainercli dm ...` for initialization, schema inspection, dataset
  registry, ingest, query, processing operators, indexing, recall, recipes,
  snapshots, lineage, and export.
- **Reuse the active DataMixer warehouse.** Treat `.loopai/lake.yaml` as a
  project pointer to a reusable DataMixer warehouse. Do not create a new lake per
  task unless the user explicitly asks for a new warehouse. Use `dm lake load`
  to point the project at an existing warehouse and `dm lake delete` to unload
  the pointer; deletion preserves the warehouse unless `--delete-warehouse
  --yes` is explicitly supplied. Prefer `dm lake scan` before choosing a
  warehouse, so the agent sees project and cache candidates instead of guessing
  paths.
- **Search before acquiring from a report.** First recognize the
  dataset-acquisition intent: target sample shape, task types, domains, source
  hints, proportions, quality gates, and concrete search objectives. Pass that
  intent to `searchagent` via `--objective` / `--keywords` or a `--task-json`
  file. Never pass the raw Analyzer report as the only search target.
- **Objectives describe dataset shape, not only error keywords.** Use objectives
  like "buggy and fixed Python code pairs for syntax error repair", not only
  "SyntaxError" or "missing".
- **Search order:** deepsearch/research context first, then provider search such
  as Hugging Face and Kaggle. The final download list must be grounded in current
  external sources.
- **Inspect `searchagent_manifest.json` before downloading.** If errors are
  non-empty, the download list is empty, candidates are unrelated to the
  interpreted intent, or sources cannot satisfy the requested sample shape,
  refine the search once. If still unsuitable, stop and report the mismatch.
- **Prune unrelated candidates before download.** After SearchAgent returns a
  download list, compare every candidate against the original user request and
  interpreted dataset intent. Remove datasets that are clearly unrelated in
  domain, task type, language, source family, target label shape, or training
  purpose before running `download manifest`. Write a filtered manifest and a
  rejection list with explicit reasons; do not download the raw manifest when it
  contains unrelated candidates.
- **Stop on download failure.** If `download manifest` fails, is interrupted, or
  creates partial/empty files for selected datasets, stop before ingest. Report
  the command, exit code, produced files, and blocker.
- **Acquisition download cap.** `download manifest` writes at most 100,000 rows
  per dataset, even if `--max-rows 0` or a larger value is supplied. Treat this
  as the bounded acquisition bridge into DataMixer, not as final production SFT
  output.
- **Production SFT budget.** If the Analyzer report or user gives no explicit
  SFT target, set and report a production default before export: at least
  100,000 total records, or an explicit token budget when token counts are
  available.
- **Preserve recipe proportions.** A 40/30/20/10 recipe over 100,000 records
  means 40,000 / 30,000 / 20,000 / 10,000 records. For token-budget recipes,
  preserve proportions against `total_tokens`.
- **Use semantic recipe filters.** Failure-taxonomy exports must use meaningful
  tags or columns such as `bug_type=syntax`, `bug_type=logic`,
  `bug_type=runtime`, and `bug_type=assertion`. If those tags do not exist in
  enough volume, stop and report that the lake cannot guarantee the requested
  mix. Do not replace them with broad proxies such as only `lang=python`.
- **Complete metadata on ingest.** Preserve source platform, source dataset
  id/name, source URI, license, language, domain, task type, processing level,
  source kind, split, loop UUID, and version id. Unknown values must be explicit,
  for example `license=unknown`; do not silently omit required provenance.
- **Never overwrite or hide provenance.** Keep dataset lineage, loop/version
  tags, recipe fingerprints, export manifests, and snapshots.

## Command Surface

Obtainer has one production data-lake command surface:

```bash
loopai-obtainercli dm --root /path/to/datamixer-warehouse <datamixer-command> --json
loopai-obtainercli dm --lake .loopai/lake.yaml <datamixer-command> --json
```

Use `--root` when operating directly on a DataMixer warehouse. Use `--lake` only
when a LoopAI lake pointer already exists and should resolve to the integrated
DataMixer warehouse. All `dm` commands emit machine-readable JSON.

Manage the project pointer to a reusable DataMixer warehouse:

```bash
loopai-obtainercli dm lake scan --link .loopai/lake.yaml --project-root .
loopai-obtainercli dm lake current --link .loopai/lake.yaml
loopai-obtainercli dm lake load --warehouse /path/to/warehouse --link .loopai/lake.yaml
loopai-obtainercli dm lake delete --link .loopai/lake.yaml
```

`dm lake delete` unloads only the pointer by default. Use
`--delete-warehouse --yes` only when the actual reusable warehouse should be
removed.

Search and provider download are Obtainer acquisition bridges. Outer Codex
normally reaches them through `dataset-acquisition-agent`; call these low-level
commands directly only for debugging or a deliberately manual acquisition run:

```bash
loopai-obtainercli searchagent ...
loopai-obtainercli download manifest ...
```

After download, all lake work returns to `loopai-obtainercli dm ...`.

## Dataset Acquisition Worker

For dataset discovery, candidate pruning, download, normalization, and DataMixer
ingest, outer Codex should use the managed acquisition worker wrapper.

Start a new worker:

```bash
loopai-obtainercli dm --root /path/to/warehouse dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --analysis-report ./outputs/analyzer_report.md \
  --objective "collect general-domain instruction and QA datasets" \
  --keywords "instruction tuning dataset, open QA dataset, summarization dataset" \
  --target-datasets 30 \
  --max-rows-per-dataset 100000 \
  --discovery-mode auto \
  --model deepseek-codex
```

`start` runs the inner Codex SDK worker in the background by default and returns
PID plus log paths. Use `--foreground` only when the caller intentionally wants
to block.

Poll status:

```bash
loopai-obtainercli dm --root /path/to/warehouse dataset-acquisition-agent status \
  --run ./outputs/acquisition_run
```

Resume the same worker:

```bash
loopai-obtainercli dm --root /path/to/warehouse dataset-acquisition-agent resume \
  --run ./outputs/acquisition_run \
  --message "Remove unrelated datasets from the filtered manifest, then continue ingest." \
  --model deepseek-codex
```

The worker wrapper injects the detailed acquisition policy: explicit objective
and keywords, candidate list review against the original request before
download, rejection report, 100,000-row per-dataset cap, normalized JSONL,
DataMixer-only ingest/status/query/index operations, complete provenance tags,
and `final_report.json`.

## DataMixer Lake Operations

Initialize and inspect:

```bash
loopai-obtainercli dm --root /path/to/warehouse init --json
loopai-obtainercli dm --root /path/to/warehouse status --json
loopai-obtainercli dm --root /path/to/warehouse schema --json
loopai-obtainercli dm --root /path/to/warehouse columns --json
loopai-obtainercli dm --root /path/to/warehouse stats --json
```

Dataset registry and ingest:

```bash
loopai-obtainercli dm --root /path/to/warehouse dataset add \
  --name code_repair_mix \
  --source huggingface \
  --license unknown \
  --description "buggy/fixed code repair datasets" \
  --json

loopai-obtainercli dm --root /path/to/warehouse ingest code_repair_mix \
  --file ./downloads/records/dataset.train.jsonl \
  --content-key content \
  --stage sft \
  --domain code \
  --lang python \
  --source huggingface \
  --license unknown \
  --task-type SFT \
  --tokenizer tiktoken:o200k_base \
  --json
```

If the downloaded file is not already normalized JSONL, use DataMixer
`agent-ingest`:

```bash
loopai-obtainercli dm --root /path/to/warehouse agent-ingest ./downloads/raw_file \
  --engine builtin \
  --dataset code_repair_mix \
  --json
```

Query, coverage, and distributions:

```bash
loopai-obtainercli dm --root /path/to/warehouse query \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 20 \
  --json

loopai-obtainercli dm --root /path/to/warehouse dist \
  --column domain \
  --json

loopai-obtainercli dm --root /path/to/warehouse grade \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --column quality_score \
  --json
```

Processing, quality, safety, and deletion:

```bash
loopai-obtainercli dm --root /path/to/warehouse op list --json
loopai-obtainercli dm --root /path/to/warehouse op run quality_score --dataset code_repair_mix --json
loopai-obtainercli dm --root /path/to/warehouse op run minhash_dedup --dataset code_repair_mix --arg k=5 --json
loopai-obtainercli dm --root /path/to/warehouse op run semantic_dedup --dataset code_repair_mix --json
loopai-obtainercli dm --root /path/to/warehouse contam add --name benchmark --file benchmark.txt --json
loopai-obtainercli dm --root /path/to/warehouse decontaminate --against benchmark --json
loopai-obtainercli dm --root /path/to/warehouse pii-redact --dataset code_repair_mix --dry-run --json
loopai-obtainercli dm --root /path/to/warehouse erase <sample_id> --reason "user request" --json
```

For downstream-task specific DataFlow processing, do not blindly select one
DataFlow operator by hand. Use the integrated Codex SDK orchestration so the
agent can inspect trial rows, plan the operator chain, generate a DataFlow
pipeline, trial-run it, and optionally merge the processed JSONL back:

```bash
loopai-obtainercli dm --root /path/to/warehouse dataflow agent-run \
  --target "score GSM8K answer-focused SFT rows and keep high-quality rows" \
  --model deepseek-codex \
  --dataset math_sft \
  --trial-rows 20 \
  --expected-outputs math_answer_quality \
  --apply \
  --json
```

The low-level `op run dataflow --arg op=<DataFlowClassName>` bridge is only for
manual/operator-specific runs when the operator choice is already known.

Index and recall:

```bash
loopai-obtainercli dm --root /path/to/warehouse index build --json
loopai-obtainercli dm --root /path/to/warehouse recall \
  --query "buggy and fixed Python code pairs for runtime exception repair" \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 50 \
  --json
```

Lineage and snapshots:

```bash
loopai-obtainercli dm --root /path/to/warehouse snapshot create --name sft_mix_v1 --json
loopai-obtainercli dm --root /path/to/warehouse lineage list --json
```

## SearchAgent Dataset Collection

This is the low-level discovery bridge used by the acquisition worker. For
manual debugging, do not call `searchagent` with only `--query-file`; first turn
the report into explicit acquisition intents.

```bash
loopai-obtainercli searchagent \
  --query-file ./outputs/analyzer_report.md \
  --objective "collect buggy and fixed Python code-pair datasets covering syntax, logic, runtime, and assertion failures for SFT/DPO/RL training" \
  --keywords "program repair dataset, buggy fixed code pairs, Python bug fix dataset, runtime exception repair, assertion failure repair" \
  --output-root ./outputs \
  --max-deep-queries 3 \
  --max-deep-pages 3 \
  --json
```

For multi-part reports, prefer `--task-json` so each acquisition need has its
own objective and keywords:

```json
{
  "tasks": [
    {
      "type": "download",
      "objective": "collect buggy and fixed code-pair datasets for syntax error repair",
      "search_keywords": ["program repair dataset", "buggy fixed code", "Python SyntaxError fix"]
    },
    {
      "type": "download",
      "objective": "collect code-pair datasets for logic bug fixes and failing tests",
      "search_keywords": ["software bug fix dataset", "failing test fixed code", "program repair"]
    }
  ]
}
```

Then run:

```bash
loopai-obtainercli searchagent \
  --query-file ./outputs/analyzer_report.md \
  --task-json ./outputs/search_intent_tasks.json \
  --output-root ./outputs \
  --json
```

SearchAgent reads model defaults from `starter.yaml` unless CLI flags override
them. The manifest contains deepsearch summaries, discovered URLs, enriched
keywords, and unranked provider download candidates.

## Manifest Download

This is the low-level download bridge used by the acquisition worker. Use
`download manifest` only to materialize SearchAgent candidates into local
lake-ready files. It is not a lake operation.

Before downloading, compare the manifest against the original user request and
write a pruned manifest, for example `searchagent_manifest.filtered.json`.
Remove clearly unrelated candidates and keep a rejection report such as
`searchagent_manifest.rejections.json` with dataset id, reason, and the mismatch
dimension. Examples of rejection reasons: wrong domain, wrong task type, wrong
language, unrelated source family, missing target label shape, license blocker,
or provider failure risk.

```bash
loopai-obtainercli download manifest \
  --manifest ./outputs/searchagent_manifest.filtered.json \
  --output-root ./outputs/downloads \
  --split train \
  --max-rows 100000 \
  --json
```

The downloader enforces a 100,000-row cap per dataset. `--max-rows 0` is also
capped to 100,000 rows per dataset for safety. Production SFT sizing and final
mixing must be handled later through DataMixer recipes.

## Production SFT Export

For production SFT outflow, outer Codex should use the managed export worker
wrapper instead of manually driving `recipe validate/plan/preview/export`.
The wrapper starts an isolated Codex SDK worker and injects the detailed
DataMixer recipe, schema, validation, snapshot, and failure-handling policy into
that worker's context.

Start a new isolated worker:

```bash
loopai-obtainercli dm --root /path/to/warehouse sft-export-agent start \
  --run ./outputs/sft_export_run \
  --analysis-report ./outputs/analyzer_report.md \
  --format alpaca \
  --target-records 100000 \
  --out ./outputs/sft_export_run/export \
  --model deepseek-codex
```

`start` returns after launching a background worker by default. Use
`--foreground` only when the caller intentionally wants to block until the
inner Codex SDK worker finishes.

Check a worker:

```bash
loopai-obtainercli dm --root /path/to/warehouse sft-export-agent status \
  --run ./outputs/sft_export_run
```

Continue the same inner Codex thread when the final report exposes a repairable
schema or quality problem:

```bash
loopai-obtainercli dm --root /path/to/warehouse sft-export-agent resume \
  --run ./outputs/sft_export_run \
  --message "Exclude buckets whose output field falls back to text, then re-export." \
  --model deepseek-codex
```

`resume` also runs in the background by default and returns a PID plus log
paths. Poll with `status`.

Outer Codex decides between `resume` and a fresh `start`:

- Use `resume` when the same worker understood the target but needs a bounded
  correction to recipe mapping, bucket filters, normalization, or validation.
- Use a fresh `start` when the worker context is polluted, picked the wrong
  task, or needs a different high-level strategy.

The worker wrapper owns the detailed constraints. In particular, for Alpaca SFT
it requires final rows to contain exactly `instruction`, `input`, and `output`,
forbids `output` fallback to whole-record text fields, rejects
`instruction == output`, requires DataMixer recipe export with snapshot, and
writes `final_report.json` with manifest, snapshot, digest, validation evidence,
and blockers.

## End-To-End Agent Workflow

1. Read the Analyzer report or user request and extract the dataset intent.
2. Start `dataset-acquisition-agent` for discovery, candidate pruning,
   download, normalization, and DataMixer ingest.
3. Poll `dataset-acquisition-agent status`; resume or restart based on
   `final_report.json` and blockers.
4. Run DataMixer operators for quality, deduplication, safety, and post-training
   validity tags. For downstream-task specific processing, prefer
   `dm dataflow agent-run` so Codex SDK plans and trial-runs the DataFlow
   operator chain before merge-back.
5. Build indexes when semantic recall or semantic deduplication is needed.
6. Start `sft-export-agent` for production recipe planning and export.
7. Poll `sft-export-agent status`; resume or restart based on blockers.
8. Report warehouse path, datasets, record counts, processing results, recipe
    fingerprint, snapshot id, export path, and manifest path.

## Failure Handling

- Missing warehouse: run DataMixer `init` at the intended `--root`.
- Missing or unreliable semantic tags: do not export the requested taxonomy mix;
  tag/process more data first.
- Insufficient bucket size: report the exact bucket, available count/tokens, and
  target count/tokens from `recipe plan`.
- Download failure or empty selected file: stop before ingest.
- Unknown license or source: tag as unknown and avoid restricted training export
  unless explicitly approved.
- Embedding/index failure: report the failed DataMixer command and continue only
  if the requested recipe does not depend on semantic recall/deduplication.

## References

Detailed CLI usage:

```text
docs/OBTAINERCLI_USAGE.md
```
