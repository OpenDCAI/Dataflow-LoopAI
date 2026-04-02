# -*- coding: utf-8 -*-
from typing import Dict, Any, List

from langgraph.config import get_stream_writer

from loopai.schema.events import StreamEvent
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

# 你已经放进 Analyzer/eval_metrics 里的模块
from ..eval_metrics.metrics.dispatcher import metric_dispatcher

logger = get_logger()


def _emit(writer, message: str, *, progress=None, data=None):
    if writer:
        writer(StreamEvent(
            current="AnalyzerAgent.metric_recommend_node",
            message=message,
            progress=progress,
            data=data
        ).json())


def _normalize_metric_list(metrics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    统一 metric 格式，兼容 dispatcher 返回的不同字段风格。
    最终统一成：
    [
        {"name": "...", "priority": "...", "args": {...}},
        ...
    ]
    """
    normalized: List[Dict[str, Any]] = []
    seen = set()

    for m in metrics or []:
        if not isinstance(m, dict):
            continue

        name = m.get("name") or m.get("metric_name")
        if not name or name in seen:
            continue

        item = {
            "name": name,
            "priority": m.get("priority") or "secondary",
        }

        if "args" in m and isinstance(m["args"], dict):
            item["args"] = m["args"]
        elif "params" in m and isinstance(m["params"], dict):
            item["args"] = m["params"]
        elif "k" in m:
            item["args"] = {"k": m["k"]}

        normalized.append(item)
        seen.add(name)

    if normalized and not any(x.get("priority") == "primary" for x in normalized):
        normalized[0]["priority"] = "primary"

    return normalized


def _fallback_metrics(eval_type: str) -> List[Dict[str, Any]]:
    """
    如果 dispatcher 按 bench_name 没推荐到，就按 eval_type 兜底。
    """
    mapping = {
        "key2_qa": [
            {"name": "exact_match", "priority": "primary"},
            {"name": "token_f1", "priority": "secondary"},
            {"name": "extraction_rate", "priority": "diagnostic"},
        ],
        "key2_q_ma": [
            {"name": "exact_match", "priority": "primary"},
            {"name": "token_f1", "priority": "secondary"},
            {"name": "extraction_rate", "priority": "diagnostic"},
        ],
        "key3_q_choices_a": [
            {"name": "choice_accuracy", "priority": "primary"},
            {"name": "extraction_rate", "priority": "diagnostic"},
        ],
        "key3_q_choices_as": [
            {"name": "choice_accuracy", "priority": "primary"},
            {"name": "extraction_rate", "priority": "diagnostic"},
        ],
        "key3_q_a_rejected": [
            {"name": "win_rate", "priority": "primary"},
            {"name": "extraction_rate", "priority": "diagnostic"},
        ],
        "key1_text_score": [
            {"name": "rouge_l", "priority": "primary"},
            {"name": "bleu", "priority": "secondary"},
            {"name": "chrf", "priority": "diagnostic"},
        ],
    }

    return mapping.get(
        eval_type,
        [
            {"name": "exact_match", "priority": "primary"},
            {"name": "extraction_rate", "priority": "diagnostic"},
        ]
    )


def metric_recommend_node(state: LoopAIState):
    """
    在 eval_general_text_node 后执行：
    1. 先基于 bench_name 用 dispatcher 推荐 metric
    2. 如果没命中，再按 eval_type fallback
    3. 将 metric_plan 写回 state
    """
    writer = get_stream_writer()

    bench = state.get("bench")
    if bench is None:
        raise ValueError("metric_recommend_node: state['bench'] 不存在，请先执行 eval_general_text_node")

    bench_name = getattr(bench, "bench_name", None) or "general_text_eval"
    eval_type = getattr(bench, "bench_dataflow_eval_type", None) or "unknown"

    _emit(
        writer,
        "开始推荐指标",
        progress=0.0,
        data={
            "bench_name": bench_name,
            "eval_type": eval_type,
        }
    )

    recommended = []
    try:
        recommended = metric_dispatcher.get_metrics(bench_name) or []
    except Exception as e:
        logger.warning(f"[metric_recommend] dispatcher.get_metrics('{bench_name}') 失败: {e}")
        recommended = []

    normalized = _normalize_metric_list(recommended)

    if not normalized:
        normalized = _fallback_metrics(eval_type)
        logger.info(f"[metric_recommend] bench_name={bench_name} 未命中 registry，使用 eval_type fallback: {normalized}")
    else:
        logger.info(f"[metric_recommend] bench_name={bench_name} 命中 registry: {normalized}")

    state.setdefault("analyzer", {})
    state["analyzer"]["metric_plan"] = {
        bench_name: normalized
    }
    state["metric_plan"] = {
        bench_name: normalized
    }

    # 顺手写入 bench.meta，方便后面调试或报告阶段使用
    if getattr(bench, "meta", None) is None:
        bench.meta = {}
    bench.meta["metric_plan"] = normalized

    _emit(
        writer,
        "指标推荐完成",
        progress=1.0,
        data={
            "bench_name": bench_name,
            "metric_plan": normalized,
        }
    )

    return state