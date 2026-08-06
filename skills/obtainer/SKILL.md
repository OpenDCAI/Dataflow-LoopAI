---
name: obtainer
description: Use this skill when LoopAI needs dataset discovery, acquisition, web-page collection, DataMixer lakehouse operations, data processing, indexing, recipe planning, or production training-data export. In long-running Codex SDK loops, when Analyzer produces an analysis report, failure taxonomy, or user request that implies new training data is needed, Codex must activate this Obtainer skill, interpret the data need, and start the managed dataset-acquisition-agent worker. The worker concurrently runs SearchAgent and the registered DataMixer WebAgent; the outer Codex context must not run either acquisition bridge, download manifest, or ingest directly for normal acquisition.
---

# Obtainer Skill

## Purpose

Obtainer is the agent-facing workflow for turning a data need into a production
training-data artifact. SearchAgent discovers hosted datasets, while the
registered Domain Data Acquisition WebAgent (`domain_data_acquisition`, legacy
alias `webcrawler_dm`) collects primary vertical-domain webpages as raw L1 data. DataMixer
is the only data-lake command surface for storage, ingest, processing,
indexing, sampling, recipe planning, export, snapshots, and lineage.

ObtainerCLI is the only supported end-to-end data workflow. Requests to clean,
deduplicate, quality-filter, map, construct, or export a training dataset are
Obtainer requests and must stay in the ObtainerCLI/DataMixer workflow through
the final artifact.

When a long-running Codex SDK loop receives an Analyzer report, failure taxonomy,
training recipe, or next-iteration data request, treat it as an Obtainer input,
not a generic coding task:

1. Identify whether the report needs dataset acquisition, production export, or both.
2. For acquisition/download/ingest, start the managed
   `dataset-acquisition-agent` worker instead of manually driving
   SearchAgent/WebAgent/download/ingest from the outer Codex context.
3. Poll worker status and decide whether to resume the same worker or start a
   fresh worker.
4. Run the mandatory DataFlowAgent post-processing stage (`dm dataflow
   agent-run`), which materializes the L4 dataset (quality, decontamination,
   deduplication, normalization, safety, and post-training validity).
5. Only after the DataFlowAgent run completes and the final L4 dataset scale
   meets the recipe target, start the managed `sft-export-agent` worker for
   production SFT outflow.
6. Report warehouse path, datasets, record counts, recipe/export artifacts,
   lineage, manifests, and snapshots.


## Hard Constraints

- **DataMixer-only lakehouse.** Do not use non-DataMixer lake logic, standalone
  table sampling, compatibility shims, or hand-written tiny fixtures for lake
  operations. If a DataMixer command cannot satisfy the request, stop and report
  the blocker.
- **Outer Codex must delegate acquisition.** For any normal dataset discovery,
  download, normalization, or ingest request, the outer Codex context must start
  the CLI wrapper `loopai-obtainercli dm ... dataset-acquisition-agent start`
  or run `${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm ... dataset-acquisition-agent
  start`. If the outer shell is not using the LoopAI environment, set
  `LOOPAI_PYTHON_EXECUTABLE=/path/to/loopai-env/bin/python` or pass
  `--python-executable /path/to/loopai-env/bin/python`, then poll/resume that worker.
  Do not use a generic `spawn_agent`
  worker for data acquisition. Do not create a SearchAgent task JSON, call
  `searchagent`, call `download manifest`, normalize files, or ingest rows from
  the outer Codex context. Those operations belong inside the CLI worker policy.
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
- **Use lake context, not repeated boilerplate.** After a lake is loaded or
  initialized, use `dm --lake .loopai/lake.yaml ...` for agents. The pointer
  persists the warehouse, selected WebAgent, model name, worker/subquery
  defaults, current acquisition run, and current campaign id. Do not pass a
  FastAPI/Configer SQLite file as `--root`; `--root` must be a DataMixer
  warehouse containing `datamixer.toml`.
- **Load or init the lake before any worker.** `dataset-acquisition-agent` and
  `sft-export-agent` refuse to start (`LAKE_NOT_LOADED`) unless the resolved
  warehouse already contains `datamixer.toml`. When a previous task ended, clear
  its stale bindings first with `dm lake unbind` so the pointer never confuses
  the new run with an old task_id; then start the worker with
  `dm --lake .loopai/lake.yaml ...`.
