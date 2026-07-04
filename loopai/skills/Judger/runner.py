from __future__ import annotations

import json
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loopai.common.event_tool import StreamEvent, get_event_writer
from loopai.common.exception import emit_error, ErrorCode
from loopai.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Judger pipeline constants / 流水线步骤常量
# ---------------------------------------------------------------------------

# 完整流水线步骤列表（供 CLI --list-steps 使用）
JUDGER_PIPELINE_STEPS = (
    "validate",            # 校验必填字段和文件有效性
    "kill_vllm",           # 关闭本地 vLLM 进程
    "start_vllm",          # 启动本地 vLLM 服务
    "format_data",         # 可选的数据格式转换
    "generate",            # 生成 code/text2sql 样本
    "evaluate",            # 评测样本并计算 pass@k
    "kill_vllm_cleanup",   # 评测完成后关闭 vLLM
    "eval_general_text",   # 通用文本评测（One-Eval DataFlow）
    "finish",              # 流水线结束
)

# 步骤别名：将 LangGraph 节点名称 / 旧名称映射到标准步骤名
# 用于 CLI --from-step 参数兼容和 checkpoint 恢复
_STEP_ALIASES = {
    "check_required_fields": "validate",
    "check_param_type": "validate",
    "vllm_kill": "kill_vllm",
    "vllm_start": "start_vllm",
    "data_format": "format_data",
    "generate_code": "generate",
    "evaluate_node": "evaluate",
    "eval_general_text_node": "eval_general_text",
    "vllm_kill_node": "kill_vllm_cleanup",
    "finish_node": "finish",
}

# code / text2sql 任务的流水线步骤
_CODE_TEXTSQL_STEPS = (
    "validate",
    "kill_vllm",
    "start_vllm",
    "format_data",
    "generate",
    "evaluate",
    "kill_vllm_cleanup",
    "finish",
)

# general_text 任务的流水线步骤（不需要 vLLM 生命周期管理）
_GENERAL_TEXT_STEPS = (
    "validate",
    "eval_general_text",
    "finish",
)

def normalize_judger_step(step_name: Optional[str]) -> Optional[str]:
    """将步骤名标准化为流水线中定义的标准名称。"""
    if not step_name:
        return None
    step_name = str(step_name)
    if step_name in JUDGER_PIPELINE_STEPS:
        return step_name
    if step_name in _STEP_ALIASES:
        return _STEP_ALIASES[step_name]
    for alias, step in _STEP_ALIASES.items():
        if alias in step_name:
            return step
    for step in JUDGER_PIPELINE_STEPS:
        if step in step_name:
            return step
    return step_name


def _unwrap_configer(config: Dict[str, Any]) -> Dict[str, Any]:
    """把 Configer {key: {value: ..., type: ...}} 格式转为 {key: value}。"""
    out: Dict[str, Any] = {}
    for key, entry in (config or {}).items():
        if isinstance(entry, dict) and "value" in entry:
            out[key] = entry["value"]
        else:
            out[key] = entry
    return out


def _load_task_state(task_id: str) -> Dict[str, Any]:
    """从 Configer（TaskModel.state）+ 事件流 读取 judger 运行态配置。

    优先从 DB 读，回退到事件流推断 last_completed。
    """
    from loopai.skills.Configer import get_configer_task_state_config
    from loopai.common.event_tool import load_stream_events

    judger: Dict[str, Any] = {}
    defaults: Dict[str, Any] = {}

    # 1. 尝试从 DB 读取
    cfg = get_configer_task_state_config(section_name="judger", task_id=task_id)
    if cfg and cfg.get("data"):
        judger = _unwrap_configer(cfg["data"].get("config", {}))

    cfg = get_configer_task_state_config(section_name="default", task_id=task_id)
    if cfg and cfg.get("data"):
        defaults = _unwrap_configer(cfg["data"].get("config", {}))

    last_completed = judger.pop("_last_completed", "")
    current = judger.pop("_current", "judger")

    # 2. 如果 DB 里没有进度，从事件流推断最后完成的步骤
    if not last_completed:
        _COMPLETION_MSG_TO_STEP = {
            "配置校验通过": "validate",
            "vLLM 服务已关闭": None,  #  由 current 区分 kill_vllm/kill_vllm_cleanup
            "vLLM 服务已启动": "start_vllm",
            "数据格式转换完成": "format_data",
            "样本生成完成": "generate",
            "评测完成": "evaluate",
            "通用文本评测完成": "eval_general_text",
            "流水线完成": "finish",
        }
        try:
            events = load_stream_events(name="judger", context_id=task_id)
            for evt in reversed(events):
                msg = getattr(evt, "message", "") or evt.get("message", "")
                step = _COMPLETION_MSG_TO_STEP.get(msg)
                if step is not None:
                    last_completed = step
                    break
                if msg == "vLLM 服务已关闭":
                    cur = getattr(evt, "current", "") or evt.get("current", "")
                    last_completed = normalize_judger_step(cur) or "kill_vllm"
                    break
        except Exception:
            pass

    return {
        "judger": judger,
        "task_id": defaults.get("task_id", task_id),
        "output_dir": defaults.get("output_dir", "./outputs"),
        "last_completed": last_completed,
        "current": current,
    }


