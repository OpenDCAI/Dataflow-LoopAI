# -*- coding: utf-8 -*-
import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any
from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent
from loopai.common.prompts.prompt_loader import PromptLoader
from langchain_openai import ChatOpenAI
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

logger = get_logger()
def _analyzer(state: LoopAIState) -> dict:
    if "analyzer" not in state:
        raise KeyError("state 中缺少 analyzer 配置，请在 graph.invoke 中传入 analyzer")
    return state["analyzer"]
def init_model(state: LoopAIState) -> ChatOpenAI:
    """
    使用标准 vLLM(OpenAI 兼容) 客户端
    """
   
    cfg = _analyzer(state)
    model = ChatOpenAI(
        model=cfg['analyze_model_path'],
        api_key=cfg['analyze_api_key'],
        base_url=cfg['analyze_base_url'],
        temperature=cfg.get('analyze_temperature', 0.0),
        top_p=cfg.get('analyze_top_p', 0.95),
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


def build_prompt_for_llm(summary: Dict[str, Any], failure_snippets: List[Dict[str, Any]]) -> str:
    """
    构建 LLM 输入的 prompt，包含评测总体统计与失败样例。
    Args:
        summary: 评测总体统计信息
        failure_snippets: 失败样例列表
    
    Returns:
        构建后的 prompt 字符串
    """
    loader = PromptLoader()
    total = summary.get("total_samples", 0)
    passed = summary.get("passed_samples", 0)
    # ★ 修正：统一用 pass_rate_samples，并派生出百分比 pass_rate_pct
    pass_rate_samples = float(summary.get("pass_rate_samples", 0.0))
    fail_dist = json.dumps(summary.get("failure_stage_distribution", {}), ensure_ascii=False)
    loc_dist = json.dumps(summary.get("loc_distribution", {}), ensure_ascii=False)
    kw_dist = json.dumps(summary.get("kw_distribution", {}), ensure_ascii=False)

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

    template = loader("analyze_result_user", "analyze_user")
    return template.format(
    total=int(total),
    passed=int(passed),

    pass_rate_samples=pass_rate_samples,
    pass_rate_pct=f"{pass_rate_samples * 100:.2f}",
    pass_rate_percent=f"{pass_rate_samples * 100:.2f}",
    fail_dist=fail_dist,
    loc_dist=loc_dist,
    kw_dist=kw_dist,
    top_fail=top_fail,
    fail_block=fail_block,

    top_err=top_fail,
    by_stage_json=fail_dist,
    quick_samples=json.dumps(failure_snippets, ensure_ascii=False),
    summary_json=json.dumps(summary, ensure_ascii=False),
     )

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
    """
    分析评测结果，生成 summary 并写入文件
    """
    writer = get_stream_writer()

    def _emit(message, *, progress=None, data=None):
        if writer:
            writer(StreamEvent(
                current="AnalyzerAgent.analyze_result_node",
                message=message,
                progress=progress,
                data=data
            ).json())

    _emit(
        "开始分析评测结果",
        progress=0.0,
        data={
            "summary_path": _analyzer(state).get("analyze_output_summary_path"),
            "result_path": _analyzer(state).get("analyze_output_result_path"),
        },
    )

    summary_path = _analyzer(state).get("analyze_output_summary_path")
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    _emit(
        "已读取评测摘要",
        progress=0.15,
        data={
            "total_samples": summary.get("total_samples"),
            "passed_samples": summary.get("passed_samples"),
            "pass_rate_samples": summary.get("pass_rate_samples"),
            "failure_stage_distribution": summary.get("failure_stage_distribution"),
        },
    )


    result_path = _analyzer(state).get("analyze_output_result_path")
    with open(result_path, "r", encoding="utf-8") as f:
        results = [json.loads(line) for line in f if line.strip()]
    _emit(
        "已读取评测记录",
        progress=0.30,
        data={
            "records": len(results),
            "failed_records": sum(1 for r in results if not r.get("passed")),
        },
    )
    top_k = int(_analyzer(state).get("analyze_sampling_top_k", 5))
    failures = pick_failure_examples(results, top_k)
    _emit(
        "抽取失败样例完成",
        progress=0.45,
        data={
            "top_k": top_k,
            "picked": len(failures),
            "stages": [x.get("stage") for x in failures],
        },
    )

    llm = init_model(state)
    prompt = build_prompt_for_llm(summary, failures)
    cfg = _analyzer(state)
    _emit(
        "调用模型生成分析",
        progress=0.60,
        data={
        "model": cfg.get("analyze_model_path"),
        "base_url": cfg.get("analyze_base_url"),
        "prompt_chars": len(prompt or ""),
       },
    )
    # ChatOpenAI 支持 .batch，返回 BaseMessage，取第一个的 content
    response = llm.batch([prompt])[0].content
    _emit(
        "模型分析生成完成",
        progress=0.75,
        data={
            "response_chars": len(response or ""),
        },
    )

    rb = rule_based_brief(summary)
    _emit(
        "规则摘要生成完成",
        progress=0.82,
        data=rb,
    )


    out = {
        "meta": {
            "summary_file": str(Path(summary_path).resolve()),
            "oj_file": str(Path(result_path).resolve()) if result_path else None
        },
        "rule_brief": rb,
        "llm_review": response
    }

    ts = time.strftime("%Y%m%d_%H%M%S")
    analyzer = _analyzer(state)
    analyzer["analyze_output_report_json_path"] = os.path.join(state["output_dir"], f"report_{ts}.json")
    analyzer["analyze_output_report_text_path"] = os.path.join(state["output_dir"], f"report_{ts}.txt")
    _emit(
    "写入分析报告",
    progress=0.92,
    data={
        "report_json": analyzer["analyze_output_report_json_path"],
        "report_txt": analyzer["analyze_output_report_text_path"],
    },
    )



    Path(analyzer["analyze_output_report_json_path"]).write_text(
    json.dumps(out, ensure_ascii=False, indent=2),
    encoding="utf-8"
    )
    Path(analyzer["analyze_output_report_text_path"]).write_text(response, encoding="utf-8")
    _emit(
    "分析流程完成",
    progress=1.0,
    data={
        "report_json": analyzer["analyze_output_report_json_path"],
        "report_txt": analyzer["analyze_output_report_text_path"],
        "dominant_failure": (rb.get("dominant_failure") if isinstance(rb, dict) else None),
    },
    )

    logger.info(f"已写入：{state.get('analyzer', {}).get('analyze_output_report_json_path')}\n已写入：{state.get('analyzer', {}).get('analyze_output_report_text_path')}")
    logger.info("\n—— LLM 摘要（预览）——\n" + response)
    return state