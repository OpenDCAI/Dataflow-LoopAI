"""单测：Constructor 预采样按逻辑数据集均分（无 LLM / 无 Constructor 重依赖）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loopai.common.jsonl_dataset_sampling import (
    cleaning_sampling_plan_by_dataset,
    equal_split_quotas,
    logical_dataset_stem_from_jsonl_basename,
)


class TestLogicalDatasetStem(unittest.TestCase):
    def test_strips_part_suffix(self):
        self.assertEqual(
            logical_dataset_stem_from_jsonl_basename("hf_datasets__foo_part_001.jsonl"),
            "hf_datasets__foo",
        )
        self.assertEqual(
            logical_dataset_stem_from_jsonl_basename("hf_datasets__foo_part_099.jsonl"),
            "hf_datasets__foo",
        )

    def test_no_suffix_unchanged(self):
        self.assertEqual(
            logical_dataset_stem_from_jsonl_basename("hf_datasets__foo.jsonl"),
            "hf_datasets__foo",
        )

    def test_non_three_digit_part_not_stripped(self):
        self.assertEqual(
            logical_dataset_stem_from_jsonl_basename("hf_datasets__foo_part_12.jsonl"),
            "hf_datasets__foo_part_12",
        )


class TestCleaningSamplingPlanByDataset(unittest.TestCase):
    def test_total_equals_budget(self):
        files = [
            "/data/hf_datasets__A.jsonl",
            "/data/hf_datasets__A_part_001.jsonl",
            "/data/hf_datasets__B.jsonl",
        ]
        for budget in (1, 7, 10, 100):
            plan = cleaning_sampling_plan_by_dataset(files, budget)
            self.assertEqual(sum(plan.values()), budget)
            self.assertEqual(set(plan.keys()), set(files))

    def test_groups_split_evenly_then_per_file(self):
        # 2 datasets: A (2 shards), B (1 file). budget=10 -> 5 per group -> A: 3+2, B: 5
        files = sorted(
            [
                "/d/hf_datasets__A.jsonl",
                "/d/hf_datasets__A_part_001.jsonl",
                "/d/hf_datasets__B.jsonl",
            ]
        )
        plan = cleaning_sampling_plan_by_dataset(files, 10)
        sum_a = plan["/d/hf_datasets__A.jsonl"] + plan["/d/hf_datasets__A_part_001.jsonl"]
        sum_b = plan["/d/hf_datasets__B.jsonl"]
        self.assertEqual(sum_a, 5)
        self.assertEqual(sum_b, 5)
        self.assertEqual(equal_split_quotas(2, 5), [3, 2])
        self.assertEqual(
            sorted([plan["/d/hf_datasets__A.jsonl"], plan["/d/hf_datasets__A_part_001.jsonl"]]),
            [2, 3],
        )

    def test_equal_split_quotas_contract(self):
        self.assertEqual(equal_split_quotas(3, 10), [4, 3, 3])
        self.assertEqual(sum(equal_split_quotas(5, 23)), 23)


if __name__ == "__main__":
    unittest.main()
