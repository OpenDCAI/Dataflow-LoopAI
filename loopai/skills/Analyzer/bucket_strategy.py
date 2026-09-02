from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Tuple


_CODE_BUCKET_META = {
    "code_output_contract": {
        "label": "代码补全输出契约",
        "severity": 1.20,
        "transfer": 1.25,
        "learnability_prior": 1.30,
        "cost": 0.90,
        "sample_direction": "函数签名/Docstring -> 仅输出完整可执行代码，不输出解释或 Markdown 围栏",
    },
    "code_syntax_completion": {
        "label": "Python 语法与补全完整性",
        "severity": 1.15,
        "transfer": 1.15,
        "learnability_prior": 1.15,
        "cost": 0.90,
        "sample_direction": "缩进、括号、字符串、return 与完整函数体的短小 Python 补全样本",
    },
    "code_interface_scope": {
        "label": "函数接口、作用域与依赖",
        "severity": 1.10,
        "transfer": 1.10,
        "learnability_prior": 1.00,
        "cost": 1.00,
        "sample_direction": "保持函数名和签名，补齐局部变量、标准库导入与辅助函数",
    },
    "code_semantic_logic": {
        "label": "语义逻辑与断言正确性",
        "severity": 1.20,
        "transfer": 1.15,
        "learnability_prior": 0.75,
        "cost": 1.15,
        "sample_direction": "可执行但答案错误的短算法题，配套正常样例与反例断言",
    },
    "code_boundary_robustness": {
        "label": "边界条件与鲁棒性",
        "severity": 1.05,
        "transfer": 1.10,
        "learnability_prior": 0.75,
        "cost": 1.10,
        "sample_direction": "空输入、单元素、重复值、极值和特殊字符等对比样本",
    },
    "code_runtime_efficiency": {
        "label": "运行时与效率",
        "severity": 0.95,
        "transfer": 0.85,
        "learnability_prior": 0.55,
        "cost": 1.30,
        "sample_direction": "超时、递归深度和复杂度退化的成对优化样本",
    },
}

_SQL_BUCKET_META = {
    "sql_output_contract": {
        "label": "SQL 输出契约",
        "severity": 1.15,
        "transfer": 1.20,
        "learnability_prior": 1.25,
        "cost": 0.90,
        "sample_direction": "问题与 Schema -> 仅输出可执行 SQL，不输出解释或 Markdown",
    },
    "sql_syntax": {
        "label": "SQL 语法与结构",
        "severity": 1.15,
        "transfer": 1.10,
        "learnability_prior": 1.10,
        "cost": 0.90,
        "sample_direction": "SELECT、JOIN、GROUP BY、子查询和聚合函数的短 SQL 修复样本",
    },
    "sql_schema_linking": {
        "label": "Schema Linking",
        "severity": 1.20,
        "transfer": 1.20,
        "learnability_prior": 0.85,
        "cost": 1.15,
        "sample_direction": "问题实体与表、列、外键的显式对齐及易混淆 Schema 对比样本",
    },
    "sql_semantic_logic": {
        "label": "SQL 语义与结果正确性",
        "severity": 1.20,
        "transfer": 1.10,
        "learnability_prior": 0.70,
        "cost": 1.20,
        "sample_direction": "可执行但结果错误的查询，覆盖过滤、聚合、排序与去重语义",
    },
    "sql_type_value": {
        "label": "类型、值与条件表达",
        "severity": 1.00,
        "transfer": 1.00,
        "learnability_prior": 0.90,
        "cost": 1.00,
        "sample_direction": "日期、数值、NULL、字符串匹配和类型转换的边界样本",
    },
    "sql_runtime_efficiency": {
        "label": "SQL 运行时与效率",
        "severity": 0.90,
        "transfer": 0.80,
        "learnability_prior": 0.50,
        "cost": 1.30,
        "sample_direction": "超时查询与等价高效查询的成对样本",
    },
}

_GENERAL_BUCKET_META = {
    "general_instruction_following": {
        "label": "指令与输出格式遵循",
        "severity": 1.20,
        "transfer": 1.25,
        "learnability_prior": 1.25,
        "cost": 0.85,
        "sample_direction": "覆盖格式、长度、结构和约束组合的指令遵循样本，答案需严格满足可验证要求",
    },
    "general_relevance_intent": {
        "label": "相关性与意图理解",
        "severity": 1.10,
        "transfer": 1.15,
        "learnability_prior": 1.05,
        "cost": 0.95,
        "sample_direction": "相似意图辨析、答非所问纠正和用户目标对齐的对比样本",
    },
    "general_factuality_grounding": {
        "label": "事实性与知识依据",
        "severity": 1.25,
        "transfer": 1.15,
        "learnability_prior": 0.75,
        "cost": 1.20,
        "sample_direction": "带可信来源或给定上下文的知识问答、幻觉纠正和事实核验样本",
    },
    "general_reasoning_consistency": {
        "label": "推理与一致性",
        "severity": 1.20,
        "transfer": 1.15,
        "learnability_prior": 0.65,
        "cost": 1.30,
        "sample_direction": "多步推理、因果判断、前后一致性检查和反例验证样本",
    },
    "general_completeness_coverage": {
        "label": "完整性与要点覆盖",
        "severity": 1.05,
        "transfer": 1.10,
        "learnability_prior": 1.00,
        "cost": 0.95,
        "sample_direction": "按评分要点覆盖关键信息、补全遗漏内容和避免回答截断的样本",
    },
    "general_language_quality": {
        "label": "表达与语言质量",
        "severity": 0.90,
        "transfer": 1.00,
        "learnability_prior": 1.15,
        "cost": 0.85,
        "sample_direction": "流畅性、连贯性、简洁性、语法和结构化表达的改写对比样本",
    },
    "general_safety_refusal": {
        "label": "安全与拒答边界",
        "severity": 1.30,
        "transfer": 1.10,
        "learnability_prior": 0.75,
        "cost": 1.20,
        "sample_direction": "合理拒答、不必要拒答和安全替代回答的边界对比样本",
    },
}

