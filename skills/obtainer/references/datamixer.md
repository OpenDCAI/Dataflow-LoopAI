# DataMixer Lake Operations

Read this whenever DataMixer lake commands are needed: initialization, schema
inspection, dataset registry, ingest, query, processing operators, indexing,
recall, recipes, snapshots, lineage, and export.

## Table of Contents

- [Command surface](#command-surface)
- [Lake pointer management](#lake-pointer-management)
- [Initialize and inspect](#initialize-and-inspect)
- [Dataset registry and ingest](#dataset-registry-and-ingest)
- [Query, coverage, and distributions](#query-coverage-and-distributions)
- [Processing, quality, safety, and deletion](#processing-quality-safety-and-deletion)
- [Index and recall](#index-and-recall)
- [Lineage and snapshots](#lineage-and-snapshots)

## Command surface

Obtainer has one production data-lake command surface:

```bash
loopai-obtainercli dm --root /path/to/datamixer-warehouse <datamixer-command> --json
loopai-obtainercli dm --lake .datamixer/lake.yaml <datamixer-command> --json
```

Use `--root` when operating directly on a DataMixer warehouse. Use `--lake`
only when a LoopAI lake pointer already exists and should resolve to the
integrated DataMixer warehouse. All `dm` commands emit machine-readable JSON.

## Lake pointer management

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

## Initialize and inspect

```bash
loopai-obtainercli dm --root /path/to/warehouse init --json
loopai-obtainercli dm --root /path/to/warehouse status --json
loopai-obtainercli dm --root /path/to/warehouse schema --json
loopai-obtainercli dm --root /path/to/warehouse columns --json
loopai-obtainercli dm --root /path/to/warehouse stats --json
```

## Dataset registry and ingest

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

## Query, coverage, and distributions

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

## Processing, quality, safety, and deletion

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

## Index and recall

```bash
loopai-obtainercli dm --root /path/to/warehouse index build --json
loopai-obtainercli dm --root /path/to/warehouse recall \
  --query "buggy and fixed Python code pairs for runtime exception repair" \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 50 \
  --json
```

## Lineage and snapshots

```bash
loopai-obtainercli dm --root /path/to/warehouse snapshot create --name sft_mix_v1 --json
loopai-obtainercli dm --root /path/to/warehouse lineage list --json
```
