# -*- coding: utf-8 -*-
import os
import re
import json
import time
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, Any, List
from tqdm import tqdm

from loopai.common.prompts.prompt_loader import PromptLoader
from langchain_openai import ChatOpenAI
from ..utils.llmaj import LLMJudge
from loopai.states.base import LoopAIState
from loopai.logger import get_logger

logger = get_logger()


def init_model(state: LoopAIState) -> ChatOpenAI:
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
        model=state['analyze_model_path'],
        api_key=state['analyze_api_key'],
        base_url=state['analyze_base_url'],
        temperature=state.get('analyze_temperature', 0.0),
        top_p=state.get('analyze_top_p', 0.95),
    )
    return model


def build_judge_prompt_generic(task: str, evidence: Dict[str, Any]) -> str:
    """
    判因 schema 的通用 prompt（code/sql 均可用）
    你可以随时替换这段为你自己的 prompt；要求模型返回严格 JSON。
    Args:
        - task: 任务类型（code/sql）
        - evidence: 判因 schema 字典
    Returns:
        完整 prompt
    """
    loader = PromptLoader()

    def trunc(s, n):
        s = s or ""
        return s if len(s) <= n else s[:n] + "\n...[truncated]"

    ev = {
        "prompt_head": trunc(evidence.get("prompt_head", ""), 256),
        "completion_head": trunc(evidence.get("completion_head", ""), 256),
        "test_head": trunc(evidence.get("test_head", ""), 256),
        "stdout_tail": trunc(evidence.get("stdout_tail", ""), 256),
        "stderr_head": trunc(evidence.get("stderr_head", ""), 256),
        "err_text": trunc(evidence.get("err_text", ""), 256),
        "query": trunc(evidence.get("query", ""), 256),
    }
    tpl = loader("judge", "judge_user")
    return tpl.format(task=task, **ev)


def parse_assert_from_stdout(stdout: str) -> Dict[str, Any]:
    """
    失败断言解析
    Args:
        - stdout: 模型输出的标准输出
    Returns:
        解析后的断言信息字典，包含 input_expr, expected, actual 三键
    """
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


def summarize_brief(task_id: str, prompt_head: str, completion_head: str, test_head: str,
                    oj: Dict[str, str], llm: ChatOpenAI) -> str:
    """
    总结一句话中文短评
    Args:
        - task_id: 任务 ID
        - prompt_head: 题目片段
        - completion_head: 被测生成代码片段
        - test_head: 测试代码片段
        - oj: OJ 输出字典，包含 stdout, stderr, input_expr, expected, actual 等键
        - llm: 初始化后的模型实例
    Returns:
        一句话中文短评
    """
    loader = PromptLoader()
    user_block = loader("brief", "brief_user").format(
        prompt_head=(prompt_head or "")[:600],
        completion_head=(completion_head or "")[:800],
        test_head=(test_head or "")[:600],
        stdout=(oj.get("stdout") or "")[-600:],
        stderr=(oj.get("stderr") or "")[:400],
        meta_json=json.dumps({
            "task_id": task_id,
            "assert_parsed": {
                "input_expr": oj.get("input_expr"),
                "expected": oj.get("expected"),
                "actual": oj.get("actual"),
            }
        }, ensure_ascii=False)
    )
    # ChatOpenAI.batch 返回 BaseMessage，取 content
    out = llm.batch([user_block])[0].content
    return (out or "").strip() or "（模型未返回内容）"


def _build_and_write_summary(rows: List[Dict[str, Any]], outdir: Path, run_ts: str):
    """
    根据 rows 生成 summary 的函数
    Args:
        - rows: 评测结果行列表
        - outdir: 输出目录
        - run_ts: 运行时间戳
    Returns:
        生成的 summary JSON 路径和 TXT 路径
    """
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
    summary_txt = outdir / f"summary_{run_ts}.txt"
    with open(summary_json, "w", encoding="utf-8") as sf:
        json.dump(summary, sf, ensure_ascii=False, indent=2)

    lines = []
    lines.append(f"评测时间：{run_ts}")
    lines.append(f"样本正确率：{passed_samples}/{total_samples}（{summary['pass_rate_samples'] * 100:.2f}%）")
    if pass_at_k_task:
        lines.append("Pass@k(任务口径)： " + ", ".join([f"Pass@{k}={v * 100:.2f}%" for k, v in pass_at_k_task.items()]))
    lines.append("主要错因(stage)分布：")
    for k, v in stage_counter.most_common():
        lines.append(f"  - {k}: {v}")
    lines.append("标签 Top： " + ", ".join([f"{k}:{v}" for k, v in tag_counter.most_common(8)]))
    lines.append("代码行数分布（LOC）： " + ", ".join([f"{k}:{v}" for k, v in loc_bins.items()]))
    lines.append("控制语句分布（粗复杂度）： " + ", ".join([f"{k}:{v}" for k, v in kw_bins.items()]))
    lines.append(f"I/O断言解析成功：{io_parsed_ok}/{total_samples}")
    with open(summary_txt, "w", encoding="utf-8") as tf:
        tf.write("\n".join(lines))

    logger.info(f" 已生成 summary：{summary_json} ，文本简报：{summary_txt}")
    return str(summary_json), str(summary_txt)


