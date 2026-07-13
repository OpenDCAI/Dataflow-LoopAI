# -*- coding: utf-8 -*-
# llmaj.py — promptless, reusable LLMaJ core (code & text2sql ready)
from __future__ import annotations
import json, re
from typing import Dict, Any, Optional, Callable

# ===== 通用枚举 =====
DEFAULT_STAGES = {
    "syntax","import","name","type","value","index","key",
    "recursion","assert","timeout","perf","other",
    # for SQL
    "sql_syntax","sql_schema","sql_type","sql_timeout","sql_perf"
}

def _truncate(s: str, n: int) -> str:
    """
    将字符串截断到指定长度，并在末尾追加标记

    Args:
        s: 原始字符串
        n: 最大保留长度

    Returns:
        截断后的字符串；如果 s 为 None，返回空串
    """
    if s is None: return ""
    return s if len(s) <= n else s[:n] + "\n...[truncated]"

def _safe_json(s: Any) -> Optional[Dict[str, Any]]:
    """
    尝试从字符串或字典中解析出合法 JSON 对象

    支持以下输入形式：
    - 已经是 dict：直接返回
    - 字符串：自动去掉 ```json ... ``` 包裹，并从中提取最后一个 {...} 结构尝试解析

    Args:
        s: 待解析对象（dict 或 str）

    Returns:
        解析成功返回 dict；否则返回 None
    """
    if isinstance(s, dict):
        return s
    if not isinstance(s, str):
        return None
    t = s.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I|re.M).strip()
    for cand in (t, re.search(r"\{[\s\S]*\}\s*$", t or "") and re.search(r"\{[\s\S]*\}\s*$", t).group(0)):
        try:
            if cand: return json.loads(cand)
        except Exception:
            pass
    return None

def _valid(obj: Any, stages: set) -> bool:
    """
    检查模型返回的 JSON 结构是否符合基础要求

    规则：
    - 必须是 dict
    - 必须包含 stage、reason、evidence 字段
    - stage 在允许的阶段集合中
    - evidence 必须为 dict

    Args:
        obj: 待校验对象
        stages: 允许的阶段集合

    Returns:
        是否为合法结构
    """
    return (
        isinstance(obj, dict) and
        obj.get("stage") in stages and
        isinstance(obj.get("reason"), str) and
        isinstance(obj.get("evidence"), dict)
    )

# ---------- code 侧的可统计因子 ----------
def _infer_problem_type(prompt: str) -> str:
    """
    基于题面 prompt 的关键词，粗略推断问题类型

    Args:
        prompt: 题目描述文本

    Returns:
        归一化的问题类型标签，如 "string" / "list/array" / "math" / "general" 等
    """
    p = (prompt or "").lower()
    rules = [
        ("string", ["string","palindrome","anagram"]),
        ("list/array", ["list","array"]),
        ("math", ["prime","gcd","lcm","factorial","divisors","math"]),
        ("dp", ["dynamic program","dp","subproblem"]),
        ("greedy", ["greedy"]),
        ("tree", ["tree","bst","traversal"]),
        ("graph", ["graph","bfs","dfs","dijkstra","topo"]),
        ("matrix", ["matrix"]),
        ("hash/map", ["hash","dictionary","map"]),
        ("two-pointers", ["two pointers"]),
        ("sorting", ["sort","sorted","sorting"]),
    ]
    for name, ks in rules:
        if any(k in p for k in ks): return name
    return "general"

def _code_stats(code: str) -> Dict[str, Any]:
    """
    对代码片段做静态统计，提取结构复杂度相关特征

    特征包括：
    - 行数、token 数、函数定义数
    - 循环/分支/异常块数量
    - 近似圈复杂度（loops + branches + try + 1）
    - 是否存在递归调用

    Args:
        code: 代码字符串

    Returns:
        包含多种统计指标的字典
    """
    import ast
    code = code or ""
    n_lines = code.count("\n") + 1 if code else 0
    n_tokens = len(re.findall(r"\w+|\S", code))
    n_defs   = len(re.findall(r"^\s*def\s+\w+\(", code, flags=re.M))
    n_loops  = len(re.findall(r"\bfor\b|\bwhile\b", code))
    n_branch = len(re.findall(r"\bif\b|\belif\b|\bcase\b", code))
    n_try    = len(re.findall(r"\btry\b|\bexcept\b|\bfinally\b", code))
    cyclo = 1 + n_loops + n_branch + n_try
    rec = False
    try:
        tree = ast.parse(code)
        class V(ast.NodeVisitor):
            def __init__(self): self.stack=[]; self.hit=False
            def visit_FunctionDef(self,n): self.stack.append(n.name); self.generic_visit(n); self.stack.pop()
            def visit_Call(self,n):
                if isinstance(n.func, ast.Name) and self.stack and n.func.id==self.stack[-1]: self.hit=True
                self.generic_visit(n)
        v=V(); v.visit(tree); rec=v.hit
    except Exception: pass
    return {"lines": n_lines,"tokens": n_tokens,"func_defs": n_defs,
            "loops": n_loops,"branches": n_branch,"try_blocks": n_try,
            "approx_complexity": cyclo,"possible_recursion": rec}

