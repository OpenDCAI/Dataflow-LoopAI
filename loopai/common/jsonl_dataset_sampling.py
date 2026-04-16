"""Constructor 中间 jsonl 预采样：按逻辑数据集（Postprocess `_part_XXX` 分片）均分预算。

供单测与 filter_node 共用，避免导入 Constructor 工具链。
"""
from __future__ import annotations

import os
import re
from typing import Dict, List

_JSONL_CHUNK_STEM_SUFFIX = re.compile(r"_part_\d{3}$")


def logical_dataset_stem_from_jsonl_basename(basename: str) -> str:
    """与 Postprocess dump_dataset_raw_jsonl 的 `{name}_part_{idx:03d}.jsonl` 对齐：分片归入同一逻辑数据集。"""
    stem, ext = os.path.splitext(basename)
    if ext.lower() != ".jsonl":
        return stem
    return _JSONL_CHUNK_STEM_SUFFIX.sub("", stem)


def equal_split_quotas(num_slots: int, budget: int) -> List[int]:
    """将 budget 均分到 num_slots 个槽位；余数分给前若干项。"""
    if num_slots <= 0:
        return []
    if budget <= 0:
        return [0] * num_slots
    base = budget // num_slots
    r = budget % num_slots
    return [base + (1 if i < r else 0) for i in range(num_slots)]


def cleaning_sampling_plan_by_dataset(files: List[str], budget: int) -> Dict[str, int]:
    """将 budget 先在逻辑数据集间均分，再在各数据集的分片文件间均分。files 建议已排序。"""
    if not files or budget <= 0:
        return {}
    groups: Dict[str, List[str]] = {}
    for fp in files:
        key = logical_dataset_stem_from_jsonl_basename(os.path.basename(fp))
        groups.setdefault(key, []).append(fp)
    sorted_keys = sorted(groups.keys())
    group_budgets = equal_split_quotas(len(sorted_keys), budget)
    plan: Dict[str, int] = {}
    for gi, key in enumerate(sorted_keys):
        gfiles = sorted(groups[key])
        fq = equal_split_quotas(len(gfiles), group_budgets[gi])
        for i, fp in enumerate(gfiles):
            plan[fp] = fq[i]
    return plan
