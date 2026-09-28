---
name: obtainer
description: Use this skill when LoopAI needs Hugging Face dataset discovery and acquisition, DataMixer lakehouse operations, JSONL normalization, multi-dataset indexing, data processing, recipe planning, or production training-data export. The main agent directly controls dataset-acquisition-agent and DataFlowAgent (`dm dataflow agent-run`) workers.
---

# Obtainer Skill

所有 Obtainer agent/编排 worker 固定使用 Starter 模型池的 `codex` 角色；DataFlow
LLM 算子使用 `rollout`/`medium` 角色并统一经过 response proxy。若 Judger 已注册
保活的待测 vLLM，rollout 难度筛选优先使用该 entry，否则使用 medium，medium
缺失时才回退默认模型。

## Purpose

Obtainer is the agent-facing workflow for turning a data need into a production
training-data artifact. The lite build uses Hugging Face as its dataset source:
the acquisition worker searches the Hub, normalizes multiple datasets to JSONL,
ingests them into DataMixer, and builds the lake indexes.

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
   download/ingest from the outer Codex context.
3. Poll worker status and decide whether to resume the same worker or start a
   fresh worker.
4. Run the mandatory DataFlowAgent post-processing stage (`dm dataflow
   agent-run`), which materializes the L4 dataset (quality, decontamination,
   deduplication, normalization, safety, and post-training validity).
5. Only after the DataFlowAgent run completes and the final L4 dataset scale
   meets the recipe target, use DataMixer recipe commands for production SFT
   outflow. If the user explicitly specifies an L3 export, the L4 gate is
   waived and L3 data may be exported directly once the lake volume and quality
   gates pass.
6. Report warehouse path, datasets, record counts, recipe/export artifacts,
   lineage, manifests, and snapshots.



## Main-Agent Use: Directly Control the Two Workers

The main agent (starter) is the only Obtainer coordinator. It directly starts
and polls `dataset-acquisition-agent`, then runs and polls DataFlowAgent through
`dm dataflow agent-run`. The main agent owns lake bootstrap,
gating, downstream recipe/export commands, and final artifact reporting.

### 1. Parse the data need into an intent

Extract from the Analyzer report / user request / recipe: an `--objective`
(what sample shape is needed), `--keywords` (search / domain hints),
`--target-datasets` (how many Hugging Face datasets), and a compact `--message`
(failure taxonomy and quality gates).

### 2. Start dataset acquisition

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm \
  dataset-acquisition-agent start \
  --run ./outputs/obtainer_run_<timestamp> \
  --objective "buggy and fixed Python code pairs for syntax repair SFT" \
  --keywords "python syntax error, code repair dataset" \
  --target-datasets 2 \
  --message "Analyzer report: ...; require license=unknown and quality>=0.8" \
  --python-executable /path/to/loopai-env/bin/python
```

`start` launches the acquisition worker in the background and returns its run
directory. Use `--foreground` only when you intend to block.

### 3. Poll acquisition and run DataFlowAgent

A full acquisition plus DataFlow run can be long-running. **You MUST NOT poll
more often than every 5 minutes.**
Between polls run `sleep 300 && ... status ...`; polling faster wastes tokens and
does not speed up the run. `updated_at` / `stale` in the status show whether the
worker is alive:

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm \
  dataset-acquisition-agent status --run ./outputs/obtainer_dataset_run_<timestamp> --json
```

Read the acquisition status contract, and once lake volume and quality gates
pass, invoke `dm dataflow agent-run` directly. Poll the two workers separately
and keep their run directories in the main task state.

- `state`: `idle | running | completed | completed_with_errors | failed | interrupted | stopped`
- `progress`, `message`, `updated_at`, `stale`, `run_dir`, and `final_report`
- `dataflow agent-run`: trial pipeline delivery plus `pipeline_path` and
  `processed_jsonl`; the main agent executes the full chunked run and merges
  it with `apply-jsonl` when L4 output is required.
- `lake`: warehouse, dataset / record counts, quality levels and gate details

Never judge progress from `message` alone; use the structured fields.

### 4. Terminal handling

- `completed`: read `final_report.json` and report warehouse, datasets, counts,
  manifests and lineage.
