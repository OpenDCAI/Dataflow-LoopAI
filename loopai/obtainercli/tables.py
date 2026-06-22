from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Mapping

from .catalog import TABLES, get_catalog


def table_path(lake_root: str | Path, table: str) -> Path:
    return get_catalog(lake_root).table_path(table)


def ensure_tables(lake_root: str | Path) -> None:
    get_catalog(lake_root).ensure_tables()


def append_rows(lake_root: str | Path, table: str, rows: Iterable[Mapping]) -> None:
    get_catalog(lake_root).append_rows(table, rows)


def read_table(lake_root: str | Path, table: str) -> List[dict]:
    return get_catalog(lake_root).read_table(table)


def write_jsonl(path: str | Path, rows: Iterable[Mapping]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
