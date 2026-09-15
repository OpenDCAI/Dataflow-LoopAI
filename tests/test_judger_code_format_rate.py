#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""code 评测的「失败原因分类」测试。

低分时要能分清是哪种：

    pass@1 = 0.3
    ├─ invalid_code_rate = 60%  → 模型没交出合法代码，先查 prompt / 截断
    └─ invalid_code_rate = 2%   → 代码都合法，是逻辑写错（能力问题）

口径**只能靠异常类型判定，不能靠消息字符串** —— 见下面那几个用例：
`IndentationError` 和 `SyntaxError` 都算「不是合法 Python」，而 `NameError`
不算（代码合法，只是有 bug）；更要命的是 `AssertionError` 的消息是**空字符串**，
想从 `result` 字段反推类型根本走不通。

代码提取本身在 `sanitize` 步骤（见 test_judger_sanitize.py），这里的输入已经是
提取好的 `solution`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger.utils import evaluate as eval_mod
from loopai.skills.Judger.utils import execution as ex

_OK = "def f():\n    return 1\n"
_BAD_SYNTAX = "def f():\n    return 1\n      return 2\n"   # IndentationError
_NAME_ERROR = "def f():\n    return math.sqrt(9)\n"       # 代码合法，只是有 bug
_WRONG = "def f():\n    return 999\n"                      # AssertionError


def _problem() -> dict:
    return {"task_id": "x", "prompt": "def f():\n", "entry_point": "f",
            "test_list": ["assert f() == 1"]}


# ---------------------------------------------------------------------------
# 为什么必须记异常类型，而不是解析消息字符串
# ---------------------------------------------------------------------------

def test_passing_code_has_no_error_type():
    r = ex.check_correctness(_problem(), _OK, timeout=5.0)

    assert r["passed"] is True
    assert r["error_type"] is None
    assert r["syntax_error"] is False


def test_assertion_failure_leaves_an_empty_message():
    """要害：逻辑错那条路上，消息字符串**什么都不剩**。"""
    r = ex.check_correctness(_problem(), _WRONG, timeout=5.0)

    assert r["passed"] is False
    assert r["result"] == "failed: "          # AssertionError 的 str() 是空的
    assert r["error_type"] == "AssertionError"
    assert r["syntax_error"] is False


def test_indentation_error_counts_as_invalid_code():
    """IndentationError 是 SyntaxError 的子类，同属「不是合法 Python」。"""
    r = ex.check_correctness(_problem(), _BAD_SYNTAX, timeout=5.0)

    assert r["error_type"] == "IndentationError"
    assert r["syntax_error"] is True


def test_runtime_error_is_not_invalid_code():
    """NameError 的代码是**合法**的，只是有 bug —— 不算 invalid_code。"""
    r = ex.check_correctness(_problem(), _NAME_ERROR, timeout=5.0)

    assert r["passed"] is False
    assert r["error_type"] == "NameError"
    assert r["syntax_error"] is False


# ---------------------------------------------------------------------------
# 汇总进 invalid_code_rate
# ---------------------------------------------------------------------------

class _Writer:
    version_id = "v1"

    def __call__(self, event):
        pass


# 4 条样本，覆盖四种结局
_SAMPLES = (
    (_OK, "as_is"),
    (_WRONG, "as_is"),            # 逻辑错
    (_BAD_SYNTAX, "from_entry_point"),   # 语法错 → 计入 invalid_code
    (_NAME_ERROR, "as_is"),       # NameError → 不计入
)


def _one_task_suite(tmp_path):
    """铺一个最小的 code 评测现场：1 题 × 4 样本（已是 sanitize 后的形态）。"""
    task_id, bench = "t1", "mbpp"
    base = tmp_path / task_id / "judger" / "v1" / bench
    base.mkdir(parents=True, exist_ok=True)

    problem_path = tmp_path / "problems.jsonl"
    problem_path.write_text(json.dumps(_problem()) + "\n", encoding="utf-8")
    # completion 是 sanitize 步骤留下的**原始输出**。这里给非空值 —— 空的话会
    # 触发 _check_eval_output_health 的"模型没生成出东西"告警（那个检查本身是对的）。
    (base / f"{bench}_sanitized.jsonl").write_text(
        "".join(json.dumps({"task_id": "x", "completion": s, "solution": s,
                            "extract_method": m, "dropped_statements": 0,
                            "extract_ok": True}, ensure_ascii=False) + "\n"
                for s, m in _SAMPLES), encoding="utf-8")

    state = {"task_id": task_id, "output_dir": str(tmp_path), "judger": {
        "eval_problem_path": str(problem_path), "bench_name": bench,
        "eval_case_num": len(_SAMPLES), "eval_task_type": "code"}}
    return state, base


def test_invalid_code_rate_counts_only_syntax_errors(tmp_path):
    state, _ = _one_task_suite(tmp_path)

    result = eval_mod.run_evaluate_code(state, _Writer())

    # 4 条里只有 1 条（缩进错）不是合法 Python；NameError 那条不算
    assert result["invalid_code_rate"] == pytest.approx(25.0)

    # pass@1 是 1/4（只有第一条过）。
    # 注意单位：code 路径的 pass@k 是**小数**，和 math 路径的百分比不同。
    assert result["pass_at_k"]["pass@1"] == pytest.approx(0.25)
    assert set(result["pass_at_k"]) == {"pass@1"}   # 样本数不够的 k 不出现


def test_per_sample_error_type_is_written_to_result_file(tmp_path):
    state, _ = _one_task_suite(tmp_path)

    result = eval_mod.run_evaluate_code(state, _Writer())

    rows = [json.loads(line) for line in
            Path(result["result_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]

    by_type = {}
    for row in rows:
        by_type.setdefault(row["error_type"], []).append(row)

    assert by_type[None][0]["passed"] is True          # 通过的没有 error_type
    assert set(by_type) == {None, "IndentationError", "NameError", "AssertionError"}
    assert by_type["IndentationError"][0]["syntax_error"] is True
    assert by_type["NameError"][0]["syntax_error"] is False
    # sanitize 的元信息原样带过来，方便对比"提取前 vs 提取后"
    assert rows[0]["extract_method"] in {"as_is", "from_entry_point"}
