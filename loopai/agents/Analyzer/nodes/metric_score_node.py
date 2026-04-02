# -*- coding: utf-8 -*-
import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List

from langgraph.config import get_stream_writer

from loopai.schema.events import StreamEvent
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

from ..eval_metrics.metrics.runner import MetricRunner

logger = get_logger()


def _emit(writer, message: str, *, progress=None, data=None):
    if writer:
        writer(StreamEvent(
            current="AnalyzerAgent.metric_score_node",
            message=message,
            progress=progress,
            data=data
        ).json())


def _ensure_metric_outdir(state: LoopAIState) -> Path:
    analyzer_cfg = state.get("analyzer") or {}
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


def metric_score_node(state: LoopAIState):
    """
    在 metric_recommend_node 后执行：
    1. 读取 metric_plan
    2. 将 eval_general_text_node 产出的 detail_path 挂到 bench.meta['artifact_paths']['records_path']
    3. 调用 MetricRunner 计算指标
    4. 将结果写回 state['analyzer']
    """
    writer = get_stream_writer()

    bench = state.get("bench")
    if bench is None:
        raise ValueError("metric_score_node: state['bench'] 不存在，请先执行 eval_general_text_node")

    bench_name = getattr(bench, "bench_name", None) or "general_text_eval"

    metric_plan_obj = (
        state.get("metric_plan")
        or (state.get("analyzer") or {}).get("metric_plan")
    )
    if not metric_plan_obj:
        raise ValueError("metric_score_node: 缺少 metric_plan，请先执行 metric_recommend_node")

    metric_plan = _coerce_metric_plan(metric_plan_obj, bench_name)

    detail_path = None
    if getattr(bench, "meta", None):
        detail_path = bench.meta.get("eval_detail_path")

    if not detail_path:
        detail_path = (state.get("analyzer") or {}).get("analyze_output_result_path")

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

    # 不改你原来的 eval_general_text_node，只在这里补 runner 需要的路径
    if getattr(bench, "meta", None) is None:
        bench.meta = {}

    bench.meta.setdefault("artifact_paths", {})
    bench.meta["artifact_paths"]["records_path"] = detail_path

    # 有些实现会 fallback 到 dataset_cache，这里也保留原值不动
    logger.info(f"[metric_score] records_path={bench.meta['artifact_paths']['records_path']}")
    logger.info(f"[metric_score] dataset_cache={getattr(bench, 'dataset_cache', None)}")
    logger.info(f"[metric_score] metric_plan={metric_plan}")

    runner = MetricRunner()

    # 兼容不同 runner 接口命名
    if hasattr(runner, "run_bench"):
        metric_result = runner.run_bench(bench, metric_plan)
    elif hasattr(runner, "run"):
        metric_result = runner.run(bench, metric_plan)
    else:
        raise AttributeError("MetricRunner 缺少 run_bench / run 方法，请检查 eval_metrics.metrics.runner")

    metric_result = metric_result or {}

    outdir = _ensure_metric_outdir(state)
    run_ts = time.strftime("%Y%m%d_%H%M%S")
    metric_result_path = outdir / f"metric_eval_result_{run_ts}.json"
    _safe_write_json(metric_result_path, metric_result)

    state.setdefault("analyzer", {})
    state["analyzer"]["metric_eval_result_path"] = str(metric_result_path.resolve())
    state["analyzer"]["metric_eval_results"] = metric_result
    state["eval_results"] = metric_result

    # 同步回 bench.meta，便于后面分析/结论节点继续使用
    bench.meta["metric_eval_result_path"] = str(metric_result_path.resolve())
    bench.meta["metric_eval_results"] = metric_result

    _emit(
        writer,
        "指标计算完成",
        progress=1.0,
        data={
            "bench_name": bench_name,
            "metric_eval_result_path": str(metric_result_path.resolve()),
            "metric_eval_results": metric_result,
        }
    )

    return state