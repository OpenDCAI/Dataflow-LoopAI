---
name: obtainer
description: Use this skill when the user wants LoopAI to inspect or initialize a data lake, find and collect datasets for a data need, ingest downloaded or local data into the lake, index embeddings, tag data, or sample/export a mixed dataset for training or evaluation.
---

# Obtainer Skill

## Purpose

ObtainerCLI Skill is the agent-facing workflow for LoopAI data acquisition and lakehouse operations. Use it to turn a user's data requirement into a traceable data-lake workflow:

1. Check the current data lake status.
2. Initialize a lake if no usable lake exists.
3. Search for relevant datasets or confirm local inputs.
4. Download or collect the needed data.
5. Normalize the data to supported JSONL records.
6. Ingest the data into the lake with required metadata and tags.
7. Index embeddings when needed.
8. Sample/export a dataset mix according to the user's target proportions.

Do not treat this skill as only a thin CLI wrapper. The agent must manage the whole data flow and keep each source, tag, and export decision explicit.

## Python Implementation

```text
loopai/skills/ObtainerCLI/
├── __main__.py
├── cli.py
├── config.py
├── catalog.py
├── ingest.py
├── index.py
├── lake_init.py
├── lake_status.py
├── sample.py
├── tables.py
└── tags.py
```

The root skill description lives at:

```text
skills/obtainer/SKILL.md
```

## CLI

Main command:

```bash
loopai-obtainercli --help
python -m loopai.skills.ObtainerCLI --help
```

Supported commands:

```text
loopai-obtainercli lake init
loopai-obtainercli lake status
loopai-obtainercli searchagent
loopai-obtainercli download manifest
loopai-obtainercli ingest path
loopai-obtainercli index embed
loopai-obtainercli tag list
loopai-obtainercli sample
```

All CLI commands emit JSON. Prefer parsing the JSON result instead of scraping text.

### SearchAgent Dataset Collection

`searchagent` is a standalone CLI wrapper around the existing Obtainer dataset search logic.
It does not call the old LangGraph `download_node`; instead it reuses the same Obtainer utility components that `download_node` used for planning and candidate selection:

- deepsearch first: generate research queries, search the web, read selected pages, and summarize dataset-search clues;
- LLM download-method decision for provider search;
- Hugging Face / Kaggle candidate search managers;
- web results are research context only, not final download candidates;
- writes a `searchagent_manifest.json` containing an unranked candidate download list.

It does not select or rank entries. The manifest is consumed in order by `download manifest`, then the exported JSONL can be passed to `ingest path`.

```bash
loopai-obtainercli searchagent \
  --query-file ./outputs/analyzer_report.md \
  --output-root ./outputs \
  --max-deep-queries 3 \
  --max-deep-pages 3 \
  --json
```

By default, SearchAgent reads LLM parameters from `starter.yaml`:

- `system.starter_model_path` / `system.starter_model_name`
- `system.starter_base_url`
- `system.starter_api_key`

CLI flags override the starter values when explicitly supplied.

The command writes:

```text
<output-root>/searchagent_manifest.json
unranked `download_list` entries with `download.method` and provider-specific IDs / URLs
task entries with `deepsearch.summary`, `deepsearch.urls`, and `enriched_search_keywords`
```

### Manifest Download

`download manifest` is the minimal bridge from SearchAgent candidates to lake-ready files.
It reads `download_list` in order and exports provider records to JSONL. The first version supports Hugging Face datasets through `datasets.load_dataset`; Kaggle entries are preserved in the result as skipped unless a fuller downloader is added.

```bash
loopai-obtainercli download manifest \
  --manifest ./outputs/searchagent_manifest.json \
  --output-root ./outputs/downloads \
  --limit 1 \
  --split train \
  --max-rows 200 \
  --json
```

The command writes:

```text
<output-root>/download_results.json
<output-root>/records/<dataset>.<split>.jsonl
```

## Stream Events

ObtainerCLI can persist `StreamEvent` entries through `loopai.common.event_tool`.

Enable events by passing a task id, or by setting `TASK_ID`:

