# modelTest.py
# 作用：一键运行 V2/run.py，然后对每条样本注入（启发式 + 可选模型JSON融合）的 LLMaJ 判因，
#      并可选生成简短中文评价。兼容 promptless 版 llmaj.py。

import os, sys, re, json, time, subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict, Counter
from tqdm import tqdm  

# ===== 基本路径 =====
ROOT = Path(__file__).resolve().parent
V2_DIR = ROOT / "V2"
RUN_PY = V2_DIR / "run.py"
DEFAULT_SAMPLES = V2_DIR / "sample" / "test_samples_all.jsonl"  
DEFAULT_HUMANEVAL = V2_DIR / "data" / "mbpp.jsonl"              
DEFAULT_OUTDIR = V2_DIR / "result"


model_path = "/jizhicfs/hymiezhao/models/Qwen2.5-32B-Instruct"


from llmaj import LLMJudge


from transformers import StoppingCriteria, StoppingCriteriaList
class _StopAtCurlyJSON(StoppingCriteria):
    def __init__(self, tok): self.tok = tok
    def __call__(self, input_ids, scores, **kw):
        try:
            text = self.tok.decode(input_ids[0].tolist(), skip_special_tokens=True)
            return text.rstrip().endswith("}")
        except Exception:
            return False

def _load_llm():
    try:
        from transformers import AutoTokenizer, AutoModelForCausalLM
        import torch

        assert torch.cuda.is_available(), "未检测到 CUDA，请检查驱动/环境或 CUDA_VISIBLE_DEVICES"
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

        tok = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True, padding_side="left")
        if tok.pad_token_id is None:
            tok.pad_token = tok.eos_token

        def _try_load(attn_impl: str):
            return AutoModelForCausalLM.from_pretrained(
                model_path,
                device_map={"": 0},
                dtype=__import__("torch").float16,             
                trust_remote_code=True,
                attn_implementation=attn_impl,    
            )

        try:
            model = _try_load("flash_attention_2")
            print("使用 Flash-Attention 2", flush=True)
        except Exception as fe:
            print(f"⚠️ Flash-Attention 2 不可用，回退到 SDPA：{fe}", flush=True)
            model = _try_load("sdpa")
            print("使用 SDPA", flush=True)

        model.eval()
        return tok, model

    except Exception as e:
        raise RuntimeError(f"本地 LLM 加载失败：{e}")

def _call_llm(tok, model, prompt: str, max_new_tokens: int = 128, temperature: float = 0.0) -> str:
    import torch
    device = next(model.parameters()).device
    stops = StoppingCriteriaList([_StopAtCurlyJSON(tok)])
    with torch.no_grad():
        inputs = tok(prompt, return_tensors="pt").to(device)
        out = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,   
            temperature=temperature,
            do_sample=(temperature > 0),
            eos_token_id=tok.eos_token_id,
            pad_token_id=tok.pad_token_id,
            stopping_criteria=stops,          
        )
    text = tok.decode(out[0], skip_special_tokens=True)
    if text.startswith(prompt):
        text = text[len(prompt):]
    return text.strip()

def _call_llm_batch(tok, model, prompts: List[str], max_new_tokens: int = 128, temperature: float = 0.0) -> List[str]:
    import torch
    device = next(model.parameters()).device
    stops = StoppingCriteriaList([_StopAtCurlyJSON(tok)])
    with torch.no_grad():
        batch_inputs = tok(
            prompts,
            return_tensors="pt",
            padding=True,
            truncation=True
        ).to(device)
        out = model.generate(
            **batch_inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=(temperature > 0),
            eos_token_id=tok.eos_token_id,
            pad_token_id=tok.pad_token_id,
            stopping_criteria=stops,
        )
    texts = tok.batch_decode(out, skip_special_tokens=True)
    cleaned = []
    for p, t in zip(prompts, texts):
        cleaned.append(t[len(p):].strip() if t.startswith(p) else t.strip())
    return cleaned

