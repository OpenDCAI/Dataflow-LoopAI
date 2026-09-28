# -*- coding: utf-8 -*-
import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from loopai.common.event_tool import StreamEvent
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from loopai.skills.Analyzer.utils.stream import get_safe_stream_writer

from ..eval_metrics.metrics.runner import MetricRunner

logger = get_logger()


def _emit(writer, message: str, *, progress=None, data=None):
    if writer:
        writer(StreamEvent(
            current="analyzer.metric_score",
            message=message,
            progress=progress,
            data=data
        ).json())


def _ensure_metric_outdir(state: LoopAIState) -> Path:
    analyzer_cfg = state.get("analyzer") or {}
    runtime_outdir = analyzer_cfg.get("runtime_output_dir")
    if runtime_outdir:
        outdir = Path(runtime_outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        return outdir
    base_outdir = Path(
        analyzer_cfg.get("output_dir") or state.get("output_dir") or "./outputs"
    )
    task_id = state.get("task_id") or "default_task"
    outdir = base_outdir / task_id / "analyzer"
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir


def _safe_write_json(path: Path, data: Dict[str, Any]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _coerce_metric_plan(metric_plan_obj: Any, bench_name: str) -> List[Dict[str, Any]]:
    """
    支持两种形式：
    1. {"bench_name": [ ... ]}
    2. [ ... ]
    """
    if isinstance(metric_plan_obj, dict):
        plan = metric_plan_obj.get(bench_name)
        if isinstance(plan, list):
            return plan
        raise ValueError(f"metric_score_node: metric_plan 中未找到 bench '{bench_name}' 的配置")
    if isinstance(metric_plan_obj, list):
        return metric_plan_obj
    raise ValueError("metric_score_node: metric_plan 格式非法")


def _coerce_benchinfo(bench):
    """Standalone JSON / Judger state 常把 bench 存成 dict；MetricRunner 需要属性访问。"""
    if bench is None or not isinstance(bench, dict):
        return bench
    from dataclasses import fields
    from one_eval.core.state import BenchInfo

    allowed = {f.name for f in fields(BenchInfo)}
    return BenchInfo(**{k: v for k, v in bench.items() if k in allowed})


def metric_score_node(state: LoopAIState):
    """
    在 metric_recommend_node 后执行：
    1. 读取 metric_plan
    2. 将 eval_general_text_node 产出的 detail_path 挂到 bench.meta['artifact_paths']['records_path']
    3. 调用 MetricRunner 计算指标
    4. 将结果写回 state['analyzer']
    """
    writer = get_safe_stream_writer()
    t_node = time.perf_counter()
    stage_timing: Dict[str, float] = {}

    judger_cfg = state.get("judger", {}) or {}
    analyzer_cfg = state.get("analyzer", {}) or {}

    try:
        t_load = time.perf_counter()
        bench = judger_cfg.get("bench") or state.get("bench")

        if bench is None:
            raise ValueError("metric_score_node: 未找到 bench，请先执行 eval_general_text_node")
        bench = _coerce_benchinfo(bench)
        if isinstance(judger_cfg, dict) and judger_cfg.get("bench") is not None:
            judger_cfg["bench"] = bench
        state["bench"] = bench
        stage_timing["load_ms"] = round((time.perf_counter() - t_load) * 1000.0, 1)

    except Exception as e:
        logger.exception(f"[metric_score_node] bench加载失败: {e}")

        state["exception"] = f"metric_score_node bench error: {str(e)}"
        raise
    if isinstance(bench, dict):
        bench_name = bench.get("bench_name") or "general_text_eval"
    else:
        bench_name = getattr(bench, "bench_name", None) or "general_text_eval"

    metric_plan_obj = (
        state.get("metric_plan")
        or analyzer_cfg.get("metric_plan")
    )
    if not metric_plan_obj:
        raise ValueError("metric_score_node: 缺少 metric_plan，请先执行 metric_recommend_node")

    metric_plan = _coerce_metric_plan(metric_plan_obj, bench_name)

    detail_path = None
    if isinstance(bench, dict):
        bench_meta = bench.get("meta") or {}
    else:
        bench_meta = getattr(bench, "meta", {}) or {}

    if bench_meta:
        detail_path = (
           bench_meta.get("eval_detail_path")
           or bench_meta.get("artifact_paths", {}).get("records_path")
        )

    if not detail_path:
        detail_path = (
            judger_cfg.get("output_pred_path")
            or judger_cfg.get("output_result_path")
            or judger_cfg.get("out_result_path")
            or judger_cfg.get("eval_result_path")
            or analyzer_cfg.get("analyze_output_result_path")
            or analyzer_cfg.get("eval_result_path")
        )

    if not detail_path:
        raise ValueError("metric_score_node: 未找到评测结果文件路径（detail_path）")

    if not os.path.exists(detail_path):
        raise FileNotFoundError(f"metric_score_node: 评测结果文件不存在: {detail_path}")

    _emit(
        writer,
        "开始执行指标计算",
        progress=0.0,
        data={
            "bench_name": bench_name,
            "detail_path": detail_path,
            "metric_plan": metric_plan,
        }
    )

    if isinstance(bench, dict):
        if bench.get("meta", None) is None:
            bench["meta"] = {}
        bench["meta"].setdefault("artifact_paths", {})
        bench["meta"]["artifact_paths"]["records_path"] = detail_path
        bench_meta = bench["meta"]
    else:
        if getattr(bench, "meta", None) is None:
            bench.meta = {}
        bench.meta.setdefault("artifact_paths", {})
        bench.meta["artifact_paths"]["records_path"] = detail_path
        bench_meta = bench.meta

    logger.info(f"[metric_score] records_path={bench_meta['artifact_paths']['records_path']}")
    if isinstance(bench, dict):
        logger.info(f"[metric_score] dataset_cache={bench.get('dataset_cache', None)}")
    else:
        logger.info(f"[metric_score] dataset_cache={getattr(bench, 'dataset_cache', None)}")
    logger.info(f"[metric_score] metric_plan={metric_plan}")

    # Windows ProcessPool 开销大且易刷 spawn 噪声；默认单进程。可用 metric_max_workers 覆盖。
    analyzer_cfg = state.get("analyzer") or {}
    max_workers: Optional[int]
    if "metric_max_workers" in analyzer_cfg and analyzer_cfg.get("metric_max_workers") is not None:
        max_workers = max(1, int(analyzer_cfg.get("metric_max_workers")))
    elif sys.platform.startswith("win"):
        max_workers = 1
    else:
        max_workers = None
    runner = MetricRunner(max_workers=max_workers)
    logger.info(f"[metric_score] MetricRunner.max_workers={runner.max_workers}")

    t_metric = time.perf_counter()
    if hasattr(runner, "run_bench"):
        metric_result = runner.run_bench(bench, metric_plan)
    elif hasattr(runner, "run"):
        metric_result = runner.run(bench, metric_plan)
    else:
        raise AttributeError("MetricRunner 缺少 run_bench / run 方法，请检查 eval_metrics.metrics.runner")

    metric_result = metric_result or {}
    stage_timing["metric_ms"] = round((time.perf_counter() - t_metric) * 1000.0, 1)

    t_write = time.perf_counter()
    outdir = _ensure_metric_outdir(state)
    run_ts = time.strftime("%Y%m%d_%H%M%S")
    metric_result_path = outdir / f"metric_eval_result_{run_ts}.json"
    _safe_write_json(metric_result_path, metric_result)

    state.setdefault("analyzer", {})
    state["analyzer"]["metric_eval_result_path"] = str(metric_result_path.resolve())
    state["analyzer"]["metric_eval_results"] = metric_result
    state["eval_results"] = metric_result
    stage_timing["write_ms"] = round((time.perf_counter() - t_write) * 1000.0, 1)
    stage_timing["total_ms"] = round((time.perf_counter() - t_node) * 1000.0, 1)
    state["analyzer"].setdefault("stage_timing_ms", {})["metric"] = stage_timing

    bench_meta["metric_eval_result_path"] = str(metric_result_path.resolve())
    bench_meta["metric_eval_results"] = metric_result

    _emit(
        writer,
        "指标计算完成",
        progress=1.0,
        data={
            "bench_name": bench_name,
            "metric_eval_result_path": str(metric_result_path.resolve()),
            "metric_eval_results": metric_result,
            "stage_timing_ms": stage_timing,
        }
    )

    return state
