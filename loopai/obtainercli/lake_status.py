from __future__ import annotations

from pathlib import Path

from .config import resolve_lake_root
from .tables import TABLES, read_table, table_path


def lake_status(*, lake: str | Path) -> dict:
    lake_root = resolve_lake_root(lake)
    counts = {}
    paths = {}
    for table in TABLES:
        path = table_path(lake_root, table)
        paths[table] = str(path)
        counts[table] = len(read_table(lake_root, table))
    return {
        "ok": True,
        "command": "lake status",
        "status": "success",
        "warnings": [],
        "lake_root": str(lake_root),
        "tables": counts,
        "paths": paths,
    }
