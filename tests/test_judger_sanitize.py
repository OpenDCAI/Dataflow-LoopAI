#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""code 评测的代码提取（sanitize）测试。

为什么不用 markdown 围栏正则：围栏不是任何地方定下的约束，只是"模型恰好这么
写"。而「先给函数定义、再给 Example Usage」是最常见的输出形状之一 —— 两块都用
```python 包着，**取最后一块就会拿到用法示例**（只有调用、没有定义），拼进测试
程序后报 `NameError: name 'chkList' is not defined`。线上 task 201 就是这个形态：
模型函数完全正确，被判错。

现在的做法是语法驱动：先看整段能不能 parse，不行就从 `def <entry_point>` 往后长，
最后丢掉顶层的非定义语句（模型的"示例/自测"会真的被执行，里面写错的 assert
会把整条样本判错，而错不在解答本身）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger.utils import sanitize as st

_PROMPT = 'def chkList(lst):\n    """Write a python function to check ...\n    """'

# 线上 task 201 的原文：定义 + Example Usage，两块都是 ```python
_TASK_201 = """Here's a Python function to check whether all elements are the same:

```python
def chkList(lst):
    if not lst:
        return True
    first = lst[0]
    for item in lst:
        if item != first:
            return False
    return True
```

### Example Usage:

```python
print(chkList(['one', 'one']))  # True
print(chkList(['one', 'Two']))  # False
```
"""


def _sanitize(text, prompt=_PROMPT, entry_point="chkList"):
    return st.sanitize(text, entry_point=entry_point, prompt=prompt)


# ---------------------------------------------------------------------------
# 选对代码
# ---------------------------------------------------------------------------

def test_task_201_definition_wins_over_example_block():
    """这条以前判 NameError —— 提取拿到的是 Example Usage 那块。"""
    result = _sanitize(_TASK_201)

    assert result["solution"].startswith("def chkList(lst):")
    assert "print(" not in result["solution"]
    assert result["extract_ok"] is True


def test_pure_code_is_taken_as_is():
    """base 模型续写出来的就是纯代码 —— 直接过，不做任何改动。"""
    result = _sanitize("def chkList(lst):\n    return len(set(lst)) == 1\n")

    assert result["extract_method"] == st.METHOD_AS_IS
    assert result["solution"] == "def chkList(lst):\n    return len(set(lst)) == 1"


def test_corrected_version_wins():
    """先写错一版再给修正版时，从 entry_point 往后长会取到完整的最后那版。"""
    text = ("```python\ndef f():\n    return 0\n```\n"
            "修正后：\n```python\ndef f():\n    return 1\n```")

    result = st.sanitize(text, entry_point="f")

    assert "return 1" in result["solution"]


def test_falls_back_to_raw_when_entry_point_never_defined():
    """模型压根没定义入口函数 —— 标记成 raw，不要假装成功。"""
    result = _sanitize("I don't know how to do this.")

    assert result["extract_method"] == st.METHOD_RAW


def test_import_above_the_definition_is_kept():
    """import 通常写在 def **上面**，只往后长会把它漏在外面。

    线上 task 209（`import heapq as hq` 在 `def heap_replace` 上方）就是这样：
    模型写法完全正确，提取出来 `hq` 未定义 → NameError。
    """
    text = ("```python\nimport heapq as hq\n\n"
            "def heap_replace(heap, a):\n    hq.heappop(heap)\n    return heap\n```")

    result = st.sanitize(text, entry_point="heap_replace")

    assert "import heapq as hq" in result["solution"]
    assert result["solution"].index("import") < result["solution"].index("def ")


def test_definition_before_the_entry_point_is_pulled_in():
    """入口函数依赖的前置定义也要带进来，否则 NameError。"""
    text = ("def helper(x):\n    return x + 1\n\n"
            "def f(x):\n    return helper(x)\n")

    result = st.sanitize(text, entry_point="f")

    assert "def helper" in result["solution"]
    assert "def f" in result["solution"]


# ---------------------------------------------------------------------------
# 只回函数体（补全式 prompt 最自然的续写）
# ---------------------------------------------------------------------------

# 题目的 prompt 是「半成品函数」：签名 + docstring，没有函数体
_INCOMPLETE = ('def parallelogram_area(b,h):\n'
               '    """Write a function to caluclate area of a parallelogram.\n'
               '    """')


def test_body_only_reply_gets_the_prompt_signature_attached():
    """模型只回函数体时，回复里没有 `def <entry_point>`。

    直接拿它去 exec 就是一段悬空的缩进代码 → IndentationError。标准约定是
    「完整解答 = prompt + completion」，所以要接上题目的签名再提取。
    """
    result = st.sanitize("    area = b * h\n    return area\n",
                         entry_point="parallelogram_area", prompt=_INCOMPLETE)

    assert result["extract_method"] == st.METHOD_COMPLETION
    assert result["solution"].startswith("def parallelogram_area(b,h):")
    assert "area = b * h" in result["solution"]


