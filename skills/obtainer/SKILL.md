---
name: obtainer
description: Use this skill when LoopAI needs dataset discovery, acquisition, web-page collection, DataMixer lakehouse operations, data processing, indexing, recipe planning, or production training-data export. In long-running Codex SDK loops, when Analyzer produces an analysis report, failure taxonomy, or user request that implies new training data is needed, Codex must activate this Obtainer skill, parse the data need into an intent, and delegate the whole workflow to the Obtainer Orchestrator agent (`dm obtainer-orchestrator start`), then poll its structured status. The orchestrator owns lake bootstrap and the dispatch/gating of the managed sub-agents (dataset-acquisition-agent, dataflow agent-run, sft-export-agent); the outer Codex context must not run lake init, acquisition bridges, download manifest, ingest, or export itself for a normal obtain task. The SKILL.md body is the domain policy the orchestrator worker follows; detailed status contracts, command surfaces, and worker policies live in the references/ files.
---

# Obtainer Skill

## Purpose

Obtainer turns a data need into a production training-data artifact.
SearchAgent discovers hosted datasets, while the registered Domain Data
Acquisition WebAgent (`domain_data_acquisition`, legacy alias `webcrawler_dm`)
collects primary vertical-domain webpages as raw L1 data. DataMixer is the only
data-lake command surface for storage, ingest, processing, indexing, sampling,
recipe planning, export, snapshots, and lineage.

ObtainerCLI is the only supported end-to-end data workflow. Requests to clean,
deduplicate, quality-filter, map, construct, or export a training dataset are
Obtainer requests and must stay in the ObtainerCLI/DataMixer workflow through
the final artifact.

Treat an Analyzer report, failure taxonomy, training recipe, or next-iteration
data request as an Obtainer input, not a generic coding task: identify whether
it needs dataset acquisition, production export, or both, then run the
main-agent workflow below.

## Main-Agent Use: Delegate to the Obtainer Orchestrator

The main agent (starter) must NOT bootstrap the lake, dispatch acquisition /
export workers, or run DataFlowAgent itself for a normal obtain task. Those
responsibilities belong to the dedicated Obtainer Orchestrator agent
(`dm obtainer-orchestrator`). The orchestrator owns lake bootstrap, sub-agent
dispatch, progress gating, and the final deliverable report; the policy below
is its domain policy. The main agent only parses intent, starts the
orchestrator, polls its structured status, and reports the terminal artifacts.

### 1. Parse the data need into an intent

Extract from the Analyzer report / user request / recipe: an `--objective`
(what sample shape is needed), `--keywords` (search / domain hints),
`--target-datasets` (how many buckets/datasets), and a compact `--message`
(failure taxonomy, quality gates, proportions).

### 2. Start the orchestrator

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm \
  obtainer-orchestrator start \
  --run ./outputs/obtainer_run_<timestamp> \
  --objective "buggy and fixed Python code pairs for syntax repair SFT" \
  --keywords "python syntax error, code repair dataset" \
  --target-datasets 2 \
  --message "Analyzer report: ...; require license=unknown and quality>=0.8" \
  --python-executable /path/to/loopai-env/bin/python
```

`start` launches the orchestrator's inner Codex SDK worker in the background
and returns the run directory. Use `--foreground` only when you intend to
block.

### 3. Poll the orchestrator

A full obtainer orchestration runs for roughly 3-4 hours (acquisition +
DataFlow L4 + export). Poll no more often than every 5 minutes
(`sleep 300 && ... status ...`); faster polling wastes tokens and does not
speed up the run. Judge liveness from `updated_at` / `stale`, never from
`message` alone. Read the full status contract and terminal handling in
[references/orchestrator.md](references/orchestrator.md).

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm \
  obtainer-orchestrator status --run ./outputs/obtainer_run_<timestamp> --json
```

### 4. Terminal handling

- `completed` / `next_action=report`: read `final_report.json` and report
  warehouse, datasets, record counts, recipe / export artifacts, lineage,
  manifests, and snapshots.
- `interrupted` / `next_action=resume`: run `obtainer-orchestrator resume` with
  a reason; do NOT take over its sub-agents.
- `failed` / `next_action=blocked`: read `error` + failing `gates`, tell the
  user, and offer `resume` once the blocker is addressed.
- `stale=true` while `state=running`: warn that the orchestrator may be hung
  and offer `stop` or `resume`.

### Hard constraints for the main agent

- Never run `dm lake ...`, `dataset-acquisition-agent`, `sft-export-agent`,
  `dataflow agent-run`, `searchagent`, `webagent`, or `download manifest`
  yourself for a normal obtain task - the orchestrator owns those.