# ======= 判因 schema 的通用 prompt（code/sql 均可用）=======
def build_judge_prompt_generic(task: str, evidence: Dict[str, Any]) -> str:
    """
    你可以随时替换这段为你自己的 prompt；要求模型返回严格 JSON。
    """
    sys_inst = (
        "你是故障诊断助手。请根据给定证据输出严格 JSON（不含多余文字）。"
        "字段：stage, exception_type, reason, evidence, factors, advice, tags。"
    )

    def trunc(s, n):
        s = s or ""
        return s if len(s) <= n else s[:n] + "\n...[truncated]"
    ev = {
        "prompt_head":    trunc(evidence.get("prompt_head",""),    256),
        "completion_head":trunc(evidence.get("completion_head",""),256),
        "test_head":      trunc(evidence.get("test_head",""),      256),
        "stdout_tail":    trunc(evidence.get("stdout_tail",""),    256),
        "stderr_head":    trunc(evidence.get("stderr_head",""),    256),
        "err_text":       trunc(evidence.get("err_text",""),       256),
        "query":          trunc(evidence.get("query",""),          256),
    }
    schema_hint = (
        'stage ∈ ["syntax","import","name","type","value","index","key","recursion","assert",'
        '"timeout","perf","other","sql_syntax","sql_schema","sql_type","sql_timeout","sql_perf"]'
    )
    user = f"""
任务类型: {task}

证据(截断后):
- prompt_head: {ev["prompt_head"]}
- completion_head: {ev["completion_head"]}
- test_head: {ev["test_head"]}
- stdout_tail: {ev["stdout_tail"]}
- stderr_head: {ev["stderr_head"]}
- err_text: {ev["err_text"]}
- query: {ev["query"]}

请输出 JSON，含字段：
- stage, exception_type, reason（一句话）, evidence{{stderr_head, stdout_tail, file_line, assert_msg}},
- factors（自定义键值，如 io_diff/code_stats/sql_stats 等）, advice（<=25字）, tags（3-6 个）。
{schema_hint}
仅输出 JSON。
""".strip()
    return sys_inst + "\n\n" + user

# ===== 失败断言解析 =====
def parse_assert_from_stdout(stdout: str) -> Dict[str, Optional[str]]:
    actual = expected = input_expr = None
    s = stdout or ""
    m = re.search(r"^E\s+assert\s+(.+?)\s*==\s*(.+?)\s*$", s, flags=re.M)
    if m:
        actual = m.group(1).strip()
        expected = m.group(2).strip()
    if actual is None or expected is None:
        m = re.search(r"AssertionError[: ]\s*assert\s+(.+?)\s*==\s*(.+?)\s*$", s, flags=re.M)
        if m:
            actual = actual or m.group(1).strip()
            expected = expected or m.group(2).strip()
    m = re.search(r"candidate\((.*?)\)", s, flags=re.S)
    if m:
        input_expr = "candidate(" + " ".join(m.group(1).split()) + ")"
    return {"input_expr": input_expr, "expected": expected, "actual": actual}

# ===== 一句话中文短评 =====
def summarize_brief(task_id: str,
                    prompt_head: str,
                    completion_head: str,
                    test_head: str,
                    oj: Dict[str, str],
                    llm_tok=None,
                    llm_model=None) -> str:
    prompt_head = (prompt_head or "")[:600]
    completion_head = (completion_head or "")[:800]
    test_head = (test_head or "")[:600]
    stdout = (oj.get("stdout") or "")[-600:]
    stderr = (oj.get("stderr") or "")[:400]
    meta = {
        "task_id": task_id,
        "assert_parsed": {
            "input_expr": oj.get("input_expr"),
            "expected": oj.get("expected"),
            "actual": oj.get("actual"),
        }
    }
    sys_prompt = (
        "你是代码评测总结器。请用中文给出简短结论（1~3句），格式："
        "【结论】：…；【原因要点】：…；【建议】：。"
    )
    user_block = (
        f"【题目片段】\n{prompt_head}\n\n"
        f"【被测生成代码片段】\n{completion_head}\n\n"
        f"【测试代码片段】\n{test_head}\n\n"
        f"【OJ输出-stdout(尾)】\n{stdout}\n\n"
        f"【OJ输出-stderr(头)】\n{stderr}\n\n"
        f"【已解析关键信息(JSON)】\n{json.dumps(meta, ensure_ascii=False)}\n\n"
        "请按要求输出。"
    )
    out = _call_llm(llm_tok, llm_model, sys_prompt + "\n\n" + user_block, max_new_tokens=128, temperature=0.0)
    return out.strip() or "（模型未返回内容）"

