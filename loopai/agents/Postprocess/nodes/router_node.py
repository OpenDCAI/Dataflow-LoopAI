"""
Router node — scans the download directory for dataset source folders
and builds a list of DatasetSourceInfo objects.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from loopai.logger import get_logger
from ..schemas import DatasetSourceInfo, DatasetFolderProfile

logger = get_logger()

README_PATTERNS = {"readme", "readme.md", "readme.txt", "readme.rst", "metadata.json", "metadata.yaml", "dataset_infos.json", "dataset_info.json"}
SCRIPT_EXTENSIONS = {".py", ".sh", ".bash"}
DATA_EXTENSIONS = {".json", ".jsonl", ".csv", ".tsv", ".parquet", ".arrow", ".txt", ".bin", ".gz", ".zip", ".tar", ".bz2", ".xz", ".zst", ".h5", ".hdf5", ".npy", ".npz", ".feather", ".orc", ".avro"}
SKIP_DIRS = {".cache", ".tmp", "__pycache__", ".git", "processed_output"}


def _classify_file(filename: str) -> str:
    """Return 'readme', 'data', 'script', or 'other'."""
    lower = filename.lower()
    if lower in README_PATTERNS:
        return "readme"
    _, ext = os.path.splitext(lower)
    if ext in {".md", ".rst", ".txt", ".yaml", ".yml"} and "readme" in lower:
        return "readme"
    if ext in SCRIPT_EXTENSIONS:
        return "script"
    if ext in DATA_EXTENSIONS:
        return "data"
    if lower in {"license", "license.md", "license.txt", ".gitattributes"}:
        return "other"
    if ext in {".md", ".rst", ".yaml", ".yml", ".cfg", ".ini", ".toml"}:
        return "readme"
    return "other"


def _scan_dataset_dir(dataset_dir: str, source_type: str, dataset_name: str) -> DatasetSourceInfo:
    """Walk a single dataset directory and classify its contents."""
    readme: List[str] = []
    data: List[str] = []
    script: List[str] = []
    other: List[str] = []

    for entry in os.listdir(dataset_dir):
        full_path = os.path.join(dataset_dir, entry)
        if entry.startswith(".") or entry in SKIP_DIRS:
            continue
        if os.path.isdir(full_path):
            for sub_entry in os.listdir(full_path):
                sub_full = os.path.join(full_path, sub_entry)
                if os.path.isfile(sub_full):
                    rel = os.path.join(entry, sub_entry)
                    cls = _classify_file(sub_entry)
                    {"readme": readme, "data": data, "script": script, "other": other}[cls].append(rel)
        elif os.path.isfile(full_path):
            cls = _classify_file(entry)
            {"readme": readme, "data": data, "script": script, "other": other}[cls].append(entry)

    return DatasetSourceInfo(
        source_type=source_type,
        dataset_name=dataset_name,
        dataset_dir=dataset_dir,
        readme_files=readme,
        data_files=data,
        script_files=script,
        other_files=other,
    )


def router_node(download_dir: str) -> List[DatasetSourceInfo]:
    """Discover all dataset source directories under *download_dir*.

    Expected layout::

        download_dir/
          hf_datasets/
            <dataset_1>/
            <dataset_2>/
          kaggle_datasets/
            <dataset_3>/
          web_downloads/
            <file_or_dir>/
    """
    sources: List[DatasetSourceInfo] = []

    source_dirs = [
        ("hf_datasets", os.path.join(download_dir, "hf_datasets")),
        ("kaggle_datasets", os.path.join(download_dir, "kaggle_datasets")),
        ("web_downloads", os.path.join(download_dir, "web_downloads")),
    ]

    for source_type, parent_dir in source_dirs:
        if not os.path.isdir(parent_dir):
            continue
        for entry in sorted(os.listdir(parent_dir)):
            entry_path = os.path.join(parent_dir, entry)
            if entry.startswith(".") or entry in SKIP_DIRS:
                continue
            if os.path.isdir(entry_path):
                info = _scan_dataset_dir(entry_path, source_type, entry)
                sources.append(info)
                logger.info(
                    f"[router] Found {source_type}/{entry}: "
                    f"{len(info.data_files)} data, {len(info.readme_files)} readme"
                )
            elif os.path.isfile(entry_path) and source_type == "web_downloads":
                info = DatasetSourceInfo(
                    source_type=source_type,
                    dataset_name=entry,
                    dataset_dir=parent_dir,
                    data_files=[entry],
                )
                sources.append(info)

    logger.info(f"[router] Total dataset sources discovered: {len(sources)}")
    return sources
