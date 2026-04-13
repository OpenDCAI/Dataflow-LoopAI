"""
Dispatch node — runs one DatasetAgent per source (relevance-only),
then writes full raw rows to unified JSONL when the verdict is related.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from loopai.logger import add_context_file_handler, get_logger, logging_context, remove_handler
from ..dataset_agent import DatasetAgent
from ..memory import PostprocessMemoryManager
from ..schemas import DatasetAgentResult, DatasetSourceInfo

logger = get_logger()

RELATED_JSONL_SUBDIR = "related_jsonl"


def _safe_name(name: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in (name or "unknown"))
    return safe.strip("._") or "unknown"


def _load_data_file(file_path: str, cache_dir: Optional[str] = None):
    """Load a data file and yield (column_names, iterable_of_dicts, total, split_name)."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        from datasets import load_dataset, DatasetDict

        builder_map = {
            ".json": "json",
            ".jsonl": "json",
            ".csv": "csv",
            ".tsv": "csv",
            ".parquet": "parquet",
            ".arrow": "arrow",
            ".txt": "text",
        }
        builder = builder_map.get(ext)
        if builder:
            kwargs: Dict[str, Any] = {"data_files": file_path}
            if ext == ".tsv":
                kwargs["delimiter"] = "\t"
            load_kw: Dict[str, Any] = {"trust_remote_code": True}
            if cache_dir:
                load_kw["cache_dir"] = cache_dir
            ds = load_dataset(builder, **kwargs, **load_kw)
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


def _dict_row(row: Any) -> Optional[Dict[str, Any]]:
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return None


