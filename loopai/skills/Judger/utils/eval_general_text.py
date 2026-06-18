# -*- coding: utf-8 -*-
"""Standalone general_text evaluation — no LangGraph dependency.

Extracted from ``loopai.agents.Judger.nodes.eval_general_text_node``,
replaced ``get_stream_writer()`` with a passed-in ``writer`` parameter.
"""

import json
import os
import time
import traceback
import multiprocessing as mp
import queue as pyqueue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from one_eval.toolkits.dataflow_eval_tool import DataFlowEvalTool
from one_eval.core.state import ModelConfig
from loopai.agents.Judger.utils.oj.const import field_mapping
from loopai.common.event_tool import StreamEvent
from loopai.logger import get_logger

logger = get_logger()


@dataclass
class BenchAdapter:
    bench_name: str
    dataset_cache: str
    bench_dataflow_eval_type: str
    eval_status: str = "pending"
    meta: Dict[str, Any] = field(default_factory=dict)
    key_mapping: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
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


def _build_model_config(cfg: Dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        model_name_or_path=cfg.get("eval_model_path") or "dummy",
        is_api=bool(cfg.get("is_api", False)),
        api_url=cfg.get("eval_base_url", ""),
        api_key=cfg.get("eval_api_key", "EMPTY"),
        temperature=float(cfg.get("eval_temperature", 0.0)),
        top_p=float(cfg.get("eval_top_p", 1.0)),
        tensor_parallel_size=int(cfg.get("eval_vllm_tensor_parallel_size", 1)),
        max_tokens=int(cfg.get("eval_max_tokens", cfg.get("max_tokens", 2048))),
        gpu_memory_utilization=cfg.get("eval_vllm_gpu_memory_utilization", 0.9),
    )