- `failed` / `stale=true`: inspect the worker report, then resume the same
  dataset-acquisition run or start a fresh bounded run.

### Hard constraints for the main agent

- The main agent may run `dm lake ...`, `dataset-acquisition-agent`, and
  `dataflow agent-run` directly.
- Keep the acquisition worker lifecycle under the CLI (`start` / `resume` /
  `status`) and use its run directory as the source of truth for progress.
- Never claim obtainer completion without the worker's `final_report.json`.

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
  Do not use a generic `spawn_agent` worker for data acquisition. Do not call
  `download manifest`, normalize files, or ingest rows from the outer Codex
  context. Those operations belong inside the CLI worker policy.
- **DataMixer is the only lake command surface.** Use
  `loopai-obtainercli dm ...` for initialization, schema inspection, dataset
  registry, ingest, query, processing operators, indexing, recall, recipes,
  snapshots, lineage, and export.
- **Reuse the active DataMixer warehouse.** Treat `.datamixer/lake.yaml` as a
  project pointer to a reusable DataMixer warehouse. Do not create a new lake per
  task unless the user explicitly asks for a new warehouse. Use `dm lake load`
  to point the project at an existing warehouse and `dm lake delete` to unload
  the pointer; deletion preserves the warehouse unless `--delete-warehouse
  --yes` is explicitly supplied. Prefer `dm lake scan` before choosing a
  warehouse, so the agent sees project and cache candidates instead of guessing
  paths.
- **Use lake context, not repeated boilerplate.** After a lake is loaded or
  initialized, use `dm --lake .datamixer/lake.yaml ...` for agents. The pointer
  persists the warehouse, model name, and current acquisition run. Do not pass a
  FastAPI/Configer SQLite file as `--root`; `--root` must be a DataMixer
  warehouse containing `datamixer.toml`.
- **Load or init the lake before any worker.** `dataset-acquisition-agent`
  refuses to start (`LAKE_NOT_LOADED`) unless the resolved
  warehouse already contains `datamixer.toml`. When a previous task ended, clear
  its stale bindings first with `dm lake unbind` so the pointer never confuses
  the new run with an old task_id; then start the worker with
  `dm --lake .datamixer/lake.yaml ...`.
- **Prepare worker intent before acquiring from a report.** Pass a concrete
  sample shape and search terms through `--objective`, `--keywords`,
  `--target-datasets`, and `--message`. The worker searches Hugging Face
  metadata, records candidate evidence, and prefers datasets created or updated
  in 2025-2026.
- **Hugging Face acquisition path:** the lite worker selects several Hub dataset
  ids, downloads each selected split, preserves source fields, writes one
  normalized JSONL per dataset, ingests each file into DataMixer, and builds the
  shared index after ingestion.
- **Lake readiness is the downstream gate:** after acquisition reports its
  normalized files, ingested datasets, and index result, the main agent can
  start the DataFlowAgent post-processing stage and any recipe work.
- **DataFlowAgent is a mandatory pre-export gate by default.** Every
  production export must first complete the DataFlowAgent post-processing stage
  (`dm dataflow agent-run`), which delivers a trial-verified L4 pipeline; the
  outer Codex then executes it over the selected input with the chunked runner
  to produce the L4 dataset. L4 is the DataFlow-processed level on top of the
  normalized dataset records and is the default sample source for production
  export. Skipping, deferring, or folding this stage into the export worker is
  not allowed; an export without a completed L4 source is a blocker. If the
  user explicitly specifies an L3 export, the L4 gate is waived and L3 data
  may be exported directly instead.
- **L4 scale gates export by default.** The DataFlowAgent stage is considered
  complete only when the final L4 dataset scale meets the recipe target
  overall. Only then use DataMixer recipe export. If the user explicitly
  specifies an L3 export, this L4
  scale gate is waived and L3 data may be exported once the lake
  volume and quality gates pass.
- **Candidate and download reports:** write `candidates.json`,
  `filtered_manifest.json`, and `rejections.json` before downloading. Keep the
  download result and each normalized JSONL path in the run directory so ingest
  and index results can be traced to their Hugging Face source.
