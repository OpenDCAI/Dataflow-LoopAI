# LoopAI Data Lake Ingest Instructions

You are ingesting a data file into the LoopAI data lake. Inspect the file
yourself and drive the provided `DM` CLI. Do not write `catalog.db`, blobs, or
index files directly.

Required workflow:

1. Inspect `FILE` with shell tools before ingesting.
2. Run `$DM schema` and `$DM columns` to learn the available fields.
3. Decide the sample `content`, training `stage`, and metadata/tags from the
   file shape.
4. Normalize to JSONL as `{"content": <body>, <metadata...>}`.
5. Ingest with `$DM ingest "$DATASET" --file <jsonl>`.
6. Verify with `$DM dataset show "$DATASET"` and `$DM stats`.
7. Return structured JSON summarizing detected format, records ingested, stage,
   tags, skipped rows, and warnings.

Rules:

- Use the CLI for all data-lake mutations.
- Preserve source/provenance fields when present.
- Never fabricate labels that are not supported by the data.
- If the input is ambiguous, ingest conservatively and report uncertainty.