# ===== 工具 =====
def _latest_file(dirpath: Path, pattern: str) -> Optional[Path]:
    files = list(dirpath.glob(pattern))
    if not files: return None
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]

def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: out.append(json.loads(line))
            except Exception: continue
    return out

def _dump_jsonl(path: Path, rows: List[Dict[str, Any]]):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

# ===== 新增：根据 rows 生成 summary 的函数 =====
def _build_and_write_summary(rows: List[Dict[str, Any]], outdir: Path, run_ts: str):

    stage_counter = Counter()
    tag_counter = Counter()
    passed_samples = 0
    total_samples = 0

    loc_bins = Counter()
    kw_bins = Counter()

    io_parsed_ok = 0
    actual_top = Counter()
    expected_top = Counter()

    task_pass_map = defaultdict(bool)
    task_ids = set()

    def bin_loc(n: int) -> str:
        if n <= 10: return "<=10"
        if n <= 30: return "11-30"
        if n <= 60: return "31-60"
        return ">60"

    def bin_kw(n: int) -> str:
        if n == 0: return "0"
        if n <= 3: return "1-3"
        if n <= 8: return "4-8"
        return ">8"

    for rec in rows:
        total_samples += 1
        if rec.get("passed"):
            passed_samples += 1
        else:
            stg = (rec.get("judge") or {}).get("stage", "other")
            stage_counter[stg] += 1
            tags = (rec.get("judge") or {}).get("tags") or []
            for t in tags:
                tag_counter[t] += 1

        m = rec.get("code_metrics") or {}
        try:
            loc = int(m.get("loc", 0))
        except Exception:
            loc = 0
        try:
            kw = int(m.get("kw_total", 0))
        except Exception:
            kw = 0
        loc_bins[bin_loc(loc)] += 1
        kw_bins[bin_kw(kw)] += 1

        ap = rec.get("assert_parsed") or {}
        if ap.get("expected") is not None or ap.get("actual") is not None:
            io_parsed_ok += 1
            if ap.get("actual"):
                actual_top[ap["actual"][:60]] += 1
            if ap.get("expected"):
                expected_top[ap["expected"][:60]] += 1

        tid = rec.get("task_id")
        if tid is not None:
            task_ids.add(tid)
            if rec.get("passed"):
                task_pass_map[tid] = True

    total_tasks = len(task_ids)
    passed_tasks = sum(1 for v in task_pass_map.values() if v)
    pass_at_k_task = {}
    if total_tasks > 0:
        pass_at_k_task[1] = round(passed_tasks / total_tasks, 4)
        pass_at_k_task[10] = pass_at_k_task[1] 
    else:
        pass_at_k_task = {}

    summary = {
        "run_ts": run_ts,
        "results_file": None, 
        "total_samples": total_samples,
        "passed_samples": passed_samples,
        "pass_rate_samples": round(passed_samples / (total_samples or 1), 4),
        "pass_at_k_task": pass_at_k_task,
        "failure_stage_distribution": dict(stage_counter),
        "loc_distribution": dict(loc_bins),
        "kw_distribution": dict(kw_bins),
        "io_assert_parse": {
            "parsed_ok": io_parsed_ok,
            "parsed_rate": round(io_parsed_ok / (total_samples or 1), 4),
            "actual_top10": actual_top.most_common(10),
            "expected_top10": expected_top.most_common(10),
        },
        "tag_top10": tag_counter.most_common(10),
    }


    os.makedirs(outdir, exist_ok=True)
    summary_json = outdir / f"summary_{run_ts}.json"
    summary_txt  = outdir / f"summary_{run_ts}.txt"
    with open(summary_json, "w", encoding="utf-8") as sf:
        json.dump(summary, sf, ensure_ascii=False, indent=2)

    lines = []
    lines.append(f"评测时间：{run_ts}")
    lines.append(f"样本正确率：{passed_samples}/{total_samples}（{summary['pass_rate_samples']*100:.2f}%）")
    if pass_at_k_task:
        lines.append("Pass@k(任务口径)： " + ", ".join([f"Pass@{k}={v*100:.2f}%" for k, v in pass_at_k_task.items()]))
    lines.append("主要错因(stage)分布：")
    for k, v in stage_counter.most_common():
        lines.append(f"  - {k}: {v}")
    lines.append("标签 Top： " + ", ".join([f"{k}:{v}" for k, v in tag_counter.most_common(8)]))
    lines.append("代码行数分布（LOC）： " + ", ".join([f"{k}:{v}" for k, v in loc_bins.items()]))
    lines.append("控制语句分布（粗复杂度）： " + ", ".join([f"{k}:{v}" for k, v in kw_bins.items()]))
    lines.append(f"I/O断言解析成功：{io_parsed_ok}/{total_samples}")
    with open(summary_txt, "w", encoding="utf-8") as tf:
        tf.write("\n".join(lines))

    print(f" 已生成 summary：{summary_json} ，文本简报：{summary_txt}", flush=True)
    return summary_json, summary_txt