_MATH_BUCKET_META = {
    "math_output_contract": {
        "label": "答案提取与格式遵循",
        "severity": 1.10,
        "transfer": 1.20,
        "learnability_prior": 1.30,
        "cost": 0.80,
        "sample_direction": "题目与推理过程 -> 按要求输出可提取的最终答案，覆盖 boxed、数值、选项和多问格式",
    },
    "math_arithmetic_calculation": {
        "label": "基础计算与数值精度",
        "severity": 1.10,
        "transfer": 1.20,
        "learnability_prior": 1.20,
        "cost": 0.85,
        "sample_direction": "分数、小数、比例、符号、单位和代入计算的短步骤纠错与验算样本",
    },
    "math_algebra_symbolic": {
        "label": "代数与符号变换",
        "severity": 1.15,
        "transfer": 1.20,
        "learnability_prior": 0.90,
        "cost": 1.05,
        "sample_direction": "方程求解、展开、因式分解、恒等变形和符号化简的逐步等价变换样本",
    },
    "math_modeling": {
        "label": "题意理解与数学建模",
        "severity": 1.25,
        "transfer": 1.20,
        "learnability_prior": 0.70,
        "cost": 1.20,
        "sample_direction": "自然语言条件 -> 变量、方程、约束和目标函数的显式建模对比样本",
    },
    "math_strategy_theorem": {
        "label": "解题策略与定理选择",
        "severity": 1.25,
        "transfer": 1.15,
        "learnability_prior": 0.65,
        "cost": 1.30,
        "sample_direction": "同题多策略、公式或定理适用条件辨析，以及错误路线到正确路线的修正样本",
    },
    "math_reasoning_consistency": {
        "label": "多步推理与过程一致性",
        "severity": 1.20,
        "transfer": 1.20,
        "learnability_prior": 0.70,
        "cost": 1.20,
        "sample_direction": "逐步推导、局部结论校验、反例检查和答案与过程一致性训练样本",
    },
    "math_verification_completeness": {
        "label": "验证、约束与解答完整性",
        "severity": 1.05,
        "transfer": 1.10,
        "learnability_prior": 1.00,
        "cost": 0.95,
        "sample_direction": "定义域、边界条件、增根漏解、单位、证明闭合和多问完整作答样本",
    },
}

_GENERAL_METHOD_REFERENCES = [
    {
        "paper": "Holistic Evaluation of Language Models (HELM)",
        "venue": "TMLR 2023",
        "applied_to": "将通用文本质量拆成正确性、鲁棒性、安全性等可区分维度",
        "url": "https://arxiv.org/abs/2211.09110",
    },
    {
        "paper": "Training Language Models to Follow Instructions with Human Feedback",
        "venue": "NeurIPS 2022",
        "applied_to": "把用户意图与指令遵循作为独立能力，而非普通内容错误",
        "url": "https://proceedings.neurips.cc/paper_files/paper/2022/hash/b1efde53be364a73914f58805a001731-Abstract.html",
    },
    {
        "paper": "TruthfulQA: Measuring How Models Mimic Human Falsehoods",
        "venue": "ACL 2022",
        "applied_to": "把事实性和信息充分性从表面文本相似度中分离",
        "url": "https://aclanthology.org/2022.acl-long.229/",
    },
    {
        "paper": "Skill-It! A Data-Driven Skills Framework for Understanding and Training Language Models",
        "venue": "NeurIPS 2023",
        "applied_to": "处理能力先后依赖，并为被前置失败遮蔽的能力保留探索预算",
        "url": "https://proceedings.neurips.cc/paper_files/paper/2023/hash/70b8505ac79e3e131756f793cd80eb8d-Abstract-Conference.html",
    },
    {
        "paper": "DoReMi: Optimizing Data Mixtures Speeds Up Language Model Pretraining",
        "venue": "NeurIPS 2023",
        "applied_to": "不把观察频率直接等同于固定数据混合比例",
        "url": "https://proceedings.neurips.cc/paper_files/paper/2023/hash/dcba6be91359358c2355cd920da3fcbd-Abstract-Conference.html",
    },
    {
        "paper": "LESS: Selecting Influential Data for Targeted Instruction Tuning",
        "venue": "ICML 2024",
        "applied_to": "后续以小规模试训的目标能力收益替代静态学习效率先验",
        "url": "https://proceedings.mlr.press/v235/xia24c.html",
    },
]

_MATH_METHOD_REFERENCES = [
    {
        "paper": "Measuring Mathematical Problem Solving With the MATH Dataset",
        "venue": "NeurIPS 2021",
        "applied_to": "将数学主题作为二级领域标签，并保留完整解题过程用于错误定位",
        "url": "https://arxiv.org/abs/2103.03874",
    },
    {
        "paper": "Training Verifiers to Solve Math Word Problems",
        "venue": "arXiv 2021",
        "applied_to": "区分最终答案判定与过程能力诊断，优先使用可验证信号",
        "url": "https://arxiv.org/abs/2110.14168",
    },
    {
        "paper": "Let's Verify Step by Step",
        "venue": "ICLR 2024",
        "applied_to": "使用步骤级反馈定位局部推理错误，而非仅按最终答案失败分桶",
        "url": "https://arxiv.org/abs/2305.20050",
    },
    {
        "paper": "Skill-It! A Data-Driven Skills Framework for Understanding and Training Language Models",
        "venue": "NeurIPS 2023",
        "applied_to": "按可训练能力组织数据，并为被前置错误遮蔽的能力保留探索预算",
        "url": "https://proceedings.neurips.cc/paper_files/paper/2023/hash/70b8505ac79e3e131756f793cd80eb8d-Abstract-Conference.html",
    },
]

