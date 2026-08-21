# Dataset Acquisition Worker

Read this when starting, polling, or resuming the `dataset-acquisition-agent`,
or when the worker needs its injected acquisition policy. "Worker" means the
`dataset-acquisition-agent start` CLI command below, not a generic spawned
Codex worker.

## Start a new worker

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .datamixer/lake.yaml dataset-acquisition-agent start \
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

`start` runs the inner Codex SDK worker in the background by default and
returns PID plus log paths. Use `--foreground` only when the caller intends to
block. If `loopai-obtainercli` is not installed as a console script, use the
`${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli ...` form.

## Poll and resume

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .datamixer/lake.yaml dataset-acquisition-agent status \
  --run ./outputs/acquisition_run

${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm --lake .datamixer/lake.yaml dataset-acquisition-agent resume \
  --run ./outputs/acquisition_run \
  --message "Remove unrelated datasets from the filtered manifest, then continue ingest."
```

Do not pass `--model` to `dataset-acquisition-agent` unless the user explicitly
requests a one-off override. By default the wrapper resolves the Codex worker
model from Starter's model pool, preferring the configured Codex default model.

## Injected worker policy

The worker wrapper injects the detailed acquisition policy:

- Explicit objective and keywords; never pass the raw Analyzer report as the
  only search target.
- Concurrent SearchAgent/WebAgent discovery. SearchAgent finds hosted datasets
  for the provider download manifest; WebAgent (`domain_data_acquisition`,
  legacy alias `webcrawler_dm`) collects primary webpages into a distinct
  DataMixer L1 dataset with the L1 -> L2 -> L3 streaming pipeline. Wait only
  for the SearchAgent artifact needed for hosted downloads; keep the WebAgent
  campaign collecting.
- Candidate-list review before download against the original request and
  Analyzer report; write a filtered manifest and an exact rejection report.
- 100,000-row and 2GiB JSONL-output caps per dataset.
- Normalized JSONL, DataMixer-only ingest/status/query/index operations,
  complete provenance tags, and `final_report.json`.
- Prefer mirror sources: when primary hosts such as Hugging Face/Kaggle are
  slow or unstable, prefer an available mirror or cache source and record the
  actual source in the manifest/report.

For multi-domain requests such as text2sql + math + code, describe the domain
split in `--objective` / `--keywords` / `--message`; the worker policy creates
isolated SearchAgent tasks and a WebAgent campaign internally, then runs the
two discovery streams concurrently.

## Manifest download

Low-level download bridge used by the acquisition worker. Outer Codex must not
call `download manifest` during a normal workflow; let the worker materialize
SearchAgent candidates into local lake-ready files.

Before downloading, compare the manifest against the original user request and
write a pruned manifest (e.g. `searchagent_manifest.filtered.json`) plus a
rejection report (e.g. `searchagent_manifest.rejections.json`) with dataset id,
reason, and mismatch dimension. Examples include wrong domain, wrong task type,
wrong language, unrelated source family, missing target label shape, license
blocker, or provider failure risk.

For human debugging only, use `loopai-obtainercli download manifest ...` after
writing a filtered manifest and rejection report.

The downloader enforces a 100,000-row cap and a 2GiB local JSONL output cap per
dataset. `--max-rows 0` is also capped to 100,000 rows per dataset for safety.
When the byte cap is reached, the partial JSONL remains usable and the download
result must report the truncation. Production SFT sizing and final mixing must
be handled later through DataMixer recipes.
