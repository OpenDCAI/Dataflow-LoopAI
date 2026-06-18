from __future__ import annotations

import os
from typing import Any, Dict, Optional

_DEFAULT_THREAD_ID = "judger-default"
_DEFAULT_OUTPUT_DIR = "./outputs"

# JudgerState schema defaults (mirror loopai/schema/states.py JudgerState)
_SCHEMA_DEFAULTS: Dict[str, Any] = {
    "eval_task_type": "code",
    "eval_temperature": 0,
    "eval_top_p": 0.95,
    "eval_batch_size": 10,
    "eval_case_num": 10,
    "eval_vllm_tensor_parallel_size": 2,
    "eval_vllm_gpu_memory_utilization": 0.9,
    "cuda_visible_devices": "0",
    "bench_name": "general_text_eval",
    "bench_dataflow_eval_type": "",
    "key_mapping": {},
}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _judger(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    judger = state.setdefault("judger", {})
    if not isinstance(judger, dict):
        state["judger"] = {}
        return state["judger"]
    return judger


def resolve_judger_runtime_config(
    state: Optional[Dict[str, Any]],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Resolve Judger runtime values from kwargs, env, state, defaults.

    Priority: kwargs > env > state["judger"] > schema defaults.

    Returns a flat dict with resolved keys suitable for the standalone runner.
    """
    judger = _judger(state)
    is_state_dict = isinstance(state, dict)

    # --- model / vllm ---
    model_path = _first_non_empty(
        kwargs.get("judger_model_path"),
        kwargs.get("model_path"),
        os.getenv("JUDGER_MODEL_PATH"),
        judger.get("eval_model_path"),
    )
    task_type = _first_non_empty(
        kwargs.get("judger_task_type"),
        kwargs.get("task_type"),
        os.getenv("JUDGER_TASK_TYPE"),
        judger.get("eval_task_type"),
        _SCHEMA_DEFAULTS["eval_task_type"],
    )
    temperature = _first_non_empty(
        kwargs.get("judger_temperature"),
        os.getenv("JUDGER_TEMPERATURE"),
        judger.get("eval_temperature"),
        _SCHEMA_DEFAULTS["eval_temperature"],
    )
    top_p = _first_non_empty(
        kwargs.get("judger_top_p"),
        os.getenv("JUDGER_TOP_P"),
        judger.get("eval_top_p"),
        _SCHEMA_DEFAULTS["eval_top_p"],
    )
    problem_path = _first_non_empty(
        kwargs.get("judger_problem_path"),
        kwargs.get("problem_path"),
        os.getenv("JUDGER_PROBLEM_PATH"),
        judger.get("eval_problem_path"),
    )
    batch_size = _first_non_empty(
        kwargs.get("judger_batch_size"),
        os.getenv("JUDGER_BATCH_SIZE"),
        judger.get("eval_batch_size"),
        _SCHEMA_DEFAULTS["eval_batch_size"],
    )
    case_num = _first_non_empty(
        kwargs.get("judger_case_num"),
        os.getenv("JUDGER_CASE_NUM"),
        judger.get("eval_case_num"),
        _SCHEMA_DEFAULTS["eval_case_num"],
    )

    # --- optional: format / data ---
    format_type = _first_non_empty(
        kwargs.get("judger_format_type"),
        os.getenv("JUDGER_FORMAT_TYPE"),
        judger.get("eval_format_type"),
    )
    text2sql_dir = _first_non_empty(
        kwargs.get("judger_text2sql_dir"),
        os.getenv("JUDGER_TEXT2SQL_DIR"),
        judger.get("eval_text2sql_dir"),
    )

    # --- vllm local startup ---
    tensor_parallel_size = _first_non_empty(
        kwargs.get("judger_tensor_parallel_size"),
        os.getenv("JUDGER_TENSOR_PARALLEL_SIZE"),
        judger.get("eval_vllm_tensor_parallel_size"),
        judger.get("tensor_parallel_size"),  # 兼容 starter.yaml 中的短键名
        _SCHEMA_DEFAULTS["eval_vllm_tensor_parallel_size"],
    )
    gpu_memory_utilization = _first_non_empty(
        kwargs.get("judger_gpu_memory_utilization"),
        os.getenv("JUDGER_GPU_MEMORY_UTILIZATION"),
        judger.get("eval_vllm_gpu_memory_utilization"),
        _SCHEMA_DEFAULTS["eval_vllm_gpu_memory_utilization"],
    )
    cuda_visible_devices = _first_non_empty(
        kwargs.get("cuda_visible_devices"),
        os.getenv("CUDA_VISIBLE_DEVICES"),
        judger.get("cuda_visible_devices"),
        _SCHEMA_DEFAULTS["cuda_visible_devices"],
    )

    # --- general_text ---
    bench_name = _first_non_empty(
        kwargs.get("judger_bench_name"),
        os.getenv("JUDGER_BENCH_NAME"),
        judger.get("bench_name"),
        _SCHEMA_DEFAULTS["bench_name"],
    )
    bench_dataflow_eval_type = _first_non_empty(
        kwargs.get("judger_bench_dataflow_eval_type"),
        os.getenv("JUDGER_BENCH_DATAFLOW_EVAL_TYPE"),
        judger.get("bench_dataflow_eval_type"),
        _SCHEMA_DEFAULTS["bench_dataflow_eval_type"],
    )
    key_mapping = _first_non_empty(
        kwargs.get("judger_key_mapping"),
        judger.get("key_mapping"),
        _SCHEMA_DEFAULTS["key_mapping"],
    )
    if isinstance(key_mapping, str):
        import json
        try:
            key_mapping = json.loads(key_mapping)
        except (json.JSONDecodeError, TypeError):
            key_mapping = {}

    # --- global ---
    task_id = _first_non_empty(
        kwargs.get("thread_id"),
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state.get("task_id") if is_state_dict else None,
        _DEFAULT_THREAD_ID,
    )
    output_dir = _first_non_empty(
        kwargs.get("output_dir"),
        os.getenv("OUTPUT_DIR"),
        state.get("output_dir") if is_state_dict else None,
        _DEFAULT_OUTPUT_DIR,
    )
    db_path = _first_non_empty(
        kwargs.get("db_path"),
        os.getenv("DB_PATH"),
    )

    # --- type coercion ---
    try:
        temperature = float(temperature) if temperature is not None else 0.0
    except (TypeError, ValueError):
        temperature = 0.0
    try:
        top_p = float(top_p) if top_p is not None else 0.95
    except (TypeError, ValueError):
        top_p = 0.95
    try:
        batch_size = int(batch_size) if batch_size is not None else 10
    except (TypeError, ValueError):
        batch_size = 10
    try:
        case_num = int(case_num) if case_num is not None else 10
    except (TypeError, ValueError):
        case_num = 10
    try:
        tensor_parallel_size = int(tensor_parallel_size) if tensor_parallel_size is not None else 2
    except (TypeError, ValueError):
        tensor_parallel_size = 2
    try:
        gpu_memory_utilization = float(gpu_memory_utilization) if gpu_memory_utilization is not None else 0.9
    except (TypeError, ValueError):
        gpu_memory_utilization = 0.9

    # --- write resolved values back into state ---
    if is_state_dict:
        if task_id and not state.get("task_id"):
            state["task_id"] = task_id
        if output_dir:
            state["output_dir"] = output_dir

        state["judger"]["eval_model_path"] = model_path
        state["judger"]["eval_task_type"] = task_type
        state["judger"]["eval_temperature"] = temperature
        state["judger"]["eval_top_p"] = top_p
        state["judger"]["eval_problem_path"] = problem_path
        state["judger"]["eval_batch_size"] = batch_size
        state["judger"]["eval_case_num"] = case_num
        if format_type is not None:
            state["judger"]["eval_format_type"] = format_type
        if text2sql_dir is not None:
            state["judger"]["eval_text2sql_dir"] = text2sql_dir
        state["judger"]["eval_vllm_tensor_parallel_size"] = tensor_parallel_size
        state["judger"]["eval_vllm_gpu_memory_utilization"] = gpu_memory_utilization
        state["judger"]["cuda_visible_devices"] = cuda_visible_devices
        state["judger"]["bench_name"] = bench_name
        state["judger"]["bench_dataflow_eval_type"] = bench_dataflow_eval_type
        state["judger"]["key_mapping"] = key_mapping
        if db_path:
            state["DB_PATH"] = db_path

    return {
        "thread_id": str(task_id or _DEFAULT_THREAD_ID),
        "output_dir": str(output_dir or _DEFAULT_OUTPUT_DIR),
        "db_path": db_path,
        "model_path": model_path,
        "task_type": str(task_type),
        "temperature": temperature,
        "top_p": top_p,
        "problem_path": problem_path,
        "batch_size": batch_size,
        "case_num": case_num,
        "format_type": format_type,
        "text2sql_dir": text2sql_dir,
        "tensor_parallel_size": tensor_parallel_size,
        "gpu_memory_utilization": gpu_memory_utilization,
        "cuda_visible_devices": str(cuda_visible_devices),
        "bench_name": str(bench_name),
        "bench_dataflow_eval_type": str(bench_dataflow_eval_type or ""),
        "key_mapping": key_mapping if isinstance(key_mapping, dict) else {},
    }
