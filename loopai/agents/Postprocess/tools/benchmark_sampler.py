from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from loopai.logger import get_logger
from ..benchmark_sample_agent import BenchmarkSampleAgent
from ..schemas import DatasetSourceInfo

logger = get_logger()

BENCHMARK_DATA_EXTENSIONS = (
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".parquet",
    ".arrow",
    ".txt",
)

# 递归遍历时跳过的目录名（缓存/版本控制等）
_BENCHMARK_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".cache",
        ".tmp",
        "node_modules",
        ".venv",
        "venv",
    }
)
_BENCHMARK_SKIP_FILE_NAMES = frozenset(
    {
        "dataset_dict.json",
        "dataset_info.json",
        "state.json",
    }
)


def _is_benchmark_data_file(filename: str) -> bool:
    lower = filename.lower()
    if lower in _BENCHMARK_SKIP_FILE_NAMES:
        return False
    return any(lower.endswith(ext) for ext in BENCHMARK_DATA_EXTENSIONS)


def discover_benchmark_sources(benchmark_dir: str) -> List[DatasetSourceInfo]:
    """
    递归遍历 benchmark_dir 下所有子目录，收集符合扩展名的数据文件。

    每个数据文件对应一个 DatasetSourceInfo（dataset_dir 为该文件所在目录，
    data_files 仅含文件名），以便 sample_benchmark_sources 对每个文件各采 1 条样例。
    """
    if not benchmark_dir or not os.path.isdir(benchmark_dir):
        return []

    benchmark_dir = os.path.abspath(benchmark_dir)
    hits: List[str] = []

    for root, dirs, files in os.walk(benchmark_dir, topdown=True, followlinks=False):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".") and d not in _BENCHMARK_SKIP_DIR_NAMES
        ]
        for name in files:
            if name.startswith("."):
                continue
            if not _is_benchmark_data_file(name):
                continue
            full = os.path.join(root, name)
            if os.path.isfile(full):
                hits.append(full)

    hits.sort()
    sources: List[DatasetSourceInfo] = []
    for full_path in hits:
        rel = os.path.relpath(full_path, benchmark_dir)
        rel_posix = rel.replace(os.sep, "/")
        stem_for_name = os.path.splitext(rel_posix)[0]
        dataset_name = stem_for_name.replace("/", "__").replace("\\", "__")
        if not dataset_name.strip():
            dataset_name = os.path.splitext(os.path.basename(full_path))[0] or "benchmark_file"

        sources.append(
            DatasetSourceInfo(
                source_type="benchmark_datasets",
                dataset_name=dataset_name,
                dataset_dir=os.path.dirname(full_path),
                data_files=[os.path.basename(full_path)],
            )
        )

    logger.info(
        f"[discover_benchmark_sources] dir={benchmark_dir} recursive_files={len(sources)}"
    )
    return sources


def _dict_row(row: Any) -> Optional[Dict[str, Any]]:
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return None


def _load_data_file(file_path: str, cache_dir: Optional[str] = None):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        from datasets import DatasetDict, load_dataset

        builder_map = {
            ".json": "json",
            ".jsonl": "json",
            ".csv": "csv",
            ".tsv": "csv",
            ".parquet": "parquet",
            ".txt": "text",
        }
        builder = builder_map.get(ext)
        if builder:
            kwargs = {"data_files": file_path}
            if ext == ".tsv":
                kwargs["delimiter"] = "\t"
            load_kw: Dict[str, Any] = {"trust_remote_code": True}
            if cache_dir:
                load_kw["cache_dir"] = cache_dir
            ds = load_dataset(builder, **kwargs, **load_kw)
            if isinstance(ds, DatasetDict):
                for split_name in ds:
                    split = ds[split_name]
                    yield split, split_name
                return
            yield ds, "train"
            return
    except Exception as e:
        logger.debug(f"[benchmark_sampler] datasets load failed for {file_path}: {e}")

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
        elif ext == ".txt":
            df = pd.DataFrame({"text": pd.read_csv(file_path, header=None)[0]})
        else:
            return
        yield df.to_dict(orient="records"), "train"
    except Exception as e:
        logger.debug(f"[benchmark_sampler] pandas load failed for {file_path}: {e}")


