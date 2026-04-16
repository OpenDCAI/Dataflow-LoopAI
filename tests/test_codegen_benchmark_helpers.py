"""单元测试：code-gen benchmark 辅助函数（无真实 LLM 调用）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# CI / 最小环境可能未安装 tree-sitter 语言包；导入 data_filter_tools 前注入 stub。
_TREE_SITTER_STUBS = (
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_java",
    "tree_sitter_cpp",
    "tree_sitter_c",
    "tree_sitter_c_sharp",
    "tree_sitter_go",
    "tree_sitter_rust",
    "tree_sitter_ruby",
    "tree_sitter_swift",
    "tree_sitter_kotlin",
    "tree_sitter_scala",
    "tree_sitter_bash",
    "tree_sitter_html",
    "tree_sitter_css",
    "tree_sitter_json",
    "tree_sitter_yaml",
    "tree_sitter_markdown",
)
for _ts in _TREE_SITTER_STUBS:
    sys.modules.setdefault(_ts, MagicMock())
if "tree_sitter" not in sys.modules:
    _ts_mod = MagicMock()
    sys.modules["tree_sitter"] = _ts_mod

from loopai.agents.Constructor.tools.data_filter_tools import (
    _ensure_record_has_system,
    _merge_record_identity,
    load_benchmark_raw_for_codegen,
    parse_codegen_phase_b_response,
)


class TestLoadBenchmarkRaw(unittest.TestCase):
    def test_benchmark_samples_extracts_sample_record(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "benchmark_samples.jsonl")
            row = {
                "benchmark_name": "HumanEval",
                "sample_record": {"task_id": "HumanEval/0", "prompt": "def foo(): pass"},
            }
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            state = {"constructor": {"benchmark_samples_path": p}}
            bundle = load_benchmark_raw_for_codegen(state)
            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle["source"], "benchmark_samples")
            self.assertEqual(bundle["benchmark_name"], "HumanEval")
            self.assertEqual(bundle["raw"]["task_id"], "HumanEval/0")

    def test_fallback_banckmark_jsonl_file(self):
        with tempfile.TemporaryDirectory() as td:
            jp = os.path.join(td, "humaneval.jsonl")
            rec = {"task_id": "x", "prompt": "p"}
            with open(jp, "w", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            state = {"constructor": {}, "banckmark_jsonl_path": jp}
            bundle = load_benchmark_raw_for_codegen(state)
            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle["source"], "banckmark_jsonl_path")
            self.assertEqual(bundle["raw"]["task_id"], "x")


class TestParsePhaseB(unittest.TestCase):
    def test_accept_explicit(self):
        self.assertEqual(
            parse_codegen_phase_b_response(
                {"accept": True, "record": {"dataset_type": "sft", "messages": []}}
            ),
            "accept",
        )

    def test_accept_implicit_record_only(self):
        self.assertEqual(
            parse_codegen_phase_b_response(
                {"record": {"dataset_type": "sft", "messages": [{"role": "user", "content": "hi"}]}}
            ),
            "accept",
        )

    def test_reject_accept_false(self):
        self.assertEqual(
            parse_codegen_phase_b_response(
                {
                    "accept": False,
                    "reject_mapping": True,
                    "reasoning": "unrelated",
                }
            ),
            "reject",
        )

    def test_reject_flag_only(self):
        self.assertEqual(
            parse_codegen_phase_b_response(
                {"reject_mapping": True, "reasoning": "x"}
            ),
            "reject",
        )

    def test_parse_error(self):
        self.assertEqual(parse_codegen_phase_b_response({"foo": 1}), "parse_error")
        self.assertEqual(parse_codegen_phase_b_response({}), "parse_error")


class TestEnsureSystem(unittest.TestCase):
    def test_inserts_when_first_is_user(self):
        rec = {"messages": [{"role": "user", "content": "hi"}]}
        _ensure_record_has_system(rec, "python codegen")
        self.assertEqual(rec["messages"][0]["role"], "system")
        self.assertIn("python codegen", rec["messages"][0]["content"])
        self.assertEqual(rec["messages"][1]["role"], "user")

    def test_fills_empty_system(self):
        rec = {"messages": [{"role": "system", "content": "  "}]}
        _ensure_record_has_system(rec, "")
        self.assertTrue(len(rec["messages"][0]["content"].strip()) > 0)


class TestMergeRecordIdentity(unittest.TestCase):
    def test_preserves_id_from_source(self):
        tgt = {"dataset_type": "sft", "messages": []}
        src = {"id": "keep-me", "meta": {"a": 1}}
        _merge_record_identity(tgt, src)
        self.assertEqual(tgt["id"], "keep-me")
        self.assertEqual(tgt["meta"]["a"], 1)

    def test_does_not_overwrite_existing_id(self):
        tgt = {"id": "new", "messages": []}
        src = {"id": "old"}
        _merge_record_identity(tgt, src)
        self.assertEqual(tgt["id"], "new")


if __name__ == "__main__":
    unittest.main()
