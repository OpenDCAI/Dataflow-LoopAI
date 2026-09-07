# -*- coding: utf-8 -*-
"""Regression tests for Math LLMaJ reliability (parse / cache / confidence)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loopai.skills.Analyzer.nodes import math_llmaj_label_node as m
from loopai.skills.Analyzer.eval_metrics.metrics.runner import MetricRunner
from loopai.skills.Analyzer.math_llmaj_quality import MATH_TAG_DESCRIPTIONS


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        if self.calls >= len(self.responses):
            raise RuntimeError("no more fake responses")
        content = self.responses[self.calls]
        self.calls += 1
        if isinstance(content, Exception):
            raise content
        resp = MagicMock()
        resp.content = content
        resp.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        return resp


def test_parse_single_array_response():
    """Prompt asks for JSON array; single-item path must accept array."""
    content = '[{"case_id": 7, "tags": ["计算错误"], "reason": "算错", "confidence": 0.9, "domain": "algebra"}]'
    by_id, missing = m._parse_batch_response(content, [7])
    assert missing == []
    assert by_id[7]["tags"] == ["计算错误"]
    assert by_id[7]["origin_source"] == "llm"


def test_parse_single_object_still_accepted():
    content = '{"case_id": 3, "tags": ["化简错误"], "reason": "变形错", "confidence": 0.8, "domain": "algebra"}'
    by_id, missing = m._parse_batch_response(content, [3])
    assert missing == []
    assert by_id[3]["tags"] == ["化简错误"]


def test_parse_critique_then_overall_tag_contract():
    content = (
        '[{"case_id": 8, "first_error_step": "2+2=5", '
        '"evidence_quote": "2+2=5", '
        '"short_critique": "解答在基础加法处算错，导致最终答案错误。", '
        '"overall_error_tag": "计算错误", "confidence": 0.91, '
        '"process_status": "substantive", "domain": "arithmetic"}]'
    )
    by_id, missing = m._parse_batch_response(content, [8])
    assert missing == []
    assert by_id[8]["short_critique"] == "解答在基础加法处算错，导致最终答案错误。"
    assert by_id[8]["overall_error_tag"] == "计算错误"
    assert by_id[8]["tags"] == ["计算错误"]


def test_positive_audit_without_tag_becomes_metric_anomaly():
    content = (
        '[{"case_id": 12, "short_critique": '
        '"模型推导及最终答案与参考答案等价，失败来自评测匹配问题。", '
        '"confidence": 0.95, "process_status": "substantive"}]'
    )
    by_id, missing = m._parse_batch_response(content, [12])
    assert missing == []
    assert by_id[12]["overall_error_tag"] == "评测异常"


def test_ambiguous_empty_tag_is_retried_instead_of_published():
    content = '[{"case_id": 13, "short_critique": "无法判断。", "confidence": 0.2}]'
    by_id, missing = m._parse_batch_response(content, [13])
    assert by_id == {}
    assert missing == [13]


def test_parse_repairs_unescaped_latex_backslashes():
    content = r'''[{"case_id": 9, "process_status": "substantive",
        "first_error_step": "x=\frac{1}{2}",
        "evidence_quote": "x=\frac{1}{2}, y=\sqrt{2}",
        "short_critique": "首次化简错误。",
        "overall_error_tag": "化简错误", "confidence": 0.9,
        "domain": "algebra"}]'''
    by_id, missing = m._parse_batch_response(content, [9])
    assert missing == []
    assert by_id[9]["tags"] == ["化简错误"]
    assert by_id[9]["evidence_quote"] == r"x=\frac{1}{2}, y=\sqrt{2}"


def test_overall_tag_wins_and_stays_singular():
    label = m._normalize_label_obj(
        {
            "overall_error_tag": "公式使用错误或遗漏",
            "tags": ["计算错误", "化简错误"],
            "short_critique": "解答遗漏公式成立所需条件，后续推导不成立。",
            "confidence": 0.9,
            "evidence_quote": "use the formula",
        }
    )
    assert label["overall_error_tag"] == "公式使用错误或遗漏"
    assert label["tags"] == ["公式使用错误或遗漏"]


def test_prompt_describes_every_allowed_error_tag():
    prompt = m._build_batch_label_prompt(
        [{"idx": 1, "question": "q", "target": "1", "prediction": "p"}]
    )
    for tag in m.MATH_ERROR_TAG_WHITELIST:
        assert f"- {tag}: {MATH_TAG_DESCRIPTIONS[tag]}" in prompt
    assert "short_critique" in prompt
    assert "overall_error_tag" in prompt


def test_batch_missing_and_exception_isolated():
    """Missing case is retried as single; single exception does not abort node."""
    llm = _FakeLLM(
        [
            # batch: only case 1, missing 2
            '[{"case_id": 1, "tags": ["计算错误"], "reason": "a", "confidence": 0.9, "domain": "algebra"}]',
            # single retry for case 2: raise
            RuntimeError("boom"),
            # second retry for case 2: ok
            '[{"case_id": 2, "tags": ["答题步骤不完整"], "reason": "b", "confidence": 0.85, "domain": "geometry"}]',
        ]
    )
    stats = m.LlmCallStats()
    batch = [
        {"idx": 1, "question": "q1", "target": "1", "prediction": "p1"},
        {"idx": 2, "question": "q2", "target": "2", "prediction": "p2"},
    ]
    results = m._invoke_label_batch(
        llm,
        batch,
        stats,
        max_retries_per_item=2,
        max_batch_retries=0,
    )
    assert results[1]["tags"] == ["计算错误"]
    assert results[2]["tags"] == ["答题步骤不完整"]
    assert stats.requests == 3
    assert stats.errors >= 1
    assert stats.retries >= 1


def test_low_confidence_cold_hot_consistent(tmp_path: Path):
    """Low-confidence labels keep their bucket but use whole-case construction."""
    pred = (
        "Step 1: Let S = sum. Then S = 10/81.\n"
        "Step 2: Multiply by 81 to clear denominator.\n"
        "Step 3: Final arithmetic 2+2=5. The answer is 5."
    )
    label = {
        "tags": ["计算错误"],
        "reason": "不确定",
        "confidence": 0.4,
        "domain": "algebra",
        "label_source": "llm",
        "origin_source": "llm",
        "process_status": "substantive",
        "first_error_step": "2+2=5",
        "evidence_quote": "2+2=5",
    }
    record = {"id": 1, "question": "求值", "target": "4", "generated_ans": pred}
    cold = m._attach_judge(record, label)
    assert cold["judge"]["tags"] == ["计算错误"]
    assert cold["judge"]["confidence_cleared"] is False
    assert cold["judge"]["origin_source"] == "llm"
    assert cold["judge"]["actionable"] is True
    assert cold["judge"]["construction_scope"] == "whole_case"

    cache_path = tmp_path / "math_llmaj_label_cache.json"
    m._save_label_cache(cache_path, {"k1": label})
    loaded = m._load_label_cache(cache_path)
    hot_label = dict(loaded["k1"])
    hot_label["cache_hit"] = True
    hot_label["origin_source"] = hot_label.get("origin_source") or "llm"
    hot_label["label_source"] = "cache"
    hot = m._attach_judge(record, hot_label)
    assert hot["judge"]["tags"] == cold["judge"]["tags"] == ["计算错误"]
    assert hot["judge"]["cache_hit"] is True
    assert hot["judge"]["origin_source"] == "llm"
    action = m._build_obtainer_action(hot)
    assert action is not None
    assert action["construction_scope"] == "whole_case"


def test_cache_key_invalidates_on_prediction_tail_change():
    fp = "cfg_fp_test"
    base = {
        "sample_id": 9,
        "question": "求值",
        "target": "42",
        "prediction": "A" * 2000 + "TAIL_OLD",
        "extracted": "41",
        "match_type": "none",
    }
    k1 = m._cache_key(base, fp)
    changed = dict(base)
    changed["prediction"] = "A" * 2000 + "TAIL_NEW"
    k2 = m._cache_key(changed, fp)
    assert k1 != k2


def test_cache_key_includes_question_and_schema():
    fp = "cfg_fp_test"
    a = {
        "sample_id": 1,
        "question": "题A",
        "target": "1",
        "prediction": "ans",
        "extracted": "1",
        "match_type": "none",
    }
    b = dict(a)
    b["question"] = "题B"
    assert m._cache_key(a, fp) != m._cache_key(b, fp)


def test_metric_runner_max_workers_one_skips_process_pool(monkeypatch):
    runner = MetricRunner(max_workers=1)
    called = {"parallel_pool": False, "direct": False}

    def fake_fn(preds, refs, **kwargs):
        called["direct"] = True
        return {"score": 1.0, "details": [1.0] * len(preds)}

    class BoomPool:
        def __init__(self, *args, **kwargs):
            called["parallel_pool"] = True
            raise AssertionError("ProcessPoolExecutor should not be used when max_workers==1")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "loopai.skills.Analyzer.eval_metrics.metrics.runner.concurrent.futures.ProcessPoolExecutor",
        BoomPool,
    )
    preds = list(range(150))
    refs = list(range(150))
    out = runner._run_metric_parallel(fake_fn, preds, refs, {})
    assert called["direct"] is True
    assert called["parallel_pool"] is False
    assert out["score"] == 1.0


def test_pack_batches_respects_item_and_token_caps():
    items = []
    for i in range(6):
        items.append(
            {
                "idx": i,
                "question": "题" * 200,
                "target": "1",
                "prediction": "解" * 800,
                "extracted": "0",
                "match_type": "none",
            }
        )
    # Tiny token budget forces smaller packs than max_items=4
    packs = m._pack_batches(items, max_items=4, max_input_tokens=1200)
    assert all(len(p) <= 4 for p in packs)
    assert sum(len(p) for p in packs) == 6
    assert len(packs) >= 2


def test_output_token_budget_scales_with_batch():
    assert m._output_token_budget(1, 96) >= 96
    assert m._output_token_budget(4, 96) > m._output_token_budget(1, 96)
