"""
Postprocess Hugging Face `datasets` cache layout (no `datasets` / `huggingface_hub` imports).

Cache lives under the v2 output directory: ``<download_dir>/processed_output/.cache/huggingface/``.
"""
from __future__ import annotations

import os


def hf_datasets_cache_dir(output_dir: str) -> str:
    """Return ``<output_dir>/.cache/huggingface/datasets`` (absolute)."""
    return os.path.join(os.path.abspath(output_dir), ".cache", "huggingface", "datasets")


def ensure_postprocess_hf_cache_env(output_dir: str) -> str:
    """
    Set ``HF_HOME`` and ``HF_DATASETS_CACHE`` under *output_dir*/.cache before HF libraries load.

    Creates the cache directory. Returns the absolute ``HF_DATASETS_CACHE`` path.
    """
    abs_out = os.path.abspath(output_dir)
    hf_home = os.path.join(abs_out, ".cache", "huggingface")
    datasets_cache = os.path.join(hf_home, "datasets")
    os.makedirs(datasets_cache, exist_ok=True)
    os.environ["HF_HOME"] = hf_home
    os.environ["HF_DATASETS_CACHE"] = datasets_cache
    return datasets_cache