def _save_task_progress(state: Dict[str, Any], task_id: str) -> None:
    """将流水线进度写回 Configer（TaskModel.state）。"""
    from loopai.skills.Configer import update_configer_task_state_config

    judger = state.get("judger", {})
    updates: Dict[str, Any] = {}
    for k in ("output_result_path", "output_case_path", "output_problem_path",
              "output_pred_path", "bench"):
        if k in judger and judger[k]:
            updates[k] = judger[k]
    update_configer_task_state_config("judger", updates, task_id=task_id)


def _start_index(step_name: str, steps: tuple) -> int:
    """获取指定步骤在流水线步骤元组中的索引位置。"""
    norm = normalize_judger_step(step_name)
    if norm not in steps:
        available = ", ".join(steps)
        emit_error(
            ValueError(f"Unknown Judger step: {step_name}. Available: {available}"),
            code=ErrorCode.INVALID_INPUT, recoverable=True,
            message=f"Step '{step_name}' is not valid for the current pipeline.",
        )
    return steps.index(norm)


def _resume_step_from_state(state: Dict[str, Any]) -> str:
    """根据 state 中的 last_completed 推断应从哪个步骤恢复。

    last_completed 是上次已完成步骤名，从它的下一个步骤继续。
    如果 last_completed 不存在或为 "finish"，从头开始。
    """
    last_completed = normalize_judger_step(state.get("last_completed"))

    task_type = (state.get("judger") or {}).get("eval_task_type", "code")
    steps = _GENERAL_TEXT_STEPS if task_type == "general_text" else _CODE_TEXTSQL_STEPS

    if last_completed and last_completed in steps and last_completed != "finish":
        next_index = min(_start_index(last_completed, steps) + 1, len(steps) - 1)
        return steps[next_index]
    return steps[0]


def _is_finished(state: Dict[str, Any]) -> bool:
    """检查流水线是否已经完成（last_completed == "finish"）。"""
    return normalize_judger_step(state.get("last_completed")) == "finish"


# ---------------------------------------------------------------------------
# Per-step implementations / 各步骤实现
# ---------------------------------------------------------------------------

def _find_best_checkpoint(
    checkpoints: List[str],
    training_step_losses: List[Dict[str, Any]],
) -> str:
    """根据训练 loss 选择最佳 checkpoint 目录。"""
    best_step = min(training_step_losses, key=lambda x: (x["loss"], x["step"]))["step"]

    def _extract_num(cp: str) -> int:
        return int(cp.split("-")[-1])

    return min(
        checkpoints,
        key=lambda cp: (abs(_extract_num(cp) - best_step), _extract_num(cp)),
    )