- **Prepare worker intent before acquiring from a report.** First recognize the
  dataset-acquisition intent: target sample shape, task types, domains, source
  hints, proportions, quality gates, and concrete search objectives. Pass that
  intent to `dataset-acquisition-agent start` via `--objective`, `--keywords`,
  `--target-datasets`, and `--message`. The worker may then use SearchAgent
  internally. Never pass the raw Analyzer report as the only search target.
- **Objectives describe dataset shape, not only error keywords.** Use objectives
  like "buggy and fixed Python code pairs for syntax error repair", not only
  "SyntaxError" or "missing".
- **Continuous dual-stream pipeline:** inside the acquisition worker, start
  SearchAgent and the registered `domain_data_acquisition` campaign concurrently.
  It is a vertical-domain data source collector, not a general browser helper.
  SearchAgent finds hosted datasets for the provider download manifest; WebAgent
  collects primary webpages into a distinct DataMixer L1 dataset. Start WebAgent
  detached with its L1 -> L2 -> L3 streaming pipeline enabled. Its downstream
  queues consume new L1 rows while collection continues. Wait only for the
  SearchAgent artifact needed for hosted downloads; do not wait for the WebAgent
  campaign to complete before filtering, downloading, normalizing, ingesting, or
  beginning the next planned DataMixer stage. Retain separate artifacts/statuses
  in `final_report.json`. A launch or persistent processing failure is terminal;
  an active WebAgent campaign is not.
- **Lake readiness is the downstream gate:** while acquisition continues, poll
  per-bucket record/token counts and the planned quality gates. As soon as the
  lake satisfies the required volume, mix, and quality, immediately start the
  DataFlowAgent post-processing stage and required indexing and recipe planning;
  start production export only after the L4 gate below passes. Never use
  WebAgent completion, acquisition-worker completion, or empty producer queues
  as prerequisites; keep those producers running concurrently.
- **DataFlowAgent is a mandatory pre-export gate.** Every production export
  must first complete the DataFlowAgent post-processing stage
  (`dm dataflow agent-run`), which delivers a trial-verified L4 pipeline; the
  outer Codex then executes it over the exported 1.5x bucket-buffer input with
  the chunked runner to produce the L4 dataset. L4 is the DataFlow-processed
  level on top of the L1 -> L2 -> L3 chain (raw webpages -> normalized PT ->
  SFT QA -> post-processed) and is the only sample source for production
  export. Skipping, deferring, or folding this stage into the export worker is
  not allowed; an export without a completed L4 source is a blocker.
- **1.5x in-lake redundancy is a hard requirement.** To guarantee that the
  export mix can be met, the lake must hold at least 1.5x the recipe target
  volume both overall and per bucket (`available_samples >= 1.5 x
  target_samples` for every bucket). If any bucket falls below this floor,
  continue acquisition and DataFlow post-processing until the redundancy is
  satisfied; never export a mix from a non-redundant lake.
- **L4 scale gates export.** The DataFlowAgent stage is considered complete only
  when the final L4 dataset scale meets the recipe target (overall and per
  bucket, after the 1.5x redundancy floor). Only then call `sft-export-agent`.
- **WebAgent model prerequisite:** the wrapper resolves the Codex default model,
  registers that same provider in the DataMixer model pool, and records
  `resolved_model`, `webagent_model`, and `model_source` in `thread.json`.
  WebAgent must use that exact `webagent_model`; never select an arbitrary local
  model from `dm model list` and never continue when the value is absent.
- **Worker must inspect `searchagent_manifest.json` before downloading.** If
  errors are non-empty, the download list is empty, candidates are unrelated to
  the interpreted intent, or sources cannot satisfy the requested sample shape,
  the worker refines the search once. If still unsuitable, stop and report the
  mismatch.
- **Worker must prune unrelated candidates before download.** After internal
  SearchAgent returns a download list, the worker compares every candidate
  against the original user request and interpreted dataset intent. Remove
  datasets that are clearly unrelated in domain, task type, language, source
  family, target label shape, or training purpose before the worker runs
  `download manifest`. Write a filtered manifest and a rejection list with
  explicit reasons; do not download the raw manifest when it contains unrelated
  candidates.