def _extract_io_diff(stdout_tail: str, err_text: str) -> Dict[str, Any]:
    """
    从 stdout / err 文本中，启发式抽取断言失败的 I/O 差异信息

    尝试识别以下内容：
    - failing_expr: 实际断言表达式
    - expected: 期望值
    - got: 实际值（如 where ... = xxx）

    Args:
        stdout_tail: 评测标准输出尾部
        err_text: 错误输出文本

    Returns:
        包含 failing_expr / expected / got 的字典，均已截断到安全长度
    """
    text = (stdout_tail or "") + "\n" + (err_text or "")
    failing = expected = got = None
    m = re.search(r"E\s+assert\s+(.*)\s*==\s*(.*)", text) or re.search(r"AssertionError[: ]\s*assert\s+(.*)\s*==\s*(.*)", text)
    if m: failing, expected = m.group(1).strip(), m.group(2).strip()
    m2 = re.search(r"E\s+\+\s+where\s+.*=\s*(.*)", text)
    if m2: got = m2.group(1).strip()
    if not got and failing and "None" in failing: got = "None"
    return {"failing_expr": _truncate(failing, 300),"expected": _truncate(expected, 300),"got": _truncate(got, 300)}

# ---------- SQL 侧的轻量统计 ----------
def _sql_stats(query: str) -> Dict[str, Any]:
    """
    对 SQL 查询做轻量统计，用于附加诊断信息

    特征包括：
    - 长度、SELECT 数量、JOIN 数量
    - 子查询数量、聚合函数、GROUP BY / ORDER BY / LIMIT 等使用情况

    Args:
        query: SQL 查询字符串

    Returns:
        包含 SQL 结构统计的字典
    """
    q = (query or "").lower()
    return {
        "length": len(q),
        "selects": q.count("select"),
        "joins": q.count(" join "),
        "subqueries": q.count("(select"),
        "aggregations": sum(q.count(k) for k in [" count("," sum("," avg("," min("," max("]),
        "group_by": q.count(" group by "),
        "order_by": q.count(" order by "),
        "limits": q.count(" limit "),
    }

