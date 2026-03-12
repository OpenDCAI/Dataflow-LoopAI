"""
Merge node — aggregates per-dataset results into a single
PostprocessMergeResult that is compatible with the old postprocess contract.
"""
from __future__ import annotations

import os
from typing import List

from loopai.logger import get_logger
from ..schemas import DatasetAgentResult, PostprocessMergeResult

logger = get_logger()


def merge_node(
    per_dataset_results: List[DatasetAgentResult],
    output_dir: str,
) -> PostprocessMergeResult:
    """Combine all per-dataset agent results into a single merge result."""

    total_records = 0
    source_count = 0
    errors: List[str] = []

    for r in per_dataset_results:
        if r.success:
            total_records += r.records_processed
            if r.records_processed > 0:
                source_count += 1
        if r.error:
            errors.append(f"{r.dataset_name}: {r.error}")

    result = PostprocessMergeResult(
        total_records_processed=total_records,
        processed_sources_count=source_count,
        output_dir=os.path.abspath(output_dir) if output_dir else "",
        per_dataset_results=per_dataset_results,
        errors=errors,
    )

    logger.info(
        f"[merge] Merged {len(per_dataset_results)} datasets: "
        f"{total_records} total records, {source_count} successful sources, "
        f"{len(errors)} errors"
    )
    return result
