# changeJson.py
# 读取 HumanEval 汇总 summary_*.json → 生成 final_report_*.json + final_report_*.txt
# 若加 --suggest 参数，会自动调用本地 Qwen2.5-32B-Instruct 生成改进建议

import argparse
import json
import os
from collections import Counter
from datetime import datetime

# ============================= 基础工具函数 =============================

def pct(x, y, digits=2):
    if not y:
        return "0.00%"
    return f"{(x / y) * 100:.{digits}f}%"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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
        judge = rec.get("judge")
        tags = judge.get("tags") if isinstance(judge, dict) else None
        if isinstance(tags, list):
            tags_counter.update([t for t in tags if isinstance(t, str) and t])

        ap = rec.get("assert_parsed") or {}
        if any([ap.get("input_expr"), ap.get("expected"), ap.get("actual")]):
            io_total += 1
            if all([ap.get("input_expr"), ap.get("expected"), ap.get("actual")]):
                io_ok += 1

        if not rec.get("passed", False):
            io = (judge.get("factors") or {}).get("io_diff") if isinstance(judge, dict) else {}
            io = io or {}
            failing_expr = io.get("failing_expr") or str(ap.get("input_expr") or "")
            expected = io.get("expected") or str(ap.get("expected") or "")
            actual = io.get("got") or str(ap.get("actual") or "")

            if failing_expr:
                failing_expr_counter.update([failing_expr.strip()])
            if expected:
                expected_counter.update([expected.strip()])
            if actual:
                actual_counter.update([actual.strip()])

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

# ============================= 报告生成 =============================

def make_final_json(summary: dict, oj_records: list):
    run_ts = summary.get("run_ts") or datetime.now().strftime("%Y%m%d_%H%M%S")
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
        "source_summary": summary.get("_file_path", "<unknown>"),
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


def make_human_text(final_json: dict) -> str:
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

# ============================= 模型生成改进建议 =============================

def build_suggestion_prompt(final_json: dict) -> str:
    t = final_json["totals"]["total_samples"]
    p = final_json["totals"]["passed_samples"]
    stage_top = final_json["failure_stage_distribution"]["top"]
    top_err = stage_top[0][0] if stage_top else "无明显错误类型"
    return f"""请根据以下评测结果，为模型改进提出建议：
- 样本总数：{t}
- 通过数：{p}
- 最常见错误类型：{top_err}
- 失败阶段统计：{final_json["failure_stage_distribution"]["by_stage"]}
请用简短中文输出三点主要改进方向。
"""


def call_model_generate_suggestions_local(prompt: str):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import torch

    model_path = "/jizhicfs/hymiezhao/models/Qwen2.5-32B-Instruct"
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        device_map="auto",
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        trust_remote_code=True,
    )

    messages = [
        {"role": "system", "content": "你是一名负责AI模型性能评估的专家。"},
        {"role": "user", "content": prompt},
    ]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer([text], return_tensors="pt").to("cuda" if torch.cuda.is_available() else "cpu")

    with torch.no_grad():
        outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.3, do_sample=False)
    result = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return result.strip()

# ============================= 主流程 =============================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("summary_json", help="summary_YYYYMMDD_HHMMSS.json 的路径")
    ap.add_argument("--outdir", default=None, help="输出目录（默认与 summary 同目录）")
    ap.add_argument("--suggest", action="store_true", help="是否调用模型生成改进建议")
    args = ap.parse_args()

    summary_path = os.path.abspath(args.summary_json)
    outdir = args.outdir or os.path.dirname(summary_path)
    os.makedirs(outdir, exist_ok=True)

    summary = load_json(summary_path)
    summary["_file_path"] = summary_path
    oj_records, _ = try_read_oj_records(summary.get("results_file"))
    run_ts, final_json = make_final_json(summary, oj_records)

    # 保存 json 和 txt
    final_json_path = os.path.join(outdir, f"final_report_{run_ts}.json")
    final_txt_path = os.path.join(outdir, f"final_report_{run_ts}.txt")
    with open(final_json_path, "w", encoding="utf-8") as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)
    with open(final_txt_path, "w", encoding="utf-8") as f:
        f.write(make_human_text(final_json))

    print("基本报告生成完成：")
    print("JSON：", final_json_path)
    print("文本：", final_txt_path)

    # 若启用模型建议
    if args.suggest:
        print("🤖 正在调用本地模型生成改进建议……")
        prompt = build_suggestion_prompt(final_json)
        suggestion = call_model_generate_suggestions_local(prompt)

        suggest_path = os.path.join(outdir, f"final_report_{run_ts}.suggestions.txt")
        with open(suggest_path, "w", encoding="utf-8") as f:
            f.write(suggestion)

        with open(final_txt_path, "a", encoding="utf-8") as f:
            f.write("\n---------------------\n模型生成的改进建议：\n")
            f.write(suggestion.strip() + "\n")

        print("模型建议生成完成并已写入报告：")
        print("→", suggest_path)
        print("→", final_txt_path)

if __name__ == "__main__":
    main()