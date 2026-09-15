# -*- coding: utf-8 -*-
"""从模型输出里提取可执行的 Python 代码。

**为什么不用 markdown 围栏正则**：围栏不是任何地方定下的约束，只是"模型恰好
这么写"。而「先给函数定义、再给 Example Usage」是最常见的输出形状之一 ——
两块都用 ```python 包着，无脑取最后一块就会拿到**用法示例**（只有调用、没有
定义），拼进测试程序后报 `NameError: name 'chkList' is not defined`。
线上 task 201 就是这个形态：模型函数完全正确，被判错。

改成语法驱动，三步：

1. 整段能 ``ast.parse`` → 直接用（base 模型续写、或模型只给了代码）
2. 否则从 ``def <entry_point>`` 那一行往后长，取最长的合法前缀
3. 再丢掉顶层的非定义语句（示例调用、自测、print），只留 import / 定义 / 赋值

第 3 步很关键：模型常在函数后面跟一段"示例/自测"，那些是**顶层表达式语句**，
exec 时真的会跑；里面若带 assert 且恰好写错，整条样本就被判错 —— 而错不在解答。
"""

from __future__ import annotations

import ast
import re
from typing import Optional, Tuple

# 顶层保留的节点类型。**用允许列表而不是禁止列表**：顶层任何"会执行的东西"
# （裸调用、if __name__ == "__main__"、for 循环）一律丢掉，只留声明。
_KEEP_NODE_TYPES = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Assign,      # 顶层常量，比如 MOD = 10**9
    ast.AnnAssign,
)

# 提取方式，写进样本方便事后统计
METHOD_AS_IS = "as_is"                    # 整段就是合法 Python
METHOD_FROM_ENTRY_POINT = "from_entry_point"   # 从 def <entry_point> 往后长的
METHOD_COMPLETION = "completion"          # 回复里没有 def，接上题目签名才提出来
METHOD_RAW = "raw"                        # 都没成，原样返回


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except (SyntaxError, MemoryError, ValueError):
        return False


def _definition_lines(lines, entry_point: str) -> list:
    pattern = re.compile(rf"^\s*def\s+{re.escape(entry_point)}\s*\(")
    return [index for index, line in enumerate(lines) if pattern.match(line)]


def _grow_from(lines, start: int) -> Optional[str]:
    """从 ``start`` 行开始，往后取最长的合法片段，再往前尽量多带。

    往后：不 break —— 函数体是逐行"补全"的，中间会短暂合法（比如 def + docstring
    就已经能解析），要取能一直走到最后的那个。

    往前：**import 通常写在 def 上面**，只往后长会把它漏在外面，模型写法明明
    没问题也会 NameError（线上 task 209：`import heapq as hq` 在 `def
    heap_replace` 上方，提取出来 `hq` 未定义）。往前一直吃到句法不成立为止，
    顺手也能把函数依赖的其它定义带进来。
    """
    end = None
    for candidate_end in range(start + 1, len(lines) + 1):
        if _parses("\n".join(lines[start:candidate_end])):
            end = candidate_end
    if end is None:
        return None

    begin = start
    while begin > 0 and _parses("\n".join(lines[begin - 1:end])):
        begin -= 1

    return "\n".join(lines[begin:end])


def extract_code(text: str, entry_point: Optional[str] = None) -> Tuple[str, str]:
    """取出代码，返回 ``(code, method)``。

    从 ``def <entry_point>`` 往后长（而不是暴力枚举所有行区间）有两个原因：
    一是知道要哪个函数，锚点更准；二是复杂度从 O(n²) 降到 O(n) 次 ``ast.parse``
    —— 长回复（带 thinking 的模型轻松上千行）用 O(n²) 会直接卡死。

    锚点取**最后一个** ``def <entry_point>``：模型有时先给一版、再说"修正一下"
    给第二版，取第一个会拿到错的那版。
    """
    if _parses(text):
        return text, METHOD_AS_IS

    lines = text.split("\n")
    if entry_point:
        spans = [_grow_from(lines, start) for start in _definition_lines(lines, entry_point)]
        valid = [span for span in spans if span]
        if valid:
            return valid[-1], METHOD_FROM_ENTRY_POINT

    return text, METHOD_RAW


