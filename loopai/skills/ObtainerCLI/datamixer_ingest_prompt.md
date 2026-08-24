# LoopAI Data Lake Ingest Instructions

You are ingesting a data file into the LoopAI data lake. Inspect the file
yourself and drive the provided `DM` CLI. Do not write `catalog.db`, blobs, or
index files directly.

Required workflow:

1. Inspect `FILE` with shell tools before ingesting.
2. Run `$DM schema` and `$DM columns` to learn the available fields.
3. Review the file against the quality-level definitions, then use the supplied
   `QUALITY_LEVEL` unchanged: L1 is raw source data, L2 is extracted or
   basically cleaned source data, L3 is standard SFT/DPO/training data, and L4
   is output explicitly refined by an internal data-lake pipeline. Do not infer
   a different value or omit the supplied value.
4. Decide the sample `content`, training `stage`, and metadata/tags from the
   file shape.
5. Write a Markdown dataset card for the dataset before ingest. The card must
   describe source, license, split, row count, original fields, derived fields,
   derivation rules, validation checks, intended training use, and known risks.
6. Normalize to JSONL as `{"content": <body>, <metadata...>}`. Preserve every
   original payload field. You may add derived fields, but must not delete,
   overwrite, rename, or silently collapse original information.
7. Use derived fields for embedded complex formats when useful: parse step
   traces into reasoning fields, flatten multi-turn conversations into explicit
   messages/dialogue/instruction-response fields, and combine question+options,
   evidence, schema, or code blocks into prompt/input fields while keeping gold
   labels/answers separate.
8. Keep the same row count as the selected source rows. If you add derived
   fields, they must be non-empty on every row.
9. Ingest with `$DM ingest "$DATASET" --file <jsonl> --dataset-card <md> --quality-level "$QUALITY_LEVEL"`.
   When derived fields are present, also pass `--derived-field <name>` once per
   derived field and `--source-row-count <n>`.
10. Verify with `$DM dataset show "$DATASET"` and `$DM stats`.
11. Return structured JSON summarizing detected format, records ingested, stage,
    quality_level, tags, dataset card, derived fields, validation results,
    skipped rows, and warnings.

Rules:

- Use the CLI for all data-lake mutations.
- Preserve source/provenance fields when present.
- The final ingest command must use `--quality-level "$QUALITY_LEVEL"` exactly;
  never replace it with a self-selected value.
- Preserve raw/original fields. Dataset-specific normalization is additive:
  adding fields is allowed; removing or overwriting original information is not.
- Never fabricate labels that are not supported by the data.
- If the input is ambiguous, ingest conservatively and report uncertainty.
- Do not mark ingestion successful if the dataset card is missing, if row count
  changed during derivation, or if any declared derived field is empty.