def sample_benchmark_sources(
    benchmark_sources: List[DatasetSourceInfo],
    output_dir: str,
    max_per_dataset: int = 1,
    hf_datasets_cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Sample benchmark records. Default: one record per benchmark dataset."""
    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, "benchmark_samples.jsonl")

    sampled: List[Dict[str, Any]] = []
    failures = 0

    for source in benchmark_sources:
        if not source.data_files:
            continue
        taken = 0
        for rel_file in source.data_files:
            file_path = os.path.join(source.dataset_dir, rel_file)
            if not os.path.isfile(file_path):
                continue
            try:
                for dataset_like, split_name in _load_data_file(
                    file_path, cache_dir=hf_datasets_cache_dir
                ):
                    for row in dataset_like:
                        row_dict = _dict_row(row)
                        if row_dict is None:
                            continue
                        sampled.append(
                            {
                                "benchmark_name": source.dataset_name,
                                "source_type": source.source_type,
                                "dataset_dir": source.dataset_dir,
                                "file_path": file_path,
                                "split_name": split_name,
                                "sample_record": row_dict,
                            }
                        )
                        taken += 1
                        break
                    if taken >= max_per_dataset:
                        break
                if taken >= max_per_dataset:
                    break
            except Exception:
                failures += 1
                logger.warning(
                    f"[benchmark_sampler] failed sampling from {file_path}",
                    exc_info=True,
                )

    with open(out_file, "w", encoding="utf-8") as f:
        for item in sampled:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    logger.info(
        f"[benchmark_sampler] sampled={len(sampled)}, "
        f"datasets={len(benchmark_sources)}, failures={failures}, file={out_file}"
    )
    return {
        "benchmark_samples_file": out_file,
        "benchmark_samples": sampled,
        "benchmark_source_count": len(benchmark_sources),
        "benchmark_sampled_count": len(sampled),
        "benchmark_sampling_failures": failures,
    }


async def sample_benchmark_sources_agent_v2(
    benchmark_sources: List[DatasetSourceInfo],
    output_dir: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.0,
    max_concurrent: int = 3,
    hf_datasets_cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Agent-based benchmark sampler with strict tool boundary.
    Uses a dedicated sub-agent (data_load only) to avoid reading explanatory/meta files.
    """
    import asyncio

    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, "benchmark_samples.jsonl")

    sampled: List[Dict[str, Any]] = []
    failures = 0
    sem = asyncio.Semaphore(max(1, int(max_concurrent)))

    async def _one_source(source: DatasetSourceInfo) -> Optional[Dict[str, Any]]:
        nonlocal failures
        candidate_files = [f for f in source.data_files if _is_benchmark_data_file(f)]
        if not candidate_files:
            return None
        try:
            async with sem:
                agent = BenchmarkSampleAgent(
                    source_info=source,
                    candidate_files=candidate_files,
                    model_name=model_name,
                    base_url=base_url,
                    api_key=api_key,
                    temperature=temperature,
                    hf_datasets_cache_dir=hf_datasets_cache_dir,
                )
                result = await agent.run()
        except Exception:
            failures += 1
            logger.warning(f"[benchmark_sampler_agent_v2] failed source={source.dataset_name}", exc_info=True)
            return None

        sample_record = result.get("sample_record")
        rel_file = str(result.get("file_path", "")).strip()
        if not isinstance(sample_record, dict) or not sample_record:
            return None
        if rel_file not in candidate_files:
            rel_file = candidate_files[0]
        abs_file = os.path.join(source.dataset_dir, rel_file)
        return {
            "benchmark_name": source.dataset_name,
            "source_type": source.source_type,
            "dataset_dir": source.dataset_dir,
            "file_path": abs_file,
            "split_name": str(result.get("split_name", "train") or "train"),
            "sample_record": sample_record,
        }

    results = await asyncio.gather(*[_one_source(s) for s in benchmark_sources], return_exceptions=False)
    for item in results:
        if item:
            sampled.append(item)

    with open(out_file, "w", encoding="utf-8") as f:
        for item in sampled:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    logger.info(
        f"[benchmark_sampler_agent_v2] sampled={len(sampled)}, "
        f"datasets={len(benchmark_sources)}, failures={failures}, file={out_file}"
    )
    return {
        "benchmark_samples_file": out_file,
        "benchmark_samples": sampled,
        "benchmark_source_count": len(benchmark_sources),
        "benchmark_sampled_count": len(sampled),
        "benchmark_sampling_failures": failures,
    }