- **Worker stops on download failure.** If internal `download manifest` fails,
  is interrupted, or creates partial/empty files for selected datasets, stop
  before ingest. Report the command, exit code, produced files, and blocker.
- **Acquisition download cap.** Internal `download manifest` writes at most
  100,000 rows and 2GiB of local JSONL output per dataset, even if `--max-rows
  0`, a larger row value, or an oversized `--max-bytes-per-dataset` value is
  supplied. If the byte cap is reached, keep the partial JSONL and report
  `truncated`, `truncated_reason`, `rows_written`, and `bytes_written`. Treat
  this as the bounded acquisition bridge into DataMixer, not as final
  production SFT output.
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
  managed workers reject successful completion when those artifacts are absent,
  inconsistent, or lack per-bucket rationale.
- **Use semantic recipe filters.** Failure-taxonomy exports must use meaningful
  tags or columns such as `bug_type=syntax`, `bug_type=logic`,
  `bug_type=runtime`, and `bug_type=assertion`. If those tags do not exist in
  enough volume, stop and report that the lake cannot guarantee the requested
  mix. Do not replace them with broad proxies such as only `lang=python`.
- **Complete metadata on ingest.** Preserve source platform, source dataset
  id/name, source URI, license, language, domain, task type, processing level,
  source kind, split, loop UUID, and version id. Unknown values must be explicit,
  for example `license=unknown`; do not silently omit required provenance.
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
  fields, derivation rules, validation checks, intended training use, and known
  risks. Dataset-specific derived fields are allowed for embedded complex
  formats such as step traces, multi-turn conversations, or question+options,
  but derivation must be additive: preserve every original field, keep the row
  count unchanged, and validate that every declared derived field is non-empty
  before ingest succeeds.
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
loopai-obtainercli dm lake context --link .loopai/lake.yaml
loopai-obtainercli dm lake unbind --link .loopai/lake.yaml
```

`dm lake delete` unloads only the pointer by default. Use
`--delete-warehouse --yes` only when the actual reusable warehouse should be
removed.

SearchAgent, WebAgent, and provider download are internal acquisition bridges.
In the normal product workflow, outer Codex reaches them only by starting
`dataset-acquisition-agent`. Do not call low-level `searchagent`, `webagent`,
or `download manifest` from the outer Codex context.

## Dataset Acquisition Worker

For dataset discovery, WebAgent collection, candidate pruning, download,
normalization, and DataMixer ingest, outer Codex must use the managed acquisition
worker CLI wrapper. Here
"worker" means the `dataset-acquisition-agent start` command below, not a
generic spawned Codex worker.

Start a new worker:

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .loopai/lake.yaml dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --analysis-report ./outputs/analyzer_report.md \
  --objective "collect general-domain instruction and QA datasets" \
  --keywords "instruction tuning dataset, open QA dataset, summarization dataset" \
  --target-datasets 30 \
  --max-rows-per-dataset 100000 \
  --max-bytes-per-dataset 2147483648 \
  --discovery-mode auto \
  --python-executable /path/to/loopai-env/bin/python
```

`start` runs the inner Codex SDK worker in the background by default and returns
PID plus log paths. Use `--foreground` only when the caller intentionally wants
to block. If `loopai-obtainercli` is not installed as a console script, use the
`${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli ...` form.

Poll status:

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .loopai/lake.yaml dataset-acquisition-agent status \
  --run ./outputs/acquisition_run
```

Resume the same worker:

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .loopai/lake.yaml dataset-acquisition-agent resume \
  --run ./outputs/acquisition_run \
  --message "Remove unrelated datasets from the filtered manifest, then continue ingest."
```

Do not pass `--model` to `dataset-acquisition-agent` unless the user explicitly
requests a one-off override. By default the wrapper resolves the Codex worker
model from Starter's model pool, preferring the configured Codex default model.

The worker wrapper injects the detailed acquisition policy: explicit objective
and keywords, concurrent SearchAgent/WebAgent discovery, candidate list review
against the original request before download, rejection report, 100,000-row and
2GiB JSONL-output per-dataset caps, normalized JSONL, DataMixer-only
ingest/status/query/index operations, complete provenance tags, and
`final_report.json`.

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
  --dataset-card ./manifest/dataset_cards/code_repair_mix.md \
  --derived-field train_output \
  --source-row-count 100000 \
  --stage sft \
  --domain code \
  --lang python \
  --source huggingface \
  --license unknown \
  --task-type SFT \
  --quality-level L3 \
  --tokenizer tiktoken:o200k_base \
  --json
