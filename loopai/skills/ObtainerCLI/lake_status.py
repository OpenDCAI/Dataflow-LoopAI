from __future__ import annotations

from pathlib import Path

from .datamixer_adapter import lake_datamixer_status


def lake_status(*, lake: str | Path) -> dict:
    return lake_datamixer_status(lake=lake)
