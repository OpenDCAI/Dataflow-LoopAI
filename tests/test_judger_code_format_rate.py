#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""code 评测的「产出是不是合法 Python」统计测试。

背景：`filter_code` 找不到 ```python 代码块时会打一行 ``logger.error``，但它
**不必然**意味着这条样本失败 —— 整段就是裸代码的话，下游 ``add_import`` 会把
prompt 前缀接回去、照样执行通过。

所以早期想统计「围栏率」是错的：**没有任何地方要求模型用围栏**，而没围栏的
绝大多数本来就能跑。有意义的指标是 ``invalid_code_rate`` —— 产出压根不是合法
Python 的比例。它不预设任何格式要求，围栏也好裸代码也好，能 parse 就算数。

它回答的是「低分到底是哪一种」：

    pass@1 = 0.3
    ├─ invalid_code_rate = 60%  → 模型没交出合法代码，先查 prompt / 截断
    └─ invalid_code_rate = 2%   → 代码都合法，是逻辑写错（能力问题）

口径只能靠**异常类型**判定，不能靠消息字符串 —— 见
``test_error_type_cannot_be_inferred_from_message``。
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

_FENCED = "Here you go:\n```python\ndef f():\n    return 1\n```\nHope this helps"
_BARE = "def f():\n    return 1"
_PROSE = "The solution is simple:\n\ndef f():\n    return 1\n\nThat should work."


# ---------------------------------------------------------------------------
# filter_code
# ---------------------------------------------------------------------------

def test_fenced_code_is_extracted_and_flagged():
    code, fenced = ex.filter_code(_FENCED)

    assert fenced is True
    assert code == "def f():\n    return 1"


def test_bare_code_returns_whole_string_and_is_not_flagged():
    code, fenced = ex.filter_code(_BARE)

    assert fenced is False
    assert code == _BARE      # 原样返回，由 add_import 负责接 prefix


def test_takes_the_last_fence():
    text = "```python\ndef bad(): pass\n```\n修正后：\n```python\ndef f():\n    return 1\n```"

    code, fenced = ex.filter_code(text)

    assert fenced is True
    assert code == "def f():\n    return 1"


# ---------------------------------------------------------------------------
# 关键事实：没有围栏 != 失败
# ---------------------------------------------------------------------------

def _problem() -> dict:
    return {"task_id": "x", "prompt": "def f():\n", "entry_point": "f",
            "test_list": ["assert f() == 1"]}


def test_bare_code_passes_despite_no_fence():
    """这条是重点：没围栏的**裸代码**照样能跑通 —— 所以不能拿它当失败信号。"""
    r = ex.check_correctness(_problem(), _BARE, timeout=5.0)

    assert r["passed"] is True
    assert r["has_python_fence"] is False


def test_fenced_code_passes():
    r = ex.check_correctness(_problem(), _FENCED, timeout=5.0)

    assert r["passed"] is True
    assert r["has_python_fence"] is True


def test_prose_wrapped_code_fails():
    """真正会失败的是这种：围栏外的散文被当成代码 exec。"""
    r = ex.check_correctness(_problem(), _PROSE, timeout=5.0)

    assert r["passed"] is False
    assert r["has_python_fence"] is False
    assert "invalid syntax" in r["result"]


# ---------------------------------------------------------------------------
# 为什么必须记异常类型，而不是解析消息字符串
# ---------------------------------------------------------------------------

def test_assertion_failure_leaves_an_empty_message():
    """要害：逻辑错那条路上，消息字符串**什么都不剩**。"""
    r = ex.check_correctness(_problem(), "def f():\n    return 999\n", timeout=5.0)

    assert r["passed"] is False
    assert r["result"] == "failed: "          # AssertionError 的 str() 是空的
    assert r["error_type"] == "AssertionError"
    assert r["syntax_error"] is False


def test_indentation_error_counts_as_invalid_code():
    """IndentationError 是 SyntaxError 的子类，同属「不是合法 Python」。"""
    r = ex.check_correctness(
        _problem(), "def f():\n    return 1\n      return 2\n", timeout=5.0)

    assert r["error_type"] == "IndentationError"
    assert r["syntax_error"] is True


def test_runtime_error_is_not_invalid_code():
    """NameError 的代码是**合法**的，只是有 bug —— 不算 invalid_code。"""
    r = ex.check_correctness(
        _problem(), "def f():\n    return math.sqrt(9)\n", timeout=5.0)

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
    _FENCED,                                                     # 过
    _BARE,                                                       # 过（无围栏）
    _PROSE,                                                      # 语法错 → 计入 invalid_code
    "def f():\n    return math.sqrt(9)\n",                       # NameError → 不计入
)


def _one_task_suite(tmp_path):
    """铺一个最小的 code 评测现场：1 题 × 4 样本。"""
    task_id, bench = "t1", "mbpp"
    base = tmp_path / task_id / "judger" / "v1" / bench
    base.mkdir(parents=True, exist_ok=True)

    problem_path = tmp_path / "problems.jsonl"
    problem_path.write_text(json.dumps(_problem()) + "\n", encoding="utf-8")
    (base / f"{bench}_sample.jsonl").write_text(
        "".join(json.dumps({"task_id": "x", "completion": c}, ensure_ascii=False) + "\n"
                for c in _SAMPLES), encoding="utf-8")

    state = {"task_id": task_id, "output_dir": str(tmp_path), "judger": {
        "eval_problem_path": str(problem_path), "bench_name": bench,
        "eval_case_num": len(_SAMPLES), "eval_task_type": "code"}}
    return state, base


def test_invalid_code_rate_counts_only_syntax_errors(tmp_path):
    state, _ = _one_task_suite(tmp_path)

    result = eval_mod.run_evaluate_code(state, _Writer())

    # 4 条里只有 1 条（散文）不是合法 Python；NameError 那条不算
    assert result["invalid_code_rate"] == pytest.approx(25.0)

    # pass@1 是 2/4（围栏 + 裸代码过）。
    # 注意单位：code 路径的 pass@k 是**小数**，和 math 路径的百分比不同。
    assert result["pass_at_k"]["pass@1"] == pytest.approx(0.5)
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
    assert set(by_type) == {None, "NameError", "SyntaxError"}
    assert by_type["SyntaxError"][0]["syntax_error"] is True
    assert by_type["NameError"][0]["syntax_error"] is False
    # 围栏标记仍然作为诊断细节留着：1 条带围栏，3 条没有
    assert sorted(r["has_python_fence"] for r in rows) == [False, False, False, True]