def _generate_key_mapping(cfg: Dict[str, Any]) -> Dict[str, Any]:
    key_mapping: Dict[str, Any] = {}
    eval_type = cfg["bench_dataflow_eval_type"]
    eval_problem_path = cfg["eval_problem_path"]
    count = 0
    with open(eval_problem_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            keys = list(data.keys())
            for key in keys:
                if eval_type == "key1_text_score":
                    if key in field_mapping["text"]:
                        key_mapping["input_text_key"] = key
                else:
                    if key in field_mapping["question"]:
                        key_mapping["input_question_key"] = key
                    elif eval_type == "key2_qa":
                        if key in field_mapping["target"]:
                            key_mapping["input_target_key"] = key
                        elif key in field_mapping["prediction"]:
                            key_mapping["input_pred_key"] = key
                    elif eval_type == "key2_q_ma":
                        if key in field_mapping["targets"]:
                            key_mapping["input_targets_key"] = key
                        elif key in field_mapping["prediction"]:
                            key_mapping["input_pred_key"] = key
                    elif eval_type == "key3_q_choices_a":
                        if key in field_mapping["choices"]:
                            key_mapping["input_choices_key"] = key
                        elif key in field_mapping["label"]:
                            key_mapping["input_label_key"] = key
                    elif eval_type == "key3_q_choices_as":
                        if key in field_mapping["choices"]:
                            key_mapping["input_choices_key"] = key
                        elif key in field_mapping["labels"]:
                            key_mapping["input_labels_key"] = key
                    elif eval_type == "key3_q_a_rejected":
                        if key in field_mapping["answer"]:
                            key_mapping["input_answer_key"] = key
                        elif key in field_mapping["rejected"]:
                            key_mapping["input_rejected_key"] = key
                        elif key in field_mapping["better"]:
                            key_mapping["input_better_key"] = key
            count += 1
            if count == 2:
                break
    return key_mapping


def _infer_pred_ref_keys(
    eval_type: str, final_key_mapping: Dict[str, Any]
) -> Dict[str, Optional[str]]:
    default_pred_key = "generated_ans"
    pred_key = final_key_mapping.get("input_pred_key") or default_pred_key
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
    return {"pred_key": pred_key, "ref_key": ref_key}


def _build_summary_payload(
    run_ts: str,
    result: Dict[str, Any],
    bench: BenchAdapter,
    dataset_cache_path: str,
) -> Dict[str, Any]:
    stats = result.get("stats") or {}
    return {
        "run_ts": run_ts,
        "task_type": bench.bench_dataflow_eval_type,
        "bench_name": bench.bench_name,
        "bench_dataflow_eval_type": bench.bench_dataflow_eval_type,
        "dataset_cache": dataset_cache_path,
        "detail_path": result.get("detail_path"),
        "key_mapping": result.get("key_mapping") or {},
        "stats": stats,
        "num_samples": (
            stats.get("total_samples")
            if stats.get("total_samples") is not None
            else stats.get("valid_samples", 0)
        ),
        "average": stats,
    }


def _write_summary_files(
    outdir: Path, summary: Dict[str, Any], run_ts: str
) -> tuple:
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
    return str(summary_json.resolve()), str(summary_txt.resolve())


def _run_eval_in_subprocess(
    output_root: str,
    bench: BenchAdapter,
    model_config: ModelConfig,
    result_queue: mp.Queue,
):
    try:
        logger.info("==== DataFlowEvalTool runEval starting ====")
        tool = DataFlowEvalTool(output_root=output_root)
        result = tool.run_eval(bench, model_config)
        tool.release_serving()
        logger.info("==== Released vLLM serving after workflow ====")
        result_queue.put({
            "ok": True,
            "result": result,
            "bench_meta": bench.meta,
            "bench_key_mapping": bench.key_mapping,
            "eval_status": bench.eval_status,
        })
    except Exception as exc:
        result_queue.put({
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_eval_general_text(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """通用文本评测（One-Eval DataFlowEvalTool），无 LangGraph 依赖。

    进度事件通过 ``writer`` 发送（PickleEventWriter），
    评测结果存入 ``state["judger"]["bench"]`` 和 ``output_result_path``。
    """
    os.environ["CUDA_VISIBLE_DEVICES"] = (state.get("judger") or {}).get(
        "cuda_visible_devices", "2"
    )

    cfg: Dict[str, Any] = state.get("judger") or {}
    outdir = (
        Path(state.get("output_dir") or "./outputs")
        / (state.get("task_id") or "default_task")
        / "judger"
    )
    outdir.mkdir(parents=True, exist_ok=True)
    run_ts = time.strftime("%Y%m%d_%H%M%S")

    # 校验必填字段
    eval_result_path = cfg.get("eval_problem_path")
    if not eval_result_path:
        raise ValueError("缺少评测输入路径：请提供 judger.eval_problem_path")
    if not os.path.exists(eval_result_path):
        raise FileNotFoundError(f"评测输入路径不存在：{eval_result_path}")

    eval_type = cfg.get("bench_dataflow_eval_type")
    if not eval_type:
        raise ValueError(
            "通用文本评测缺少 eval_type。请在 judger.bench_dataflow_eval_type 中提供"
        )

    logger.info(f"[Judger/general_text] model={cfg.get('eval_model_path')} "
                f"eval_type={eval_type} gpu={cfg.get('cuda_visible_devices')} "
                f"tp_size={cfg.get('eval_vllm_tensor_parallel_size', 1)} "
                f"problem_path={eval_result_path}")

    # 解析 key_mapping
    key_mapping = cfg.get("key_mapping") or {}
    if isinstance(key_mapping, str):
        try:
            key_mapping = json.loads(key_mapping)
        except Exception:
            key_mapping = _generate_key_mapping(cfg)
    if not key_mapping:
        key_mapping = _generate_key_mapping(cfg)

    bench_name = cfg.get("bench_name") or "general_text_eval"

    writer(StreamEvent(
        current="judger", progress=0.0,
        message="开始通用文本评测 (One-Eval)",
        data={"bench_name": bench_name, "eval_type": eval_type}))

    # 读取待评测样本
    rows = _read_jsonl(eval_result_path)
    writer(StreamEvent(
        current="judger", progress=0.1, message="读取待评测样本完成",
        data={"records": len(rows)}))

    # 生成 dataset_cache
    dataset_cache_path = outdir / f"general_text_dataset_cache_{run_ts}.jsonl"
    _write_jsonl(dataset_cache_path, rows)
    dataset_cache_path_s = str(dataset_cache_path.resolve())
    state["judger"]["output_problem_path"] = dataset_cache_path_s
    writer(StreamEvent(
        current="judger", progress=0.2, message="已生成 dataset_cache",
        data={"output_problem_path": dataset_cache_path_s}))

    # 构造 bench 和 model_config
    bench = BenchAdapter(
        bench_name=bench_name,
        dataset_cache=dataset_cache_path_s,
        bench_dataflow_eval_type=eval_type,
        meta={},
        key_mapping=key_mapping or {},
    )
    if key_mapping:
        bench.meta["key_mapping"] = key_mapping

    model_config = _build_model_config(cfg)
    writer(StreamEvent(
        current="judger", progress=0.35,
        message="One-Eval 配置完成，正在调用 DataFlowEvalTool",
        data={"bench_name": bench_name, "eval_type": eval_type}))

    if not bench.dataset_cache:
        raise ValueError(f"[{bench_name}] 缺少 dataset_cache")
    if not bench.bench_dataflow_eval_type:
        raise ValueError(f"[{bench_name}] 缺少 eval_type")

    # 子进程执行 DataFlowEvalTool
    bench.eval_status = "running"
    if getattr(bench, "key_mapping", None):
        bench.meta["key_mapping"] = bench.key_mapping
    elif (bench.meta or {}).get("key_mapping"):
        bench.key_mapping = bench.meta["key_mapping"]

    result_queue: mp.Queue = mp.Queue()
    proc = mp.Process(
        target=_run_eval_in_subprocess,
        args=(str(outdir), bench, model_config, result_queue),
        daemon=False,
    )
    proc.start()
    logger.info(f"[Judger/general_text] DataFlowEvalTool subprocess started "
                f"pid={proc.pid}")
    writer(StreamEvent(
        current="judger", progress=0.5,
        message="DataFlowEvalTool 子进程已启动，等待评测完成",
        data={"pid": proc.pid}))

    try:
        wait_start = time.time()
        payload = None
        while proc.is_alive():
            try:
                payload = result_queue.get_nowait()
                break
            except pyqueue.Empty:
                pass
            proc.join(timeout=5)
            if proc.is_alive():
                elapsed = int(time.time() - wait_start)
                writer(StreamEvent(
                    current="judger", progress=0.55,
                    message="DataFlowEvalTool 子进程仍在运行",
                    data={"pid": proc.pid, "waited_seconds": elapsed}))
        if payload is None:
            try:
                payload = result_queue.get_nowait()
            except pyqueue.Empty:
                raise RuntimeError(
                    f"[{bench_name}] run_eval 子进程未返回结果, exitcode={proc.exitcode}"
                )
        if not payload.get("ok"):
            raise RuntimeError(
                f"{payload.get('error', 'run_eval failed')}\n"
                f"{payload.get('traceback', '')}"
            )

        result = payload["result"]
        bench.meta = payload.get("bench_meta", bench.meta)
        bench.key_mapping = payload.get("bench_key_mapping", bench.key_mapping)
        bench.eval_status = payload.get("eval_status", bench.eval_status)
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=3)
        result_queue.cancel_join_thread()
        result_queue.close()

    writer(StreamEvent(
        current="judger", progress=0.7,
        message="DataFlowEvalTool 子进程完成，生成评测结果"))

    if not bench.meta:
        bench.meta = {}
    stats = result["stats"]
    logger.info(f"[Judger/general_text] subprocess result: stats={stats}")
    detail_path = result.get("detail_path")
    bench.meta["eval_result"] = stats
    bench.meta["eval_detail_path"] = detail_path

    # 推断 pred_key / ref_key
    final_key_mapping = result.get("key_mapping", {})
    inferred = _infer_pred_ref_keys(eval_type, final_key_mapping)
    if inferred["ref_key"]:
        bench.meta["ref_key"] = inferred["ref_key"]
    bench.meta["pred_key"] = inferred["pred_key"]
    if final_key_mapping:
        bench.meta["key_mapping"] = final_key_mapping
        bench.key_mapping = final_key_mapping
    bench.eval_status = "success"

    # 检测异常零分
    total_samples = stats.get("total_samples", 0)
    if total_samples > 0 and stats.get("accuracy", 0) == 0 and stats.get("score", 0) == 0:
        reason = "Score is 0. Possibly a hidden test set without public labels."
        if stats.get("valid_samples", 0) == 0:
            reason += " (No valid samples found for evaluation)"
        bench.meta["eval_abnormality"] = {
            "is_abnormal": True, "reason": reason, "type": "zero_score",
        }

    writer(StreamEvent(
        current="judger", progress=0.8, message="等待评测结果...",
        data={"output_pred_path": detail_path, "stats": stats}))

    # 确定 detail 文件路径
    if detail_path and os.path.exists(detail_path):
        step2_file_path = str(Path(detail_path).resolve())
    else:
        fallback = outdir / f"text_eval_scored_{run_ts}.json"
        with open(fallback, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        step2_file_path = str(fallback.resolve())

    # 写摘要文件
    summary = _build_summary_payload(run_ts, result, bench, dataset_cache_path_s)
    summary_json_path, summary_txt_path = _write_summary_files(outdir, summary, run_ts)

    bench.meta.setdefault("artifact_paths", {})
    bench.meta["artifact_paths"]["records_path"] = step2_file_path
    bench.meta["eval_detail_path"] = step2_file_path

    # 回写 state
    state["judger"]["bench"] = {
        "bench_name": bench.bench_name,
        "dataset_cache": bench.dataset_cache,
        "bench_dataflow_eval_type": bench.bench_dataflow_eval_type,
        "eval_status": bench.eval_status,
        "meta": bench.meta or {},
        "key_mapping": bench.key_mapping or {},
    }
    state["judger"]["output_result_path"] = summary_json_path
    state["judger"]["output_pred_path"] = step2_file_path

    writer(StreamEvent(
        current="judger", progress=1.0, message="通用文本评测完成",
        data={"output_result_path": summary_json_path, "output_pred_path": step2_file_path}))

    return state