- **Acquisition download cap.** Internal `download manifest` writes at most
  100,000 rows and 2GiB of local JSONL output per dataset, even if `--max-rows
  0`, a larger row value, or an oversized `--max-bytes-per-dataset` value is
  supplied. If the byte cap is reached, keep the partial JSONL and report
  `truncated`, `truncated_reason`, `rows_written`, and `bytes_written`. Treat
  this as the bounded acquisition bridge into DataMixer, not as final
  production SFT output.

## Command Surface

Obtainer has one production data-lake command surface:

```bash
loopai-obtainercli dm --root /path/to/datamixer-warehouse <datamixer-command> --json
loopai-obtainercli dm --lake .datamixer/lake.yaml <datamixer-command> --json
```

Use `--root` when operating directly on a DataMixer warehouse. Use `--lake` only
when a LoopAI lake pointer already exists and should resolve to the integrated
DataMixer warehouse. All `dm` commands emit machine-readable JSON.

Manage the project pointer to a reusable DataMixer warehouse:

```bash
loopai-obtainercli dm lake scan --link .datamixer/lake.yaml --project-root .
loopai-obtainercli dm lake current --link .datamixer/lake.yaml
loopai-obtainercli dm lake load --warehouse /path/to/warehouse --link .datamixer/lake.yaml
loopai-obtainercli dm lake delete --link .datamixer/lake.yaml
loopai-obtainercli dm lake context --link .datamixer/lake.yaml
loopai-obtainercli dm lake unbind --link .datamixer/lake.yaml
```

`dm lake delete` unloads only the pointer by default. Use
`--delete-warehouse --yes` only when the actual reusable warehouse should be
removed.

Hugging Face manifest download is an internal acquisition bridge. In the normal
product workflow, outer Codex reaches it only by starting
`dataset-acquisition-agent`. Do not call low-level `download manifest` from the
outer Codex context.

## Dataset Acquisition Worker

For dataset discovery, candidate pruning, download, normalization, and DataMixer
ingest, outer Codex must use the managed acquisition
worker CLI wrapper. Here
"worker" means the `dataset-acquisition-agent start` command below, not a
generic spawned Codex worker.

Start a new worker:

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .datamixer/lake.yaml dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --analysis-report ./outputs/analyzer_report.md \
  --objective "collect general-domain instruction and QA datasets" \
  --keywords "instruction tuning dataset, open QA dataset, summarization dataset" \
  --target-datasets 30 \
  --max-rows-per-dataset 100000 \
  --max-bytes-per-dataset 2147483648 \
  --python-executable /path/to/loopai-env/bin/python