def initialize_benchmark_pool(
    benchmark_source_dir: str,
    pool_path: str,
    pool_size: int = 500,
    hf_datasets_cache_dir: Optional[str] = None,
    random_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    初始化 benchmark 采样池：从 benchmark_source_dir 读取数据，采样 pool_size 条，存到 pool_path。

    Args:
        benchmark_source_dir: benchmark 数据集源目录
        pool_path: 采样池输出路径
        pool_size: 采样池大小（默认500）
        hf_datasets_cache_dir: 传给 ``datasets.load_dataset(..., cache_dir=...)``；默认 None 使用环境变量/全局默认
        random_seed: 若给定，则在 ``random.sample`` 前 ``random.seed(random_seed)``，便于复现采样结果

    Returns:
        包含采样统计信息的字典
    """
    import random

    if not benchmark_source_dir or not os.path.isdir(benchmark_source_dir):
        logger.warning(f"Invalid benchmark_source_dir: {benchmark_source_dir}")
        return {"success": False, "error": "Invalid benchmark source directory"}

    logger.info(f"Initializing benchmark pool from {benchmark_source_dir}, target size: {pool_size}")

    # 发现所有 benchmark 数据源
    sources = discover_benchmark_sources(benchmark_source_dir)
    if not sources:
        logger.warning(f"No benchmark sources found in {benchmark_source_dir}")
        return {"success": False, "error": "No benchmark sources found"}

    # 收集所有记录
    all_records: List[Dict[str, Any]] = []
    failures = 0

    for source in sources:
        if not source.data_files:
            continue
        for rel_file in source.data_files:
            file_path = os.path.join(source.dataset_dir, rel_file)
            if not os.path.isfile(file_path):
                continue
            try:
                for dataset_like, split_name in _load_data_file(
                    file_path, cache_dir=hf_datasets_cache_dir
                ):
                    for row in dataset_like:
                        row_dict = _dict_row(row)
                        if row_dict is None:
                            continue
                        all_records.append(
                            {
                                "benchmark_name": source.dataset_name,
                                "source_type": source.source_type,
                                "file_path": file_path,
                                "split_name": split_name,
                                "sample_record": row_dict,
                            }
                        )
            except Exception:
                failures += 1
                logger.warning(f"Failed loading {file_path}", exc_info=True)

    if not all_records:
        logger.warning("No records collected from benchmark sources")
        return {"success": False, "error": "No records collected"}

    # 采样
    if random_seed is not None:
        random.seed(random_seed)
        logger.info(f"[benchmark_sampler] benchmark pool sampling random_seed={random_seed}")
    sampled = random.sample(all_records, min(pool_size, len(all_records)))

    # 保存到采样池
    os.makedirs(os.path.dirname(pool_path), exist_ok=True)
    with open(pool_path, "w", encoding="utf-8") as f:
        for item in sampled:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    logger.info(
        f"Benchmark pool initialized: {len(sampled)} samples from {len(all_records)} total records, "
        f"saved to {pool_path}"
    )

    return {
        "success": True,
        "pool_path": pool_path,
        "pool_size": len(sampled),
        "total_records": len(all_records),
        "source_count": len(sources),
        "failures": failures,
        "random_seed": random_seed,
    }


def sample_from_benchmark_pool(pool_path: str) -> Optional[Dict[str, Any]]:
    """
    从 benchmark 采样池中随机采样一条记录。

    Args:
        pool_path: 采样池文件路径

    Returns:
        采样的记录，如果失败返回 None
    """
    import random

    if not pool_path or not os.path.isfile(pool_path):
        logger.warning(f"Benchmark pool not found: {pool_path}")
        return None

    try:
        records = []
        with open(pool_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        if not records:
            logger.warning(f"Benchmark pool is empty: {pool_path}")
            return None

        sampled = random.choice(records)
        logger.debug(f"Sampled from benchmark pool: {sampled.get('benchmark_name', 'unknown')}")
        return sampled

    except Exception as e:
        logger.error(f"Error sampling from benchmark pool {pool_path}: {e}")
        return None