# ---------- 启发式（不依赖 prompt） ----------
def _heuristic_code(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    仅依赖日志内容，对 code 题进行启发式错误归因

    优先级顺序大致为：
    - timeout / SyntaxError / ImportError
    - NameError / TypeError / AssertionError / ValueError / ZeroDivisionError
    - IndexError / KeyError / RecursionError
    - 兜底的 other

    Args:
        ev: 证据信息字典，期望包含 stderr_head / err_text / stdout_tail 等字段

    Returns:
        基于规则的初步判因结果 dict
    """
    text = f'{ev.get("stderr_head","")}\n{ev.get("err_text","")}\n{ev.get("stdout_tail","")}'
    def has(s): return s in text
    stage, exc, reason, file_line, assert_msg = "other","", "运行失败（未知错误），请看 evidence", "", ""
    if "TIMEOUT" in text or "timed out" in text or "TimeoutExpired" in text:
        return _pack("timeout","Timeout","执行超时", ev)
    if has("SyntaxError") or has("IndentationError"):
        m = re.search(r'File "([^"]+)", line (\d+)', text); 
        file_line = f"{m.group(1)}:{m.group(2)}" if m else ""
        return _pack("syntax","SyntaxError","语法/缩进错误"+(f"（{file_line}）" if file_line else ""), ev, file_line=file_line)
    if has("ModuleNotFoundError") or has("ImportError"):
        m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", text)
        return _pack("import","ImportError", f"导入失败：缺少模块 {m.group(1)}" if m else "导入失败", ev)
    if has("NameError"):     return _pack("name","NameError","未定义名称", ev)
    if has("TypeError"):     return _pack("type","TypeError","类型不匹配/参数不正确", ev)
    if has("AssertionError"):
        m = re.search(r"AssertionError[: ](.*)", text); assert_msg = m.group(1).strip() if m else ""
        return _pack("assert","AssertionError","断言失败"+(f"；{assert_msg}" if assert_msg else ""), ev, assert_msg=assert_msg)
    if has("ValueError"):    return _pack("value","ValueError","取值非法", ev)
    if has("ZeroDivisionError"): return _pack("value","ZeroDivisionError","除零错误", ev)
    if has("IndexError"):    return _pack("index","IndexError","下标越界", ev)
    if has("KeyError"):      return _pack("key","KeyError","键不存在", ev)
    if has("RecursionError"):return _pack("recursion","RecursionError","递归过深/无终止条件", ev)
    return _pack("other","",reason, ev, score=0.3)

def _heuristic_sql(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    仅依赖错误信息，对 SQL / text2sql 任务做启发式错误分类

    主要类别：
    - sql_syntax: 语法错误
    - sql_schema: 表/列不存在等 schema 问题
    - sql_type: 类型不匹配
    - sql_timeout: 执行超时
    - sql_perf: 性能问题（如全表扫描/内存不足）
    - other: 未知原因

    Args:
        ev: 证据信息字典

    Returns:
        初步 SQL 判因结果 dict
    """
    text = f'{ev.get("stderr_head","")}\n{ev.get("err_text","")}\n{ev.get("stdout_tail","")}'.lower()
    def has(*keys): return any(k in text for k in keys)
    if has("syntax error","parse error","near"):
        return _pack("sql_syntax","SQLSyntaxError","SQL 语法错误", ev)
    if has("no such table","relation does not exist","table not found","column does not exist","unknown column"):
        return _pack("sql_schema","SQLSchemaError","库/表/列不存在", ev)
    if has("type mismatch","cannot cast","invalid input syntax for"):
        return _pack("sql_type","SQLTypeError","类型不匹配/转换失败", ev)
    if has("timeout","canceling statement due to statement timeout"):
        return _pack("sql_timeout","SQLTimeout","查询超时/索引缺失", ev)
    if has("out of memory","too many rows","full table scan"):
        return _pack("sql_perf","SQLPerf","性能问题（全表扫描/行数过大）", ev)
    return _pack("other","", "SQL 执行失败（未知原因）", ev, score=0.3)

def _pack(stage: str, exc: str, reason: str, ev: Dict[str, Any], *, file_line: str="", assert_msg: str="", score: float=0.0) -> Dict[str, Any]:
    """
    将核心判因信息打包为统一结构

    Args:
        stage: 错误阶段标签
        exc: 异常类型字符串
        reason: 人类可读的简要原因描述
        ev: 原始 evidence 字典
        file_line: 可选，文件名+行号信息
        assert_msg: 可选，断言失败信息
        score: 规则信心分（0~1 之间，主要用于融合）

    Returns:
        标准化的判因结果字典
    """
    return {
        "stage": stage, "exception_type": exc or "", "reason": reason,
        "evidence": {
            "stderr_head": _truncate(ev.get("stderr_head",""), 600),
            "stdout_tail": _truncate(ev.get("stdout_tail",""), 400),
            "file_line": file_line, "assert_msg": assert_msg,
        },
        "score": round(float(score), 4),
    }

def _quick_advice(stage: str) -> str:
    """
    根据错误阶段快速给出一句中文建议

    Args:
        stage: 错误阶段标签

    Returns:
        简短的中文建议句子
    """
    m = {
        "assert":"检查返回值与题意，完善边界条件",
        "syntax":"修复语法/缩进错误后重试",
        "timeout":"优化复杂度或避免死循环",
        "name":"补齐未定义变量或导入",
        "type":"核对参数与类型转换",
        "sql_syntax":"修正 SQL 语法（括号/别名/聚合）",
        "sql_schema":"核对库表/列名与数据源映射",
        "sql_type":"调整类型转换/CAST",
        "sql_perf":"增加索引或重写查询以减少全表扫描",
        "sql_timeout":"添加限流/索引或拆分查询"
    }
    return m.get(stage, "对照日志逐步定位问题")

# =============== 主类（无 prompt，任务可切换）================
class LLMJudge:
    """
    一个“没有内置 prompt”的轻量 Judge 核心。

    特点：
    - 不依赖具体 prompt，只消费“模型 JSON 输出 + OJ 证据”
    - 支持 task='code' 与 task='sql'（text2sql）
    - 当模型输出结构非法或缺失时，会自动回退到纯规则判因
    - 内部附带 code_stats / sql_stats 等统计信息，便于后续分析
    """
    def __init__(self, task: str = "code", stages: Optional[set] = None):
        """
        初始化 Judge

        Args:
            task: 任务类型，"code" 或 "sql"
            stages: 允许的错误阶段集合；默认为 DEFAULT_STAGES
        """
        self.task = task  # "code" | "sql"
        self.stages = stages or DEFAULT_STAGES

    def analyze(self,
                evidence: Dict[str, Any],
                *,
                model_result: Optional[Dict[str, Any] | str] = None) -> Dict[str, Any]:
        """
        综合“规则启发式 + 模型 JSON 输出”给出最终判因结果

        处理流程：
        1. 根据 task 类型生成 rule-based 基线结果（code/sql 走不同分支）
        2. 尝试从 model_result 解析出合法 JSON
        3. 如果模型结果结构合法且 stage 非 "other"，则作为主导并与规则结果融合
        4. 否则仅补充模型 factors / advice / tags 等信息

        Args:
            evidence: OJ/执行日志证据信息（包含 stderr_head / stdout_tail / err_text 等字段）
            model_result: 可选，来自 LLM 的判因 JSON（dict 或 str）

        Returns:
            标准化后的判因结果 dict，包含：
            - stage / exception_type / reason / evidence / factors / advice / tags
            - 以及透传的 task_id / sample_index / passed / raw_model_output 等
        """
        if self.task == "sql":
            base = _heuristic_sql(evidence)
            # SQL 的 factors（轻量统计）
            base["factors"] = {"sql_stats": _sql_stats(evidence.get("query","") or evidence.get("completion_head",""))}
        else:
            base = _heuristic_code(evidence)
            base["factors"] = {
                "problem_type": _infer_problem_type(evidence.get("prompt_head","")),
                "code_stats": _code_stats(evidence.get("completion_head","")),
                "io_diff": _extract_io_diff(evidence.get("stdout_tail",""), evidence.get("err_text","")),
            }

        model_obj = None
        raw = None
        if model_result is not None:
            raw = model_result if isinstance(model_result, str) else json.dumps(model_result, ensure_ascii=False)
            cand = _safe_json(model_result)
            if _valid(cand, self.stages):
                model_obj = cand

        out = self._merge(base, model_obj)

        out.setdefault("task_id", evidence.get("task_id"))
        out.setdefault("sample_index", evidence.get("sample_index"))
        out.setdefault("passed", False)
        if raw is not None:
            out["raw_model_output"] = _truncate(raw, 2000)
        if "advice" not in out:
            out["advice"] = _quick_advice(out.get("stage","other"))
        if "tags" not in out:
            out["tags"] = self._auto_tags(out)
        return out

    def _merge(self, rule_obj: Dict[str, Any], model_obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        将规则结果与模型 JSON 结果进行融合

        融合策略：
        - 若模型结果合法且 stage 在允许集合且非 "other"，以模型结果为主：
          * 非 evidence / factors 字段优先采用模型值
          * evidence / factors 做字段级 merge（模型覆盖规则）
        - 否则，仅把模型的 factors / advice / tags 视为增量信息附加到规则结果上

        Args:
            rule_obj: 规则启发式生成的判因结果
            model_obj: 解析出的模型判因 JSON，可能为 None

        Returns:
            融合后的判因结果 dict
        """
        if model_obj is None:
            return rule_obj
        if model_obj.get("stage") in self.stages and model_obj.get("stage") != "other":
            merged = dict(rule_obj)
            for k,v in model_obj.items():
                if k not in {"evidence","factors"}:
                    merged[k] = v
            evd = dict(rule_obj.get("evidence", {})); evd.update(model_obj.get("evidence", {}) or {})
            fac = dict(rule_obj.get("factors", {}));  fac.update(model_obj.get("factors", {}) or {})
            merged["evidence"], merged["factors"] = evd, fac
            return merged
  
        out = dict(rule_obj)
        fac = dict(out.get("factors", {})); fac.update((model_obj or {}).get("factors", {}) or {})
        out["factors"] = fac
        if model_obj and model_obj.get("advice"): out["advice"] = model_obj["advice"]
        if model_obj and isinstance(model_obj.get("tags"), list) and model_obj["tags"]:
            out["tags"] = list(dict.fromkeys((out.get("tags") or []) + model_obj["tags"]))
        return out

    def _auto_tags(self, obj: Dict[str, Any]) -> list:
        """
        在缺少模型 tags 时，基于 stage / 统计特征自动生成一组标签

        规则示例：
        - code 任务：根据 problem_type / 复杂度 / 是否返回 None 等打标签
        - sql 任务：根据 joins / subqueries 等打标签

        Args:
            obj: 当前判因结果 dict

        Returns:
            去重后的标签列表
        """
        tags = [obj.get("stage","other")]
        if self.task == "sql":
            s = (obj.get("factors") or {}).get("sql_stats") or {}
            if s.get("joins",0) >= 3: tags.append("many-joins")
            if s.get("subqueries",0) >= 2: tags.append("nested-subqueries")
        else:
            pt = ((obj.get("factors") or {}).get("problem_type")) or "general"
            tags.append(pt)
            io = ((obj.get("factors") or {}).get("io_diff")) or {}
            if (io.get("got") == "None") or ("None" in (io.get("failing_expr") or "")):
                tags.append("returns-none")
            stats = ((obj.get("factors") or {}).get("code_stats")) or {}
            if stats.get("loops",0) > 3 or stats.get("approx_complexity",0) >= 6:
                tags.append("high-complexity")
        return list(dict.fromkeys(tags))