```

`start` runs the inner Codex SDK worker in the background by default and returns
PID plus log paths. Use `--foreground` only when the caller intentionally wants
to block. If `loopai-obtainercli` is not installed as a console script, use the
`${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli ...` form.

Poll status:

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .datamixer/lake.yaml dataset-acquisition-agent status \
  --run ./outputs/acquisition_run
```

Resume the same worker:

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .datamixer/lake.yaml dataset-acquisition-agent resume \
  --run ./outputs/acquisition_run \
  --message "Remove unrelated datasets from the filtered manifest, then continue ingest."
```

Do not pass `--model` to `dataset-acquisition-agent` unless the user explicitly
requests a one-off override. By default the wrapper resolves the Codex worker
model from Starter's model pool, preferring the configured Codex default model.

The worker wrapper injects the detailed acquisition policy: explicit objective
and keywords, candidate list review against the original request before download,
rejection report, 100,000-row and
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

When the user query or Analyzer report explicitly names a benchmark/eval
dataset type to collect, register it in the benchmark registration layer
first with `contam add` before any acquisition or ingest; the subsequent
`decontaminate` pass then excludes those rows from downstream training export
and prevents benchmark leakage.

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

## Bad-Case Multi-Route Recall (Pre-DataFlowAgent Candidate Outflow)

进入 DataFlowAgent 处理的**不是整湖数据**，而是先按 bad case 的题目做**多路
召回**得到的候选集。这一步位于「采集入湖」与「DataFlowAgent 后处理」之间，
目的是把湖里与本轮 bad case 相关的样本先召回、出湖成候选数据集，再交给
DataFlowAgent。

召回查询来源:默认读取 Analyzer **最新一轮**产物里的 bad case 题目 JSONL
(`badcase_questions_<ts>.jsonl`，`schema_version=analyzer_badcase_question_v1`)。
每行的 `question` 字段就是召回 query,`domain` / `capability_bucket` /
`overall_error_tag` 可用于路由或 `--filter` 限定。该产物由 Analyzer 的
`analyze_metric_report_node` 生成,其 `recall.purpose` 已标注为
`domain_dataset_search_and_multi_route_recall`。

这一整步由单个子命令 `dm recall-badcases` 完成:它读取 bad case 题目 JSONL,
逐条 `question` 做多路召回,把命中按 `sample_id` 并集去重,再直接出湖成
DataFlowAgent 的候选输入 JSONL。`--from` 省略时自动定位 Analyzer 最新一轮的
`badcase_questions_*.jsonl`(按 `--output-dir`/`--task-id` 下的 analyzer 目录
取 mtime 最新的一份)。召回数量由 `--limit` 控制,**默认 6000**(每条 bad case
query 的 top-k;多路 = 逐题各召回 top-k 再并集去重)。

```bash
# 0) 先建索引(向量 + 全文),多路召回依赖它
loopai-obtainercli dm --root /path/to/warehouse index build --json

# 1) 一步完成:读 bad case 题目 -> 多路召回 -> 并集去重 -> 出湖候选集
#    --from 省略时自动取 Analyzer 最新一轮的 badcase_questions_*.jsonl
loopai-obtainercli dm --root /path/to/warehouse recall-badcases \
  --out ./outputs/obtainer/recall_candidates.jsonl \
  --limit 6000 \
  --json

# 也可显式指定题目文件、domain 路由、相似度阈值,或走关键词召回:
loopai-obtainercli dm --root /path/to/warehouse recall-badcases \
  --from ./outputs/<task>/analyzer/.../badcase_questions_<ts>.jsonl \
  --filter "domain = 'math' AND task_type = 'SFT'" \
  --min-sim 0.3 \
  --out ./outputs/obtainer/recall_candidates.jsonl \
  --json
```

`recall-badcases` 参数:

- `--from`:bad case 题目 JSONL;省略时自动定位 Analyzer 最新一轮产物。
- `--out`:候选集出湖路径(必填),即 DataFlowAgent 的 `full_input.jsonl`。
- `--limit`:每条 query 的召回 top-k,默认 6000;多路命中并集去重后即候选集。
- `--total-limit`: 可选，按分数排序后限制多路去重并集的总量；不改变每路 `--limit`。
- `--filter`:限定 domain/task_type 等标量做路由(白名单语法)。
- `--min-sim`:语义召回的余弦相似度下限。
- `--keyword`:走关键词(FTS5)召回,默认走语义(向量)召回。
- `--question-field`:题目文本字段名,默认 `question`。
- `--field`:出湖文本列名,默认 `raw_content`(DataFlow 读作 `input_key`)。

召回规则:

- **默认查询源是 Analyzer 最新一轮的 bad case 题目产物**。命令自动定位最新
  一轮 `badcase_questions_*.jsonl`,逐行取 `question` 作为召回 query;找不到时
  报错并提示显式传 `--from`。
- **召回数量 agent 可控**。`--limit` 是每条 query 的 top-k(默认 6000);多路
  召回把每条 bad case 的命中并集去重后作为候选集。规模不足时放大 `--limit`
  或放宽 `--min-sim` / `--filter` 再召回。
- **召回产物 = DataFlowAgent 的输入**。输出的候选 JSONL 直接作为
  `dataflow agent-run` 与 chunked 全量的 `full_input.jsonl`,而不是整湖。每行
  带 `recall_score` 和 `recall_question_id` 便于追溯。
- **召回而非重采**:不覆盖原始字段;召回只筛选进入后处理的样本子集。

后处理阶段是必须要使用 dataflowagent 的，不要手工盲选单个 DataFlow operator。
`dataflow agent-run` 会让 Codex SDK 先导出试跑样本、按 DataFlow-Skills 规则
规划算子链、生成并试跑 pipeline；**试跑成功即交付**（`mode=trial_run`，
交付物 = `pipeline.py` + 试跑输出 `trial_processed.jsonl`）。**全量执行由
上层 Codex 负责**：拿到交付的 pipeline 后，用 chunk 脚手架跑
`full_input.jsonl`，产出 `full_processed.jsonl`
（L4），再按 `sample_id` 用 `apply-jsonl` merge 回 DataMixer。不要让
dataflowagent 自己跑全量或 merge。

**质量评估必须使用 DataFlow 的 LLM 评估算子**（如 `PromptedEvaluator` /
`PromptedFilter` 这类 LLM 打分/过滤算子），不得因耗时或成本而退化成纯启发式
规则打分；只有任务本身没有 LLM 打分语义、或 LLM serving 不可用时才允许规则
算子兜底并说明具体原因。不得覆盖原始字段和值；后训练内容需要构造或改写时，
使用生成算子写入新的派生字段，再使用 LLM 评估算子打分和筛选生成内容。

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

- **Review rejection requires continuation.** A valid `planned_only` response
  with a failed pipeline review is a repair checkpoint, not task completion.
  The CLI resumes the same thread within its three-turn attempt budget and
  preserves each failed review under `review_checkpoints/`. If it returns
  `continuation_required=true`, inspect the remaining findings and resume with
  `--resume-thread-id <thread_id>` and a new work directory. Continue feasible
  repairs within the authorized task; report a concrete external blocker only
  after mitigation attempts. `mode=trial_run` requires nonempty output plus all
  six review dimensions, correct score arithmetic, at least 85 points and no
  redlines. Full-run/apply commands are provided only for a released trial.
- **Trial -> deliver -> upstream full is the contract.** The agent must
  trial-run the pipeline and deliver it (`mode=trial_run`, `pipeline_path` +
  `processed_jsonl`); it must NOT launch the full processing or write
  `full_processed.jsonl` itself. The upper-layer Codex runs the delivered
  pipeline over the exported full input and only treats L4 as complete when
  `full_processed.jsonl` exists and is verified.
- **LLM quality-evaluation operators are mandatory.** Use DataFlow LLM
  scoring/filter operators (`PromptedEvaluator`, `PromptedFilter`, ...) for
  quality scoring. Cost/latency is NOT a valid reason to fall back to pure
  heuristic rules - a slow LLM pass just takes longer. Rule operators are
  allowed only when the task has no LLM-scoring semantics or the LLM serving is
  unavailable; say so in the summary. Preserve original fields and values.
  When post-training content needs construction or rewriting, use generation
  operators to add derived fields, then score and filter the generated content.
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
  whose rules require it to deliver the trial-verified pipeline and never
  launch the full run itself.

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

## Hugging Face Search Guide

The acquisition worker uses the Hugging Face Hub catalog to build its candidate
set. Search by the intent keywords, sort by `lastModified` descending, inspect
dataset metadata and cards, and keep the freshness evidence for each selected
dataset. Prefer datasets created or updated in 2025-2026.

```bash
loopai-obtainercli dm --root /path/to/warehouse dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --objective "collect buggy and fixed Python code-pair datasets covering syntax, logic, runtime, and assertion failures for SFT training" \
  --keywords "program repair dataset, buggy fixed code pairs, Python SyntaxError fix, runtime exception repair" \
  --target-datasets 8 \
  --max-rows-per-dataset 100000 \
  --max-bytes-per-dataset 2147483648 \
  --json
```

For multi-domain requests such as text2sql + math + code, include each domain in
the objective and keywords so the selected HF datasets remain separated in the
candidate and ingest reports. Each selected dataset is downloaded to its own
normalized JSONL and then registered separately in DataMixer.

The downloader enforces a 100,000-row cap and a 2GiB local JSONL output cap per
dataset. `--max-rows 0` is also capped to 100,000 rows per dataset for safety.
When the byte cap is reached, the partial JSONL remains usable and the download
result must report the truncation. Production SFT sizing must be handled later
through DataMixer recipes.

## Production SFT Export

For production SFT outflow, the main agent directly drives DataMixer's
`recipe validate/plan/preview/export` commands after the DataFlowAgent gate.
There is no intermediate export worker in the lite workflow.

For heterogeneous SFT exports, schema mapping must be dataset-aware. Do not use
one global `output.sources` fallback order across datasets whose
fields have different semantics. Prefer bucket-level schema blocks such as
`recipe.buckets[].schema.fields` or `recipe.buckets[].export.schema.fields`.
Fields may be composed with templates when the final training row needs several
source fields, for example `output.template: "<think>{chain}</think>{answer}"`
for reasoning + answer, or for text2sql:
`instruction.template: "{question}"` and
`input.template: "{evidence}\n{sql_schema}\n{sql_block}"`.

Validate, preview, and export the recipe directly:

```bash
loopai-obtainercli dm --root /path/to/warehouse recipe validate ./recipe.yaml --json
loopai-obtainercli dm --root /path/to/warehouse recipe plan ./recipe.yaml --json
loopai-obtainercli dm --root /path/to/warehouse recipe preview ./recipe.yaml --per-bucket 3 --json
loopai-obtainercli dm --root /path/to/warehouse recipe export ./recipe.yaml \
  --out ./outputs/obtainer/export --snapshot --json
```

Run export only after the DataFlowAgent post-processing stage has completed and
the final L4 dataset scale meets the recipe target.

The recipe contract still applies. In particular, for Alpaca SFT
it requires final rows to contain exactly `instruction`, `input`, and `output`,
forbids `output` fallback to whole-record text fields, rejects
`instruction == output`, requires DataMixer recipe export with snapshot, and
writes `final_report.json` with manifest, snapshot, digest, validation evidence,
and blockers. For datasets where a field like `output` is a noisy trace and
`answer` is the gold label, define that dataset's schema explicitly instead of
letting a global mapping choose the wrong source.

## End-To-End Agent Workflow

1. Read the Analyzer report or user request and extract the dataset intent.
2. Start `dataset-acquisition-agent`; it handles candidate discovery, pruning,
   download, normalization, and DataMixer ingest in one bounded run.
3. Poll current dataset record counts and quality gates before moving
   to downstream processing.
4. **Bad-case multi-route recall (candidate outflow before DataFlowAgent).**
   Do not feed the whole acquired lake into DataFlowAgent. First run a
   bad-case-driven multi-route recall with `dm recall-badcases`: build the
   index, then the command reads the latest Analyzer round's
   `badcase_questions_*.jsonl` (auto-located, or `--from`), fans out one recall
   per bad-case question over the freshly-embedded lake, unions the hits, and
   writes the candidate set that becomes the DataFlowAgent input (see "Bad-Case
   Multi-Route Recall" below). The recall breadth (top-k per query) is
   agent-controllable via `--limit` (default 6000). Use the emitted candidate
   JSONL as the DataFlowAgent input.
5. Run the mandatory DataFlowAgent stage
   (`dm dataflow agent-run`) on the recalled candidate set for quality,
   deduplication, safety, and post-training validity; it delivers a
   trial-verified pipeline, then the outer Codex runs it over
   `full_input.jsonl` with the chunked runner
   (`dataflow_chunked_runner --chunk-size 10000`) and merges the L4 output
   with `apply-jsonl`. L4 must be produced before any export (unless the user
   explicitly requests an L3 export).
6. Build additional indexes when further semantic recall or semantic
   deduplication is needed.
7. Use DataMixer recipe planning/export commands directly after the DataFlowAgent
   stage completed and the L4 dataset scale meets the recipe target.
8. Poll `dataset-acquisition-agent status` independently; resume or restart it
   based on `final_report.json` and blockers without stopping downstream work.
9. Report warehouse path, datasets, record counts, processing results, recipe
    fingerprint, snapshot id, export path, and manifest path.

## Failure Handling

- Missing warehouse: run DataMixer `init` at the intended `--root`.
- Missing or unreliable semantic tags: report the quality limitation and process
  more data before export.
- Insufficient data volume: report the available and target counts from
  `recipe plan`.
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
