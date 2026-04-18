# -*- coding: utf-8 -*-
import os
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional

from langgraph.config import get_stream_writer

from loopai.schema.events import StreamEvent
from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

from one_eval.toolkits.dataflow_eval_tool import DataFlowEvalTool
from one_eval.core.state import ModelConfig

logger = get_logger()
@dataclass
class BenchAdapter:
    """
    最小 bench 适配对象：
    对齐 One-Eval DataFlowEvalNode / DataFlowEvalTool。
    """
    bench_name: str
    dataset_cache: str
    bench_dataflow_eval_type: str
    eval_status: str = "pending"
    meta: Dict[str, Any] = field(default_factory=dict)
    key_mapping: Dict[str, Any] = field(default_factory=dict)


def _emit(writer, message: str, *, progress=None, data=None):
    if writer:
        writer(StreamEvent(
            current="AnalyzerAgent.eval_general_text_node",
            message=message,
            progress=progress,
            data=data
        ).json())


def _judger(state: LoopAIState) -> dict:
    if "analyzer" not in state:
        raise KeyError("state 中缺少 analyzer 配置，请在 graph.invoke 中传入 analyzer")
    return state["judger"]


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ensure_outdir(state: LoopAIState) -> Path:
    cfg = _judger(state)

    base_outdir = Path(
        cfg.get("output_dir") or state.get("output_dir") or "./outputs"
    )

    task_id = state.get("task_id") or "default_task"

    outdir = base_outdir / task_id / "judger"
    outdir.mkdir(parents=True, exist_ok=True)

    return outdir


def _build_model_config(cfg: Dict[str, Any]) -> ModelConfig:
    model_name_or_path = (
        cfg.get("eval_model_path")
        or "dummy"
    )

    is_api = bool(cfg.get("is_api", False))

    api_url = cfg.get("eval_base_url", "")
    if is_api and api_url and api_url.rstrip("/").endswith("/v1"):
        api_url = api_url.rstrip("/") + "/chat/completions"

    return ModelConfig(
        model_name_or_path=model_name_or_path,
        is_api=is_api,
        api_url=api_url,
        api_key="",
        temperature=float(cfg.get("eval_temperature", 0.0)),
        top_p=float(cfg.get("eval_top_p", 1.0)),
        tensor_parallel_size=int(cfg.get("tensor_parallel_size", 1)),
        max_tokens=int(cfg.get("eval_max_tokens", cfg.get("max_tokens", 2048))),
    )