- Never `kill` / `pkill` the orchestrator's worker processes. The worker is
  managed by the CLI (`start` / `resume` / `stop`); raw process kills leave it
  in a stuck `running` state and break the run. If the status looks stuck,
  first check `updated_at` / `stale`; only then use
  `dm obtainer-orchestrator stop --run <dir>` followed by `resume`, and keep
  polling otherwise.
- Never claim obtainer completion without a `final_report.json` reported by
  the orchestrator.

## End-To-End Agent Workflow

The orchestrator worker follows this sequence. Detailed command surfaces live
in the references listed at the end of this file.

1. Read the Analyzer report or user request and extract the dataset intent.
2. Start `dataset-acquisition-agent`; it concurrently runs SearchAgent for
   hosted-dataset discovery and a detached WebAgent for raw webpage L1
   collection. WebAgent's L1 -> L2 -> L3 queues run continuously while
   candidate pruning, download, normalization, and DataMixer ingest proceed
   independently. See [references/acquisition.md](references/acquisition.md).
3. While both producers continue, poll per-bucket record/token counts and
   quality gates. Treat lake sufficiency, not WebAgent or worker completion,
   as the transition condition for every downstream step.
4. As soon as those gates pass, run the mandatory DataFlowAgent stage
   (`dm dataflow agent-run`) for quality, deduplication, safety, and
   post-training validity; it delivers a trial-verified pipeline, then the
   outer Codex runs it over `full_input.jsonl` with the chunked runner
   (`dataflow_chunked_runner --chunk-size 10000`) and merges the L4 output
   with `apply-jsonl`. L4 must be produced before any export (unless the user
   explicitly requests an L3 export). See
   [references/dataflow.md](references/dataflow.md).
5. Build indexes when semantic recall or semantic deduplication is needed.
6. Start `sft-export-agent` for production recipe planning and export only
   after the DataFlowAgent stage completed and the L4 dataset scale meets the
   recipe target with at least 5x in-lake redundancy per bucket. Do not wait
   for WebAgent or `dataset-acquisition-agent` to reach a terminal state. See
   [references/export.md](references/export.md).
7. Poll `sft-export-agent status`; resume or restart based on blockers.
8. Poll `dataset-acquisition-agent status` independently; resume or restart it
   based on `final_report.json` and blockers without stopping downstream work.
9. Report warehouse path, datasets, record counts, processing results, recipe
   fingerprint, snapshot id, export path, and manifest path.

## Hard Constraints

- **DataMixer-only lakehouse.** Do not use non-DataMixer lake logic, standalone
  table sampling, compatibility shims, or hand-written tiny fixtures for lake
  operations. If a DataMixer command cannot satisfy the request, stop and
  report the blocker.
- **Outer Codex must delegate acquisition.** For any normal dataset discovery,
  download, normalization, or ingest request, the outer Codex context must
  start the CLI wrapper `loopai-obtainercli dm ... dataset-acquisition-agent
  start` or run
  `${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm ... dataset-acquisition-agent start`.
  If the outer shell is not using the LoopAI environment, set
  `LOOPAI_PYTHON_EXECUTABLE=/path/to/loopai-env/bin/python` or pass
  `--python-executable /path/to/loopai-env/bin/python`, then poll/resume that
  worker. Do not use a generic `spawn_agent` worker for data acquisition. Do
  not create a SearchAgent task JSON, call `searchagent`, call
  `download manifest`, normalize files, or ingest rows from the outer Codex
  context. Those operations belong inside the CLI worker policy.
- **DataMixer is the only lake command surface.** Use
  `loopai-obtainercli dm ...` for initialization, schema inspection, dataset
  registry, ingest, query, processing operators, indexing, recall, recipes,
  snapshots, lineage, and export.
- **Reuse the active DataMixer warehouse.** Treat `.datamixer/lake.yaml` as a
  project pointer to a reusable DataMixer warehouse. Do not create a new lake
  per task unless the user explicitly asks for a new warehouse. Use
  `dm lake load` to point the project at an existing warehouse and
  `dm lake delete` to unload the pointer; deletion preserves the warehouse
  unless `--delete-warehouse --yes` is explicitly supplied. Prefer `dm lake
  scan` before choosing a warehouse, so the agent sees project and cache
  candidates instead of guessing paths.
- **Use lake context, not repeated boilerplate.** After a lake is loaded or
  initialized, use `dm --lake .datamixer/lake.yaml ...` for agents. The pointer
  persists the warehouse, selected WebAgent, model name, worker/subquery
  defaults, current acquisition run, and current campaign id. Do not pass a
  FastAPI/Configer SQLite file as `--root`; `--root` must be a DataMixer
  warehouse containing `datamixer.toml`.
