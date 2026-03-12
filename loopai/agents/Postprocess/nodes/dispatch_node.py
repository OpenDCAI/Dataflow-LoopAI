"""
Dispatch node — creates and runs a DatasetAgent for each discovered source,
then applies the mapping plan to produce intermediate-format JSONL records.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from loopai.logger import get_logger
from ..dataset_agent import DatasetAgent
from ..memory import PostprocessMemoryManager
from ..schemas import DatasetAgentResult, DatasetMappingPlan, DatasetSourceInfo

logger = get_logger()

CHUNK_SIZE = 10_000


# ---------------------------------------------------------------------------
# Data conversion using the mapping plan
# ---------------------------------------------------------------------------

def _load_data_file(file_path: str):
    """Load a data file and return (column_names, iterable_of_dicts, total)."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        from datasets import load_dataset, DatasetDict
        builder_map = {
            ".json": "json", ".jsonl": "json",
            ".csv": "csv", ".tsv": "csv",
            ".parquet": "parquet", ".arrow": "arrow",
            ".txt": "text",
        }
        builder = builder_map.get(ext)
        if builder:
            kwargs = {"data_files": file_path}
            if ext == ".tsv":
                kwargs["delimiter"] = "\t"
            ds = load_dataset(builder, **kwargs, trust_remote_code=True)
            if isinstance(ds, DatasetDict):
                for split_name in ds:
                    split = ds[split_name]
                    yield split.column_names, split, len(split), split_name
                return
            yield ds.column_names, ds, len(ds), "train"
            return
    except Exception as e:
        logger.debug(f"[dispatch] datasets load failed for {file_path}: {e}")

    try:
        import pandas as pd
        if ext in (".json", ".jsonl"):
            df = pd.read_json(file_path, lines=(ext == ".jsonl"))
        elif ext == ".csv":
            df = pd.read_csv(file_path)
        elif ext == ".tsv":
            df = pd.read_csv(file_path, sep="\t")
        elif ext == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            return
        cols = list(df.columns)
        records = df.to_dict(orient="records")
        yield cols, records, len(records), "train"
    except Exception as e:
        logger.debug(f"[dispatch] pandas load failed for {file_path}: {e}")


def _generate_record_id(file_path: str, index: int) -> str:
    base = os.path.splitext(os.path.basename(file_path))[0]
    return f"{base}_{index:06d}"


def _build_pt_record(row: Dict, plan: DatasetMappingPlan, file_path: str, idx: int) -> Optional[Dict]:
    text_field = plan.text_field
    if text_field is None:
        return None
    if isinstance(text_field, list):
        parts = [str(row.get(f, "")).strip() for f in text_field]
        text = " ".join(p for p in parts if p)
    else:
        text = str(row.get(text_field, "")).strip()
    if not text:
        return None
    meta = {k: str(row.get(v, "")) for k, v in (plan.meta_fields or {}).items()}
    return {
        "id": _generate_record_id(file_path, idx),
        "dataset_type": "pretrain",
        "text": text,
        "meta": meta,
    }


def _build_sft_record(row: Dict, plan: DatasetMappingPlan, file_path: str, idx: int) -> Optional[Dict]:
    if not plan.messages:
        return None
    messages = []
    for spec in plan.messages:
        role = spec.get("role")
        content_field = spec.get("content")
        loss_mask = spec.get("loss_mask")
        if not role or not content_field:
            continue
        if isinstance(content_field, list):
            parts = [str(row.get(f, "")).strip() for f in content_field]
            content = " ".join(p for p in parts if p)
        else:
            content = str(row.get(content_field, "")).strip()
        if not content:
            continue
        if loss_mask is None:
            loss_mask = role == "assistant"
        messages.append({"role": role, "content": content, "loss_mask": loss_mask})

    if not messages:
        return None
    has_user = any(m["role"] == "user" for m in messages)
    has_assistant = any(m["role"] == "assistant" for m in messages)
    if not (has_user and has_assistant):
        return None

    meta = {k: str(row.get(v, "")) for k, v in (plan.meta_fields or {}).items()}
    result: Dict[str, Any] = {
        "id": _generate_record_id(file_path, idx),
        "dataset_type": "sft",
        "messages": messages,
        "meta": meta,
    }
    if plan.system:
        sys_val = str(row.get(plan.system, "")).strip()
        if sys_val:
            result["system"] = sys_val
    return result


