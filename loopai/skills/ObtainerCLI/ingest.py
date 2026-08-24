from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .datamixer_adapter import ingest_datamixer_path


def ingest_path(
    *,
    lake: str | Path,
    input_path: str | Path,
    dataset: str,
    quality_level: str,
    stage: str = "bronze",
    domain: str = "general",
    task_type: str = "PT",
    processing_level: str = "raw_web",
    source_kind: str = "local",
    tags: Iterable[str] | None = None,
    idempotency_key: str | None = None,
    on_duplicate: str = "skip",
) -> dict:
    return ingest_datamixer_path(
        lake=lake,
        input_path=input_path,
        dataset=dataset,
        quality_level=quality_level,
        stage=stage,
        domain=domain,
        task_type=task_type,
        processing_level=processing_level,
        source_kind=source_kind,
        tags=tags,
        idempotency_key=idempotency_key,
        on_duplicate=on_duplicate,
    )
