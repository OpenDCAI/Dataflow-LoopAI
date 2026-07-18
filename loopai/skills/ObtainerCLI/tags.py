from __future__ import annotations

from pathlib import Path

from .datamixer_adapter import list_datamixer_tags


def list_tags(*, lake: str | Path) -> dict:
    return list_datamixer_tags(lake=lake)
