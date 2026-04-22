# -*- coding: utf-8 -*-
import os
import json
import time
import traceback
import multiprocessing as mp
import queue as pyqueue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional
from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer

from loopai.agents.Judger.utils.oj.const import field_mapping
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


def _emit(current:str,writer, message: str, *, progress=None, data=None):
    if writer:
        writer(StreamEvent(
            current=current,
            message=message,
            progress=progress,
            data=data
        ).json())


def _run_eval_in_subprocess(
    output_root: str,
    bench: BenchAdapter,
    model_config: ModelConfig,
    result_queue: mp.Queue,
):
    """
    在子进程中执行 DataFlowEvalTool.run_eval，并通过 Queue 回传结果/异常。
    """
    try:
        tool = DataFlowEvalTool(output_root=output_root)
        result = tool.run_eval(bench, model_config)
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


def _judger(state: LoopAIState) -> dict:
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
    base_outdir = Path(
        state.get("output_dir") or "./outputs"
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
        api_key=cfg.get("eval_api_key",""),
        temperature=float(cfg.get("eval_temperature", 0.0)),
        top_p=float(cfg.get("eval_top_p", 1.0)),
        tensor_parallel_size=int(cfg.get("eval_vllm_tensor_parallel_size", 1)),
        max_tokens=int(cfg.get("eval_max_tokens", cfg.get("max_tokens", 2048))),
        gpu_memory_utilization=cfg.get("eval_vllm_gpu_memory_utilization",0.9)
    )