def eval_model_node(state: LoopAIState):
    """
    模型评测分析节点函数
    该函数处理模型评测结果，分析失败案例原因，并生成增强版评测记录和总结报告
    
    Args:
        state: LoopAIState对象，包含以下关键参数：
            - analyze_task_type: 任务类型
            - eval_result_path: 评测结果文件路径
            - analyze_batch_size: 批处理大小，默认为20
            - analyze_model_path: 分析模型路径
            - analyze_base_url: 模型基础URL
            - analyze_api_key: 模型API密钥
            - analyze_temperature: 模型温度参数
            - analyze_top_p: 模型top_p参数
            - output_dir: 输出目录路径
            - output_brief: 是否输出中文短评，默认False
    
    Returns:
        更新后的LoopAIState对象，包含以下新增字段：
            - analyze_output_result_path: 增强版评测记录文件路径
            - analyze_output_summary_path: 评测摘要JSON文件路径
    
    处理流程：
        1. 初始化LLMJudge和分析模型
        2. 读取评测结果并过滤失败案例
        3. 批量处理失败案例，收集证据并构建提示词
        4. 使用模型分析失败原因
        5. 可选地生成中文短评
        6. 输出增强版评测记录
        7. 生成评测摘要报告
    """
    task_type = state['analyze_task_type']
    judge = LLMJudge(task=task_type)

    # 读取评测结果（JSONL）
    with open(state['eval_result_path'], 'r', encoding='utf-8') as f:
        lines = [ln for ln in f if ln.strip()]
    result_content = [json.loads(ln) for ln in lines]

    # 仅失败样本做判因
    failed_results = [r for r in result_content if not r.get("passed")]

    # 初始化 LLM
    batch_size = int(state.get("analyze_batch_size", 20))
    llm = init_model(state)

    # 批量处理
    for i in tqdm(range(0, len(failed_results), batch_size)):
        batch = failed_results[i:i + batch_size]

        evidences: List[Dict[str, Any]] = []
        prompts: List[str] = []
        for rec in batch:
            evidence = {
                "task_id": rec.get("task_id"),
                "sample_index": rec.get("sample_index"),
                "entry_point": rec.get("entry_point", ""),
                "prompt_head": (rec.get("problem_prompt") or "")[:800],
                "completion_head": (rec.get("completion") or "")[:800],
                "test_head": (rec.get("test_code") or "")[:800],
                "stdout_tail": (rec.get("stdout") or "")[-600:],
                "stderr_head": (rec.get("stderr") or "")[:600],
                "err_text": f"stdout:\n{rec.get('stdout', '')}\n\nstderr:\n{rec.get('stderr', '')}",
                "query": rec.get("query", "") or rec.get("completion", ""),
            }
            evidences.append(evidence)
            prompts.append(build_judge_prompt_generic(task_type, evidence))

        # 模型批处理
        batch_responses = llm.batch(prompts)

        # 合并判因 +（可选）中文短评
        for j, rec in enumerate(batch):
            # ChatOpenAI.batch 返回 BaseMessage，取 content 作为 JSON 字符串
            model_json = batch_responses[j].content
            if not rec.get("judge"):
                try:
                    rec["judge"] = judge.analyze(evidences[j], model_result=model_json)
                except Exception as e:
                    rec["judge"] = {"stage": "other", "reason": f"LLMaJ 运行异常：{e}", "evidence": {}}

            if state.get('output_brief', False):
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
                        llm=llm
                    )
                except Exception as e:
                    rec["brief_analysis"] = f"（短评生成失败：{e}）"

    logger.info(f" 判因完成：共 {len(failed_results)} 条")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_jsonl_path = Path(state['output_dir']) / f"oj_records_enriched_{ts}.jsonl"
    with open(out_jsonl_path, "w", encoding="utf-8") as f:
        for r in failed_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    state['analyze_output_result_path'] = str(out_jsonl_path.resolve())
    logger.info(f" 已写入增强版 OJ：{out_jsonl_path}")
    logger.info(" 完成：V2 评测 + 每条样本 LLMaJ（启发式/模型融合）" + (" + 中文短评" if state.get('output_brief', False) else ""))

    # 生成 summary
    try:
        summary_json_path, summary_txt_path = _build_and_write_summary(failed_results, Path(state['output_dir']), ts)
        with open(summary_json_path, "r", encoding="utf-8") as f:
            sdata = json.load(f)
        sdata["results_file"] = str(out_jsonl_path.resolve())
        with open(summary_json_path, "w", encoding="utf-8") as f:
            json.dump(sdata, f, ensure_ascii=False, indent=2)
        state['analyze_output_summary_path'] = summary_json_path
        logger.info(f" 已在 {state['output_dir']} 生成 summary，路径：{summary_json_path} / {summary_txt_path}")
    except Exception as e:
        logger.warning(f"[WARN] 生成 summary 时发生错误：{e}")

    return state