def _step_validate(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """验证步骤：检查必填字段、文件存在性和 JSONL 字段结构。"""
    from loopai.schema.states import get_missing_fields

    judger = state.get("judger", {})
    task_type = judger.get("eval_task_type","")

    writer(StreamEvent(
        current=state.get("current"), progress=0.0, message="开始校验配置参数"))

    # 1. 检查通用必填字段
    required_fields = {
        "judger": [
            "eval_temperature", "eval_top_p", "eval_problem_path",
            "eval_case_num", "eval_task_type",
        ],
        "default": ["output_dir", "task_id"],
    }
    missing = get_missing_fields(required_fields, state)

    # 2. 模型路径：未配置时尝试从 trainer 的 checkpoint 推断
    if not missing:
        model_path = judger.get("eval_model_path", "")
        if not model_path or model_path == "":
            trainer = state.get("trainer", {})
            trainer_task_id = trainer.get("trainer_task_id", "")
            training_checkpoints = trainer.get("training_checkpoints", "")
            training_step_losses = trainer.get("training_step_losses", "")
            output_dir = state.get("output_dir", "")
            if trainer_task_id and training_checkpoints and training_step_losses:
                best = _find_best_checkpoint(training_checkpoints, training_step_losses)
                state["judger"]["eval_model_path"] = (
                    f"{output_dir}/{state.get('task_id')}/trainer/"
                    f"{trainer_task_id}/{best}/"
                )
            else:
                missing.setdefault("judger", []).append("eval_model_path")

    # 3. 特定任务类型额外字段
    if not missing and task_type == "text2sql":
        missing = get_missing_fields({"judger": ["eval_text2sql_dir"]}, state)
    if not missing and task_type == "general_text":
        missing = get_missing_fields({"judger": ["bench_dataflow_eval_type"]}, state)

    # 4. 问题文件存在性
    problem_path = judger.get("eval_problem_path", "")
    if not problem_path or not os.path.exists(problem_path):
        missing.setdefault("judger", []).append("eval_problem_path")

    if missing:
        emit_error(
            ValueError(f"Missing required fields: "
                       f"{json.dumps({'missing_fields': missing}, ensure_ascii=False)}"),
            code=ErrorCode.CONFIG_ERROR, recoverable=True,
            message="Judger configuration is incomplete.",
        )

    # 5. JSONL 字段校验
    if task_type == "code":
        from loopai.agents.Judger.utils.oj.data import check_jsonl_fields
        fmt = judger.get("eval_format_type", "")
        if fmt == "mbpp":
            required = ["text", "code", "task_id", "challenge_test_list", "test_list"]
        elif fmt == "human-eval":
            required = ["task_id", "prompt", "entry_point", "canonical_solution", "test"]
        else:
            required = ["task_id", "prompt", "entry_point", "canonical_solution", "test_list"]
        ok, details = check_jsonl_fields(problem_path, required)
        if not ok:
            emit_error(
                ValueError(f"JSONL field validation failed: {json.dumps(details, ensure_ascii=False, indent=2)}"),
                code=ErrorCode.INVALID_INPUT, recoverable=True,
                message=f"Problem file {problem_path} has invalid fields for task type {task_type}.",
            )
    elif task_type == "text2sql":
        from loopai.agents.Judger.utils.oj.data import check_jsonl_fields
        required = ["task_id", "prompt", "db_id", "question", "ground_truth"]
        ok, details = check_jsonl_fields(problem_path, required)
        if not ok:
            emit_error(
                ValueError(f"JSONL field validation failed: {json.dumps(details, ensure_ascii=False, indent=2)}"),
                code=ErrorCode.INVALID_INPUT, recoverable=True,
                message=f"Problem file {problem_path} has invalid fields for task type {task_type}.",
            )

    # 6. 重置输出路径
    state["judger"]["output_result_path"] = ""
    state["judger"]["output_case_path"] = ""
    state["judger"]["output_problem_path"] = ""

    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="配置校验通过",
        data={"task_type": task_type, "problem_path": problem_path}))
    return state


