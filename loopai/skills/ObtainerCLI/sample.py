from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .datamixer_adapter import sample_datamixer_records


def sample_records(
    *,
    lake: str | Path,
    output: str | Path,
    domain: str | None = None,
    processing_level: str | None = None,
    source_kind: str | None = None,
    task_type: str | None = None,
    include_tags: Iterable[str] | None = None,
    exclude_tags: Iterable[str] | None = None,
    n: int = 100,
    allow_smaller: bool = False,
    seed: int = 42,
    strategy: str = "random",
    balance_by: str = "",
) -> dict:
    return sample_datamixer_records(
        lake=lake,
        output=output,
        domain=domain,
        processing_level=processing_level,
        source_kind=source_kind,
        task_type=task_type,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        n=n,
        allow_smaller=allow_smaller,
        seed=seed,
        strategy=strategy,
        balance_by=balance_by,
    )
