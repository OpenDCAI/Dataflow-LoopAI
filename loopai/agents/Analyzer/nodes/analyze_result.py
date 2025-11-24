import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from langchain_openai import ChatOpenAI

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

logger = get_logger()

def init_model(model_path: str, base_url: str, api_key: str, temperature: float = 0, top_p: float = 0.95):
    """
    初始化模型
    Args:
        - model_path: 模型路径
        - base_url: 模型基础 URL
        - api_key: 模型 API 密钥
        - temperature: 温度参数，默认 0
        - top_p: Top-p 参数，默认 0.95
    Returns:
        初始化后的模型实例
    """
    model = ChatOpenAI(
        model=model_path,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        top_p=top_p
    )
    return model

def pick_failure_examples(oj_records: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    抽取少量失败样例（含断言解析/题干/completion），用于给 LLM 当证据。
    Args:
        oj_records: 评测记录列表
        top_k: 抽取失败样例的数量
    
    Returns:
        失败样例列表，每个样例包含 task_id, entry_point, assert_parsed, problem_head, completion_head, stdout_tail, stage 字段
    """
    fails = [r for r in oj_records if not r.get("passed")]

    def stage_rank(rec):
        st = ((rec.get("judge") or {}).get("stage")) or "other"
        return 0 if st == "assert" else 1
    fails.sort(key=stage_rank)
    picked = []
    for r in fails[:top_k]:
        picked.append({
            "task_id": r.get("task_id"),
            "entry_point": r.get("entry_point"),
            "assert_parsed": r.get("assert_parsed"),
            "problem_head": (r.get("problem_prompt") or "")[:260],
            "completion_head": (r.get("completion") or "")[:260],
            "stdout_tail": (r.get("stdout") or "")[-260:],
            "stage": ((r.get("judge") or {}).get("stage") or "other"),
        })
    return picked

def build_prompt_for_llm(summary: Dict[str, Any],
                         failure_snippets: List[Dict[str, Any]]) -> str:
    """
    构建 LLM 输入的 prompt，包含评测总体统计与失败样例。
    Args:
        summary: 评测总体统计信息
        failure_snippets: 失败样例列表
    
    Returns:
        构建后的 prompt 字符串
    """
    total = summary.get("total_samples", 0)
    passed = summary.get("passed_samples", 0)
    pass_rate = summary.get("pass_rate_samples", 0)
    fail_dist = summary.get("failure_stage_distribution", {})
    loc_dist = summary.get("loc_distribution", {})
    kw_dist  = summary.get("kw_distribution", {})

    major_fail = sorted(fail_dist.items(), key=lambda x: x[1], reverse=True)
    top_fail = major_fail[0][0] if major_fail else "N/A"

    def short(d: Dict[str, Any]) -> str:
        ap = d.get("assert_parsed") or {}
        problem_head = (d.get("problem_head") or "").replace("\n", " ")[:200]
        completion_head = (d.get("completion_head") or "").replace("\n", " ")[:200]
        return (
        f"- {d.get('task_id','?')}::{d.get('entry_point','?')} | stage={d.get('stage','?')}\n"
        f"  input: {ap.get('input_expr')}\n"
        f"  expected: {ap.get('expected')} | actual: {ap.get('actual')}\n"
        f"  problem: {problem_head}\n"
        f"  completion: {completion_head}\n"
    )

    fail_block = "\n".join(short(x) for x in failure_snippets)

    sys = (
        "你是代码评测与数据治理的专家。请阅读以下统计信息与若干失败样例，"
        "生成两段自然语言总结：\n"
        "A) 面向‘模型能力’的评估与可执行建议；\n"
        "B) 面向‘数据/爬取/评测策略’的建议。\n"
        "语言要简洁、可落地，避免堆砌术语。"
    )

    usr = f"""
【评测总体统计】
- 样本总数: {total}
- 通过样本: {passed}
- 正确率: {pass_rate*100:.2f}%
- 主要失败类型分布: {json.dumps(fail_dist, ensure_ascii=False)}
- 代码行数分布(LOC): {json.dumps(loc_dist, ensure_ascii=False)}
- 控制语句分布(粗复杂度): {json.dumps(kw_dist, ensure_ascii=False)}
- 最高频失败类型: {top_fail}

【失败样例(最多5条)】
{fail_block or '(无)'} 

【输出要求】
1) 给出 “模型表现评估（A段）”：一句总体结论 + 3~5条针对性的优化建议（可涉及 prompt、训练数据、RLHF 奖励、推理步骤约束、返回值校验等）。
2) 给出 “数据/爬取/评测建议（B段）”：一句总体判断 + 3~5条建议（样本覆盖、边界/异常用例、题型分布、断言设计、超时与性能、样本难度分层等）。
3) 不要重复粘贴原始数字；必要时可引用统计结论，但请用自然语言表达。
"""
    return sys + "\n\n" + usr.strip()

def rule_based_brief(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    基于规则的摘要，包含评测总体统计与主要失败类型。
    Args:
        summary: 评测总体统计信息
    
    Returns:
        包含评测总体统计与主要失败类型的字典
    """
    total = summary.get("total_samples", 0)
    passed = summary.get("passed_samples", 0)
    pr = summary.get("pass_rate_samples", 0)
    fail_dist = summary.get("failure_stage_distribution", {})
    major = sorted(fail_dist.items(), key=lambda x: x[1], reverse=True)
    top = major[0][0] if major else "N/A"
    return {
        "quick_numbers": {
            "total": total,
            "passed": passed,
            "pass_rate": round(float(pr), 4),
        },
        "dominant_failure": top,
    }

def analyze_result_node(state: LoopAIState):
    """
    分析评测结果，生成 summary 并写入文件
    """
    summary_path = state['analyze_output_summary_path']
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    
    result_path = state['analyze_output_result_path']
    with open(result_path, "r", encoding="utf-8") as f:
        results = [json.loads(line) for line in f.readlines()]
    
    failures = pick_failure_examples(results, state['analyze_sampling_top_k'])

    llm = init_model(
        model_path=state['analyze_model_path'],
        base_url=state['analyze_base_url'],
        api_key=state['analyze_api_key'],
        temperature=state['analyze_temperature'],
        top_p=state['analyze_top_p']
    )

    prompt = build_prompt_for_llm(summary, failures)
    response = llm.batch([prompt])[0].content

    rb = rule_based_brief(summary)

    out = {
        "meta": {
            "summary_file": str(Path(state['analyze_output_summary_path']).resolve()),
            "oj_file": str(Path(state['analyze_output_result_path']).resolve()) if state['analyze_output_result_path'] else None
        },
        "rule_brief": rb,
        "llm_review": response
    }

    ts = time.strftime("%Y%m%d_%H%M%S")
    state['analyze_output_report_json_path'] = os.path.join(state['output_dir'], f"report_{ts}.json")
    state['analyze_output_report_text_path'] = os.path.join(state['output_dir'], f"report_{ts}.txt")
    Path(state['analyze_output_report_json_path']).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(state['analyze_output_report_text_path']).write_text(response, encoding="utf-8")

    logger.info(f"已写入：{state['analyze_output_report_json_path']}\n已写入：{state['analyze_output_report_text_path']}")
    logger.info("\n—— LLM 摘要（预览）——\n")
    logger.info(response)
    return state
