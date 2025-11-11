# -*- coding: utf-8 -*-
import os
import json
import time
import datetime
from pathlib import Path
from collections import Counter
from typing import List, Dict, Any

from ..utils.vllm_chat import VLLMChat
from loopai.states.base import LoopAIState
from loopai.logger import get_logger

from loopai.common.prompts.prompt_loader import PromptLoader  
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

        system_prompt_type=state.get('system_prompt_type', 'system'),
        system_prompt_name=state.get('system_prompt_name', 'default_prompt')
    )

def try_read_oj_records(path_from_summary: str):
    if not path_from_summary:
        return [], "summary.results_file 为空"
    oj_path = path_from_summary
    if not os.path.exists(oj_path):
        maybe = os.path.join(os.path.dirname(path_from_summary), os.path.basename(path_from_summary))
        if os.path.exists(maybe):
            oj_path = maybe
        else:
            return [], f"找不到 OJ 记录文件：{oj_path}"

    records = []
    try:
        with open(oj_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records.append(rec)
                except Exception:
                    pass
        return records, f"读取到 {len(records)} 条 OJ 记录"
    except Exception as e:
        return [], f"读取 OJ 记录失败：{e}"

def enhance_stats_with_oj(records):
    tags_counter = Counter()
    io_ok = 0
    io_total = 0
    failing_expr_counter = Counter()
    expected_counter = Counter()
    actual_counter = Counter()

    for rec in records:
        judge = rec.get("judge") or {}
        tags = judge.get("tags")
        if isinstance(tags, list):
            tags_counter.update([t for t in tags if isinstance(t, str) and t])

        ap = rec.get("assert_parsed") or {}
        if any([ap.get("input_expr"), ap.get("expected"), ap.get("actual")]):
            io_total += 1
            if all([ap.get("input_expr"), ap.get("expected"), ap.get("actual")]):
                io_ok += 1

        if not rec.get("passed", False):
            io = (judge.get("factors") or {}).get("io_diff") or {}
            failing_expr = io.get("failing_expr") or str(ap.get("input_expr") or "")
            expected = io.get("expected") or str(ap.get("expected") or "")
            actual = io.get("got") or str(ap.get("actual") or "")
            if failing_expr: failing_expr_counter.update([failing_expr.strip()])
            if expected: expected_counter.update([expected.strip()])
            if actual: actual_counter.update([actual.strip()])

    extras = {
        "top_tags": dict(tags_counter.most_common(10)),
        "io_assert_parse": {
            "parsed_ok": io_ok,
            "parsed_total": io_total,
            "parsed_rate": float(f"{(io_ok / io_total):.4f}") if io_total else 0.0
        },
        "common_fail_io_snippets": {
            "failing_expr_top": [k for k, _ in failing_expr_counter.most_common(5)],
            "expected_top": [k for k, _ in expected_counter.most_common(5)],
            "actual_top": [k for k, _ in actual_counter.most_common(5)],
        }
    }
    return extras

def make_final_json(summary: dict, oj_records: list):
    run_ts = summary.get("run_ts") or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    total = int(summary.get("total_samples", 0) or 0)
    passed = int(summary.get("passed_samples", 0) or 0)
    pass_rate_samples = float(summary.get("pass_rate_samples", 0.0) or 0.0)
    pass_at_k = summary.get("pass_at_k_task") or {}
    stage_dist = summary.get("failure_stage_distribution") or {}
    loc_dist = summary.get("loc_distribution") or {}
    kw_dist = summary.get("kw_distribution") or {}

    stage_sorted = sorted(stage_dist.items(), key=lambda x: (-x[1], x[0]))
    loc_sorted = sorted(loc_dist.items(), key=lambda x: x[0])
    kw_sorted = sorted(kw_dist.items(), key=lambda x: x[0])

    final_json = {
        "run_ts": run_ts,
        "source_summary": str(summary.get("_file_path", "<unknown>")),
        "totals": {
            "total_samples": total,
            "passed_samples": passed,
            "pass_rate_samples": round(pass_rate_samples, 4)
        },
        "pass_at_k": pass_at_k,
        "failure_stage_distribution": {
            "by_stage": stage_dist,
            "top": stage_sorted
        },
        "loc_distribution": {
            "by_bucket": loc_dist,
            "ordered": loc_sorted
        },
        "control_kw_distribution": {
            "by_bucket": kw_dist,
            "ordered": kw_sorted
        },
        "results_file": summary.get("results_file")
    }

    if oj_records:
        extras = enhance_stats_with_oj(oj_records)
        final_json["extras"] = extras

    return run_ts, final_json

def build_suggestion_prompt(final_json: dict) -> str:
    """
    使用 common/prompts/suggestion_prompt.json
    需要包含：
    {
      "suggest": {
        "suggest_user": "……这里是一段包含 {total} {passed} {top_err} {by_stage} 的模板……"
      }
    }
    """
    loader = PromptLoader()
    t = final_json["totals"]["total_samples"]
    p = final_json["totals"]["passed_samples"]
    stage_top = final_json["failure_stage_distribution"]["top"]
    top_err = stage_top[0][0] if stage_top else "无明显错误类型"
    by_stage = json.dumps(final_json["failure_stage_distribution"]["by_stage"], ensure_ascii=False)
    tpl = loader("suggest", "suggest_user") 
    return tpl.format(total=t, passed=p, top_err=top_err, by_stage=by_stage)

def make_human_text(final_json: dict) -> str:
    def pct(x, y, digits=2):
        if not y:
            return "0.00%"
        return f"{(x / y) * 100:.{digits}f}%"

    t = final_json["totals"]["total_samples"]
    p = final_json["totals"]["passed_samples"]
    pass_rate_str = pct(p, t)
    stage_top = final_json["failure_stage_distribution"]["top"]
    top_name, top_cnt = stage_top[0] if stage_top else ("（无）", 0)
    loc_desc = ", ".join([f"{k}:{v}" for k, v in final_json["loc_distribution"]["ordered"]]) or "（无数据）"
    kw_desc = ", ".join([f"{k}:{v}" for k, v in final_json["control_kw_distribution"]["ordered"]]) or "（无数据）"

    lines = [f"本次评测共 {t} 个样本，其中通过 {p} 个，样本正确率 {pass_rate_str}。"]
    if top_cnt > 0:
        lines.append(f"最主要的失败类型是 “{top_name}”，共有 {top_cnt} 次。")
    lines.append(f"代码行数分布（LOC）：{loc_desc}。")
    lines.append(f"控制语句分布：{kw_desc}。")

    extras = final_json.get("extras") or {}
    if extras:
        tags = extras.get("top_tags") or {}
        if tags:
            tags_str = ", ".join([f"{k}:{v}" for k, v in list(tags.items())[:8]])
            lines.append(f"常见标签 Top：{tags_str}。")

    lines.append("整体来看，建议优先修复最常见错误并优化边界测试。")
    return "\n".join(lines)

def draw_conclusion_node(state: LoopAIState):
    outdir = state['output_dir']
    summary_path = state['analyze_output_summary_path']
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    summary["_file_path"] = summary_path
    oj_records, _ = try_read_oj_records(summary.get("results_file"))
    run_ts, final_json = make_final_json(summary, oj_records)

    final_json_path = os.path.join(outdir, f"final_report_{run_ts}.json")
    final_txt_path = os.path.join(outdir, f"final_report_{run_ts}.txt")
    with open(final_json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(final_json, ensure_ascii=False, indent=2))
    with open(final_txt_path, "w", encoding="utf-8") as f:
        f.write(make_human_text(final_json))

    logger.info("基本报告生成完成：")
    logger.info(f"JSON：{final_json_path}")
    logger.info(f"文本：{final_txt_path}")

    if state.get('output_suggestion'):
        logger.info("🤖 正在调用本地模型生成改进建议……")
        llm = init_model(state)
        prompt = build_suggestion_prompt(final_json)
        suggestion = llm.batch([prompt])[0]

        suggest_path = os.path.join(outdir, f"final_report_{run_ts}.suggestions.txt")
        with open(suggest_path, "w", encoding="utf-8") as f:
            f.write(suggestion)
        state['analyze_output_suggestion_path'] = suggest_path

        with open(final_txt_path, "a", encoding="utf-8") as f:
            f.write("\n---------------------\n模型生成的改进建议：\n")
            f.write(suggestion.strip() + "\n")

        logger.info("模型建议生成完成并已写入报告：")
        logger.info(f"→ {suggest_path}")
        logger.info(f"→ {final_txt_path}")

    return state