- **Load or init the lake before any worker.** `dataset-acquisition-agent` and
  `sft-export-agent` refuse to start (`LAKE_NOT_LOADED`) unless the resolved
  warehouse already contains `datamixer.toml`. When a previous task ended,
  clear its stale bindings first with `dm lake unbind` so the pointer never
  confuses the new run with an old task_id; then start the worker with
  `dm --lake .datamixer/lake.yaml ...`.
- **Prepare worker intent before acquiring from a report.** First recognize the
  dataset-acquisition intent: target sample shape, task types, domains, source
  hints, proportions, quality gates, and concrete search objectives. Pass that
  intent to `dataset-acquisition-agent start` via `--objective`, `--keywords`,
  `--target-datasets`, and `--message`. The worker may then use SearchAgent
  internally. Never pass the raw Analyzer report as the only search target.
- **Objectives describe dataset shape, not only error keywords.** Use
  objectives like "buggy and fixed Python code pairs for syntax error repair",
  not only "SyntaxError" or "missing".
- **Continuous dual-stream pipeline.** Inside the acquisition worker, start
  SearchAgent and the registered `domain_data_acquisition` campaign
  concurrently. It is a vertical-domain data source collector, not a general
  browser helper. SearchAgent finds hosted datasets for the provider download
  manifest; WebAgent collects primary webpages into a distinct DataMixer L1
  dataset. Start WebAgent detached with its L1 -> L2 -> L3 streaming pipeline
  enabled. Its downstream queues consume new L1 rows while collection
  continues. Wait only for the SearchAgent artifact needed for hosted
  downloads; do not wait for the WebAgent campaign to complete before
  filtering, downloading, normalizing, ingesting, or beginning the next planned
  DataMixer stage. Retain separate artifacts/statuses in `final_report.json`.
  A launch or persistent processing failure is terminal; an active WebAgent
  campaign is not.
- **Lake readiness is the downstream gate.** While acquisition continues, poll
  per-bucket record/token counts and the planned quality gates. As soon as the
  lake satisfies the required volume, mix, and quality, immediately start the
  DataFlowAgent post-processing stage and required indexing and recipe
  planning; start production export only after the L4 gate below passes (or
  directly, when the user explicitly specifies an L3 export). Never use
  WebAgent completion, acquisition-worker completion, or empty producer queues
  as prerequisites; keep those producers running concurrently.
- **DataFlowAgent is a mandatory pre-export gate by default.** Every
  production export must first complete the DataFlowAgent post-processing
  stage (`dm dataflow agent-run`), which delivers a trial-verified L4
  pipeline; the outer Codex then executes it over the exported 5x
  bucket-buffer input with the chunked runner to produce the L4 dataset. L4 is
  the DataFlow-processed level on top of the L1 -> L2 -> L3 chain (raw
  webpages -> normalized PT -> SFT QA -> post-processed) and is the default
  sample source for production export. Skipping, deferring, or folding this
  stage into the export worker is not allowed; an export without a completed L4
  source is a blocker. If the user explicitly specifies an L3 export, the L4
  gate is waived and L3 data may be exported directly instead.
- **5x in-lake redundancy is a hard requirement.** To guarantee that the
  export mix can be met, the lake must hold at least 5x the recipe target
  volume both overall and per bucket (`available_samples >= 5 x
  target_samples` for every bucket). If any bucket falls below this floor,
  continue acquisition and DataFlow post-processing until the redundancy is
  satisfied; never export a mix from a non-redundant lake.
- **L4 scale gates export by default.** The DataFlowAgent stage is considered
  complete only when the final L4 dataset scale meets the recipe target
  (overall and per bucket, after the 5x redundancy floor). Only then call
  `sft-export-agent`. If the user explicitly specifies an L3 export, this L4
  scale gate is waived and L3 data may be exported once the lake
  volume/mix/quality gates pass.
- **WebAgent model prerequisite.** The wrapper resolves the Codex default
  model, registers that same provider in the DataMixer model pool, and records
  `resolved_model`, `webagent_model`, and `model_source` in `thread.json`.
  WebAgent must use that exact `webagent_model`; never select an arbitrary
  local model from `dm model list` and never continue when the value is
  absent.
- **Worker must inspect `searchagent_manifest.json` before downloading.** If
  errors are non-empty, the download list is empty, candidates are unrelated to
  the interpreted intent, or sources cannot satisfy the requested sample shape,
  the worker refines the search once. If still unsuitable, stop and report the
  mismatch.
- **Worker stops on download failure.** If internal `download manifest` fails,
  is interrupted, or creates partial/empty files for selected datasets, stop
  before ingest. Report the command, exit code, produced files, and blocker.
- **Acquisition download cap.** Internal `download manifest` writes at most
  100,000 rows and 2GiB of local JSONL output per dataset, even if
  `--max-rows 0`, a larger row value, or an oversized
  `--max-bytes-per-dataset` value is supplied. If the byte cap is reached,
  keep the partial JSONL and report `truncated`, `truncated_reason`,
  `rows_written`, and `bytes_written`. Treat this as the bounded acquisition
  bridge into DataMixer, not as final production SFT output.