```

If the downloaded file is not already normalized JSONL, use DataMixer
`agent-ingest`:

```bash
loopai-obtainercli dm --root /path/to/warehouse agent-ingest ./downloads/raw_file \
  --engine builtin \
  --dataset code_repair_mix \
  --quality-level L3 \
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

后处理阶段是必须要使用 dataflowagent 的，不要手工盲选单个 DataFlow operator。
`dataflow agent-run` 会让 Codex SDK 先导出试跑样本、按 DataFlow-Skills 规则
规划算子链、生成并试跑 pipeline；**试跑成功即交付**（`mode=trial_run`，
交付物 = `pipeline.py` + 试跑输出 `trial_processed.jsonl`）。**全量执行由
上层 Codex 负责**：拿到交付的 pipeline 后，用 chunk 脚手架跑
`full_input.jsonl`（1.5x 桶缓冲导出，不是全湖），产出 `full_processed.jsonl`
（L4），再按 `sample_id` 用 `apply-jsonl` merge 回 DataMixer。不要让
dataflowagent 自己跑全量或 merge。

**按桶 1.5x 缓冲导出，不是全量导出。** 调用 `agent-run` 时尽量带上出湖
`--recipe`（recipe.yaml）或 `--mix-plan`（mix_plan.json）：full input 会按
每个桶 `ceil(bucket_target * 1.5)` 行、固定 seed 抽样导出（可用行不足则全取），
避免把整个湖（动辄十几万行 / 数 GB）无谓地全量处理后处理。只对
`full_input.jsonl` 给到的行做后处理，不要自行重新全量导出或扩大范围。

**质量评估必须使用 DataFlow 的 LLM 评估算子**（如 `PromptedEvaluator` /
`PromptedFilter` 这类 LLM 打分/过滤算子），不得因耗时或成本而退化成纯启发式
规则打分；只有任务本身没有 LLM 打分语义、或 LLM serving 不可用时才允许规则
算子兜底并说明具体原因。**禁止 LLM 逐条重写或改写文本内容**，LLM 只做打分
与筛选。

全量执行由上层用 chunked runner 跑，**可能非常耗时**——LLM 质量评估算子
逐条打分时，数小时到十几小时属正常，跑完为止。**不要用外层 shell `timeout`
包住 agent-run 或 chunked runner**；`agent-run` 只做试跑，其 Codex 会话预算
默认 1 小时足够，与全量耗时无关。

```bash
# 1) dataflowagent 交付试跑成功的 pipeline（不跑全量、不 merge）
loopai-obtainercli dm --root /path/to/warehouse dataflow agent-run \
  --target "score GSM8K answer-focused SFT rows and keep high-quality rows" \
  --dataset math_sft \
  --trial-rows 20 \
  --expected-outputs math_answer_quality \
  --recipe /path/to/recipe.yaml \
  --json

# 2) 上层 Codex 用交付的 pipeline 跑 chunk 全量（结果在 agent-run 的
#    upstream.chunked_run_command / apply_command 里）
python -m loopai.agents.Obtainer.datamixer.dataflow_chunked_runner \
  --input /path/to/full_input.jsonl \
  --pipeline /path/to/pipeline.py \
  --output /path/to/full_processed.jsonl --chunk-size 10000

# 3) 全量完成后合并回湖
loopai-obtainercli dm --root /path/to/warehouse apply-jsonl \
  --file /path/to/full_processed.jsonl --field content --json
```

DataFlowAgent agent-run rules:

- **Trial -> deliver -> upstream full is the contract.** The agent must
  trial-run the pipeline and deliver it (`mode=trial_run`, `pipeline_path` +
  `processed_jsonl`); it must NOT launch the full processing or write
  `full_processed.jsonl` itself. The upper-layer Codex runs the delivered
  pipeline over the exported full input and only treats L4 as complete when
  `full_processed.jsonl` exists and is verified.
- **Export the 1.5x bucket buffer, not the whole lake.** Pass `--recipe`
  (recipe.yaml) or `--mix-plan` (mix_plan.json) so the full input is sampled
  per bucket to `ceil(bucket_target * 1.5)` rows (fixed seed, short buckets
  export everything available). The processing scope is exactly
  `full_input.jsonl`; never re-export or widen it.