_UNKNOWN_BUCKET = "diagnostic_unknown"
_UNKNOWN_META = {
    "label": "待诊断样本",
    "severity": 0.0,
    "transfer": 0.0,
    "learnability_prior": 0.0,
    "cost": 1.0,
    "sample_direction": "补充执行日志或人工复核，不直接进入训练分桶",
}

_PROSE_PREFIX_RE = re.compile(
    r"^(?:to solve|let(?:'s| us)|here(?:'s| is| are)|the function|this function|"
    r"we need|in this|first[, ]|sure[, ]|certainly[, ]|below is|based on|"
    r"the provided|the task|the problem|an? (?:simple )?approach)",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return str(value)
    return str(value)


def _record_text(record: Dict[str, Any]) -> Tuple[str, str, str, str]:
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    completion = _text(
        record.get("completion")
        or record.get("prediction")
        or record.get("pred")
        or record.get("generated_text")
        or record.get("generated_ans")
        or record.get("response")
        or record.get("output")
        or record.get("model_output")
    ).strip()
    result = " ".join(
        _text(record.get(key))
        for key in ("result", "stdout", "stderr", "execution_result", "error")
        if record.get(key) is not None
    ).strip()
    tags = " ".join(_text(tag) for tag in (judge.get("tags") or []))
    judge_text = " ".join(
        _text(judge.get(key))
        for key in ("stage", "reason", "exception_type", "advice")
        if judge.get(key) is not None
    )
    return completion, result, tags, judge_text


def _normalize_task_type(task_type: Any) -> str:
    normalized = str(task_type or "code").strip().lower().replace("-", "_")
    if normalized in {"text2sql", "text_to_sql", "sql"}:
        return "text2sql"
    if normalized in {"code", "coding", "programming", "python"}:
        return "code"
    if normalized in {
        "math", "mathematics", "mathematical", "math_reasoning", "math_qa",
        "数学", "数学推理",
    }:
        return "math"
    return "general"


def _first_text(record: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return _text(value).strip()
    return ""


def _general_evidence(record: Dict[str, Any]) -> Tuple[str, str, str, str]:
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    detail = record.get("metric_detail") if isinstance(record.get("metric_detail"), dict) else {}
    question = _first_text(record, ("question", "prompt", "input", "query", "instruction"))
    reference = _first_text(record, (
        "target", "reference", "ground_truth", "answer", "correct_answer", "gold", "solution",
    ))
    completion, result, tags, judge_text = _record_text(record)
    top_level = " ".join(
        _text(record.get(key))
        for key in (
            "error_type", "errors", "reason", "feedback", "critique", "category",
            "match_type", "primary_metric", "failure_type",
        )
        if record.get(key) not in (None, "", [], {})
    )
    detail_text = " ".join(
        _text(detail.get(key))
        for key in ("match_type", "error_type", "reason", "feedback", "label", "category", "analysis")
        if detail.get(key) not in (None, "", [], {})
    )
    judge_extra = " ".join(
        _text(judge.get(key))
        for key in (
            "error_type", "primary_error", "secondary_error", "errors", "labels",
            "dimensions", "feedback", "critique",
        )
        if judge.get(key) not in (None, "", [], {})
    )
    evidence = " ".join((result, tags, judge_text, judge_extra, top_level, detail_text)).strip().lower()
    return question, reference, completion, evidence


def _structured_math_error_labels(record: Dict[str, Any]) -> List[str]:
    """Collect structured Math labels without treating free-form answers as labels."""
    labels: List[str] = []
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    detail = record.get("metric_detail") if isinstance(record.get("metric_detail"), dict) else {}

    def _append(value: Any) -> None:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                labels.append(stripped)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                _append(item)

    for container in (record, judge, detail):
        for key in (
            "error_type", "errors", "primary_error", "secondary_error", "labels",
            "failure_type",
        ):
            _append(container.get(key))

    # Judger predictions are preferred. Gold/manual step labels are only a
    # compatibility fallback for files that do not contain pred_steps.
    steps = record.get("pred_steps")
    if not isinstance(steps, list):
        steps = record.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            for key in ("errors", "error_type", "primary_error", "secondary_error", "labels"):
                _append(step.get(key))

    ignored = {
        "步骤正确", "正确", "无错误", "none", "no error", "passed", "pass", "matched",
    }
    return [label for label in labels if label.strip().lower() not in ignored]


def _math_label_bucket(label: str) -> str | None:
    normalized = label.strip().lower().replace("_", " ")
    mappings = (
        ("math_output_contract", (
            "输出格式", "答案格式", "格式错误", "无法提取", "提取失败", "未输出答案",
            "空答案", "answer format", "output format", "extraction", "missing answer",
        )),
        ("math_modeling", (
            "题意理解", "建模", "列式", "变量定义", "条件理解", "关系式错误",
            "problem understanding", "modeling", "equation setup", "translate the problem",
        )),
        ("math_algebra_symbolic", (
            "代数", "符号变换", "符号错误", "化简", "方程求解", "解方程", "展开错误",
            "因式分解", "等价变形", "algebra", "symbolic", "simplification",
            "equation solving", "factorization", "transformation",
        )),
        ("math_arithmetic_calculation", (
            "计算错误", "算术错误", "数值错误", "代入后算错", "代入错误", "小数错误",
            "分数错误", "正负号", "单位换算", "arithmetic", "calculation", "numeric error",
            "sign error", "rounding error",
        )),
        ("math_strategy_theorem", (
            "解题思路混乱", "公式使用错误或遗漏", "公式错误", "定理使用", "定理选择",
            "方法错误", "策略错误", "概念错误", "wrong formula", "wrong theorem",
            "wrong strategy", "conceptual error", "incorrect approach",
        )),
        ("math_reasoning_consistency", (
            "答案与过程不符", "推理错误", "逻辑错误", "前后矛盾", "结论不一致",
            "invalid inference", "reasoning error", "logical error", "contradiction",
            "answer process mismatch", "inconsistent",
        )),
        ("math_verification_completeness", (
            "答题步骤不完整", "步骤不完整", "遗漏步骤", "漏解", "增根", "定义域",
            "边界条件", "未验证", "证明不完整", "条件遗漏", "incomplete", "missing step",
            "extraneous root", "domain restriction", "boundary condition", "verification",
        )),
    )
    for bucket, tokens in mappings:
        if any(token in normalized for token in tokens):
            return bucket
    return None


def _math_evidence(record: Dict[str, Any]) -> Tuple[str, str, str, str, List[str]]:
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    detail = record.get("metric_detail") if isinstance(record.get("metric_detail"), dict) else {}
    metric_details = record.get("metric_details") if isinstance(record.get("metric_details"), dict) else {}
    question = _first_text(record, ("question", "prompt", "input", "query", "instruction"))
    reference = _first_text(record, (
        "target", "reference", "ground_truth", "answer", "correct_answer", "gold", "solution",
    ))
    completion, result, tags, judge_text = _record_text(record)
    if not completion:
        steps = record.get("steps") if isinstance(record.get("steps"), list) else []
        completion = "\n".join(
            _text(step.get("response")).strip()
            for step in steps
            if isinstance(step, dict) and _text(step.get("response")).strip()
        )
    labels = _structured_math_error_labels(record)
    top_level = " ".join(
        _text(record.get(key))
        for key in ("reason", "feedback", "critique", "analysis_reason", "failure_reason")
        if record.get(key) not in (None, "", [], {})
    )
    judge_extra = " ".join(
        _text(judge.get(key))
        for key in ("reason", "feedback", "critique", "advice")
        if judge.get(key) not in (None, "", [], {})
    )
    detail_text = " ".join(
        _text(detail.get(key))
        for key in ("match_type", "reason", "feedback", "analysis")
        if detail.get(key) not in (None, "", [], {})
    )
    metric_detail_text = " ".join(
        f"{name}:{_text(value)}" for name, value in metric_details.items()
    )
    evidence = " ".join(
        (result, tags, judge_text, judge_extra, top_level, detail_text, metric_detail_text, " ".join(labels))
    ).strip().lower()
    return question, reference, completion, evidence, labels


def _math_extraction_failed(record: Dict[str, Any], completion: str, evidence: str) -> bool:
    if not completion.strip():
        return True
    metric_details = record.get("metric_details") if isinstance(record.get("metric_details"), dict) else {}
    extraction_detail = metric_details.get("extraction_rate")
    if isinstance(extraction_detail, (int, float)) and float(extraction_detail) == 0.0:
        return True
    if isinstance(extraction_detail, dict) and float(extraction_detail.get("score", 0.0)) == 0.0:
        return True
    return _contains_any(evidence, (
        "extraction failed", "answer extraction failed", "无法提取答案", "答案提取失败", "未输出最终答案",
    ))


def _infer_math_domain(record: Dict[str, Any]) -> str:
    explicit = _first_text(record, ("domain", "subset", "subject", "math_domain", "category"))
    if explicit and explicit.strip().lower() not in {"math", "mathematics", "数学", "unknown", "general"}:
        return explicit
    question = _first_text(record, ("question", "prompt", "input", "query", "instruction")).lower()
    domain_tokens = (
        ("geometry", ("geometry", "triangle", "circle", "angle", "ellipse", "parabola", "hyperbola", "几何", "三角形", "圆", "角", "椭圆", "抛物线", "双曲线", "向量")),
        ("probability_statistics", ("probability", "statistics", "random variable", "distribution", "expectation", "variance", "概率", "统计", "随机变量", "分布", "期望", "方差", "抽样", "卡方")),
        ("calculus_analysis", ("derivative", "integral", "limit", "continuity", "monotonic", "导数", "积分", "极限", "连续", "单调", "微分")),
        ("number_theory", ("prime", "divisibility", "congruence", "integer solution", "质数", "素数", "整除", "同余", "整数解")),
        ("combinatorics", ("permutation", "combination", "counting", "排列", "组合", "计数", "容斥")),
        ("algebra", ("equation", "polynomial", "sequence", "matrix", "function", "方程", "多项式", "数列", "矩阵", "函数", "不等式")),
        ("arithmetic", ("ratio", "percentage", "fraction", "比例", "百分比", "分数", "算术")),
    )
    for domain, tokens in domain_tokens:
        if any(token in question for token in tokens):
            return domain
    return "other_math"


def _contains_any(text: str, tokens: Tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _looks_like_refusal(completion: str) -> bool:
    return bool(re.match(
        r"^(?:i(?:'m| am) sorry|sorry|i cannot|i can't|i am unable|as an ai|"
        r"抱歉|对不起|我不能|我无法|无法帮助|不能协助)",
        completion.lstrip(),
        re.IGNORECASE,
    ))


def _violates_general_output_contract(question: str, reference: str, completion: str) -> bool:
    prompt = question.lower()
    stripped = completion.strip()
    reference_stripped = reference.strip()
    expects_json = (
        "json" in prompt
        or "json 对象" in question
        or reference_stripped.startswith(("{", "["))
    )
    if expects_json and stripped:
        candidate = stripped
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
        try:
            import json

            json.loads(candidate)
        except (TypeError, ValueError):
            return True
    if _contains_any(prompt, ("only answer", "answer only", "only output", "respond only")):
        if "\n" in stripped or stripped.startswith(("Here", "Sure", "The answer")):
            return True
    if _contains_any(question, ("只回答", "仅回答", "只输出", "仅输出")):
        if "\n" in stripped or stripped.startswith(("答案是", "下面", "当然")):
            return True
    if _contains_any(prompt, ("no markdown", "without markdown")) and "```" in stripped:
        return True
    if _contains_any(question, ("不要使用 Markdown", "不使用 Markdown")) and "```" in stripped:
        return True
    return False


def _looks_like_prose_or_wrapped_code(completion: str, *, sql: bool = False) -> bool:
    stripped = completion.lstrip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith("```") or _PROSE_PREFIX_RE.search(stripped):
        return True
    if any(marker in stripped[:240] for marker in ("### ", "**", "---")):
        return True
    if sql:
        return not re.match(r"^(?:select|with|insert|update|delete)\b", lowered)
    return not re.match(r"^(?:async\s+def|def|class|from|import|@|#|[A-Za-z_]\w*\s*=|return)\b", stripped)


def classify_failure_bucket(
    record: Dict[str, Any],
    task_type: str = "code",
) -> Dict[str, Any]:
    """Map one failed record to an actionable capability bucket.

    Runtime/parser evidence is intentionally preferred over the model's
    fallback ``other`` label. The original label remains in the result for
    auditability.
    """
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    metric_detail = record.get("metric_detail") if isinstance(record.get("metric_detail"), dict) else {}
    task_route = _normalize_task_type(task_type)
    original_stage = _text(
        judge.get("stage")
        or record.get("error_type")
        or record.get("match_type")
        or metric_detail.get("match_type")
        or "other"
    ).lower()
    completion, result, tags, judge_text = _record_text(record)
    evidence = f"{result} {tags} {judge_text}".lower()
    failed = not bool(record.get("passed", False))

    if task_route == "math":
        question, reference, completion, evidence, labels = _math_evidence(record)
        label_bucket_counts = Counter(
            bucket for bucket in (_math_label_bucket(label) for label in labels) if bucket
        )
        if labels:
            original_stage = Counter(label.strip().lower() for label in labels).most_common(1)[0][0]
        if label_bucket_counts:
            bucket, count = label_bucket_counts.most_common(1)[0]
            signal_labels = [label for label in labels if _math_label_bucket(label) == bucket]
            result_item = _classification(
                bucket,
                original_stage,
                min(0.97, 0.86 + 0.03 * count),
                f"结构化数学错因中“{signal_labels[0]}”等信号出现 {count} 次",
            )
            result_item["math_error_signals"] = dict(label_bucket_counts)
            return result_item
        if _math_extraction_failed(record, completion, evidence):
            return _classification(
                "math_output_contract", original_stage, 0.96, "答案为空或诊断指标确认最终答案提取失败"
            )
        if _contains_any(evidence, (
            "题意理解", "建模", "列式", "变量定义", "条件理解", "problem understanding",
            "modeling", "equation setup",
        )):
            return _classification("math_modeling", original_stage, 0.88, "诊断证据指向题意理解或数学建模错误")
        if _contains_any(evidence, (
            "代数", "符号变换", "化简", "方程求解", "因式分解", "algebra", "symbolic",
            "simplification", "equation solving", "factorization",
        )):
            return _classification("math_algebra_symbolic", original_stage, 0.88, "诊断证据指向代数或符号变换错误")
        if _contains_any(evidence, (
            "计算错误", "算术错误", "数值错误", "代入错误", "正负号", "arithmetic",
            "calculation", "numeric error", "sign error",
        )):
            return _classification("math_arithmetic_calculation", original_stage, 0.88, "诊断证据指向基础计算或数值错误")
        if _contains_any(evidence, (
            "解题思路", "公式使用", "定理", "策略", "概念错误", "wrong formula",
            "wrong theorem", "wrong strategy", "incorrect approach",
        )) or _contains_any(completion.lower(), ("不知道如何", "无法继续", "只能猜测", "i don't know how")):
            return _classification("math_strategy_theorem", original_stage, 0.86, "诊断证据指向解题策略、公式或定理选择错误")
        if _contains_any(evidence, (
            "答案与过程不符", "推理错误", "逻辑错误", "前后矛盾", "invalid inference",
            "reasoning error", "logical error", "contradiction", "inconsistent",
        )):
            return _classification("math_reasoning_consistency", original_stage, 0.86, "诊断证据指向多步推理或过程一致性错误")
        if _contains_any(evidence, (
            "步骤不完整", "漏解", "增根", "定义域", "边界条件", "未验证", "incomplete",
            "missing step", "extraneous root", "domain restriction", "verification",
        )):
            return _classification("math_verification_completeness", original_stage, 0.86, "诊断证据指向约束检查、验证或作答完整性")
        if failed and reference and len(reference) >= 160 and len(completion) < max(24, int(len(reference) * 0.12)):
            return _classification(
                "math_verification_completeness", original_stage, 0.58, "解答显著短于参考过程，疑似多问或关键推导未完成"
            )
        return _classification(
            _UNKNOWN_BUCKET,
            original_stage,
            0.25,
            "最终答案未通过数学指标，但缺少可靠的步骤级错因，不能据此猜测训练能力桶",
        )

    if task_route == "general":
        question, reference, completion, evidence = _general_evidence(record)
        if failed and not completion.strip():
            return _classification(
                "general_completeness_coverage", original_stage, 0.98, "回答为空，无法覆盖任务要求"
            )
        if _contains_any(evidence, (
            "over-refusal", "overrefusal", "unnecessary refusal", "should not refuse",
            "unsafe", "safety", "harmful", "toxicity", "toxic", "jailbreak", "bias",
            "过度拒答", "不应拒答", "安全", "有害", "毒性", "偏见",
        )):
            return _classification(
                "general_safety_refusal", original_stage, 0.92, "结构化评测证据指向安全或拒答边界问题"
            )
        if _contains_any(evidence, (
            "instruction_following", "instruction following", "instruction", "format_error", "format error", "format",
            "output format", "constraint violation", "did not follow", "length constraint",
            "schema violation", "指令遵循", "未遵循", "格式错误", "输出格式", "违反约束",
        )) or _violates_general_output_contract(question, reference, completion):
            return _classification(
                "general_instruction_following", original_stage, 0.92, "输出违反明确指令、格式或结构约束"
            )
        if _contains_any(evidence, (
            "irrelevant", "off-topic", "off topic", "intent mismatch", "answer relevance", "relevance", "intent",
            "does not answer", "wrong task", "答非所问", "无关", "偏题", "意图错误", "相关性",
        )):
            return _classification(
                "general_relevance_intent", original_stage, 0.88, "评测证据指向答复相关性或意图理解错误"
            )
        if _contains_any(evidence, (
            "hallucination", "factual", "factually incorrect", "unsupported", "fabricated",
            "misinformation", "faithfulness", "groundedness", "unverified claim",
            "事实错误", "幻觉", "虚构", "无依据", "知识错误", "事实性", "忠实度",
        )):
            return _classification(
                "general_factuality_grounding", original_stage, 0.90, "评测证据指向事实性、幻觉或依据不足"
            )
        if _contains_any(evidence, (
            "reasoning", "logical error", "invalid inference", "inconsistent", "contradiction",
            "causal error", "calculation error", "推理错误", "逻辑错误", "前后矛盾", "因果错误", "计算错误",
        )):
            return _classification(
                "general_reasoning_consistency", original_stage, 0.86, "评测证据指向推理链或前后一致性错误"
            )
        if _contains_any(evidence, (
            "incomplete", "completeness", "missing key", "omission", "coverage", "insufficient", "partial answer",
            "truncated", "not comprehensive", "不完整", "遗漏", "缺少要点", "覆盖不足", "回答截断",
        )):
            return _classification(
                "general_completeness_coverage", original_stage, 0.88, "评测证据指向回答不完整或要点遗漏"
            )
        if _contains_any(evidence, (
            "language_quality", "language quality", "fluency", "grammar", "style", "coherence", "readability", "verbosity", "verbose",
            "repetition", "ambiguous", "语言质量", "语法问题", "表达", "不流畅", "啰嗦", "重复", "歧义", "连贯性",
        )):
            return _classification(
                "general_language_quality", original_stage, 0.84, "评测证据指向语言、风格或表达质量问题"
            )
        if failed and completion and _looks_like_refusal(completion):
            return _classification(
                "general_safety_refusal", original_stage, 0.76, "失败回答表现为拒答，需要复核是否属于过度拒答"
            )
        if failed and reference and len(reference) >= 160 and len(completion) < max(24, int(len(reference) * 0.15)):
            return _classification(
                "general_completeness_coverage", original_stage, 0.62, "回答显著短于参考内容，疑似关键要点覆盖不足"
            )
        return _classification(_UNKNOWN_BUCKET, original_stage, 0.30, "通用文本缺少可靠的结构化归因证据，进入诊断池")

    if task_route == "text2sql":
        if failed and completion and _looks_like_prose_or_wrapped_code(completion, sql=True):
            return _classification("sql_output_contract", original_stage, 0.96, "输出不是可直接执行的 SQL")
        if any(token in evidence for token in ("no such table", "no such column", "unknown column", "schema", "foreign key", "sql_schema")):
            return _classification("sql_schema_linking", original_stage, 0.94, "执行证据指向表、列或 Schema 对齐错误")
        if any(token in evidence for token in ("syntax error", "parse error", "sql_syntax", "near \"")):
            return _classification("sql_syntax", original_stage, 0.94, "SQL 解析或语法错误")
        if any(token in evidence for token in ("timeout", "timed out", "too many", "sql_perf", "sql_timeout")):
            return _classification("sql_runtime_efficiency", original_stage, 0.90, "查询超时或效率问题")
        if any(token in evidence for token in ("datatype", "type mismatch", "null", "conversion", "sql_type")):
            return _classification("sql_type_value", original_stage, 0.86, "类型、NULL 或值条件错误")
        if any(token in evidence for token in ("wrong answer", "assert", "mismatch", "value", "sql_result")):
            return _classification("sql_semantic_logic", original_stage, 0.84, "SQL 可执行但结果或语义不正确")
        if original_stage in {"sql_schema"}:
            return _classification("sql_schema_linking", original_stage, 0.78, "沿用细粒度 SQL stage")
        if original_stage in {"sql_syntax", "syntax"}:
            return _classification("sql_syntax", original_stage, 0.78, "沿用 SQL 语法 stage")
        if original_stage in {"sql_type"}:
            return _classification("sql_type_value", original_stage, 0.76, "沿用 SQL 类型 stage")
        if original_stage in {"sql_timeout", "sql_perf", "timeout", "perf"}:
            return _classification("sql_runtime_efficiency", original_stage, 0.76, "沿用 SQL 运行时 stage")
        return _classification(_UNKNOWN_BUCKET, original_stage, 0.30, "现有证据不足，进入诊断池")

    if failed and completion and _looks_like_prose_or_wrapped_code(completion):
        return _classification("code_output_contract", original_stage, 0.97, "代码任务输出了说明文字或 Markdown 包装")
    if any(token in evidence for token in (
        "syntaxerror", "syntax error", "invalid syntax", "unterminated", "unexpected eof",
        "indentationerror", "indentation error", "was never closed", "truncated",
    )):
        return _classification("code_syntax_completion", original_stage, 0.95, "执行器报告语法错误或补全不完整")
    if any(token in evidence for token in (
        "nameerror", "not defined", "importerror", "modulenotfound", "missing function",
        "entry point", "wrong signature", "argument", "scope",
    )):
        return _classification("code_interface_scope", original_stage, 0.92, "函数接口、名称、作用域或依赖不完整")
    if any(token in evidence for token in (
        "timeout", "timed out", "memoryerror", "recursionerror", "recursion", "performance", "perf",
    )):
        return _classification("code_runtime_efficiency", original_stage, 0.90, "运行超时、内存或递归问题")
    if any(token in evidence for token in ("edge case", "boundary", "empty input", "corner case")):
        return _classification("code_boundary_robustness", original_stage, 0.82, "证据明确指向边界条件")
    if any(token in evidence for token in (
        "assertionerror", "assert", "wrong answer", "expected", "actual", "valueerror", "value",
    )):
        return _classification("code_semantic_logic", original_stage, 0.84, "代码进入执行但结果或断言不正确")
    if original_stage in {"syntax", "import", "compile"}:
        return _classification("code_syntax_completion", original_stage, 0.76, "沿用代码语法 stage")
    if original_stage in {"assert", "value", "logic", "type"}:
        return _classification("code_semantic_logic", original_stage, 0.72, "沿用代码语义 stage")
    if original_stage in {"timeout", "perf", "recursion"}:
        return _classification("code_runtime_efficiency", original_stage, 0.76, "沿用代码运行时 stage")
    return _classification(_UNKNOWN_BUCKET, original_stage, 0.30, "现有证据不足，进入诊断池")


def _classification(bucket: str, original_stage: str, confidence: float, reason: str) -> Dict[str, Any]:
    meta = (
        _CODE_BUCKET_META.get(bucket)
        or _SQL_BUCKET_META.get(bucket)
        or _GENERAL_BUCKET_META.get(bucket)
        or _MATH_BUCKET_META.get(bucket)
        or _UNKNOWN_META
    )
    return {
        "bucket": bucket,
        "label": meta["label"],
        "confidence": round(float(confidence), 4),
        "reason": reason,
        "original_stage": original_stage,
        "reclassified_from_other": original_stage == "other" and bucket != _UNKNOWN_BUCKET,
    }


def _project_with_floor_and_cap(
    weights: Dict[str, float],
    floor: float,
    cap: float,
) -> Dict[str, float]:
    keys = [key for key, value in weights.items() if value > 0]
    if not keys:
        return {}
    floor = max(0.0, min(float(floor), 1.0 / len(keys)))
    cap = max(1.0 / len(keys), min(1.0, float(cap)))
    fixed: Dict[str, float] = {}
    free = set(keys)

    for _ in range(len(keys) * 2 + 2):
        remaining = max(0.0, 1.0 - sum(fixed.values()))
        denominator = sum(weights[key] for key in free)
        proposed = {
            key: (remaining * weights[key] / denominator if denominator else remaining / max(len(free), 1))
            for key in free
        }
        violations = {
            key: floor if value < floor else cap
            for key, value in proposed.items()
            if value < floor or value > cap
        }
        if not violations:
            fixed.update(proposed)
            break
        key = max(violations, key=lambda item: abs(proposed[item] - violations[item]))
        fixed[key] = violations[key]
        free.remove(key)
        if not free:
            break

    total = sum(fixed.values())
    if total and not math.isclose(total, 1.0):
        adjustable = max(fixed, key=fixed.get)
        fixed[adjustable] += 1.0 - total
    return {key: max(0.0, round(value, 6)) for key, value in fixed.items()}


def build_training_bucket_strategy(
    records: Iterable[Dict[str, Any]],
    task_type: str = "code",
    *,
    alpha: float = 1.0,
    min_share: float = 0.05,
    max_share: float = 0.50,
) -> Dict[str, Any]:
    """Build an actionable, confidence-aware data allocation plan.

    ``learnability_prior`` is deliberately marked as a prior. A later
    training round should replace it with measured metric gain per sample.
    """
    task_route = _normalize_task_type(task_type)
    failed = [record for record in records if isinstance(record, dict) and not record.get("passed", False)]
    classifications = [classify_failure_bucket(record, task_route) for record in failed]
    counts = Counter(item["bucket"] for item in classifications)
    confidence_sum: Dict[str, float] = defaultdict(float)
    examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    domains: Dict[str, Counter] = defaultdict(Counter)
    original_other = 0

    for record, item in zip(failed, classifications):
        confidence_sum[item["bucket"]] += item["confidence"]
        if item["original_stage"] == "other":
            original_other += 1
        if task_route == "math":
            domain = _infer_math_domain(record)
        else:
            domain = _first_text(record, ("domain", "subset", "source", "subject", "category"))
        if not domain:
            judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
            domain = _text(judge.get("domain") or "unknown")
        domains[item["bucket"]][domain] += 1
        if len(examples[item["bucket"]]) < 3:
            completion, result, _, _ = _record_text(record)
            examples[item["bucket"]].append({
                "task_id": record.get("task_id") or record.get("id") or record.get("sample_id"),
                "original_stage": item["original_stage"],
                "reason": item["reason"],
                "result_head": result[:180],
                "completion_head": completion.replace("\n", " ")[:180],
            })

    total_failed = len(failed)
    if task_route == "text2sql":
        meta_map = _SQL_BUCKET_META
    elif task_route == "math":
        meta_map = _MATH_BUCKET_META
    elif task_route == "general":
        meta_map = _GENERAL_BUCKET_META
    else:
        meta_map = _CODE_BUCKET_META
    actionable_counts = {key: count for key, count in counts.items() if key != _UNKNOWN_BUCKET}
    candidate_counts = dict(actionable_counts)
    exploration_priors: Dict[str, float] = {}
    # When almost every sample fails before executable code/SQL is produced,
    # downstream semantic ability is censored rather than proven healthy.
    # Reserve a small first-round exploration budget instead of allocating
    # 100% to the visible prerequisite failure.
    if total_failed:
        if task_route == "text2sql" and counts.get("sql_output_contract", 0) / total_failed >= 0.50:
            exploration_priors = {
                "sql_syntax": 0.25,
                "sql_schema_linking": 0.15,
                "sql_semantic_logic": 0.15,
            }
        elif task_route == "code" and counts.get("code_output_contract", 0) / total_failed >= 0.50:
            exploration_priors = {
                "code_syntax_completion": 0.25,
                "code_interface_scope": 0.15,
                "code_semantic_logic": 0.15,
            }
        elif task_route == "general":
            prerequisite_count = max(
                counts.get("general_instruction_following", 0),
                counts.get("general_completeness_coverage", 0),
                counts.get("general_safety_refusal", 0),
            )
            if prerequisite_count / total_failed >= 0.50:
                exploration_priors = {
                    "general_relevance_intent": 0.15,
                    "general_factuality_grounding": 0.12,
                    "general_reasoning_consistency": 0.08,
                }
        elif task_route == "math" and counts.get("math_output_contract", 0) / total_failed >= 0.50:
            exploration_priors = {
                "math_arithmetic_calculation": 0.12,
                "math_algebra_symbolic": 0.10,
                "math_modeling": 0.08,
                "math_reasoning_consistency": 0.08,
            }
    for key in exploration_priors:
        candidate_counts.setdefault(key, 0)

    power_denominator = sum(float(count) ** float(alpha) for count in actionable_counts.values())
    utility_weights: Dict[str, float] = {}
    bucket_rows = []

    for key, count in sorted(candidate_counts.items(), key=lambda item: (-item[1], item[0])):
        meta = meta_map.get(key, _UNKNOWN_META)
        observed_share = count / max(total_failed, 1)
        confidence = confidence_sum[key] / count if count else 0.55
        if count:
            need_signal = (observed_share + 1e-9) ** float(alpha)
            allocation_basis = "observed_failure_and_priors"
        else:
            need_signal = exploration_priors.get(key, 0.0)
            allocation_basis = "censored_capability_exploration"
        utility = (
            need_signal
            * confidence
            * meta["severity"]
            * meta["transfer"]
            * meta["learnability_prior"]
            / max(meta["cost"], 1e-6)
        )
        utility_weights[key] = utility
        bucket_rows.append({
            "bucket": key,
            "label": meta["label"],
            "count": count,
            "observed_share": round(observed_share, 4),
            "power_adjusted_share": round((count ** float(alpha)) / power_denominator, 4)
            if power_denominator else 0.0,
            "classification_confidence": round(confidence, 4),
            "severity": meta["severity"],
            "transfer_value": meta["transfer"],
            "learnability_prior": meta["learnability_prior"],
            "data_cost": meta["cost"],
            "allocation_basis": allocation_basis,
            "censored_by_upstream_failure": count == 0 and key in exploration_priors,
            "sample_direction": meta["sample_direction"],
            "examples": examples[key],
            "domain_breakdown": [
                {
                    "domain": domain,
                    "count": domain_count,
                    "share_within_bucket": round(domain_count / max(count, 1), 4),
                }
                for domain, domain_count in domains[key].most_common(10)
            ],
        })

    allocation = _project_with_floor_and_cap(utility_weights, min_share, max_share)
    for row in bucket_rows:
        row["recommended_share"] = allocation.get(row["bucket"], 0.0)
        row["recommended_percent"] = round(row["recommended_share"] * 100, 2)

    unresolved = counts.get(_UNKNOWN_BUCKET, 0)
    naive_alpha = 2.0
    original_stage_counts = Counter(item["original_stage"] for item in classifications)
    naive_denominator = sum(float(count) ** naive_alpha for count in original_stage_counts.values())
    naive_other_share = (
        (float(original_stage_counts.get("other", 0)) ** naive_alpha) / naive_denominator
        if naive_denominator else 0.0
    )
    unresolved_share = unresolved / max(total_failed, 1)
    warnings = []
    if unresolved_share > 0.10:
        warnings.append("待诊断样本超过失败样本的 10%，分桶预算置信度不足，应先补充执行证据或人工复核。")
    if original_other:
        warnings.append("原始 other 不参与训练预算；先用执行证据重分类，仍无法归因的样本进入诊断池。")

    return {
        "strategy": "confidence_and_marginal_gain_aware",
        "task_type": task_route,
        "requested_task_type": str(task_type),
        "failed_total": total_failed,
        "parameters": {
            "power_alpha": float(alpha),
            "min_bucket_share": float(min_share),
            "max_bucket_share": float(max_share),
            "learnability": "prior_until_pilot_gain_is_available",
        },
        "other_impact": {
            "original_other_count": original_other,
            "original_other_share": round(original_other / max(total_failed, 1), 4),
            "naive_alpha_2_other_share": round(naive_other_share, 4),
            "reclassified_from_other_count": sum(
                1 for item in classifications if item["reclassified_from_other"]
            ),
            "unresolved_count": unresolved,
            "unresolved_share": round(unresolved_share, 4),
            "training_allocation_share": 0.0,
        },
        "buckets": bucket_rows,
        "diagnostic_bucket": {
            "bucket": _UNKNOWN_BUCKET,
            "label": _UNKNOWN_META["label"],
            "count": unresolved,
            "recommended_share": 0.0,
            "sample_direction": _UNKNOWN_META["sample_direction"],
            "examples": examples[_UNKNOWN_BUCKET],
        },
        "pilot_update_rule": (
            "每轮小规模补数后，以该桶目标指标增量/新增样本数更新 learnability，"
            "下一轮按边际收益重新分配；不把当前错误占比永久固化为训练占比。"
        ),
        "methodology_references": (
            _GENERAL_METHOD_REFERENCES if task_route == "general"
            else _MATH_METHOD_REFERENCES if task_route == "math"
            else []
        ),
        "warnings": warnings,
    }