```bash
loopai-obtainercli lake status \
  --lake .loopai/lake.yaml \
  --task-id data_task_001 \
  --output-dir ./outputs \
  --json
```

Events are written to:

```text
<output_dir>/<task_id>/obtainercli.pkl
```

Read them from Python:

```python
from loopai.skills.ObtainerCLI import load_events

events = load_events(task_id="data_task_001", output_dir="./outputs")
```

Each event uses:

- `current="obtainercli"`
- `node` as the command path, such as `lake.status`, `lake.init`, `ingest.path`, `index.embed`, or `sample`
- `status` as `started`, `running`, `completed`, or `failed`
- `progress` from `0.0` to `1.0` where the command can report phases

Use `--no-events` to suppress event persistence even when `TASK_ID` is set.

## End-To-End Agent Workflow

### 1. Understand The Data Need

Before searching or ingesting, extract the user's target:

- Domain, such as code, math, general, finance, medical, legal, multilingual, web, synthetic.
- Task type, such as `PT`, `SFT`, `RL`, or `EVAL`.
- Processing level, such as `raw_web`, `extracted_text`, `pretrain_ready`, `postprocessed_high_quality`, or `synthetic_validated`.
- Source kind, such as `web`, `local`, `api`, `huggingface`, `kaggle`, or `synthetic`.
- Size target and mix proportions, such as 70% code + 20% math + 10% general.
- Quality constraints, licenses, language, freshness, contamination concerns, and required output format.

If a requested dataset may have legal, privacy, license, or safety implications, verify source terms before ingesting and preserve license/source tags.

### 2. Check Lake Status First

Always check whether a usable lake already exists before creating a new one:

```bash
loopai-obtainercli lake status --lake .loopai/lake.yaml --json
```

Use the status result to decide:

- If the lake exists and is healthy, reuse it.
- If `.loopai/lake.yaml` is missing or points to a broken root, initialize or ask for the desired root.
- If records already satisfy the user need, sample/export directly instead of downloading duplicate data.
- If embeddings are missing but semantic sampling/search is needed, run indexing first.

### 3. Initialize The Lake When Needed

Create the lake outside the repo when possible and keep only `.loopai/lake.yaml` in the repo:

```bash
loopai-obtainercli lake init \
  --root /path/to/lake-root \
  --link .loopai/lake.yaml \
  --if-not-exists \
  --auto-embed \
  --embedding-provider openai-compatible \
  --embedding-base-url http://127.0.0.1:8000/v1 \
  --embedding-model BAAI/bge-small-zh-v1.5
```

If no embedding service is available, either initialize with `--no-auto-embed` or use the local hash provider later for lightweight indexing.

### 4. Search And Select Data Sources

ObtainerCLI currently ingests local files; it does not yet provide dedicated `ingest hf`, `ingest kaggle`, or `ingest web` commands. The agent is responsible for source discovery and download before ingestion.

Recommended source-selection process:

- Search datasets that match the user need, license, domain, task type, language, and recency requirements.
- Prefer primary dataset pages and official mirrors over reposts.
- Record source URL, dataset name, version or snapshot date, license, and filtering assumptions.
- Avoid downloading data that is clearly unrelated, duplicated, private, or license-incompatible.
- For web collection, use the WebCrawler skill or an existing crawler pipeline, then treat the collected output as local input for ingestion.

Downloaded or generated data must be converted to JSONL before ingestion. Each line should be a JSON object.

Minimum input shape:

```jsonl
{"text":"example text","source_uri":"file:///path/or/source/url"}
```

Useful fields:

- `text`
- `instruction`
- `input`
- `output`
- `messages`
- `source_uri`
- `source_domain`
- `split`
- `quality_score`
- `quality_findings`
- `parent_record_ids`

### 5. Ingest Data With Required Metadata

Use `ingest path` for every normalized JSONL file:

```bash
loopai-obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input /path/to/data.jsonl \
  --dataset dataset_name_or_batch_id \
  --stage bronze \
  --domain code \
  --task-type PT \
  --processing-level raw_web \
  --source-kind local \
  --tags source=huggingface,license=apache-2.0,lang=python \
  --idempotency-key dataset_name_snapshot_20260622 \
  --json
```

