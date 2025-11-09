# -*- coding: utf-8 -*-
# model_eval_llm.py — 基于 summary +（可选）oj_records，调用本地大模型生成“模型表现评估”与“数据侧建议”

import json, os, argparse, re
from pathlib import Path
from typing import List, Dict, Any, Optional

# ======== 1) 本地大模型配置 ========
MODEL_PATH = os.environ.get("LLM_PATH", "/jizhicfs/hymiezhao/models/Qwen2.5-32B-Instruct")

from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

def load_llm(model_path: str = MODEL_PATH):

    assert torch.cuda.is_available(), "未检测到 CUDA，请检查驱动/环境或设置 CUDA_VISIBLE_DEVICES"

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    tok = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        device_map={"": 0},         
        torch_dtype=torch.float16,  
        trust_remote_code=True
    )
    model.eval()
    return tok, model

@torch.no_grad()
def call_llm(tok, model, prompt: str, max_new_tokens: int = 640, temperature: float = 0.0) -> str:
    """纯文本 prompt -> 纯文本输出。默认不采样（稳定可复现）。"""
    device = next(model.parameters()).device  
    inputs = tok(prompt, return_tensors="pt").to(device)
    out = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        do_sample=(temperature > 0),
        use_cache=True,
        eos_token_id=tok.eos_token_id,
        pad_token_id=tok.eos_token_id
    )
    text = tok.decode(out[0], skip_special_tokens=True)
    if text.startswith(prompt):
        text = text[len(prompt):]
    return text.strip()

# ======== 2) 读取 summary 与oj_records ========
def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_jsonl(path: str, max_lines: Optional[int] = None) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out

def pick_failure_examples(oj_records: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """抽取少量失败样例（含断言解析/题干/completion），用于给 LLM 当证据。"""
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

# ======== 3) 构造 Prompt ========
def build_prompt_for_llm(summary: Dict[str, Any],
                         failure_snippets: List[Dict[str, Any]]) -> str:
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

# ======== 4) 规则摘要（与 LLM 结果合并输出） ========
def rule_based_brief(summary: Dict[str, Any]) -> Dict[str, Any]:
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

# ======== 5) 主流程 ========
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("summary_path", help="summary_*.json 路径")
    ap.add_argument("--oj_path", help="oj_records_*.jsonl 路径，可选", default=None)
    ap.add_argument("--topk", type=int, default=5, help="抽取失败样例条数")
    ap.add_argument("--out_json", default="model_eval_llm.json")
    ap.add_argument("--out_txt",  default="model_eval_llm.txt")
    ap.add_argument("--temp", type=float, default=0.0, help="LLM 温度（0 为确定性）")
    args = ap.parse_args()

    if not Path(args.summary_path).is_file():
        raise FileNotFoundError(f"找不到 summary: {args.summary_path}")
    summary = load_json(args.summary_path)

    failures = []
    if args.oj_path and Path(args.oj_path).is_file():
        recs = load_jsonl(args.oj_path, max_lines=20000)  
        failures = pick_failure_examples(recs, top_k=args.topk)

    tok, model = load_llm(MODEL_PATH)
    prompt = build_prompt_for_llm(summary, failures)
    llm_text = call_llm(tok, model, prompt, max_new_tokens=800, temperature=args.temp)

    rb = rule_based_brief(summary)

    out = {
        "meta": {
            "summary_file": str(Path(args.summary_path).resolve()),
            "oj_file": str(Path(args.oj_path).resolve()) if args.oj_path else None,
            "model_path": MODEL_PATH,
        },
        "rule_brief": rb,
        "llm_review": llm_text
    }

    Path(args.out_json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.out_txt).write_text(llm_text, encoding="utf-8")

    print(f"已写入：{args.out_json}\n已写入：{args.out_txt}")
    print("\n—— LLM 摘要（预览）——\n")
    print(llm_text)

if __name__ == "__main__":
    main()