- **LLM quality-evaluation operators are mandatory.** Use DataFlow LLM
  scoring/filter operators (`PromptedEvaluator`, `PromptedFilter`, ...) for
  quality scoring. Cost/latency is NOT a valid reason to fall back to pure
  heuristic rules - a slow LLM pass just takes longer. Rule operators are
  allowed only when the task has no LLM-scoring semantics or the LLM serving is
  unavailable; say so in the summary. Never let the LLM rewrite row text -
  score and filter only.
- **Full run is streaming, chunked, and executed by the upper layer.** The
  outer Codex drives the full scale through
  `loopai.agents.Obtainer.datamixer.dataflow_chunked_runner`
  (`--chunk-size 10000`, one chunk per pipeline launch, ordered merge) and must
  never load the whole export into a single DataFrame. The delivered pipeline
  must follow the `DATAFLOW_INPUT` / `DATAFLOW_CACHE_DIR` / `DATAFLOW_PREFIX`
  env-var convention so the scaffold can run it per chunk.
- **Never wrap agent-run or the chunked full run in a shell `timeout`**
  (e.g. `timeout 60 ...`). A shell timeout kills the inner Codex session or the
  chunked runner mid-flight and leaves the lake in a half-processed state. The
  **1-hour budget applies only to the agent-run Codex session (trial delivery)**;
  the upper-layer full run has no time budget and may take many hours when LLM
  quality-evaluation operators score every row - let it finish.
- The agent runs with its own Codex home (`codex_home_dataflow/AGENTS.md`),
  whose rules require it to deliver the trial-verified pipeline (never launch
  the full run itself) and to gate export on the 1.5x L4 redundancy floor.

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

## Internal Discovery Bridges

This low-level discovery bridge is for the isolated acquisition worker and for
human debugging only. If you are the outer Codex agent responding to a user
workflow request, skip this section and start `dataset-acquisition-agent`
instead. Do not create task JSON or run this command from the outer Codex
context.

```bash
loopai-obtainercli dm --root /path/to/warehouse dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --objective "collect buggy and fixed Python code-pair datasets covering syntax, logic, runtime, and assertion failures for SFT training" \
  --keywords "program repair dataset, buggy fixed code pairs, Python SyntaxError fix, runtime exception repair" \
  --target-datasets 8 \
  --max-rows-per-dataset 100000 \
  --max-bytes-per-dataset 2147483648 \
  --discovery-mode auto \
  --json
```

For multi-domain requests such as text2sql + math + code, describe the domain
split in `--objective` / `--keywords` / `--message`; the worker policy will
create isolated SearchAgent tasks and a WebAgent campaign internally, then run
the two discovery streams concurrently. 尽量使用镜像源；当 Hugging Face/Kaggle 等主站访问慢或不稳定时，
优先选择可用镜像或缓存源，并在 manifest/report 里记录实际来源。

## Manifest Download

This is the low-level download bridge used by the acquisition worker. Outer
Codex must not call `download manifest` during a normal workflow. Let
`dataset-acquisition-agent` materialize SearchAgent candidates into local
lake-ready files. It is not a lake operation.

Before downloading, compare the manifest against the original user request and
write a pruned manifest, for example `searchagent_manifest.filtered.json`.
Remove clearly unrelated candidates and keep a rejection report such as
`searchagent_manifest.rejections.json` with dataset id, reason, and the mismatch
dimension. Examples of rejection reasons: wrong domain, wrong task type, wrong
language, unrelated source family, missing target label shape, license blocker,
or provider failure risk.

For human debugging only, use `loopai-obtainercli download manifest ...` after
writing a filtered manifest and rejection report.

The downloader enforces a 100,000-row cap and a 2GiB local JSONL output cap per
dataset. `--max-rows 0` is also capped to 100,000 rows per dataset for safety.
When the byte cap is reached, the partial JSONL remains usable and the download
result must report the truncation. Production SFT sizing and final mixing must
be handled later through DataMixer recipes.

## Production SFT Export

