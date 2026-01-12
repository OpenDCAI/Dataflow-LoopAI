import os
import json
import time
import datetime
from pathlib import Path
from collections import Counter
from typing import List, Dict, Any

from langchain_openai import ChatOpenAI
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

from loopai.common.prompts.prompt_loader import PromptLoader
from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent
logger = get_logger()
from collections import defaultdict
from typing import List, Dict, Any
def _analyzer(state: LoopAIState) -> dict:
    if "analyzer" not in state:
        raise KeyError("state 中缺少 analyzer 配置，请在 graph.invoke 中传入 analyzer")
    return state["analyzer"]

def pick_samples_by_stage(
    records: List[Dict[str, Any]],
    limit: int = 20,
    stage_key: str = "stage",
) -> List[Dict[str, Any]]:
    """
    从失败样本中按 judge.stage 覆盖性抽样
    - 小类优先
    - 轮询抽样
    - 顺序稳定
    """
    groups = defaultdict(list)

    # 1. 按 stage 分组
    for r in records:
        if not isinstance(r, dict):
            continue
        judge = r.get("judge") or {}
        stage = str(judge.get(stage_key) or "other")
        groups[stage].append(r)

    if not groups:
        return []

    # 2. 组内稳定排序（保证多次运行一致）
    def stable_key(r):
        return (
            str(r.get("task_id") or ""),
            int(r.get("sample_index") or -1),
        )

    for k in groups:
        groups[k].sort(key=stable_key)

    # 3. 小类优先
    stage_order = sorted(groups.keys(), key=lambda k: (len(groups[k]), k))
    ptr = {k: 0 for k in stage_order}

    picked = []
    while len(picked) < limit:
        progressed = False
        for k in stage_order:
            if ptr[k] < len(groups[k]):
                picked.append(groups[k][ptr[k]])
                ptr[k] += 1
                progressed = True
                if len(picked) >= limit:
                    break
        if not progressed:
            break

    return picked
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

    cfg = _analyzer(state)
    model = ChatOpenAI(
        model=cfg['analyze_model_path'],
        api_key=cfg['analyze_api_key'],
        base_url=cfg['analyze_base_url'],
        temperature=cfg.get('analyze_temperature', 0.0),
        top_p=cfg.get('analyze_top_p', 0.95),
    )
    return model


def try_read_oj_records(path_from_summary: str):
    """
    尝试读取 OJ 记录文件
    Args:
        path_from_summary: 从 summary 文件中读取的 OJ 记录文件路径
    Returns:
        评测记录列表，每个评测记录包含 task_id, entry_point, assert_parsed,
        problem_prompt, completion, stdout, passed, judge 字段
    """
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
                    # 单条解析失败直接跳过
                    pass
        return records, f"读取到 {len(records)} 条 OJ 记录"
    except Exception as e:
        return [], f"读取 OJ 记录失败：{e}"

def _as_dict(x) -> Dict[str, Any]:
    """兼容 dict / JSON字符串 / 其它类型"""
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}

def enhance_stats_with_oj(records):
    """
    从 OJ 评测记录中提取统计信息
    Args:
        records: OJ 评测记录列表
    Returns:
        包含标签统计、IO 断言解析统计、常见失败 IO 片段统计的字典
    """
    tags_counter = Counter()
    io_ok = 0
    io_total = 0
    failing_expr_counter = Counter()
    expected_counter = Counter()
    actual_counter = Counter()

    for rec in records:
        if not isinstance(rec, dict):
            continue

        judge = _as_dict(rec.get("judge") or {})
        tags = judge.get("tags")
        if isinstance(tags, list):
            tags_counter.update([t for t in tags if isinstance(t, str) and t])

        ap = _as_dict(rec.get("assert_parsed") or {})
        if any([ap.get("input_expr"), ap.get("expected"), ap.get("actual")]):
            io_total += 1
            if all([ap.get("input_expr"), ap.get("expected"), ap.get("actual")]):
                io_ok += 1

        if not rec.get("passed", False):
            factors = _as_dict(judge.get("factors") or {})
            io = _as_dict(factors.get("io_diff") or {})

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