# ===== 主流程 =====
def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="One-shot runner: 调用 V2/run.py + 注入（启发式/可选模型融合）的 LLMaJ 判因 + 可选中文短评。"
    )
    ap.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    ap.add_argument("--humaneval", default=str(DEFAULT_HUMANEVAL))
    ap.add_argument("--outdir", default=str(DEFAULT_OUTDIR))

    ap.add_argument("--no-brief", action="store_true",
                    help="仅判因，不生成中文短评")
    ap.add_argument("--max-new", type=int, default=128)  # ← 默认更短
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--task", choices=["code","sql"], default="code",
                    help="LLMaJ 判因任务类型，code 或 sql（text2sql 用）")

    ap.add_argument("extra", nargs="*")
    args = ap.parse_args()

    if not RUN_PY.is_file():
        raise FileNotFoundError(f"未找到 V2/run.py：{RUN_PY}")
    if not Path(args.samples).is_file():
        raise FileNotFoundError(f"未找到样本：{args.samples}")
    if not Path(args.humaneval).is_file():
        raise FileNotFoundError(f"未找到 HumanEval：{args.humaneval}")
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    print("⚠️ 跳过 V2/run.py（不重新评测），直接使用已有结果文件")
    print("✅ 评测阶段已跳过，继续进行判因/汇总")

    preferred = outdir / "mbpp_result.jsonl"
    oj_path = preferred if preferred.is_file() else (_latest_file(outdir, "oj_records_*.jsonl") or _latest_file(outdir, "*.jsonl"))
    if oj_path is None:
        raise FileNotFoundError(f"在 {outdir} 下未找到结果文件（优先 mbpp_result.jsonl，其次 oj_records_*.jsonl 或 *.jsonl）。")
    print(f"📄 使用 OJ 记录文件：{oj_path.name}")

    tok, model = _load_llm()

    judge = LLMJudge(task=args.task)

    rows = _load_jsonl(oj_path)
    total = len(rows)

    fails_by_task = defaultdict(list)
    for i, r in enumerate(rows):
        if not r.get("passed"):
            fails_by_task[r.get("task_id")].append(i)

    fail_indices = [idxs[0] for idxs in fails_by_task.values()]

    def _len_score(i):
        rec = rows[i]
        return len((rec.get("problem_prompt") or "")) + len((rec.get("completion") or "")) + len((rec.get("test_code") or ""))
    fail_indices.sort(key=_len_score)

    print(f"⏳ 待处理样本（总）：{total}；按任务首个失败样本计：{len(fail_indices)} 条（仅对这些样本做判因）", flush=True)

    BATCH = 16 
    judge_cnt = 0
    brief_cnt = 0
    fail_cnt = 0

    pbar = tqdm(total=len(fail_indices), desc="判因处理中（任务级早停）", unit="条")

    for start in range(0, len(fail_indices), BATCH):
        idx_chunk = fail_indices[start:start+BATCH]

        evidences = []
        prompts = []
        for idx in idx_chunk:
            rec = rows[idx]
            evidence = {
                "task_id": rec.get("task_id"),
                "sample_index": rec.get("sample_index"),
                "entry_point": rec.get("entry_point", ""),
                "prompt_head": (rec.get("problem_prompt") or "")[:800],
                "completion_head": (rec.get("completion") or "")[:800],
                "test_head": (rec.get("test_code") or "")[:800],
                "stdout_tail": (rec.get("stdout") or "")[-600:],
                "stderr_head": (rec.get("stderr") or "")[:600],
                "err_text": f"stdout:\n{rec.get('stdout','')}\n\nstderr:\n{rec.get('stderr','')}",
                "query": rec.get("query","") or rec.get("completion",""),
            }
            evidences.append(evidence)
            prompts.append(build_judge_prompt_generic(args.task, evidence))

        try:
            batch_json = _call_llm_batch(tok, model, prompts, max_new_tokens=args.max_new, temperature=args.temperature)
            judge_cnt += len(idx_chunk)
        except Exception as e:
            print(f"[WARN] 批量 LLM 生成失败：{e}", flush=True)
            batch_json = ['{"stage":"other","reason":"LLM 批量生成异常","evidence":{}}' for _ in idx_chunk]
            fail_cnt += len(idx_chunk)

        for k, idx in enumerate(idx_chunk):
            rec = rows[idx]
            model_json = batch_json[k]

            if not rec.get("judge"):
                try:
                    rec["judge"] = judge.analyze(evidences[k], model_result=model_json)
                except Exception as e:
                    rec["judge"] = {"stage": "other", "reason": f"LLMaJ 运行异常：{e}", "evidence": {}}

            if not args.no_brief and not rec.get("brief_analysis"):
                apx = rec.get("assert_parsed") or parse_assert_from_stdout(rec.get("stdout") or "")
                try:
                    rec["brief_analysis"] = summarize_brief(
                        task_id=str(rec.get("task_id")),
                        prompt_head=rec.get("problem_prompt") or "",
                        completion_head=rec.get("completion") or "",
                        test_head=rec.get("test_code") or "",
                        oj={
                            "stdout": rec.get("stdout") or "",
                            "stderr": rec.get("stderr") or "",
                            "input_expr": apx.get("input_expr"),
                            "expected": apx.get("expected"),
                            "actual": apx.get("actual"),
                        },
                        llm_tok=tok,
                        llm_model=model
                    )
                    brief_cnt += 1
                except Exception as e:
                    rec["brief_analysis"] = f"（短评生成失败：{e}）"

            pbar.update(1)

    pbar.close()
    print(f" 判因完成：共 {total} 条，按任务首失败计 {len(fail_indices)} 条已处理；判因 {judge_cnt} 条，短评 {brief_cnt} 条，失败 {fail_cnt} 条。", flush=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_jsonl = Path(args.outdir) / f"oj_records_enriched_{ts}.jsonl"
    _dump_jsonl(out_jsonl, rows)
    print(f" 已写入增强版 OJ：{out_jsonl}")
    print(" 完成：V2 评测 + 每条样本 LLMaJ（启发式/模型融合）" + ("" if args.no_brief else " + 中文短评"))

    # ===== 基于刚写出的 enriched oj_records 生成 summary_*.json / summary_*.txt =====
    try:
        
        summary_json_path, summary_txt_path = _build_and_write_summary(rows, Path(args.outdir), ts)

        with open(summary_json_path, "r", encoding="utf-8") as f:
            sdata = json.load(f)
        sdata["results_file"] = str(out_jsonl.resolve())
        with open(summary_json_path, "w", encoding="utf-8") as f:
            json.dump(sdata, f, ensure_ascii=False, indent=2)
        print(f" 已在 {args.outdir} 生成 summary，路径：{summary_json_path} / {summary_txt_path}", flush=True)
    except Exception as e:
        print(f"[WARN] 生成 summary 时发生错误：{e}", flush=True)

if __name__ == "__main__":
    main()