#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Judger bench entry 校验与 problem_path 报错的单元测试。

覆盖两处修复：
1. bench entry 结构校验（_collect_bench_problems / _validate_bench），
   特别是 task_type 拼错不能被静默接受。
2. _step_validate 把「problem_path 未配置」和「问题文件不存在」分开报，
   不再统一伪装成 missing required fields。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger import runner


# ---------------------------------------------------------------------------
# _collect_bench_problems
# ---------------------------------------------------------------------------

def test_accepts_valid_math_bench():
    bench = {"name": "aime26", "task_type": "math", "problem_path": "/data/aime26.jsonl"}

    assert runner._collect_bench_problems(bench) == []


def test_reports_missing_required_fields():
    problems = runner._collect_bench_problems({"name": "aime26"})

    assert any("task_type" in p for p in problems)
    assert any("problem_path" in p for p in problems)


def test_rejects_blank_string_fields():
    problems = runner._collect_bench_problems(
        {"name": "  ", "task_type": "math", "problem_path": "  "})

    assert any("name" in p for p in problems)
    assert any("problem_path" in p for p in problems)


def test_rejects_unknown_task_type_instead_of_silently_falling_back_to_code():
    problems = runner._collect_bench_problems(
        {"name": "aime26", "task_type": "maths", "problem_path": "/data/a.jsonl"})

    assert len(problems) == 1
    assert "maths" in problems[0]
    assert "math" in problems[0]


@pytest.mark.parametrize(
    "bench, expected_field",
    [
        ({"name": "bird", "task_type": "text2sql", "problem_path": "p.jsonl"},
         "text2sql_dir"),
        ({"name": "gsm8k", "task_type": "general_text", "problem_path": "p.jsonl"},
         "eval_type"),
    ],
)
def test_requires_task_type_specific_fields(bench, expected_field):
    problems = runner._collect_bench_problems(bench)

    assert any(expected_field in p for p in problems)


def test_rejects_non_dict_entry():
    problems = runner._collect_bench_problems(["not", "a", "dict"])

    assert len(problems) == 1
    assert "list" in problems[0]


def test_problem_path_is_only_checked_on_demand(tmp_path):
    bench = {
        "name": "aime26",
        "task_type": "math",
        "problem_path": str(tmp_path / "missing.jsonl"),
    }

    # 结构校验不碰文件系统，dry-run 才能在数据缺席时跑通
    assert runner._collect_bench_problems(bench) == []

    problems = runner._collect_bench_problems(bench, check_problem_path=True)
    assert any("不存在" in p for p in problems)


def test_problem_path_check_passes_when_file_exists(tmp_path):
    dataset = tmp_path / "aime26.jsonl"
    dataset.write_text('{"problem": "1+1", "answer": "2"}\n', encoding="utf-8")
    bench = {"name": "aime26", "task_type": "math", "problem_path": str(dataset)}

    assert runner._collect_bench_problems(bench, check_problem_path=True) == []


# ---------------------------------------------------------------------------
# _validate_bench
# ---------------------------------------------------------------------------

def test_validate_bench_accepts_valid_entry():
    runner._validate_bench(
        {"name": "aime26", "task_type": "math", "problem_path": "/data/aime26.jsonl"})


def test_validate_bench_exits_on_invalid_entry(capsys):
    with pytest.raises(SystemExit):
        runner._validate_bench({"name": "aime26"})

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CONFIG_ERROR"


# ---------------------------------------------------------------------------
# _step_validate 的 problem_path 报错
# ---------------------------------------------------------------------------

def _math_state(tmp_path, problem_path: str) -> dict:
    return {
        "task_id": "task-1",
        "output_dir": str(tmp_path),
        "judger": {
            "eval_task_type": "math",
            "eval_temperature": 1.0,
            "eval_top_p": 0.95,
            "eval_case_num": 2,
            "eval_max_tokens": 1024,
            "eval_model_path": "/models/Qwen3-8B",
            "bench_name": "aime26",
            "eval_problem_path": problem_path,
        },
    }


def test_missing_problem_file_reports_file_error_not_missing_field(tmp_path, capsys):
    state = _math_state(tmp_path, str(tmp_path / "nope.jsonl"))

    with pytest.raises(SystemExit):
        runner._step_validate(state, writer=lambda event: None)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    # 关键：报的是「文件不存在」，而不是「缺 eval_problem_path 字段」
    assert "不存在" in payload["message"]
    assert str(tmp_path / "nope.jsonl") in json.dumps(payload, ensure_ascii=False)
    assert "eval_problem_path" not in json.dumps(payload["error"].get("detail") or "")


def test_unset_problem_path_still_reports_missing_field(tmp_path, capsys):
    state = _math_state(tmp_path, "")

    with pytest.raises(SystemExit):
        runner._step_validate(state, writer=lambda event: None)

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "CONFIG_ERROR"
    assert "eval_problem_path" in json.dumps(payload["error"]["detail"], ensure_ascii=False)


# ---------------------------------------------------------------------------
# math 数据集的 JSONL 校验
#
# jsonl 分支曾经用惰性生成器读取 handle，而 with 退出时文件已关闭，
# 导致任何 .jsonl 格式的 math 数据集都会以 "I/O operation on closed file" 失败。
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suffix", [".jsonl", ".json"])
def test_math_dataset_validation_accepts_valid_file(tmp_path, suffix):
    dataset = tmp_path / f"aime26{suffix}"
    row = {"problem": "1+1", "answer": "2"}
    payload = json.dumps(row) + "\n" if suffix == ".jsonl" else json.dumps([row])
    dataset.write_text(payload, encoding="utf-8")

    state = _math_state(tmp_path, str(dataset))

    assert runner._step_validate(state, writer=lambda event: None) is state


def test_math_jsonl_validation_rejects_rows_without_answer(tmp_path, capsys):
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text(json.dumps({"problem": "1+1"}) + "\n", encoding="utf-8")
    state = _math_state(tmp_path, str(dataset))

    with pytest.raises(SystemExit):
        runner._step_validate(state, writer=lambda event: None)

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_INPUT"