def _step_kill_vllm(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """关闭本地 vLLM 进程（端口 8911）。"""
    from loopai.agents.Judger.utils.oj.vllm_killer import kill_vllm_openai_api_server
    from loopai.agents.Judger.utils.oj.vllm_starter import DEFAULT_VLLM_PORT

    writer(StreamEvent(current=state.get("current"), progress=0.0, message="正在关闭本地 vLLM 服务"))
    kill_vllm_openai_api_server(DEFAULT_VLLM_PORT)
    state["judger"]["eval_base_url"] = None
    writer(StreamEvent(current=state.get("current"), progress=1.0, message="vLLM 服务已关闭"))
    return state


def _step_start_vllm(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """启动本地 vLLM 服务。"""
    from loopai.agents.Judger.utils.oj.vllm_starter import (
        start_vllm_openai_api_server, DEFAULT_VLLM_PORT,
    )
    from loopai.agents.Judger.nodes.eval_general_text_node import set_gpu

    judger = state.get("judger", {})
    set_gpu(state)

    tensor_parallel_size = judger.get("eval_vllm_tensor_parallel_size", 1)
    gpu_memory_utilization = judger.get("eval_vllm_gpu_memory_utilization", 0.9)
    model_path = judger.get("eval_model_path")

    if not model_path:
        emit_error(
            ValueError("eval_model_path is required for local vLLM startup"),
            code=ErrorCode.CONFIG_ERROR, recoverable=True,
            message="Missing eval_model_path for vLLM startup.",
        )

    cuda_devices = judger.get("cuda_visible_devices", "0")
    env_configs = json.dumps({
        "CUDA_VISIBLE_DEVICES": str(cuda_devices),
        "NCCL_P2P_DISABLE": "1", "NCCL_IB_DISABLE": "1",
        "NCCL_DEBUG": "INFO", "NCCL_SOCKET_IFNAME": "lo", "NCCL_BLOCKING_WAIT": "1",
    })

    writer(StreamEvent(
        current=state.get("current"), progress=0.0, message="正在启动本地 vLLM 服务",
        data={"model_path": model_path, "tensor_parallel_size": tensor_parallel_size}))
    try:
        start_vllm_openai_api_server(env_configs, tensor_parallel_size, gpu_memory_utilization, model_path)
    except Exception:
        logger.exception(f"[Judger] vLLM 启动失败")
        writer(StreamEvent(
            current=state.get("current"), progress=1.0, message="vLLM 启动失败",
            data={"error": "vLLM startup failed", "model_path": model_path,
                  "tensor_parallel_size": tensor_parallel_size}))
        raise
    state["judger"]["eval_base_url"] = f"http://localhost:{DEFAULT_VLLM_PORT}/v1"
    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="vLLM 服务已启动",
        data={"base_url": state["judger"]["eval_base_url"]}))
    return state


def _step_format_data(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """可选的数据格式转换步骤（human-eval、mbpp 等）。"""
    judger = state.get("judger", {})
    format_type = judger.get("eval_format_type")

    if format_type and format_type != "":
        from loopai.skills.Judger.utils.format import run_format_data
        writer(StreamEvent(
            current=state.get("current"), progress=0.0,
            message=f"正在进行数据格式转换 [{format_type}]"))
        run_format_data(state, writer)
        writer(StreamEvent(
            current=state.get("current"), progress=1.0, message="数据格式转换完成",
            data={"target": state["judger"]["eval_problem_path"]}))
    else:
        writer(StreamEvent(
            current=state.get("current"), progress=1.0,
            message="未设置 format_type，跳过数据格式化"))

    state["judger"]["output_problem_path"] = state["judger"]["eval_problem_path"]
    return state


def _step_generate(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """样本生成步骤：调用 vLLM 批量生成 code/text2sql 样本。"""
    from loopai.skills.Judger.utils.generate import run_generate_code, run_generate_text2sql

    task_type = state.get("judger", {}).get("eval_task_type", "code")
    batch_size = state.get("judger", {}).get("eval_batch_size", 10)
    case_num = state.get("judger", {}).get("eval_case_num", 10)

    writer(StreamEvent(
        current=state.get("current"), progress=0.0,
        message=f"开始生成样本 [task_type={task_type}]",
        data={"batch_size": batch_size, "case_num": case_num}))

    if task_type == "code":
        result_path = run_generate_code(state, writer)
    elif task_type == "text2sql":
        result_path = run_generate_text2sql(state, writer)
    else:
        emit_error(
            ValueError(f"Unsupported task type for generate step: {task_type}"),
            code=ErrorCode.INVALID_INPUT, recoverable=True,
            message=f"Task type '{task_type}' is not supported for sample generation.",
        )

    state["judger"]["output_case_path"] = result_path
    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="样本生成完成",
        data={"output_case_path": result_path}))
    return state


