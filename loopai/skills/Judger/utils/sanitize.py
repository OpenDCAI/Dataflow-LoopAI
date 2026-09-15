# -*- coding: utf-8 -*-
"""从模型输出里提取可执行的 Python 代码。

**为什么不用 markdown 围栏正则**：围栏不是任何地方定下的约束，只是"模型恰好
这么写"。而「先给函数定义、再给 Example Usage」是最常见的输出形状之一 ——
两块都用 ```python 包着，无脑取最后一块就会拿到**用法示例**（只有调用、没有
定义），拼进测试程序后报 `NameError: name 'chkList' is not defined`。
线上 task 201 就是这个形态：模型函数完全正确，被判错。

改成语法驱动，四步：

1. 整段能 ``ast.parse`` **且确实定义了入口函数** → 直接用
2. 否则从 ``def <entry_point>`` 往后长取最长合法片段，再往前尽量多吃
3. 回复里压根没有入口函数时，退回 HumanEval 的标准约定 ``prompt + 回复`` 再试
4. 裁掉入口函数够不着的顶层语句

第 3 步是给"补全式续写"的：题目的 prompt 是半成品函数（签名 + docstring、没有
函数体），模型按直觉**只回函数体**时回复里没有 ``def``，直接 exec 就是一段悬空
的缩进代码。

第 4 步按**可达性**裁而不是按"是不是赋值语句"：模型的"演示代码"

    nums = list(map(int, input().split()))

是赋值语句，exec 时照样跑（读 stdin → OSError）；模型的"自测"里若带 assert
且恰好写错，整条样本还会被误判为答错。两种都必须拦掉，而它们的形式分别是
赋值和裸调用，只有可达性能一刀切干净。
"""

from __future__ import annotations

import ast
import re
import warnings
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


def _parse(code: str):
    """``ast.parse``，顺手吞掉 SyntaxWarning。

    模型很爱写 ``"\\w+"`` 这种**非 raw** 的正则字符串，Python 3.12 会为每一条报
    一次 SyntaxWarning。一次评测几万条样本，这堆警告能把真正的输出整个淹掉，
    而它既不影响解析结果也不影响执行（这个 escape 目前仍按字面传递）。真正要
    关心的是**模型代码写错**，不是它在警告里说的话。
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(code)


def _parses(code: str) -> bool:
    try:
        _parse(code)
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
    # 整段能解析就直接用 —— 但**前提是它真的定义了入口函数**。模型的"演示代码"
    # （顶层一堆赋值 + input()）也是合法 Python，整段收下会把 demo 当解答。
    if _parses(text) and (not entry_point or defines_entry_point_with_body(text, entry_point)):
        return text, METHOD_AS_IS

    lines = text.split("\n")
    if entry_point:
        spans = [_grow_from(lines, start) for start in _definition_lines(lines, entry_point)]
        valid = [span for span in spans if span]
        if valid:
            return valid[-1], METHOD_FROM_ENTRY_POINT

    return text, METHOD_RAW


def _assigned_names(node) -> set:
    """这条顶层语句**定义**出来的名字。"""
    names = set()
    if isinstance(node, ast.Assign):
        for target in node.targets:
            names |= {sub.id for sub in ast.walk(target) if isinstance(sub, ast.Name)}
    elif isinstance(node, ast.AnnAssign):
        names |= {sub.id for sub in ast.walk(node.target) if isinstance(sub, ast.Name)}
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(node.name)
    return names


def _used_names(node) -> set:
    """这条顶层语句**引用**到的名字。"""
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def keep_definitions(code: str, entry_point: Optional[str] = None) -> Tuple[str, int]:
    """只留入口函数真正需要的声明，返回 ``(code, dropped_count)``。

    顶层任何**会执行的东西**都要丢掉 —— 不光是裸调用和 ``__main__`` 块，还包括
    模型的"演示代码"。实测截了一段：

        import heapq as hq
        nums = list(map(int, input().split()))     ← 读 stdin
        n = int(input())

    这些是**赋值语句**，光按"是不是赋值"判断挡不住，exec 时照样跑，于是
    `OSError` 而不是判错。

    所以按**可达性**裁：从 ``entry_point`` 出发，只保留它（传递地）引用到的
    函数/类/常量。顶层 import 一律保留（它们没有名字，且留着无害）。
    """
    try:
        tree = _parse(code)
    except SyntaxError:
        return code, 0

    candidates = [node for node in tree.body if isinstance(node, _KEEP_NODE_TYPES)]
    if not candidates:
        return code, 0

    if entry_point:
        defined = {}
        for node in candidates:
            for name in _assigned_names(node):
                defined.setdefault(name, node)

        reachable = {entry_point}
        queue = [entry_point]
        while queue:
            node = defined.get(queue.pop())
            if node is None:
                continue
            for ref in _used_names(node):
                if ref in defined and ref not in reachable:
                    reachable.add(ref)
                    queue.append(ref)

        candidates = [
            node for node in candidates
            if isinstance(node, (ast.Import, ast.ImportFrom))
            or (_assigned_names(node) & reachable)
        ]

    if not candidates:
        return code, len(tree.body)

    segments = [ast.get_source_segment(code, node) for node in candidates]
    body = "\n".join(segment for segment in segments if segment)
    return body + "\n", len(tree.body) - len(candidates)


def defines_entry_point_with_body(code: str, entry_point: Optional[str]) -> bool:
    """提取出来的东西里，入口函数是不是**真有实现**（不是只有 docstring）。

    题目的 prompt 本身就是个能解析的空壳函数（签名 + docstring），所以"提取成功"
    这件事本身说明不了什么 —— 模型只回一句"我不会"也会提取出那个空壳。这个判据
    用来区分"真拿到了解答"和"拿到的只是题目占位符"。
    """
    if not entry_point:
        return bool(code.strip())
    try:
        tree = _parse(code)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == entry_point:
            body = node.body
            # 函数体只有一条 Expr（docstring）→ 空壳
            if len(body) == 1 and isinstance(body[0], ast.Expr):
                return False
            return True
    return False


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

    # 守卫：题目的 prompt 本身就是个能解析的**空壳函数**（签名 + docstring），
    # 接上它再提取必然"成功" —— 哪怕模型只回了一句"我不会"。要求提取结果里
    # 入口函数**真有实现**，否则不算提到了东西，别记成 completion 假装成功。
    # （不能拿"和单独提取 prompt 的结果比字符串"代替：顶层 import 会被无条件
    # 保留，两边永远不相等，线上 task 4/7 就是这么漏过去的。）
    if not defines_entry_point_with_body(code, entry_point):
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

    code, dropped = keep_definitions(code, entry_point)

    return {
        "solution": code.strip(),
        "extract_method": method,
        "dropped_statements": dropped,
        # 「提到的东西里，入口函数是不是真有实现」—— 区分"真拿到解答"和
        # "只拿到题目占位符"。extract_method 说的是**怎么**拿到的，
        # extract_ok 说的是**拿到的是不是答案**，两件事。
        "extract_ok": defines_entry_point_with_body(code, entry_point),
    }
