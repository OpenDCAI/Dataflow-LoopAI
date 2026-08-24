# ObtainerCLI DataMixer-Only Verification Plan

日期：2026-06-30

## Goal

ObtainerCLI exposes DataMixer as the single production lakehouse command
surface. SearchAgent and `download manifest` remain acquisition bridges; all
lakehouse work uses:

```bash
loopai-obtainercli dm --root /path/to/warehouse <command> --json
loopai-obtainercli dm --lake .loopai/lake.yaml <command> --json
```

## Verification

- `dm init` creates a DataMixer warehouse with `datamixer.toml`.
- `dm ingest` writes records and metadata tags into the DataMixer catalog.
- `dm query` can filter core columns and semantic tags.
- `dm index build` and `dm recall` operate from the DataMixer catalog/CAS.
- `dm recipe plan` applies production budgets and reports shortages.
- `dm recipe export --snapshot` writes data, manifest, snapshot id, recipe
  fingerprint, and dataset digest.
- SFT recipes without explicit scale plan for at least 100000 records.
- failure-taxonomy buckets such as syntax/logic/runtime/assertion require
  semantic tags such as `bug_type`, not broad proxy filters.

Focused command:

```bash
pytest -q tests/test_obtainercli_lake.py tests/test_obtainer_monitor.py
```
