from __future__ import annotations

from pathlib import Path


TABLES = [
    "datasets",
    "assets",
    "records",
    "record_tags",
    "record_lineage",
    "embeddings",
    "quality_findings",
    "ingest_runs",
    "exports",
]


def get_catalog(lake_root: str | Path) -> None:
    raise RuntimeError(
        "ObtainerCLI data-lake operations are backed by DataMixer only; "
        "local-jsonl/local-parquet catalogs are not available."
    )