def _generate_key_mapping(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据eval_type构造key_mapping
    判断key_mapping是否为空，如果为空或者解析json错误则 直接读取文件
    """
    key_mapping = {}

    # 用户没有传入key_mapping 或者传入的key_mapping 是错的 从 问题集 中直接读取key_mapping
    eval_type = cfg["bench_dataflow_eval_type"]
    eval_problem_path = cfg["eval_problem_path"]
    count = 0
    
    # 打开文件读取前三行
    with open(eval_problem_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                
                # 开始处理每行数据，将字段名设置为列表
                keys = list(data.keys())
                # 根据任务类型进行分类
                # 例如: key1 时 检测 keys中的词是否是text_key 如果是则加入即可
                for key in keys:
                    if eval_type == "key1_text_score":
                        # key 在候选词集合中
                        if key in field_mapping["text"]:
                            key_mapping["input_text_key"] = key
                    else:
                        if key in field_mapping["question"]:
                            key_mapping["input_question_key"] = key
                        elif eval_type == "key2_qa":
                            # key2_qa
                            if key in field_mapping["target"]:
                                key_mapping["input_target_key"] = key
                            elif key in field_mapping["prediction"]:
                                key_mapping["input_pred_key"] = key
                        elif eval_type == "key2_q_ma":
                            # key2_q_ma
                            if key in field_mapping["targets"]:
                                key_mapping["input_targets_key"] = key
                            elif key in field_mapping["prediction"]:
                                key_mapping["input_pred_key"] = key
                        elif eval_type == "key3_q_choices_a":
                            # key3_q_choices_a
                            if key in field_mapping["choices"]:
                                key_mapping["input_choices_key"] = key
                            elif key in field_mapping["label"]:
                                key_mapping["input_label_key"] = key
                        elif eval_type == "key3_q_choices_as":
                            # key3_q_choices_as
                            if key in field_mapping["choices"]:
                                key_mapping["input_choices_key"] = key
                            elif key in field_mapping["labels"]:
                                key_mapping["input_labels_key"] = key
                            # key3_q_a_rejected
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
            except json.JSONDecodeError:
                # 可以添加异常处理，防止解析失败导致程序崩溃
                continue

    # 从文件中读取key_mapping
    return key_mapping
                
    
    
    


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

    return str(Path(summary_json).resolve()), str(Path(summary_txt).resolve())


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

    eval_result_path = judger_cfg.get("eval_problem_path")
    logger.info(f"eval_result_path:{eval_result_path}")
    if not eval_result_path:
        raise ValueError("缺少评测输入路径：请提供  judger.eval_problem_path")

    if not os.path.exists(eval_result_path):
        raise FileNotFoundError(f"评测输入路径不存在：{eval_result_path}")

    eval_type = judger_cfg.get("bench_dataflow_eval_type")
    if not eval_type:
        raise ValueError(
            "通用文本评测缺少 eval_type。请在 judger.bench_dataflow_eval_type 中提供"
        )
    # 获取bench_cfg或者从judger中获取 key_mapping映射关系
    key_mapping = cfg.get("key_mapping") or {}

    if isinstance(key_mapping, str):
        try:
            key_mapping = json.loads(key_mapping)
        except Exception as e:
            logger.warning(f"传入key_mapping{key_mapping}，解析错误，从文件中自动读取key_mapping")
            key_mapping = _generate_key_mapping(judger_cfg)
            raise ValueError(f"Failed to parse key_mapping as JSON: {key_mapping}, error: {e}")
    else:
        logger.warning(f"传入key_mapping{key_mapping}，解析错误，从文件中自动读取key_mapping")
        key_mapping = _generate_key_mapping(judger_cfg)
    _emit(
        state['current'],
        writer,
        "开始通用文本评测（One-Eval Phase4 / DataFlow）",
        progress=0.0,
        data={
            "eval_result_path": eval_result_path,
            "eval_type": eval_type,
            "key_mapping": json.dumps(key_mapping, indent=4, ensure_ascii=False),
        }
    )
    logger.info(f"keymapping:{key_mapping}")
    rows = _read_jsonl(eval_result_path)
    _emit(state['current'],writer, "读取待评测样本完成", progress=0.10, data={"records": len(rows)})

    # 设置 问题集数据缓存 文件路径
    dataset_cache_path = outdir / f"general_text_dataset_cache_{run_ts}.jsonl"
    _write_jsonl(dataset_cache_path, rows)
    dataset_cache_path = str(dataset_cache_path.resolve())
    _emit(
        state['current'],
        writer,
        "已生成 dataset_cache",
        progress=0.20,
        data={"output_problem_path": str(dataset_cache_path)}
    )
    # 重置 问题集 文件路径
    state['judger']['output_problem_path'] = str(dataset_cache_path)

    bench = BenchAdapter(
        bench_name=judger_cfg.get("bench_name", "general_text_eval"),
        dataset_cache=str(dataset_cache_path),
        bench_dataflow_eval_type=eval_type,
        meta={},
        key_mapping=key_mapping or {}
    )

    if key_mapping:
        bench.meta["key_mapping"] = key_mapping

    return bench, eval_type, dataset_cache_path, key_mapping, outdir, run_ts

def set_gpu(state:LoopAIState):
    os.environ['CUDA_VISIBLE_DEVICES'] = state["judger"].get("cuda_visible_devices","0")
    logger.info(f"CUDA_VISIBLE_DEVICES:{os.environ['CUDA_VISIBLE_DEVICES']} 设置完成")

def eval_general_text_node(state: LoopAIState):
    """
    通用文本评测节点：
    按 DataFlowEvalNode 的逻辑完整接入到 LoopAI。
    """
    set_gpu(state)
    writer = get_stream_writer()
    cfg = _judger(state)

    bench, eval_type, dataset_cache_path, key_mapping, outdir, run_ts = _prepare_bench_from_state(
        state, cfg, writer
    )

    model_config = _build_model_config(cfg)

    _emit(
        state['current'],
        writer,
        "One-Eval Phase4 配置完成，正在调用 DataFlowEvalTool 进行评测",
        progress=0.35,
        data={
            "bench_name": cfg["bench_name"],
            "eval_type": cfg["bench_dataflow_eval_type"],
            "model_name_or_path": getattr(model_config, "model_name_or_path", None),
            "is_api": getattr(model_config, "is_api", None),
            "max_tokens": getattr(model_config, "max_tokens", None),
        }
    )
    
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
            
            # 启动子进程跑通用文本评测。
            # 这里无限等待，但通过心跳事件持续上报进度，避免“假卡死”。
            run_timeout_s = None

            result_queue: mp.Queue = mp.Queue()
            proc = mp.Process(
                target=_run_eval_in_subprocess,
                args=(str(outdir), bench, model_config, result_queue),
                daemon=False,
            )
            proc.start()

            _emit(
                state['current'],
                writer,
                "DataFlowEvalTool 子进程已启动，等待评测完成",
                progress=0.50,
                data={"pid": proc.pid, "timeout_s": "infinite"},
            )

            try:
                wait_start = time.time()
                heartbeat_interval_s = 5
                payload = None
                while proc.is_alive():
                    try:
                        # 子进程可能已产出结果，但因内部线程未退出导致进程仍存活；
                        # 先尝试取结果，取到就不再继续等待 is_alive。
                        payload = result_queue.get_nowait()
                        break
                    except pyqueue.Empty:
                        pass
                    proc.join(timeout=heartbeat_interval_s)
                    if proc.is_alive():
                        elapsed_s = int(time.time() - wait_start)
                        _emit(
                            state['current'],
                            writer,
                            "DataFlowEvalTool 子进程仍在运行，继续等待",
                            progress=0.55,
                            data={"pid": proc.pid, "waited_seconds": elapsed_s},
                        )
                if payload is None:
                    try:
                        payload = result_queue.get_nowait()
                    except pyqueue.Empty:
                        raise RuntimeError(
                            f"[{bench.bench_name}] run_eval 子进程未返回结果，exitcode={proc.exitcode}"
                        )
                logger.info(
                    f"[{bench.bench_name}] 已收到子进程结果: alive={proc.is_alive()}, exitcode={proc.exitcode}"
                )
                if not payload.get("ok"):
                    raise RuntimeError(
                        f"{payload.get('error', 'run_eval subprocess failed')}\n{payload.get('traceback', '')}"
                    )

                result = payload["result"]
                bench.meta = payload.get("bench_meta", bench.meta)
                bench.key_mapping = payload.get("bench_key_mapping", bench.key_mapping)
                bench.eval_status = payload.get("eval_status", bench.eval_status)
            finally:
                # 等待完成后立即清理子进程，避免残留。
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=3)
                    if proc.is_alive():
                        proc.kill()
                        proc.join(timeout=3)
                # 避免 join_thread 因管道状态异常导致阻塞
                result_queue.cancel_join_thread()
                result_queue.close()
                _emit(
                    state['current'],
                    writer,
                    "DataFlowEvalTool 子进程完成，评测过程完成，等待生成评测结果",
                    progress=0.70,
                    data={"pid": proc.pid, "timeout_s": "infinite"},
                )
            # 运行结果输出
            logger.info(f"[result] : {result}")

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
            logger.info("CUDA_VISIBLE_DEVICES from environment:", os.environ.get("CUDA_VISIBLE_DEVICES"))
            logger.error(f"[{bench.bench_name}] 评测失败: {e}")
            bench.eval_status = "failed"
            if not bench.meta:
                bench.meta = {}
            bench.meta["eval_error"] = str(e)
            # 上报错误 直接完成结束
            _emit(
                state['current'],
                writer,
                f"执行发生异常，请解决异常后重新评测：{bench.meta['eval_error']}",
                progress=1.0,
            )
            # 直接结束 Judger 当前节点，不再跳转父图异常路由
            return state

    _emit(
        state['current'],
        writer,
        "等待评测结果...",
        progress=0.80,
        data={
            "output_pred_path": result.get("detail_path"),
            "stats": json.dumps(result.get("stats"), indent=4, ensure_ascii=False),
            "key_mapping": json.dumps(result.get("key_mapping"),indent=4, ensure_ascii=False),
            "pred_key": (bench.meta or {}).get("pred_key"),
            "ref_key": (bench.meta or {}).get("ref_key"),
        }
    )
    # detail_path : step2 路径 -> output
    detail_path = result.get("detail_path")
    stats = result.get("stats") or {}

    if detail_path and os.path.exists(detail_path):
        step2_file_path = str(Path(detail_path).resolve())
    else:
        fallback_detail = outdir / f"text_eval_scored_{run_ts}.json"
        with open(fallback_detail, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        step2_file_path = str(fallback_detail.resolve())

    summary = _build_summary_payload(
        run_ts=run_ts,
        result=result,
        bench=bench,
        dataset_cache_path=str(Path(dataset_cache_path).resolve()),
    )
    summary_json_path, summary_txt_path = _write_summary_files(outdir, summary, run_ts)

    state["bench"] = bench

    _emit(
        state['current'],
        writer,
        "通用文本评测完成",
        progress=1.0,
        data={
            "output_result_path": summary_json_path,
            "output_pred_path":step2_file_path,
            "stats": json.dumps(result.get("stats"), indent=4, ensure_ascii=False),
            "pred_key": (bench.meta or {}).get("pred_key"),
            "ref_key": (bench.meta or {}).get("ref_key"),
            "eval_abnormality": (bench.meta or {}).get("eval_abnormality"),
        }
    )
    state['judger']['output_result_path'] = summary_json_path
    state['judger']['output_pred_path'] = step2_file_path
    logger.info(f"[general_text] output_pred_path: {step2_file_path}")
    logger.info(f"[general_text] output_result_path/summary json: {summary_json_path}")
    logger.info(f"[general_text] summary txt: {summary_txt_path}")

    return state