def test_body_only_reply_with_trailing_prose():
    result = st.sanitize("    area = b * h\n    return area\n\n直接相乘即可。\n",
                         entry_point="parallelogram_area", prompt=_INCOMPLETE)

    assert result["extract_method"] == st.METHOD_COMPLETION
    assert "直接相乘即可" not in result["solution"]


def test_full_definition_does_not_trigger_the_completion_fallback():
    """模型给了完整 def 时走正常路径，不该被记成 completion。"""
    result = st.sanitize("def parallelogram_area(b,h):\n    return b * h\n",
                         entry_point="parallelogram_area", prompt=_INCOMPLETE)

    assert result["extract_method"] == st.METHOD_AS_IS


def test_body_only_without_prompt_stays_raw():
    """没有 prompt 可接，只能老实标 raw —— 不要假装成功。"""
    result = st.sanitize("    return 1\n", entry_point="f")

    assert result["extract_method"] == st.METHOD_RAW


# ---------------------------------------------------------------------------
# 丢掉顶层可执行语句
# ---------------------------------------------------------------------------

def test_drops_dunder_main_block():
    """模型的"自测"会被 exec 真的跑，里面断言写错就会误判整条样本。"""
    text = ("def chkList(lst):\n    return len(set(lst)) == 1\n\n"
            "if __name__ == '__main__':\n    print(chkList(['a', 'a']))\n")

    result = _sanitize(text)

    assert "__main__" not in result["solution"]
    assert result["dropped_statements"] == 1
    assert result["solution"].strip().startswith("def chkList")


def test_drops_bare_top_level_calls():
    text = "def f():\n    return 1\n\nprint(f())\nprint(f())\n"

    result = st.sanitize(text, entry_point="f")

    assert "print" not in result["solution"]
    assert result["dropped_statements"] == 2


def test_keeps_top_level_assignments():
    """顶层常量是函数会用到的，不能当"可执行语句"丢掉。"""
    text = "MOD = 10 ** 9\n\ndef f():\n    return MOD\n"

    result = st.sanitize(text, entry_point="f")

    assert "MOD" in result["solution"]


def test_drops_the_models_demo_driver_code():
    """线上 task 4/7：模型回的是"演示代码"，整段能解析，但不是解答。

        import heapq as hq
        nums = list(map(int, input().split()))   ← 读 stdin → OSError

    这些是**赋值语句**，光按"是不是赋值"判断挡不住。按可达性裁才拦得下来。
    """
    demo = ("import heapq as hq\n"
            "nums = list(map(int, input().split()))\n"
            "n = int(input())\n"
            "largest_n = [hq.heappop(nums) for i in range(n)]\n")

    result = st.sanitize(demo, entry_point="count_ways", prompt="def count_ways(n):\n    \"\"\"doc\"\"\"")

    assert result["extract_ok"] is False
    assert "input()" not in result["solution"]


def test_drops_definitions_the_entry_point_does_not_use():
    text = ("def unused_helper():\n    return 1\n\n"
            "def f(x):\n    return x\n")

    result = st.sanitize(text, entry_point="f")

    assert "unused_helper" not in result["solution"]
    assert "def f(x)" in result["solution"]


def test_extract_ok_is_false_for_a_bodyless_stub():
    """线上 task 3/6：提取"成功"了，但拿到的只是题目自己的占位符。

    `extract_method` 说的是**怎么**拿到的，`extract_ok` 说的是**拿到的是不是答案**
    —— 这两件事必须分开，否则"提到了空壳"会被记成一次成功的提取。
    """
    result = st.sanitize("def f():\n    \"\"\"doc\"\"\"\n\n我不会写。\n",
                         entry_point="f", prompt="def f():\n    \"\"\"doc\"\"\"")

    assert result["extract_ok"] is False


def test_extract_ok_is_true_when_the_body_is_there():
    result = st.sanitize("def f():\n    \"\"\"doc\"\"\"\n    return 1\n", entry_point="f")

    assert result["extract_ok"] is True


# ---------------------------------------------------------------------------
# 题目里的 import 段
# ---------------------------------------------------------------------------

def test_prompt_import_prefix_is_prepended_when_model_omits_it():
    prompt = "from typing import List\n\ndef f(x):\n    \"\"\"doc\"\"\"\n"

    result = st.sanitize("def f(x: List) -> bool:\n    return True\n",
                         entry_point="f", prompt=prompt)

    assert result["solution"].startswith("from typing import List")
    assert "def f(x: List)" in result["solution"]


def test_prompt_definition_stub_is_not_used_as_anchor():
    """题目里的 `def f(): \\"\\"\\"doc\\"\\"\\"` 是占位符，锚点必须落到模型的实现上。"""
    prompt = "def f():\n    \"\"\"doc\"\"\"\n"

    result = st.sanitize("def f():\n    return 42\n", entry_point="f", prompt=prompt)

    assert "return 42" in result["solution"]


@pytest.mark.parametrize("prompt", ["", None])
def test_no_prompt_is_fine(prompt):
    result = st.sanitize("def f():\n    return 1\n", entry_point="f", prompt=prompt)

    assert result["solution"] == "def f():\n    return 1"