def _apply_mapping_plan(
    source_info: DatasetSourceInfo,
    plan: DatasetMappingPlan,
    output_dir: str,
    category: str,
) -> Tuple[int, List[str]]:
    """Apply a mapping plan to all data files in the dataset and write JSONL."""
    total_records = 0
    output_files: List[str] = []
    chunk_idx = 1
    chunk_count = 0
    current_file = None

    def _open_chunk():
        nonlocal chunk_idx, chunk_count, current_file
        path = os.path.join(output_dir, f"{category.upper()}_{chunk_idx:05d}.jsonl")
        current_file = open(path, "a", encoding="utf-8")
        output_files.append(path)
        chunk_count = 0

    def _write_record(record: Dict):
        nonlocal total_records, chunk_count, chunk_idx, current_file
        if current_file is None:
            _open_chunk()
        current_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        total_records += 1
        chunk_count += 1
        if chunk_count >= CHUNK_SIZE:
            current_file.close()
            chunk_idx += 1
            _open_chunk()

    data_files = [
        os.path.join(source_info.dataset_dir, f) for f in source_info.data_files
    ]

    for file_path in data_files:
        if not os.path.isfile(file_path):
            logger.warning(f"[dispatch] Data file not found: {file_path}")
            continue
        logger.info(f"[dispatch] Mapping {file_path} with plan for {source_info.dataset_name}")
        for columns, data, count, split_name in _load_data_file(file_path):
            for idx, row in enumerate(data):
                if isinstance(row, dict):
                    row_dict = row
                else:
                    try:
                        row_dict = dict(row)
                    except Exception:
                        continue

                if category == "PT":
                    record = _build_pt_record(row_dict, plan, file_path, idx)
                else:
                    record = _build_sft_record(row_dict, plan, file_path, idx)

                if record is not None:
                    _write_record(record)

    if current_file is not None:
        current_file.close()

    logger.info(
        f"[dispatch] {source_info.dataset_name}: {total_records} records written to {len(output_files)} files"
    )
    return total_records, output_files


# ---------------------------------------------------------------------------
# Public dispatch function
# ---------------------------------------------------------------------------

async def dispatch_node(
    sources: List[DatasetSourceInfo],
    category: str,
    user_query: str,
    datasets_background: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    tavily_api_key: Optional[str],
    output_dir: str,
    memory_manager: Optional[PostprocessMemoryManager] = None,
    max_concurrent: int = 3,
) -> List[DatasetAgentResult]:
    """Run a DatasetAgent for each source, apply mapping, return results."""
    os.makedirs(output_dir, exist_ok=True)
    results: List[DatasetAgentResult] = []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _process_one(source: DatasetSourceInfo) -> DatasetAgentResult:
        async with semaphore:
            agent = DatasetAgent(
                source_info=source,
                category=category,
                user_query=user_query,
                datasets_background=datasets_background,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                tavily_api_key=tavily_api_key,
                memory_manager=memory_manager,
            )
            result = await agent.run()

            if result.success and result.mapping_plan and result.mapping_plan.confidence > 0:
                try:
                    count, files = _apply_mapping_plan(
                        source, result.mapping_plan, output_dir, category,
                    )
                    result.records_processed = count
                    result.output_files = files
                except Exception as e:
                    logger.error(f"[dispatch] Mapping failed for {source.dataset_name}: {e}", exc_info=True)
                    result.error = f"Mapping failed: {str(e)}"
                    result.success = False

            return result

    tasks = [_process_one(src) for src in sources]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for item in completed:
        if isinstance(item, Exception):
            logger.error(f"[dispatch] Agent task exception: {item}")
            results.append(DatasetAgentResult(
                dataset_name="unknown",
                source_type="unknown",
                dataset_dir="",
                success=False,
                error=str(item),
            ))
        else:
            results.append(item)

    return results