Metadata rules:

- `dataset` should be stable and descriptive.
- `idempotency-key` should include dataset name and version/snapshot where possible.
- `domain`, `task-type`, `processing-level`, and `source-kind` must reflect the user's need and the actual source.
- `tags` should preserve license, language, quality level, source platform, benchmark name, or any filtering decision needed for later sampling.
- Do not ingest unreviewed raw downloads as `gold` or `postprocessed_high_quality`.

If auto-embedding is enabled, ingestion may run embedding indexing after records are written. If it fails, inspect the warning and decide whether to retry indexing manually.

### 6. Index Embeddings When Needed

Run embedding indexing when semantic retrieval, coverage checks, or downstream sampling needs embeddings:

```bash
loopai-obtainercli index embed \
  --lake .loopai/lake.yaml \
  --dataset dataset_name_or_batch_id \
  --provider openai-compatible \
  --base-url http://127.0.0.1:8000/v1 \
  --model BAAI/bge-small-zh-v1.5 \
  --backend local-jsonl \
  --text-field text \
  --json
```

For lightweight local testing:

```bash
loopai-obtainercli index embed \
  --lake .loopai/lake.yaml \
  --dataset dataset_name_or_batch_id \
  --provider local-hash \
  --model local-hash-v1 \
  --json
```

### 7. Inspect Tags And Coverage

Before exporting, inspect available tags and lake status:

```bash
loopai-obtainercli tag list --lake .loopai/lake.yaml --json
loopai-obtainercli lake status --lake .loopai/lake.yaml --json
```

Use this to confirm that requested domains, licenses, quality levels, and processing levels exist in enough volume.

### 8. Sample And Export According To The Required Mix

Use `sample` to export a deterministic JSONL file:

```bash
loopai-obtainercli sample \
  --lake .loopai/lake.yaml \
  --output outputs/datasets/code_seed_sample.jsonl \
  --domain code \
  --processing-level pretrain_ready \
  --task-type PT \
  --include-tag lang=python \
  --exclude-tag license=unknown \
  --n 1000 \
  --seed 42 \
  --strategy random \
  --json
```

For mixed datasets, run one sample command per slice and then merge the resulting JSONL files in the requested proportions. Keep the slice definitions explicit in the final answer:

```text
70% code: domain=code, task_type=PT, processing_level=pretrain_ready, n=7000
20% math: domain=math, task_type=SFT, processing_level=postprocessed_high_quality, n=2000
10% general: domain=general, task_type=PT, processing_level=pretrain_ready, n=1000
```

Use a fixed seed for reproducibility. If a slice has insufficient records, report the shortage and either use `--allow-smaller` or adjust the mix only with user approval.

## Required Agent Behavior

- Start from lake status unless the user explicitly asks only for help text or documentation.
- Initialize only when there is no usable lake or the user requests a new lake.
- Search/download only after clarifying the target data requirement enough to avoid irrelevant data.
- Preserve provenance through `source_uri`, dataset names, idempotency keys, and tags.
- Prefer deterministic commands and fixed seeds for repeatable exports.
- Do not silently change requested mix proportions; report shortages.
- Do not overwrite existing export files unless the user requested that path.
- Summarize final outputs with lake path, ingested datasets, record counts, tags, index status, and export paths.

## Common Failure Handling

- Missing lake pointer: run `lake init` or ask for the target root.
- Broken lake root: report the bad path and initialize a new root only with clear intent.
- Empty ingest result: verify input JSONL, filters, and idempotency key.
- Embedding failure: check provider, base URL, API key, model, and service health; ingestion may still have succeeded.
- Not enough records for a mix slice: report actual availability and propose a smaller export or relaxed filters.
- Unknown license or source: tag it as unknown and avoid using it for restricted training exports unless approved.

## References

Detailed CLI usage:

```text
docs/OBTAINERCLI_USAGE.md
```
