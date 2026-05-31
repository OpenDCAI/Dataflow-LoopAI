from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Mapping


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


def table_path(lake_root: str | Path, table: str) -> Path:
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")
    return Path(lake_root) / "warehouse" / "loopai.db" / table / "data.jsonl"


def ensure_tables(lake_root: str | Path) -> None:
    root = Path(lake_root)
    for table in TABLES:
        path = table_path(root, table)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    for dirname in ["staging", "quarantine", "reports", "locks"]:
        (root / dirname).mkdir(parents=True, exist_ok=True)


def append_rows(lake_root: str | Path, table: str, rows: Iterable[Mapping]) -> None:
    path = table_path(lake_root, table)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def read_table(lake_root: str | Path, table: str) -> List[dict]:
    path = table_path(lake_root, table)
    if not path.exists():
        return []
    rows: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: Iterable[Mapping]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
