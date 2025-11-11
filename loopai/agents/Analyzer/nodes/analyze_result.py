# -*- coding: utf-8 -*-
import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any
from loopai.common.prompts.prompt_loader import PromptLoader
from ..utils.vllm_chat import VLLMChat
from loopai.states.base import LoopAIState
from loopai.logger import get_logger

logger = get_logger()

def init_model(state: LoopAIState) -> VLLMChat:
    """
    使用标准 vLLM(OpenAI 兼容) 客户端
    """
    return VLLMChat(
        model=state['analyze_model_path'],
        base_url=state['analyze_base_url'],
        api_key=state['analyze_api_key'],
        temperature=state.get('analyze_temperature', 0.0),
        top_p=state.get('analyze_top_p', 0.95),
        system_prompt_type=getattr(state, 'system_prompt_type', 'system'),
        system_prompt_name=getattr(state, 'system_prompt_name', 'default_prompt')
    )

def pick_failure_examples(oj_records: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
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

def build_prompt_for_llm(summary: Dict[str, Any], failure_snippets: List[Dict[str, Any]]) -> str:
    loader = PromptLoader()
    total = summary.get("total_samples", 0)
    passed = summary.get("passed_samples", 0)
    pass_rate = summary.get("pass_rate_samples", 0.0)
    fail_dist = json.dumps(summary.get("failure_stage_distribution", {}), ensure_ascii=False)
    loc_dist  = json.dumps(summary.get("loc_distribution", {}), ensure_ascii=False)
    kw_dist   = json.dumps(summary.get("kw_distribution", {}), ensure_ascii=False)

    major_fail = sorted(summary.get("failure_stage_distribution", {}).items(), key=lambda x: x[1], reverse=True)
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
    fail_block = "\n".join(short(x) for x in failure_snippets) or "(无)"

    template = loader("analyze_result_user", "v1")
    return template.format(
        total=int(total),
        passed=int(passed),
        pass_rate_percent=f"{pass_rate*100:.2f}",
        fail_dist=fail_dist,
        loc_dist=loc_dist,
        kw_dist=kw_dist,
        top_fail=top_fail,
        fail_block=fail_block
    )

def rule_based_brief(summary: Dict[str, Any]) -> Dict[str, Any]:
    total = summary.get("total_samples", 0)
    passed = summary.get("passed_samples", 0)
    pr = summary.get("pass_rate_samples", 0.0)
    fail_dist = summary.get("failure_stage_distribution", {})
    major = sorted(fail_dist.items(), key=lambda x: x[1], reverse=True)
    top = major[0][0] if major else "N/A"
    return {
        "quick_numbers": {
            "total": int(total),
            "passed": int(passed),
            "pass_rate": round(float(pr), 4),
        },
        "dominant_failure": top,
    }

def analyze_result_node(state: LoopAIState):
    summary_path = state['analyze_output_summary_path']
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    result_path = state['analyze_output_result_path']
    with open(result_path, "r", encoding="utf-8") as f:
        results = [json.loads(line) for line in f if line.strip()]

    failures = pick_failure_examples(results, state.get('analyze_sampling_top_k', 5))

    llm = init_model(state)
    prompt = build_prompt_for_llm(summary, failures)
    response = llm.batch([prompt])[0]

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
    logger.info("\n—— LLM 摘要（预览）——\n" + response)
    return state