def _infer_pred_ref_keys(eval_type: str, final_key_mapping: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    推断 pred_key / ref_key
    """
    default_pred_key = "generated_ans"
    mapped_pred_key = final_key_mapping.get("input_pred_key")
    pred_key = mapped_pred_key if mapped_pred_key else default_pred_key

    ref_key: Optional[str] = None

    if eval_type == "key2_qa":
        ref_key = final_key_mapping.get("input_target_key")

    elif eval_type == "key2_q_ma":
        ref_key = final_key_mapping.get("input_targets_key")

    elif eval_type == "key3_q_choices_a":
        ref_key = final_key_mapping.get("input_label_key")
        pred_key = "eval_pred"

    elif eval_type == "key3_q_choices_as":
        ref_key = final_key_mapping.get("input_labels_key")
        pred_key = "eval_pred"

    elif eval_type == "key3_q_a_rejected":
        ref_key = final_key_mapping.get("input_better_key")

    elif eval_type == "key1_text_score":
        ref_key = None
        if final_key_mapping.get("input_text_key"):
            pred_key = final_key_mapping.get("input_text_key")

    return {
        "pred_key": pred_key,
        "ref_key": ref_key,
    }


def _build_summary_payload(
    run_ts: str,
    result: Dict[str, Any],
    bench: BenchAdapter,
    dataset_cache_path: str,
) -> Dict[str, Any]:
    stats = result.get("stats") or {}
    detail_path = result.get("detail_path")
    key_mapping = result.get("key_mapping") or {}

    return {
        "run_ts": run_ts,
        "task_type": bench.bench_dataflow_eval_type,
        "bench_name": bench.bench_name,
        "bench_dataflow_eval_type": bench.bench_dataflow_eval_type,
        "dataset_cache": dataset_cache_path,
        "detail_path": detail_path,
        "key_mapping": key_mapping,
        "stats": stats,
        "num_samples": (
            stats.get("total_samples")
            if stats.get("total_samples") is not None
            else stats.get("valid_samples", 0)
        ),
        "average": stats,
    }


def _write_summary_files(outdir: Path, summary: Dict[str, Any], run_ts: str):
    summary_json = outdir / f"text_eval_summary_{run_ts}.json"
    summary_txt = outdir / f"text_eval_summary_{run_ts}.txt"

    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    stats = summary.get("stats") or {}
    lines = [
        f"评测时间：{run_ts}",
        f"bench_name：{summary.get('bench_name')}",
        f"eval_type：{summary.get('bench_dataflow_eval_type')}",
        f"dataset_cache：{summary.get('dataset_cache')}",
        f"detail_path：{summary.get('detail_path')}",
        "统计结果：",
    ]
    for k, v in stats.items():
        lines.append(f"  - {k}: {v}")

    with open(summary_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return str(summary_json), str(summary_txt)


def _prepare_bench_from_state(
    state: LoopAIState,
    cfg: Dict[str, Any],
    writer
) -> tuple[BenchAdapter, str, str, Dict[str, Any], Path, str]:
    """
    准备 bench：
    1. 优先用 state["bench"]
    2. 否则尝试从 state["benches"][eval_cursor] 取
    3. 再不行，才从结果路径 + bench_config 动态构造
    """
    judger_cfg = state.get("judger") or {}

    outdir = _ensure_outdir(state)
    run_ts = time.strftime("%Y%m%d_%H%M%S")

    bench = state.get("bench")

    if bench is None:
        benches = state.get("benches") or []
        eval_cursor = state.get("eval_cursor", 0)
        if isinstance(benches, list) and 0 <= eval_cursor < len(benches):
            bench = benches[eval_cursor]

    if bench is not None:
        eval_type = bench.bench_dataflow_eval_type
        bench_cfg = (
            state.get("bench_config")
            or cfg.get("bench_config")
            or {}
        )
        key_mapping = (
            getattr(bench, "key_mapping", None)
            or (bench.meta or {}).get("key_mapping", {})
            or bench_cfg.get("key_mapping", {})
            or cfg.get("key_mapping", {})
            or {}
        )

        if key_mapping:
            if bench.meta is None:
                bench.meta = {}
            bench.meta["key_mapping"] = key_mapping
            bench.key_mapping = key_mapping

        dataset_cache_path_str = str(Path(bench.dataset_cache).resolve())

        _emit(
            writer,
            "开始通用文本评测（One-Eval Phase4 / DataFlow）",
            progress=0.0,
            data={
                "eval_result_path": bench.dataset_cache,
                "eval_type": eval_type,
                "key_mapping": key_mapping,
            }
        )

        _emit(
            writer,
            "已读取传入 bench",
            progress=0.20,
            data={
                "bench_name": bench.bench_name,
                "dataset_cache": bench.dataset_cache,
                "eval_type": bench.bench_dataflow_eval_type,
            }
        )

        return bench, eval_type, dataset_cache_path_str, key_mapping, outdir, run_ts

    eval_result_path = judger_cfg.get("eval_problem_path")
    print("eval_result_path:",eval_result_path)
    if not eval_result_path:
        raise ValueError("缺少评测输入路径：请提供  judger.eval_problem_path")

    if not os.path.exists(eval_result_path):
        raise FileNotFoundError(f"评测输入路径不存在：{eval_result_path}")

    bench_cfg = (
        state.get("bench_config")
        or cfg.get("bench_config")
        or {}
    )

    eval_type = (
        bench_cfg.get("bench_dataflow_eval_type")
        or judger_cfg.get("bench_dataflow_eval_type")
    )
    if not eval_type:
        raise ValueError(
            "通用文本评测缺少 eval_type。请在 bench_config.bench_dataflow_eval_type / judger.bench_dataflow_eval_type 中提供"
        )
    
    # 获取bench_cfg或者从judger中获取 key_mapping映射关系
    key_mapping = bench_cfg.get("key_mapping") or cfg.get("key_mapping") or {}

    if isinstance(key_mapping, str):
        try:
            key_mapping = json.loads(key_mapping)
        except Exception as e:
            raise ValueError(f"Failed to parse key_mapping as JSON: {key_mapping}, error: {e}")

    _emit(
        writer,
        "开始通用文本评测（One-Eval Phase4 / DataFlow）",
        progress=0.0,
        data={
            "eval_result_path": eval_result_path,
            "eval_type": eval_type,
            "key_mapping": key_mapping,
        }
    )

    rows = _read_jsonl(eval_result_path)
    _emit(writer, "读取待评测样本完成", progress=0.10, data={"records": len(rows)})

    dataset_cache_path = outdir / f"general_text_dataset_cache_{run_ts}.jsonl"
    _write_jsonl(dataset_cache_path, rows)

    _emit(
        writer,
        "已生成 dataset_cache",
        progress=0.20,
        data={"dataset_cache": str(dataset_cache_path)}
    )

    bench = BenchAdapter(
        bench_name=bench_cfg.get("bench_name", judger_cfg.get("bench_name", "general_text_eval")),
        dataset_cache=str(dataset_cache_path),
        bench_dataflow_eval_type=eval_type,
        meta={},
        key_mapping=key_mapping or {}
    )

    if key_mapping:
        bench.meta["key_mapping"] = key_mapping

    dataset_cache_path_str = str(dataset_cache_path.resolve())
    return bench, eval_type, dataset_cache_path_str, key_mapping, outdir, run_ts

def _set_gpu(state:LoopAIState):
    os.environ['CUDA_VISIBLE_DEVICES'] = state["judger"].get("cuda_visible_devices","0")
    logger.info(f"CUDA_VISIBLE_DEVICES:{os.environ['CUDA_VISIBLE_DEVICES']} 设置完成")

def eval_general_text_node(state: LoopAIState):
    """
    通用文本评测节点：
    按 DataFlowEvalNode 的逻辑完整接入到 LoopAI。
    """
    _set_gpu(state)
    writer = get_stream_writer()
    cfg = _judger(state)

    bench, eval_type, dataset_cache_path, key_mapping, outdir, run_ts = _prepare_bench_from_state(
        state, cfg, writer
    )

    model_config = _build_model_config(cfg)

    _emit(
        writer,
        "One-Eval Phase4 配置完成，准备调用 DataFlowEvalTool",
        progress=0.35,
        data={
            "bench_name": cfg["bench_name"],
            "eval_type": cfg["bench_dataflow_eval_type"],
            "model_name_or_path": getattr(model_config, "model_name_or_path", None),
            "is_api": getattr(model_config, "is_api", None),
            "max_tokens": getattr(model_config, "max_tokens", None),
        }
    )

    tool = DataFlowEvalTool(output_root=str(outdir))

    if bench.eval_status == "success" and bench.meta and bench.meta.get("eval_result"):
        logger.info(f"[{bench.bench_name}] 已评测成功，跳过")
        stats = bench.meta.get("eval_result") or {}
        detail_path = bench.meta.get("eval_detail_path")
        result = {
            "stats": stats,
            "detail_path": detail_path,
            "key_mapping": (bench.meta or {}).get("key_mapping", {}),
        }
    else:
        if not bench.dataset_cache:
            logger.warning(f"[{bench.bench_name}] 缺少 dataset_cache，跳过")
            bench.eval_status = "failed"
            if bench.meta is None:
                bench.meta = {}
            bench.meta["eval_error"] = "missing dataset_cache"
            raise ValueError(f"[{bench.bench_name}] 缺少 dataset_cache")

        if not bench.bench_dataflow_eval_type:
            logger.warning(f"[{bench.bench_name}] 缺少 eval_type，跳过")
            bench.eval_status = "failed"
            if bench.meta is None:
                bench.meta = {}
            bench.meta["eval_error"] = "missing bench_dataflow_eval_type"
            raise ValueError(f"[{bench.bench_name}] 缺少 bench_dataflow_eval_type")

        try:
            logger.info(f"[{bench.bench_name}] 开始评测... Type={bench.bench_dataflow_eval_type}")
            bench.eval_status = "running"

            # 兼容 One-Eval 可能读取 bench.key_mapping 或 bench.meta["key_mapping"]
            if getattr(bench, "key_mapping", None):
                if bench.meta is None:
                    bench.meta = {}
                bench.meta["key_mapping"] = bench.key_mapping
            elif (bench.meta or {}).get("key_mapping"):
                bench.key_mapping = bench.meta["key_mapping"]

            logger.info(f"[{bench.bench_name}] bench.key_mapping={getattr(bench, 'key_mapping', {})}")
            logger.info(f"[{bench.bench_name}] bench.meta.key_mapping={(bench.meta or {}).get('key_mapping', {})}")
            logger.info(f"{model_config}")
            result = tool.run_eval(bench, model_config)

            if not bench.meta:
                bench.meta = {}

            stats = result["stats"]
            detail_path = result.get("detail_path")
            bench.meta["eval_result"] = stats
            bench.meta["eval_detail_path"] = detail_path

            final_key_mapping = result.get("key_mapping", {})
            inferred = _infer_pred_ref_keys(eval_type, final_key_mapping)
            pred_key = inferred["pred_key"]
            ref_key = inferred["ref_key"]

            if ref_key:
                bench.meta["ref_key"] = ref_key
                logger.info(f"[{bench.bench_name}] Set ref_key='{ref_key}' based on type '{eval_type}'")

            bench.meta["pred_key"] = pred_key
            logger.info(f"[{bench.bench_name}] Set pred_key='{pred_key}'")

            if final_key_mapping:
                bench.meta["key_mapping"] = final_key_mapping
                bench.key_mapping = final_key_mapping

            bench.eval_status = "success"

            total_samples = stats.get("total_samples", 0)
            accuracy = stats.get("accuracy", 0)
            score = stats.get("score", 0)
            valid_samples = stats.get("valid_samples", 0)

            if total_samples > 0 and (accuracy == 0 and score == 0):
                reason = "Score is 0. Possibly a hidden test set without public labels."
                if valid_samples == 0:
                    reason += " (No valid samples found for evaluation)"

                bench.meta["eval_abnormality"] = {
                    "is_abnormal": True,
                    "reason": reason,
                    "type": "zero_score"
                }
                logger.warning(f"[{bench.bench_name}] Detected abnormality: {reason}")

            logger.info(f"[{bench.bench_name}] 评测完成。Stats: {stats}")

        except Exception as e:
            print("CUDA_VISIBLE_DEVICES from environment:", os.environ.get("CUDA_VISIBLE_DEVICES"))
            logger.error(f"[{bench.bench_name}] 评测失败: {e}")
            bench.eval_status = "failed"
            if not bench.meta:
                bench.meta = {}
            bench.meta["eval_error"] = str(e)
            raise

    _emit(
        writer,
        "DataFlowEvalTool 执行完成",
        progress=0.80,
        data={
            "detail_path": result.get("detail_path"),
            "stats": result.get("stats"),
            "key_mapping": result.get("key_mapping"),
            "pred_key": (bench.meta or {}).get("pred_key"),
            "ref_key": (bench.meta or {}).get("ref_key"),
        }
    )

    detail_path = result.get("detail_path")
    stats = result.get("stats") or {}

    if detail_path and os.path.exists(detail_path):
        analyze_output_result_path = str(Path(detail_path).resolve())
    else:
        fallback_detail = outdir / f"text_eval_scored_{run_ts}.json"
        with open(fallback_detail, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        analyze_output_result_path = str(fallback_detail.resolve())

    summary = _build_summary_payload(
        run_ts=run_ts,
        result=result,
        bench=bench,
        dataset_cache_path=str(Path(dataset_cache_path).resolve()),
    )
    summary_json_path, summary_txt_path = _write_summary_files(outdir, summary, run_ts)

    state.setdefault("analyzer", {})
    state["analyzer"]["analyze_output_result_path"] = analyze_output_result_path
    state["analyzer"]["analyze_output_summary_path"] = summary_json_path
    state["analyzer"]["analyze_output_summary_txt_path"] = summary_txt_path

    state["bench"] = bench

    _emit(
        writer,
        "通用文本评测完成",
        progress=1.0,
        data={
            "result_path": state["analyzer"]["analyze_output_result_path"],
            "summary_json": summary_json_path,
            "summary_txt": summary_txt_path,
            "stats": stats,
            "pred_key": (bench.meta or {}).get("pred_key"),
            "ref_key": (bench.meta or {}).get("ref_key"),
            "eval_abnormality": (bench.meta or {}).get("eval_abnormality"),
        }
    )

    logger.info(f"[general_text] detail/result path: {state['analyzer']['analyze_output_result_path']}")
    logger.info(f"[general_text] summary json: {summary_json_path}")
    logger.info(f"[general_text] summary txt: {summary_txt_path}")

    return state