def _step_evaluate(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """样本评测步骤：执行代码/执行 SQL，计算 pass@k。"""
    from loopai.skills.Judger.utils.evaluate import run_evaluate_code, run_evaluate_text2sql

    task_type = state.get("judger", {}).get("eval_task_type", "code")

    writer(StreamEvent(
        current=state.get("current"), progress=0.0,
        message=f"开始评测样本 [task_type={task_type}]"))

    if task_type == "code":
        result = run_evaluate_code(state, writer)
    elif task_type == "text2sql":
        result = run_evaluate_text2sql(state, writer)
    else:
        emit_error(
            ValueError(f"Unsupported task type for evaluate step: {task_type}"),
            code=ErrorCode.INVALID_INPUT, recoverable=True,
            message=f"Task type '{task_type}' is not supported for evaluation.",
        )

    state["judger"]["output_result_path"] = result.get("result_path", "")
    pass_at_k = result.get("pass_at_k", {})
    state["judger"]["metrics"] = pass_at_k
    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="评测完成",
        data={
            "output_result_path": state["judger"]["output_result_path"],
            "metrics": json.dumps(pass_at_k, ensure_ascii=False) if pass_at_k else "",
        }))
    return state


def _step_eval_general_text(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """通用文本评测：One-Eval DataFlowEvalTool 子进程评测。

    逻辑来自 ``loopai.skills.Judger.utils.eval_general_text``，
    已去除 LangGraph 依赖，进度事件直接走传入的 ``writer``。
    """
    from loopai.skills.Judger.utils.eval_general_text import run_eval_general_text
    return run_eval_general_text(state, writer)


def _run_step(step_name: str, state: Dict[str, Any], writer) -> Dict[str, Any]:
    """分发执行单个流水线步骤。异常先写事件流再抛，确保错误不丢失。"""
    step = normalize_judger_step(step_name)
    dispatch = {
        "validate": _step_validate,
        "kill_vllm": _step_kill_vllm,
        "start_vllm": _step_start_vllm,
        "format_data": _step_format_data,
        "generate": _step_generate,
        "evaluate": _step_evaluate,
        "kill_vllm_cleanup": _step_kill_vllm,
        "eval_general_text": _step_eval_general_text,
    }
    if step in dispatch:
        try:
            return dispatch[step](state, writer)
        except SystemExit:
            # emit_error → sys.exit，错误 JSON 已 print 到 stdout，这里补事件流
            writer(StreamEvent(
                current=state.get("current"), progress=1.0,
                message=f"步骤失败: {step_name}",
                data={"error": "Step failed, see stdout for details"}))
            raise
        except Exception:
            logger.exception(f"[Judger] step {step_name} failed")
            writer(StreamEvent(
                current=state.get("current"), progress=1.0,
                message=f"步骤失败: {step_name}",
                data={"error": traceback.format_exc()}))
            raise
    if step == "finish":
        return state
    emit_error(
        ValueError(f"Unknown executable Judger step: {step_name}"),
        code=ErrorCode.INVALID_INPUT, recoverable=True,
        message=f"Step '{step_name}' is not a recognized Judger pipeline step.",
    )


# ---------------------------------------------------------------------------
# Main pipeline runner / 主流水线执行器
# ---------------------------------------------------------------------------

def run_judger_pipeline(
    state: Optional[Dict[str, Any]],
    task_id: Optional[str] = None,
    resume: bool = False,
    from_step: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """执行 Judger 独立函数流水线（无需 LangGraph）。

    根据 task_type 自动选择流水线路径（code/text2sql 或 general_text）。

    事件通过 ``loopai.common.event_tool.get_event_writer`` 持久化到
    ``<output_dir>/<task_id>/judger.pkl``，事后可用 ``load_events()`` 读取。

    Args:
        state: 包含 ``state["judger"]`` 配置的状态字典。
        task_id: 任务唯一标识，用于读写 state、事件流和输出目录。
        resume: 从 checkpoint 恢复执行。
        from_step: 强制从指定步骤开始。
        **kwargs: 运行时覆盖参数。

    Returns:
        最终状态字典。
    """
    from .runtime_config import resolve_judger_runtime_config

    # 加载或初始化 state
    if resume:
        state = _load_task_state(task_id)
    elif state is not None:
        state = dict(state)
    else:
        state = _load_task_state(task_id)
        if not state.get("judger"):
            # 任务无 state → 回退到全局默认配置
            from loopai.skills.Configer import get_configer_state_config
            cfg = get_configer_state_config(section_name="judger")
            if cfg and cfg.get("data"):
                state["judger"] = _unwrap_configer(cfg["data"].get("config", {}))
            cfg = get_configer_state_config(section_name="default")
            if cfg and cfg.get("data"):
                defaults = _unwrap_configer(cfg["data"].get("config", {}))
                state["task_id"] = defaults.get("task_id", task_id)
                state["output_dir"] = defaults.get("output_dir", "./outputs")

    state.setdefault("judger", {})
    resolve_judger_runtime_config(state, task_id=task_id, **kwargs)

    # task_id 优先用 env/kwargs 解析后的 state["task_id"]，回退到显式传参
    task_id = state.get("task_id") or task_id or ""
    if not task_id:
        emit_error(
            ValueError("task_id is required but was not provided."),
            code=ErrorCode.CONFIG_ERROR, recoverable=True,
            message="Please provide --task-id or set TASK_ID env var.",
        )

    output_dir = state.get("output_dir", "./outputs")

    # ---- 创建事件写入器 ----
    writer = get_event_writer(
        name="judger",
        context_id=task_id,
        log_file_path=output_dir,
    )
    writer(StreamEvent(
        current="judger", progress=0.0, message="Judger pipeline started",
        data={"task_id": task_id, "resume": resume,
              "task_type": (state.get("judger") or {}).get("eval_task_type")}))

    # 根据任务类型选择流水线
    task_type = (state.get("judger") or {}).get("eval_task_type", "code")
    steps = _GENERAL_TEXT_STEPS if task_type == "general_text" else _CODE_TEXTSQL_STEPS

    judger_cfg = state.get("judger") or {}
    logger.info(f"[Judger] task_id={task_id} task_type={task_type} "
                f"pipeline={' -> '.join(steps)}")
    logger.info(f"[Judger] model_path={judger_cfg.get('eval_model_path')} "
                f"problem_path={judger_cfg.get('eval_problem_path')} "
                f"batch_size={judger_cfg.get('eval_batch_size')} "
                f"case_num={judger_cfg.get('eval_case_num')} "
                f"gpu={judger_cfg.get('cuda_visible_devices')} "
                f"tp_size={judger_cfg.get('eval_vllm_tensor_parallel_size')}")

    # 确定起始步骤
    if from_step is not None:
        start_step = normalize_judger_step(from_step)
    elif resume:
        start_step = _resume_step_from_state(state)
    else:
        start_step = steps[0]

    if start_step is None:
        start_step = steps[0]
    start_at = _start_index(start_step, steps)

    if from_step is None and _is_finished(state):
        logger.info(f"[Judger] already finished, skip")
        return state

    # 按序执行各步骤
    for i, step_name in enumerate(steps[start_at:], start_at):
        state["current"] = f"judger.{step_name}"
        logger.info(f"[Judger] step [{i+1}/{len(steps)}] {step_name} starting...")
        _save_task_progress(state, task_id)

        writer(StreamEvent(
            current=state["current"], progress=0.0,
            message=f"步骤开始: {step_name}"))

        if step_name == "finish":
            state["last_completed"] = "finish"
            _save_task_progress(state, task_id)
            writer(StreamEvent(
                current=state["current"], progress=1.0, message="流水线完成"))
            logger.info(f"[Judger] pipeline finished")
            return state

        state = _run_step(step_name, state, writer)
        state["last_completed"] = step_name
        _save_task_progress(state, task_id)

        logger.info(f"[Judger] step [{i+1}/{len(steps)}] {step_name} done")

    return state