def dump_dataset_raw_jsonl(
    source_info: DatasetSourceInfo,
    out_path: str,
    hf_datasets_cache_dir: Optional[str] = None,
) -> int:
    """Write every row from all data files as JSONL lines; split into 100k-row chunks."""
    data_files = [os.path.join(source_info.dataset_dir, f) for f in source_info.data_files]
    total = 0
    chunk_size = 100000
    chunk_index = 0
    current_chunk_count = 0

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    base_dir = os.path.dirname(out_path)
    base_name = os.path.basename(out_path)
    name, ext = os.path.splitext(base_name)

    def get_chunk_path(idx: int) -> str:
        if idx == 0:
            return out_path
        return os.path.join(base_dir, f"{name}_part_{idx:03d}{ext}")

    current_path = get_chunk_path(chunk_index)
    fh = open(current_path, "w", encoding="utf-8")

    try:
        for file_path in data_files:
            if not os.path.isfile(file_path):
                logger.warning(f"[dispatch] Data file not found: {file_path}")
                continue
            for _, data, _, _split_name in _load_data_file(file_path, cache_dir=hf_datasets_cache_dir):
                for raw_row in data:
                    row = _dict_row(raw_row)
                    if row is None:
                        continue

                    if current_chunk_count >= chunk_size:
                        fh.close()
                        chunk_index += 1
                        current_path = get_chunk_path(chunk_index)
                        fh = open(current_path, "w", encoding="utf-8")
                        current_chunk_count = 0
                        logger.info(f"[dispatch] New chunk: {current_path}")

                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    total += 1
                    current_chunk_count += 1
    finally:
        fh.close()

    if chunk_index > 0:
        logger.info(f"[dispatch] {source_info.dataset_name}: {total} rows in {chunk_index + 1} chunks")
    else:
        logger.info(f"[dispatch] {source_info.dataset_name}: {total} rows -> {out_path}")
    return total


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
    benchmark_reference_samples: Optional[List[Dict[str, Any]]] = None,
    hf_datasets_cache_dir: Optional[str] = None,
) -> List[DatasetAgentResult]:
    """Run relevance DatasetAgent per source; export related datasets to *RELATED_JSONL_SUBDIR*."""
    os.makedirs(output_dir, exist_ok=True)
    related_dir = os.path.join(output_dir, RELATED_JSONL_SUBDIR)
    os.makedirs(related_dir, exist_ok=True)

    results: List[DatasetAgentResult] = []
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_root = os.path.normpath(os.path.join(output_dir, "..", "..", "log"))
    run_log_dir = os.path.join(log_root, f"postprocess_v2_{run_id}")
    os.makedirs(run_log_dir, exist_ok=True)

    logger.info(
        f"[dispatch] Start run {run_id}, sources={len(sources)}, max_concurrent={max_concurrent}, "
        f"related_jsonl_dir={related_dir}, dataset_log_dir={run_log_dir}, "
        f"hf_datasets_cache_dir={hf_datasets_cache_dir or '(default)'}"
    )

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _process_one(source: DatasetSourceInfo) -> DatasetAgentResult:
        async with semaphore:
            safe_dataset = _safe_name(source.dataset_name)
            dataset_log = os.path.join(run_log_dir, f"{safe_dataset}.log")
            context_id = f"{run_id}:{safe_dataset}"
            root_logger = get_logger()
            dataset_handler = add_context_file_handler(root_logger, dataset_log, context_id)
            logger.info(f"[dispatch] Dataset {source.dataset_name} log file: {dataset_log}")

            try:
                with logging_context(context_id):
                    logger.info(
                        f"[dispatch] Dataset start: source={source.source_type}, "
                        f"name={source.dataset_name}, dir={source.dataset_dir}"
                    )
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
                        benchmark_reference_samples=benchmark_reference_samples,
                        hf_datasets_cache_dir=hf_datasets_cache_dir,
                    )
                    result = await agent.run()
                    result.log_file = dataset_log

                    v = result.relevance_verdict
                    if result.success and v and v.related and not v.is_benchmark_data:
                        out_name = f"{source.source_type}__{safe_dataset}.jsonl"
                        out_path = os.path.join(related_dir, out_name)
                        try:
                            count = await asyncio.to_thread(
                                dump_dataset_raw_jsonl,
                                source,
                                out_path,
                                hf_datasets_cache_dir,
                            )
                            result.records_processed = count
                            result.output_files = [out_path]
                        except Exception as e:
                            logger.error(f"[dispatch] Raw JSONL export failed for {source.dataset_name}: {e}", exc_info=True)
                            result.error = f"JSONL export failed: {str(e)}"
                            result.success = False
                    elif result.success and v and v.related and v.is_benchmark_data:
                        logger.info(
                            f"[dispatch] Dataset {source.dataset_name}: related but is_benchmark_data=True, "
                            f"skip export to avoid benchmark leakage"
                        )

                    logger.info(
                        f"[dispatch] Dataset end: name={source.dataset_name}, success={result.success}, "
                        f"records={result.records_processed}, related="
                        f"{getattr(result.relevance_verdict, 'related', None)}, "
                        f"is_benchmark_data={getattr(result.relevance_verdict, 'is_benchmark_data', None)}"
                    )
            except Exception as e:
                with logging_context(context_id):
                    logger.error(
                        f"[dispatch] Dataset task failed unexpectedly for {source.dataset_name}: {e}",
                        exc_info=True,
                    )
                result = DatasetAgentResult(
                    dataset_name=source.dataset_name,
                    source_type=source.source_type,
                    dataset_dir=source.dataset_dir,
                    success=False,
                    error=str(e),
                    log_file=dataset_log,
                )
            finally:
                remove_handler(root_logger, dataset_handler)

            logger.info(
                f"[dispatch] Dataset summary: name={result.dataset_name}, success={result.success}, "
                f"records={result.records_processed}, log={result.log_file}"
            )
            return result

    tasks = [_process_one(src) for src in sources]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for item in completed:
        if isinstance(item, Exception):
            logger.error(f"[dispatch] Agent task exception: {item}", exc_info=True)
            results.append(
                DatasetAgentResult(
                    dataset_name="unknown",
                    source_type="unknown",
                    dataset_dir="",
                    success=False,
                    error=str(item),
                )
            )
        else:
            results.append(item)

    logger.info(
        f"[dispatch] Run {run_id} completed, datasets={len(results)}, "
        f"success={sum(1 for r in results if r.success)}, failed={sum(1 for r in results if not r.success)}"
    )
    return results