def keep_definitions(code: str) -> Tuple[str, int]:
    """丢掉顶层非定义语句，返回 ``(code, dropped_count)``。

    ``dropped_count`` 是丢掉的顶层语句数，落进样本里 —— 它大于 0 就说明模型
    确实写了"示例/自测"这类会被执行的东西。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, 0

    kept = [node for node in tree.body if isinstance(node, _KEEP_NODE_TYPES)]
    if not kept:
        # 全是裸语句（比如模型只回了一段 print），保留原样让下游报错，
        # 总比返回空字符串、连错误都看不出来强
        return code, 0

    segments = [ast.get_source_segment(code, node) for node in kept]
    body = "\n".join(segment for segment in segments if segment)
    return body + "\n", len(tree.body) - len(kept)


def prompt_import_prefix(prompt: str) -> str:
    """题目 prompt 里第一个 ``def``/``class`` 之前的内容 —— 通常就是 import 段。

    模型不一定把题目顶部的 import 抄一遍，所以拼测试程序时要补上。
    只取到第一个 ``def`` 之前，是为了**避免把题目里的函数占位符带进来**
    （那会变成 `def f(): \\"\\"\\"doc\\"\\"\\"`，锚点就落到它上面了）。
    """
    lines = []
    for line in (prompt or "").split("\n"):
        if re.match(r"^\s*(def|class)\s", line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _retry_with_prompt(prompt: str, text: str, entry_point: str) -> Tuple[str, str]:
    """把题目的签名接回模型回复前面，再提取一次。

    成功的话返回 ``(code, METHOD_COMPLETION)``，否则原样返回 ``(text, METHOD_RAW)``。
    单独给一个 method 名是为了能统计：这个数高，说明模型在写「续写」而不是
    「完整解答」，prompt 的形态和模型的预期对不上。
    """
    code, method = extract_code(f"{prompt}\n{text}", entry_point)
    if method == METHOD_RAW:
        return text, METHOD_RAW

    # 守卫：题目的 prompt 本身就是个能解析的**空壳函数**，光提取它也会"成功"。
    # 模型什么都没写（只回了句"我不会"）时，提到的东西和单独提取 prompt 一模一样
    # —— 那不算提取到了模型的代码，别记成 completion 假装成功。
    stub, _ = extract_code(prompt, entry_point)
    if code.strip() == stub.strip():
        return text, METHOD_RAW

    return code, METHOD_COMPLETION


def sanitize(
    text: str,
    entry_point: Optional[str] = None,
    prompt: Optional[str] = None,
) -> dict:
    """完整流程：补 import → 取代码 → 丢非定义语句。

    返回 ``{solution, extract_method, dropped_statements, extract_ok}``。
    """
    source = text
    prefix = prompt_import_prefix(prompt) if prompt else ""
    if prefix:
        source = f"{prefix}\n\n{text}"

    code, method = extract_code(source, entry_point)

    if method == METHOD_RAW and entry_point and prompt:
        # 题目的 prompt 是个「半成品函数」（签名 + docstring，没有函数体）。
        # 模型按补全的直觉**只回函数体**时，回复里根本没有 `def <entry_point>`，
        # 拿它去 exec 就是一段悬空的缩进代码 → IndentationError。
        # 这是 HumanEval 系列的标准约定：完整解答 = prompt + completion。
        code, method = _retry_with_prompt(prompt, text, entry_point)

    code, dropped = keep_definitions(code)

    return {
        "solution": code.strip(),
        "extract_method": method,
        "dropped_statements": dropped,
        "extract_ok": bool(code.strip()),
    }
