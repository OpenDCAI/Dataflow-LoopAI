"""
Data load tool for the Postprocess dataset agent.
Loads various data formats and returns 1-3 sample records so the LLM
can inspect the actual data structure.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from loopai.logger import get_logger

logger = get_logger()

MAX_SAMPLES = 3
MAX_PREVIEW_CHARS = 6000


class DataLoadInput(BaseModel):
    file_path: str = Field(
        ..., description="Absolute path to the data file to sample"
    )
    num_samples: int = Field(
        3, ge=1, le=5, description="Number of sample records to return (1-5)"
    )


def _try_load_with_datasets(file_path: str) -> Optional[Tuple[List[str], List[Dict], int]]:
    """Try loading with HuggingFace datasets library."""
    try:
        from datasets import load_dataset, Dataset, DatasetDict

        ext = os.path.splitext(file_path)[1].lower()
        builder_map = {
            ".json": "json", ".jsonl": "json",
            ".csv": "csv", ".tsv": "csv",
            ".parquet": "parquet", ".arrow": "arrow",
            ".txt": "text",
        }
        builder = builder_map.get(ext)
        if not builder:
            return None

        kwargs = {"data_files": file_path}
        if ext == ".tsv":
            kwargs["delimiter"] = "\t"

        ds = load_dataset(builder, **kwargs, trust_remote_code=True)

        if isinstance(ds, DatasetDict):
            split_name = list(ds.keys())[0]
            ds = ds[split_name]

        columns = ds.column_names
        total = len(ds)
        samples = []
        import random
        indices = random.sample(range(total), min(MAX_SAMPLES, total))
        for idx in indices:
            samples.append(dict(ds[idx]))
        return columns, samples, total
    except Exception as e:
        logger.debug(f"[data_load] datasets lib failed for {file_path}: {e}")
        return None


def _try_load_with_pandas(file_path: str) -> Optional[Tuple[List[str], List[Dict], int]]:
    """Fallback: load with pandas."""
    try:
        import pandas as pd

        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".json", ".jsonl"):
            df = pd.read_json(file_path, lines=(ext == ".jsonl"), nrows=100)
        elif ext == ".csv":
            df = pd.read_csv(file_path, nrows=100)
        elif ext == ".tsv":
            df = pd.read_csv(file_path, sep="\t", nrows=100)
        elif ext == ".parquet":
            df = pd.read_parquet(file_path)
            if len(df) > 100:
                df = df.head(100)
        elif ext == ".arrow":
            import pyarrow as pa
            table = pa.ipc.open_file(file_path).read_all()
            df = table.to_pandas()
            if len(df) > 100:
                df = df.head(100)
        else:
            return None

        columns = list(df.columns)
        total = len(df)
        samples = df.sample(min(MAX_SAMPLES, total)).to_dict(orient="records")
        return columns, samples, total
    except Exception as e:
        logger.debug(f"[data_load] pandas fallback failed for {file_path}: {e}")
        return None


def _try_load_raw_jsonl(file_path: str) -> Optional[Tuple[List[str], List[Dict], int]]:
    """Last resort: read JSONL line-by-line."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".json", ".jsonl"):
            return None

        records: List[Dict] = []
        count = 0
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                count += 1
                if len(records) < MAX_SAMPLES:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                if count > 100_000:
                    break

        if not records:
            return None
        columns = list(records[0].keys())
        return columns, records, count
    except Exception as e:
        logger.debug(f"[data_load] raw jsonl fallback failed: {e}")
        return None


def _truncate_sample(sample: Dict[str, Any], max_val_len: int = 500) -> Dict[str, Any]:
    """Truncate overly long field values so the preview fits in context."""
    out = {}
    for k, v in sample.items():
        sv = str(v)
        if len(sv) > max_val_len:
            out[k] = sv[:max_val_len] + f"...(truncated, {len(sv)} chars total)"
        else:
            out[k] = v
    return out


def _is_list_of_dicts(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _collect_field_paths(
    obj: Any,
    prefix: str = "",
    *,
    max_depth: int = 2,
    _depth: int = 0,
    _out: Optional[List[str]] = None,
) -> List[str]:
    out = _out if _out is not None else []
    if _depth > max_depth:
        return out

    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            out.append(path)
            _collect_field_paths(value, path, max_depth=max_depth, _depth=_depth + 1, _out=out)
        return out

    if isinstance(obj, list) and obj:
        first = obj[0]
        if isinstance(first, dict):
            _collect_field_paths(first, f"{prefix}.0" if prefix else "0", max_depth=max_depth, _depth=_depth + 1, _out=out)
    return out


def _analyze_preview_structure(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not samples:
        return {
            "top_level_type": "empty",
            "candidate_record_paths": [],
            "sample_path_fields": [],
        }

    candidate_paths = set()
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        for k, v in sample.items():
            if _is_list_of_dicts(v):
                candidate_paths.add(k)

    path_fields: List[str] = []
    for sample in samples[:2]:
        if isinstance(sample, dict):
            path_fields.extend(_collect_field_paths(sample, max_depth=2))

    dedup_paths = sorted(set(path_fields))[:40]
    return {
        "top_level_type": "list_of_records",
        "candidate_record_paths": sorted(candidate_paths),
        "sample_path_fields": dedup_paths,
    }


def create_data_load_tool(dataset_dir: str):
    """Factory that returns a @tool-decorated data_load scoped to *dataset_dir*."""

    @tool(args_schema=DataLoadInput)
    def data_load(file_path: str, num_samples: int = 3) -> str:
        """Load a data file and return sample records for inspection.
        Supports JSON, JSONL, CSV, TSV, Parquet, Arrow formats.
        Returns column names, total record count, and 1-3 sample records
        so you can understand the actual data structure."""

        abs_path = os.path.abspath(file_path)
        abs_dataset = os.path.abspath(dataset_dir)

        if not abs_path.startswith(abs_dataset):
            return f"Error: path '{file_path}' is outside the dataset directory."

        if not os.path.isfile(abs_path):
            return f"Error: '{file_path}' does not exist or is not a file."

        strategies = [
            ("datasets", _try_load_with_datasets),
            ("pandas", _try_load_with_pandas),
            ("raw_jsonl", _try_load_raw_jsonl),
        ]

        for name, loader in strategies:
            result = loader(abs_path)
            if result is not None:
                columns, samples, total = result
                samples = [_truncate_sample(s) for s in samples[:num_samples]]
                structure_info = _analyze_preview_structure(samples)
                preview = {
                    "file": os.path.basename(abs_path),
                    "loader": name,
                    "columns": columns,
                    "total_records": total,
                    "num_samples": len(samples),
                    "samples": samples,
                    "structure": structure_info,
                }
                text = json.dumps(preview, indent=2, ensure_ascii=False, default=str)
                if len(text) > MAX_PREVIEW_CHARS:
                    text = text[:MAX_PREVIEW_CHARS] + "\n...(truncated)"
                logger.info(
                    f"[data_load] Loaded {abs_path} via '{name}': "
                    f"{len(columns)} cols, {total} records"
                )
                return text

        return f"Error: unable to load '{os.path.basename(file_path)}' with any strategy."

    return data_load
