from __future__ import annotations

from pathlib import Path

from .config import resolve_lake_root
from .tables import read_table


def list_tags(*, lake: str | Path) -> dict:
    lake_root = resolve_lake_root(lake)
    tags: dict[str, dict[str, int]] = {}
    for row in read_table(lake_root, "record_tags"):
        name = str(row.get("tag_name", ""))
        value = str(row.get("tag_value", ""))
        if not name or not value:
            continue
        tags.setdefault(name, {})
        tags[name][value] = tags[name].get(value, 0) + 1
    return {
        "ok": True,
        "command": "tag list",
        "status": "success",
        "warnings": [],
        "lake_root": str(lake_root),
        "tags": tags,
    }