For production SFT outflow, outer Codex should use the managed export worker
wrapper instead of manually driving `recipe validate/plan/preview/export`.
The wrapper starts an isolated Codex SDK worker and injects the detailed
DataMixer recipe, schema, validation, snapshot, and failure-handling policy into
that worker's context.

For heterogeneous SFT exports, schema mapping must be dataset/bucket-aware.
Do not use one global `output.sources` fallback order across datasets whose
fields have different semantics. Prefer bucket-level schema blocks such as
`recipe.buckets[].schema.fields` or `recipe.buckets[].export.schema.fields`.
Fields may be composed with templates when the final training row needs several
source fields, for example `output.template: "<think>{chain}</think>{answer}"`
for reasoning + answer, or for text2sql:
`instruction.template: "{question}"` and
`input.template: "{evidence}\n{sql_schema}\n{sql_block}"`.

Start a new isolated worker:

```bash
loopai-obtainercli dm --root /path/to/warehouse sft-export-agent start \
  --run ./outputs/sft_export_run \
  --analysis-report ./outputs/analyzer_report.md \
  --format alpaca \
  --target-records 100000 \
  --out ./outputs/sft_export_run/export
```

`start` returns after launching a background worker by default. Start the export
worker only after the DataFlowAgent post-processing stage has completed and the
final L4 dataset scale meets the recipe target; the lake must hold at least 1.5x
the target volume per bucket and overall before export is allowed. WebAgent and
the acquisition worker may still be active and continue adding data; their
terminal states are not export prerequisites. Use `--foreground` only when the
caller intentionally wants to block until the inner Codex SDK worker finishes.

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
  --message "Exclude buckets whose output field falls back to text, then re-export."
```

`resume` also runs in the background by default and returns a PID plus log
paths. Poll with `status`.

Do not pass `--model` to `sft-export-agent` unless the user explicitly requests
a one-off override. The worker should use Starter's configured Codex model by
default.

Outer Codex decides between `resume` and a fresh `start`:

- Use `resume` when the same worker understood the target but needs a bounded
  correction to recipe mapping, bucket filters, normalization, or validation.
- Use a fresh `start` when the worker context is polluted, picked the wrong
  task, or needs a different high-level strategy.

The worker wrapper owns the detailed constraints. In particular, for Alpaca SFT
it requires final rows to contain exactly `instruction`, `input`, and `output`,
forbids `output` fallback to whole-record text fields, rejects
`instruction == output`, requires DataMixer recipe export with snapshot, and
writes `final_report.json` with manifest, snapshot, digest, planned-versus-actual
bucket mix, validation evidence, and blockers. For datasets where a field like `output` is a noisy trace and
`answer` is the gold label, the worker must define that bucket's schema
explicitly instead of letting a global mapping choose the wrong source.

## End-To-End Agent Workflow

1. Read the Analyzer report or user request and extract the dataset intent.
2. Start `dataset-acquisition-agent`; it concurrently runs SearchAgent for
   hosted-dataset discovery and detached WebAgent for raw webpage L1 collection.
   WebAgent's L1 -> L2 -> L3 queues run continuously while candidate pruning,
   download, normalization, and DataMixer ingest proceed independently.
3. While both producers continue, poll current per-bucket record/token counts
   and quality gates. Treat lake sufficiency, not WebAgent or worker completion,
   as the transition condition for every downstream step.
4. As soon as those gates pass, run the mandatory DataFlowAgent stage
   (`dm dataflow agent-run`) for quality, deduplication, safety, and
   post-training validity; it delivers a trial-verified pipeline, then the
   outer Codex runs it over `full_input.jsonl` with the chunked runner
   (`dataflow_chunked_runner --chunk-size 10000`) and merges the L4 output
   with `apply-jsonl`. L4 must be produced before any export.
5. Build indexes when semantic recall or semantic deduplication is needed.
6. Start `sft-export-agent` for production recipe planning and export only after
   the DataFlowAgent stage completed and the L4 dataset scale meets the recipe
   target with at least 1.5x in-lake redundancy per bucket. Do not wait for
   WebAgent or `dataset-acquisition-agent` to reach a terminal state.
7. Poll `sft-export-agent status`; resume or restart based on blockers.
8. Poll `dataset-acquisition-agent status` independently; resume or restart it
   based on `final_report.json` and blockers without stopping downstream work.
9. Report warehouse path, datasets, record counts, processing results, recipe
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
