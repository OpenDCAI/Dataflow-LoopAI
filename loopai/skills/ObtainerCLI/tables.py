from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Mapping

from .catalog import TABLES
from .config import resolve_lake_root


AUDIT_TABLES = {"assets", "ingest_runs", "embeddings", "exports", "record_lineage"}


def _validate_table(table: str) -> None:
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")


def table_path(lake_root: str | Path, table: str) -> Path:
    _validate_table(table)
    root = resolve_lake_root(lake_root)
    if table in AUDIT_TABLES:
        return root / "warehouse" / "obtainercli_audit" / f"{table}.jsonl"
    return root / "warehouse" / "datamixer_views" / table


def ensure_tables(lake_root: str | Path) -> None:
    from .datamixer_adapter import open_store

    root = resolve_lake_root(lake_root)
    store = open_store(root)
    store.close()
    for table in AUDIT_TABLES:
        path = table_path(root, table)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


def append_rows(lake_root: str | Path, table: str, rows: Iterable[Mapping]) -> None:
    _validate_table(table)
    if table not in AUDIT_TABLES:
        raise ValueError(f"Table {table} is managed by DataMixer and cannot be appended directly")
    from .datamixer_adapter import append_audit_rows

    append_audit_rows(lake_root, table, rows)


def read_table(lake_root: str | Path, table: str) -> List[dict]:
    _validate_table(table)
    from .datamixer_adapter import read_virtual_table

    return read_virtual_table(lake_root, table)


def write_jsonl(path: str | Path, rows: Iterable[Mapping]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