def make_final_json(summary: dict, oj_records: list):
    """
    构建最终的 JSON 报告输出
    Args:
        summary: 评测摘要
        oj_records: OJ 评测记录列表
    Returns:
        运行时间戳，最终 JSON 输出
    """
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
            "pass_rate_samples": round(pass_rate_samples, 4),
        },
        "pass_at_k": pass_at_k,
        "failure_stage_distribution": {
            "by_stage": stage_dist,
            "top": stage_sorted,
        },
        "loc_distribution": {
            "by_bucket": loc_dist,
            "ordered": loc_sorted,
        },
        "control_kw_distribution": {
            "by_bucket": kw_dist,
            "ordered": kw_sorted,
        },
        "results_file": summary.get("results_file"),
    }

    if oj_records:
        extras = enhance_stats_with_oj(oj_records)
        final_json["extras"] = extras

    return run_ts, final_json


def build_suggestion_prompt(final_json: dict) -> str:
    """
    构建改进建议的提示文本
    Args:
        final_json: 最终 JSON 输出
    Returns:
        改进建议的提示文本
    """
    loader = PromptLoader()
    tpl = loader("suggestion", "suggest_user")

    t = final_json["totals"]["total_samples"]
    p = final_json["totals"]["passed_samples"]

    stage_top = final_json["failure_stage_distribution"]["top"]
    top_err = stage_top[0][0] if stage_top else "无明显错误类型"

    by_stage_dict = final_json["failure_stage_distribution"]["by_stage"]
    by_stage_json = json.dumps(by_stage_dict, ensure_ascii=False, indent=2)

    quick_samples = (final_json.get("quick_brief") or {}).get("samples") or []
    quick_samples_json = json.dumps(quick_samples, ensure_ascii=False, indent=2)

    summary_obj = final_json.get("summary") or {}
    summary_json = json.dumps(summary_obj, ensure_ascii=False, indent=2)

    return tpl.format(
        total=t,
        passed=p,
        top_err=top_err,
        by_stage=by_stage_json,
        by_stage_json=by_stage_json,
        quick_samples=quick_samples_json,
        summary_json=summary_json,   # ★ NEW
    )
def build_background_prompt(final_json: dict) -> str:
    """
    构建背景介绍的提示文本：只介绍数据集本身
    """
    loader = PromptLoader()
    tpl = loader("background", "v1")

    ds_info = final_json.get("dataset", {})

    return tpl.format(ds=json.dumps(ds_info, ensure_ascii=False))


