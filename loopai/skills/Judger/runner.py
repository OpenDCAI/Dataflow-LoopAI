from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loopai.common.event_tool import StreamEvent, get_event_writer
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

# Checkpoint 表的建表 DDL（SQLite）
_CHECKPOINT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS judger_checkpoints (
    thread_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """递归转换值为 JSON 可序列化形式。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return str(value)


def normalize_judger_step(step_name: Optional[str]) -> Optional[str]:
    """将步骤名标准化为流水线中定义的标准名称。

    支持：标准名称、别名、包含别名的字符串、模糊匹配。
    """
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


def _connect(checkpoint_path: str):
    """创建到 SQLite checkpoint 数据库的连接，并自动建表。"""
    import sqlite3

    d = os.path.dirname(checkpoint_path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(checkpoint_path)
    conn.execute(_CHECKPOINT_TABLE_DDL)
    conn.commit()
    return conn


def save_judger_checkpoint(
    state: Dict[str, Any], thread_id: str, checkpoint_path: str
) -> None:
    """将当前 state 保存到 checkpoint（SQLite UPSERT）。"""
    payload = json.dumps(_json_safe(state), ensure_ascii=False)
    updated_at = datetime.now(timezone.utc).isoformat()
    with _connect(checkpoint_path) as conn:
        conn.execute(
            """INSERT INTO judger_checkpoints(thread_id, state_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(thread_id) DO UPDATE SET
                   state_json = excluded.state_json,
                   updated_at = excluded.updated_at""",
            (thread_id, payload, updated_at),
        )
        conn.commit()


def load_judger_checkpoint(thread_id: str, checkpoint_path: str) -> Dict[str, Any]:
    """从 checkpoint 加载之前保存的 state。"""
    if not os.path.exists(checkpoint_path):
        raise RuntimeError(
            f"No checkpoint found for thread_id={thread_id} in {checkpoint_path}"
        )
    with _connect(checkpoint_path) as conn:
        row = conn.execute(
            "SELECT state_json FROM judger_checkpoints WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(
            f"No checkpoint found for thread_id={thread_id} in {checkpoint_path}"
        )
    return json.loads(row[0])


def _start_index(step_name: str, steps: tuple) -> int:
    """获取指定步骤在流水线步骤元组中的索引位置。"""
    norm = normalize_judger_step(step_name)
    if norm not in steps:
        available = ", ".join(steps)
        raise ValueError(f"Unknown Judger step: {step_name}. Available: {available}")
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
    task_type = judger.get("eval_task_type", "code")

    writer(StreamEvent(
        current="judger", progress=0.0, message="开始校验配置参数"))

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
        raise ValueError(
            f"Missing required fields: "
            f"{json.dumps({'missing_fields': missing}, ensure_ascii=False)}"
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
            raise ValueError(f"JSONL field validation failed: {json.dumps(details, ensure_ascii=False, indent=2)}")
    elif task_type == "text2sql":
        from loopai.agents.Judger.utils.oj.data import check_jsonl_fields
        required = ["task_id", "prompt", "db_id", "question", "ground_truth"]
        ok, details = check_jsonl_fields(problem_path, required)
        if not ok:
            raise ValueError(f"JSONL field validation failed: {json.dumps(details, ensure_ascii=False, indent=2)}")

    # 6. 重置输出路径
    state["judger"]["output_result_path"] = ""
    state["judger"]["output_case_path"] = ""
    state["judger"]["output_problem_path"] = ""

    writer(StreamEvent(
        current="judger", progress=1.0, message="配置校验通过",
        data={"task_type": task_type, "problem_path": problem_path}))
    return state


def _step_kill_vllm(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """关闭本地 vLLM 进程（端口 8911）。"""
    from loopai.agents.Judger.utils.oj.vllm_killer import kill_vllm_openai_api_server
    from loopai.agents.Judger.utils.oj.vllm_starter import DEFAULT_VLLM_PORT

    writer(StreamEvent(current="judger", progress=0.0, message="正在关闭本地 vLLM 服务"))
    kill_vllm_openai_api_server(DEFAULT_VLLM_PORT)
    state["judger"]["eval_base_url"] = None
    writer(StreamEvent(current="judger", progress=1.0, message="vLLM 服务已关闭"))
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
        raise ValueError("eval_model_path is required for local vLLM startup")

    cuda_devices = judger.get("cuda_visible_devices", "0")
    env_configs = json.dumps({
        "CUDA_VISIBLE_DEVICES": str(cuda_devices),
        "NCCL_P2P_DISABLE": "1", "NCCL_IB_DISABLE": "1",
        "NCCL_DEBUG": "INFO", "NCCL_SOCKET_IFNAME": "lo", "NCCL_BLOCKING_WAIT": "1",
    })

    writer(StreamEvent(
        current="judger", progress=0.0, message="正在启动本地 vLLM 服务",
        data={"model_path": model_path, "tensor_parallel_size": tensor_parallel_size}))
    start_vllm_openai_api_server(env_configs, tensor_parallel_size, gpu_memory_utilization, model_path)
    state["judger"]["eval_base_url"] = f"http://localhost:{DEFAULT_VLLM_PORT}/v1"
    writer(StreamEvent(
        current="judger", progress=1.0, message="vLLM 服务已启动",
        data={"base_url": state["judger"]["eval_base_url"]}))
    return state


def _step_format_data(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """可选的数据格式转换步骤（human-eval、mbpp 等）。"""
    from loopai.agents.Judger.utils.oj.format import data_format

    judger = state.get("judger", {})
    format_type = judger.get("eval_format_type")

    if format_type and format_type != "":
        problem_path = judger.get("eval_problem_path", "")
        task_id = state.get("task_id")
        output_dir = Path(state.get("output_dir", "."))
        file_stem = Path(problem_path).stem
        target = output_dir / str(task_id) / "judger" / f"{file_stem}_format.jsonl"

        writer(StreamEvent(
            current="judger", progress=0.0,
            message=f"正在进行数据格式转换 [{format_type}]"))
        data_format(state)
        state["judger"]["eval_problem_path"] = str(target)
        writer(StreamEvent(
            current="judger", progress=1.0, message="数据格式转换完成",
            data={"target": str(target)}))
    else:
        writer(StreamEvent(
            current="judger", progress=1.0,
            message="未设置 format_type，跳过数据格式化"))

    state["judger"]["output_problem_path"] = state["judger"]["eval_problem_path"]
    return state


def _step_generate(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """样本生成步骤：调用 vLLM 批量生成 code/text2sql 样本。"""
    task_type = state.get("judger", {}).get("eval_task_type", "code")
    batch_size = state.get("judger", {}).get("eval_batch_size", 10)
    case_num = state.get("judger", {}).get("eval_case_num", 10)

    writer(StreamEvent(
        current="judger", progress=0.0,
        message=f"开始生成样本 [task_type={task_type}]",
        data={"batch_size": batch_size, "case_num": case_num}))

    if task_type == "code":
        from loopai.agents.Judger.utils.oj.generate import generate_sample_code
        result_path = generate_sample_code(state)
    elif task_type == "text2sql":
        from loopai.agents.Judger.utils.oj.generate import generate_sample_text2sql
        result_path = generate_sample_text2sql(state)
    else:
        raise ValueError(f"Unsupported task type for generate step: {task_type}")

    state["judger"]["output_case_path"] = result_path
    writer(StreamEvent(
        current="judger", progress=1.0, message="样本生成完成",
        data={"output_case_path": result_path}))
    return state


def _step_evaluate(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """样本评测步骤：执行代码/执行 SQL，计算 pass@k。"""
    task_type = state.get("judger", {}).get("eval_task_type", "code")

    writer(StreamEvent(
        current="judger", progress=0.0,
        message=f"开始评测样本 [task_type={task_type}]"))

    if task_type == "code":
        from loopai.agents.Judger.utils.oj.evaluate import evaluate_sample_code
        result = evaluate_sample_code(state)
    elif task_type == "text2sql":
        from loopai.agents.Judger.utils.oj.evaluate import evaluate_sample_text2sql
        result = evaluate_sample_text2sql(state)
    else:
        raise ValueError(f"Unsupported task type for evaluate step: {task_type}")

    state["judger"]["output_result_path"] = result.get("result_path", "")
    writer(StreamEvent(
        current="judger", progress=1.0, message="评测完成",
        data={"output_result_path": state["judger"]["output_result_path"]}))
    return state


def _step_eval_general_text(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """通用文本评测：One-Eval DataFlowEvalTool 子进程评测。

    逻辑来自 ``loopai.skills.Judger.utils.eval_general_text``，
    已去除 LangGraph 依赖，进度事件直接走传入的 ``writer``。
    """
    from loopai.skills.Judger.utils.eval_general_text import run_eval_general_text
    return run_eval_general_text(state, writer)


def _run_step(step_name: str, state: Dict[str, Any], writer) -> Dict[str, Any]:
    """分发执行单个流水线步骤。"""
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
        return dispatch[step](state, writer)
    if step == "finish":
        return state
    raise ValueError(f"Unknown executable Judger step: {step_name}")


# ---------------------------------------------------------------------------
# Main pipeline runner / 主流水线执行器
# ---------------------------------------------------------------------------

def run_judger_pipeline(
    state: Optional[Dict[str, Any]],
    thread_id: str = "judger-default",
    checkpoint_path: Optional[str] = None,
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
        thread_id: checkpoint 和事件的上下文 ID（= task_id）。
        checkpoint_path: SQLite checkpoint 文件路径。
        resume: 从 checkpoint 恢复执行。
        from_step: 强制从指定步骤开始。
        **kwargs: 运行时覆盖参数。

    Returns:
        最终状态字典。
    """
    from .runtime_config import resolve_judger_runtime_config

    if checkpoint_path is None:
        checkpoint_path = os.getenv(
            "JUDGER_CHECKPOINT_PATH", "outputs/judger_checkpoints.sqlite"
        )

    # 加载或初始化 state
    if resume:
        state = load_judger_checkpoint(thread_id, checkpoint_path)
        resolve_judger_runtime_config(state, thread_id=thread_id, **kwargs)
    elif state is None:
        raise ValueError("state is required when resume is False.")
    else:
        state = dict(state) if state is not None else {}
        state.setdefault("judger", {})
        resolve_judger_runtime_config(state, thread_id=thread_id, **kwargs)

    output_dir = state.get("output_dir", "./outputs")

    # ---- 创建事件写入器 ----
    writer = get_event_writer(
        name="judger",
        context_id=thread_id,
        log_file_path=output_dir,
    )
    writer(StreamEvent(
        current="judger", progress=0.0, message="Judger pipeline started",
        data={"task_id": thread_id, "resume": resume,
              "task_type": (state.get("judger") or {}).get("eval_task_type")}))

    # 根据任务类型选择流水线
    task_type = (state.get("judger") or {}).get("eval_task_type", "code")
    steps = _GENERAL_TEXT_STEPS if task_type == "general_text" else _CODE_TEXTSQL_STEPS

    judger_cfg = state.get("judger") or {}
    logger.info(f"[Judger] task_id={thread_id} task_type={task_type} "
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
    state["current"] = "judger"  # agent 身份标识，全程不变
    for i, step_name in enumerate(steps[start_at:], start_at):
        logger.info(f"[Judger] step [{i+1}/{len(steps)}] {step_name} starting...")
        save_judger_checkpoint(state, thread_id, checkpoint_path)

        writer(StreamEvent(
            current="judger", progress=0.0,
            message=f"步骤开始: {step_name}"))

        if step_name == "finish":
            state["last_completed"] = "finish"
            save_judger_checkpoint(state, thread_id, checkpoint_path)
            writer(StreamEvent(
                current="judger", progress=1.0, message="流水线完成"))
            logger.info(f"[Judger] pipeline finished")
            return state

        state = _run_step(step_name, state, writer)
        state["last_completed"] = step_name
        save_judger_checkpoint(state, thread_id, checkpoint_path)

        logger.info(f"[Judger] step [{i+1}/{len(steps)}] {step_name} done")
        writer(StreamEvent(
            current="judger", progress=1.0,
            message=f"步骤完成: {step_name}"))

    return state