- **Production SFT budget.** If the Analyzer report or user gives no explicit
  SFT target, set and report a production default before export: at least
  100,000 total records, or an explicit token budget when token counts are
  available.
- **Plan recipe proportions from the current need.** Do not assume a fixed
  bucket mix from examples or prior runs. The worker must choose and justify
  bucket proportions from the current user goal, Analyzer failure taxonomy,
  available lake inventory, quality filters, and record/token budget. For
  token-budget recipes, allocate against `total_tokens`; for sample-budget
  recipes, allocate against `total_samples`. Persist an acquisition
  `manifest/data_mix_plan.json` before discovery and an export
  `recipe/recipe_plan.json` plus `recipe/mix_plan.json` before outflow. The
  managed workers reject successful completion when those artifacts are
  absent, inconsistent, or lack per-bucket rationale.
- **Use semantic recipe filters.** Failure-taxonomy exports must use meaningful
  tags or columns such as `bug_type=syntax`, `bug_type=logic`,
  `bug_type=runtime`, and `bug_type=assertion`. If those tags do not exist in
  enough volume, stop and report that the lake cannot guarantee the requested
  mix. Do not replace them with broad proxies such as only `lang=python`.
- **Complete metadata on ingest.** Preserve source platform, source dataset
  id/name, source URI, license, language, domain, task type, processing level,
  source kind, split, loop UUID, and version id. Unknown values must be
  explicit, for example `license=unknown`; do not silently omit required
  provenance.
- **Two lake paths, one quality model.** WebAgent ingests per item through its
  L1 -> L2 -> L3 pipeline, where `domain_classify` judges each row against the
  campaign's `--focus-keywords` and `topic_quality_filter` approves or rejects
  it. The dataset acquisition path is batch: `dm ingest` writes every
  normalized row with the batch metadata and an explicit `--quality-level`; it
  does not run per-record LLM approval and never drops rows. A dataset name,
  source name, URL, or ingest flag is not domain evidence, so a `--domain`
  flag is only batch-level metadata, never a per-row attestation.
- **Dataset cards and additive derivation on ingest.** For every acquired
  dataset, the acquisition worker must write and register a Markdown dataset
  card describing source, license, split, row count, original fields, derived
  fields, derivation rules, validation checks, intended training use, and
  known risks. Dataset-specific derived fields are allowed for embedded
  complex formats such as step traces, multi-turn conversations, or
  question+options, but derivation must be additive: preserve every original
  field, keep the row count unchanged, and validate that every declared
  derived field is non-empty before ingest succeeds.
- **Never overwrite or hide provenance.** Keep dataset lineage, loop/version
  tags, recipe fingerprints, export manifests, and snapshots.
- **Register user-named benchmarks before acquisition/ingest.** If the user
  query or Analyzer report explicitly names the benchmark type to collect (for
  example "collect HumanEval-style code problems", "BIRD SQL pairs", or any
  named eval/test set), treat that dataset as evaluation-only and register it
  in the DataMixer benchmark registration layer (`dm contam add --name
  <benchmark> --file <file>`) before any acquisition or ingest, so downstream
  ingest and export decontamination exclude it and benchmark data cannot leak
  into training data. Do not rely on a later `decontaminate` pass alone to
  decide what to register.

## Failure Handling

- Missing warehouse: run DataMixer `init` at the intended `--root`.
- Missing or unreliable semantic tags: do not export the requested taxonomy
  mix; tag/process more data first.
- Insufficient bucket size: report the exact bucket, available count/tokens,
  and target count/tokens from `recipe plan`.
- Download failure or empty selected file: stop before ingest.
- Unknown license or source: tag as unknown and avoid restricted training
  export unless explicitly approved.
- Embedding/index failure: report the failed DataMixer command and continue
  only if the requested recipe does not depend on semantic
  recall/deduplication.

## References

- [references/orchestrator.md](references/orchestrator.md) - full poll status
  contract, terminal handling, and resume vs fresh-start rules for the
  orchestrator.
- [references/acquisition.md](references/acquisition.md) - `dataset-acquisition-agent`
  commands and injected policy (discovery bridges, manifest pruning, download
  caps).
- [references/dataflow.md](references/dataflow.md) - DataFlowAgent L4 stage
  rules and the chunked full-run contract.
- [references/export.md](references/export.md) - `sft-export-agent` commands,
  schema mapping, and injected export policy.
- [references/datamixer.md](references/datamixer.md) - DataMixer lake command
  surface (init/inspect, ingest, query, ops, index, snapshots, lineage).
- `docs/OBTAINERCLI_USAGE.md` (repo root) - detailed CLI usage.
