"""
File read tool for the Postprocess dataset agent.
Only reads non-data files (README, metadata, scripts, config).
Explicitly rejects large data files.
"""
from __future__ import annotations

import os
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from loopai.logger import get_logger

logger = get_logger()

DATA_EXTENSIONS = {
    ".json", ".jsonl", ".csv", ".tsv", ".parquet", ".arrow",
    ".bin", ".sqlite3", ".db", ".h5", ".hdf5", ".npy", ".npz",
    ".pkl", ".pickle", ".feather", ".orc", ".avro", ".tar",
    ".gz", ".zip", ".bz2", ".xz", ".zst",
}

MAX_READ_BYTES = 100_000


class FileReadInput(BaseModel):
    file_path: str = Field(..., description="Absolute path of the file to read")


def create_file_read_tool(dataset_dir: str):
    """Factory that returns a @tool-decorated file_read scoped to *dataset_dir*."""

    @tool(args_schema=FileReadInput)
    def file_read(file_path: str) -> str:
        """Read a non-data file from the dataset directory.
        Use this to read README, metadata YAML/JSON, Python scripts, or any
        documentation file. Data files (csv, jsonl, parquet, etc.) are rejected;
        use data_load for those."""

        abs_path = os.path.abspath(file_path)
        abs_dataset = os.path.abspath(dataset_dir)

        if not abs_path.startswith(abs_dataset):
            return f"Error: path '{file_path}' is outside the dataset directory."

        if not os.path.isfile(abs_path):
            return f"Error: '{file_path}' does not exist or is not a file."

        _, ext = os.path.splitext(abs_path.lower())
        if ext in DATA_EXTENSIONS:
            return (
                f"Error: '{os.path.basename(file_path)}' is a data file ({ext}). "
                "Use the data_load tool to inspect actual data."
            )

        file_size = os.path.getsize(abs_path)
        if file_size > MAX_READ_BYTES:
            logger.warning(
                f"[file_read] File {abs_path} is {file_size} bytes, truncating to {MAX_READ_BYTES}"
            )

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_READ_BYTES)
            logger.info(f"[file_read] Read {len(content)} chars from {abs_path}")
            return content
        except Exception as e:
            logger.error(f"[file_read] Error reading {abs_path}: {e}")
            return f"Error reading file: {str(e)}"

    return file_read