def make_human_text(final_json: dict, background: str = None) -> str:
    """
    生成人类可读的结论文本（包含可选的背景介绍）
    Args:
        final_json: 最终 JSON 输出
        background: 可选的背景介绍文本
    Returns:
        人类可读的结论文本
    """
    def pct(x, y, digits=2):
        """
        计算百分比
        Args:
            x: 分子
            y: 分母
            digits: 保留小数位数，默认 2
        Returns:
            百分比字符串，如 "33.33%"
        """
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

    lines = []

    if background:
        lines.append("【背景介绍】")
        lines.append(background.strip())
        lines.append("")

    lines.append(f"本次评测共 {t} 个样本，其中通过 {p} 个，样本正确率 {pass_rate_str}。")
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
    """
    绘制结论，生成 summary / final_report，并可选生成背景介绍与改进建议
    """
    writer = get_stream_writer()

    def _emit(message, *, progress=None, data=None):
        if writer:
            writer(StreamEvent(
                current="JudgerAgent.draw_conclusion_node",
                message=message,
                progress=progress,
                data=data
            ).json())

    final_json_path = None
    _emit("开始生成最终报告", progress=0.0)

    outdir = state['output_dir']
    summary_path = state['analyze_output_summary_path']

    # ===== 读取 summary =====
    _emit("读取评测摘要", data={"summary_path": summary_path})
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    summary["_file_path"] = summary_path

    # ===== 读取 OJ 记录 =====
    _emit("读取评测记录", data={"results_file": summary.get("results_file")})
    oj_records, _ = try_read_oj_records(summary.get("results_file"))
    run_ts, final_json = make_final_json(summary, oj_records)

    final_json["summary"] = summary

    failed = [
        r for r in (oj_records or [])
        if isinstance(r, dict) and not r.get("passed", False)
    ]
    limit = int(state.get("quick_brief_limit", 20))
    qb_samples = pick_samples_by_stage(failed, limit=limit)

    final_json["quick_brief"] = {
        "limit": limit,
        "failed_total": len(failed),
        "samples": qb_samples,
    }

    stage_cnt = {}
    for r in qb_samples:
        st = str((r.get("judge") or {}).get("stage") or "other")
        stage_cnt[st] = stage_cnt.get(st, 0) + 1
    final_json["quick_brief"]["by_stage"] = stage_cnt

    # ===== 构造数据集元信息 + 样例，供 background 使用 =====
    dataset_name = summary.get("dataset_name") or summary.get("task_name") or Path(summary_path).stem
    cfg = _analyzer(state)
    task_type = cfg.get("analyze_task_type", "code")
    qb = final_json.get("quick_brief") or {}
    samples = qb.get("samples") or []

    field_schema = []
    example = {}
    if samples:
        first = samples[0]
        field_schema = list(first.keys())
        example = {
            k: (str(v)[:200] + "…") if isinstance(v, str) and len(str(v)) > 200 else str(v)
            for k, v in first.items()
        }

    final_json["dataset"] = {
        "name": dataset_name,
        "task_type": task_type,
        "field_schema": field_schema,
        "example": example,
        "total_samples": final_json["totals"]["total_samples"],
    }
    final_json["samples"] = samples  

    # ===== 初始化模型（用于背景介绍 + 改进建议）=====
    llm = init_model(state)

    # ===== 生成背景介绍 =====
    _emit("生成背景介绍", progress=0.3)
    logger.info("🤖 正在生成背景介绍……")
    try:
        bg_prompt = build_background_prompt(final_json)
        background_text = llm.batch([bg_prompt])[0].content
    except Exception as e:
        logger.error(f"生成背景介绍时出错：{e}")
        background_text = ""
    final_json["background"] = background_text

    # ===== 写入 JSON 报告 =====
    final_json_path = os.path.join(outdir, f"final_report_{run_ts}.json")
    _emit("写入最终 JSON 报告", data={"path": final_json_path})
    with open(final_json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(final_json, ensure_ascii=False, indent=2))

    # ===== 写入文本报告（包含背景介绍）=====
    final_txt_path = os.path.join(outdir, f"final_report_{run_ts}.txt")
    _emit("写入文本报告", data={"path": final_txt_path})
    with open(final_txt_path, "w", encoding="utf-8") as f:
        f.write(make_human_text(final_json, background=background_text))

    logger.info("基本报告生成完成：")
    logger.info(f"JSON：{final_json_path}")
    logger.info(f"文本：{final_txt_path}")

    # ===== 可选：生成改进建议 =====
    if _analyzer(state).get('output_suggestion', False):
        _emit("生成改进建议", progress=0.6)
        logger.info("🤖 正在调用本地模型生成改进建议……")
        prompt = build_suggestion_prompt(final_json)
        try:
            suggestion = llm.batch([prompt])[0].content
        except Exception as e:
            logger.error(f"生成改进建议时出错：{e}")
            suggestion = ""

        suggest_path = os.path.join(outdir, f"final_report_{run_ts}.suggestions.txt")
        with open(suggest_path, "w", encoding="utf-8") as f:
            f.write(suggestion)
        state['analyze_output_suggestion_path'] = suggest_path

        with open(final_txt_path, "a", encoding="utf-8") as f:
            f.write("\n---------------------\n模型生成的改进建议：\n")
            f.write((suggestion or "").strip() + "\n")

        logger.info("模型建议生成完成并已写入报告：")
        _emit("改进建议已写入", data={
            "suggestion_path": suggest_path,
            "final_report": final_txt_path,
        })
        logger.info(f"→ {suggest_path}")
        logger.info(f"→ {final_txt_path}")

    _emit("最终报告生成完成", progress=1.0)
    return state