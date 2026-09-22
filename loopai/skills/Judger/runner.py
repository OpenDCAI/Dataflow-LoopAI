from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loopai.common.event_tool import StreamEvent
from loopai.common.exception import emit_error, ErrorCode
from loopai.skills.Judger.utils.data import check_jsonl_fields
from loopai.skills.Judger.utils.evaluate_code import LIVE_CODEBENCH
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
    "generate",            # 生成 code/text2sql 样本
    "sanitize",            # 从模型输出里提取可执行代码（仅 code）
    "evaluate",            # 评测样本并计算 pass@k
    "evaluate_livecodebench",  # LiveCodeBench：生成 + 判分都在容器里
    "kill_vllm_cleanup",   # 评测完成后关闭 vLLM
    "eval_general_text",   # 通用文本评测（One-Eval DataFlow）
    "evaluate_math",       # 数学评测 Docker runner
    "finish",              # 流水线结束
)

# 步骤别名：将 LangGraph 节点名称 / 旧名称映射到标准步骤名
# 供 _run_step 分发和事件流里的旧消息名兼容使用
_STEP_ALIASES = {
    "check_required_fields": "validate",
    "check_param_type": "validate",
    "vllm_kill": "kill_vllm",
    "vllm_start": "start_vllm",
    "generate_code": "generate",
    "extract_code": "sanitize",
    "sanitize_code": "sanitize",
    "evaluate_node": "evaluate",
    "eval_general_text_node": "eval_general_text",
    "eval_math": "evaluate_math",
    "evaluate_math_node": "evaluate_math",
    "vllm_kill_node": "kill_vllm_cleanup",
    "finish_node": "finish",
}

# code 任务的流水线步骤。比 text2sql 多一步 sanitize：模型返回的是散文里夹
# 代码，得先把可执行的片段提出来（见 utils/sanitize.py）。
_CODE_STEPS = (
    "validate",
    "kill_vllm",
    "start_vllm",
    "generate",
    "sanitize",
    "evaluate",
    "kill_vllm_cleanup",
    "finish",
)

# text2sql 任务的流水线步骤。SQL 的提取在评测步骤内部（compare_sql）完成，
# 不需要单独的 sanitize。
_TEXTSQL_STEPS = (
    "validate",
    "kill_vllm",
    "start_vllm",
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

_MATH_STEPS = (
    "validate",
    "kill_vllm",
    "start_vllm",
    "evaluate_math",
    "kill_vllm_cleanup",
    "finish",
)

# LiveCodeBench 分支。和 evalplus 分支的分工不同：生成与判分都在容器里完成
# （容器通过 --vllm_base_url 回调本机 vLLM），所以没有宿主机 generate/sanitize
# 两步，也就没有 <bench>_sample.jsonl 的「宿主机产出」阶段。
_LCB_STEPS = (
    "validate",
    "kill_vllm",
    "start_vllm",
    "evaluate_livecodebench",
    "kill_vllm_cleanup",
    "finish",
)


def _pipeline_for(task_type: str, format_type: str = "") -> tuple:
    """按 task_type / format_type 选流水线步骤。"""
    if task_type == "general_text":
        return _GENERAL_TEXT_STEPS
    if task_type == "math":
        return _MATH_STEPS
    if task_type == "text2sql":
        return _TEXTSQL_STEPS
    if str(format_type) == LIVE_CODEBENCH:
        return _LCB_STEPS
    return _CODE_STEPS


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
            "样本生成完成": "generate",
            "代码提取完成": "sanitize",
            "评测完成": "evaluate",
            "通用文本评测完成": "eval_general_text",
            "数学评测完成": "evaluate_math",
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
    for k in ("bench_result", "extra_bench_result"):
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

    judger = state.get("judger") or {}
    steps = _pipeline_for(judger.get("eval_task_type", "code"),
                          judger.get("format_type", ""))

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


# 支持的评测任务类型。bench 的 task_type 必须显式命中其中之一，否则会静默
# 落到 code 分支（见 _apply_bench_to_state 的默认值），拿错的数据集评测却不报错。
# 同步维护：loopai/schema/states.py 里 benchlist / extra_benchlist 的
# nested_allowed_values["task_type"]（那是配置 UI 用的元数据，不适合当运行时白名单）。
_JUDGER_TASK_TYPES = ("code", "text2sql", "general_text", "math")

# 每个 bench entry 的必填字段。
_BENCH_REQUIRED_FIELDS = ("name", "task_type", "problem_path")

# 特定 task_type 额外需要的 bench 字段。
_BENCH_TASK_TYPE_REQUIRED_FIELDS = {
    # code 的后端选择（humaneval+ / mbpp+ / livecodebench），没有默认值，必须显式配
    "code": ("format_type",),
    "text2sql": ("text2sql_dir",),
    "general_text": ("eval_type",),
}


def _collect_code_bench_problems(bench: Dict[str, Any], label: str) -> List[str]:
    """code bench 的后端字段：``format_type`` 选后端，``lcb_scenario`` 只在 LCB 侧生效。

    ``lcb_scenario`` **必须显式配**：它缺省会落到 ``codegeneration``，而 ``selfrepair``
    和 codegeneration 共用同一份数据集、同一套字段，漏写不会报错、只会静默跑错 scenario。
    evalplus 的两种 ``format_type`` 上带了它同理 —— 那是写错了地方，不猜。
    """
    from loopai.skills.Judger.utils.evaluate_code import (
        FORMAT_TYPES, LIVE_CODEBENCH, LCB_SCENARIOS)

    format_type = str(bench.get("format_type") or "").strip()
    if not format_type:      # 缺失由 _BENCH_TASK_TYPE_REQUIRED_FIELDS 报
        return []
    if format_type not in FORMAT_TYPES:
        return [f"{label}: 未知 format_type {format_type!r}，"
                f"可选值: {', '.join(FORMAT_TYPES)}"]

    scenario = str(bench.get("lcb_scenario") or "").strip()
    if format_type == LIVE_CODEBENCH:
        if not scenario:
            return [f"{label}: format_type=livecodebench 需要字段 lcb_scenario"
                    f"（可选值: {', '.join(LCB_SCENARIOS)}）"]
        if scenario not in LCB_SCENARIOS:
            return [f"{label}: 未知 lcb_scenario {scenario!r}，"
                    f"可选值: {', '.join(LCB_SCENARIOS)}"]
        return []
    if scenario:
        return [f"{label}: lcb_scenario 只对 format_type=livecodebench 生效，"
                f"当前 format_type={format_type!r}"]
    return []


def _bench_label(bench: Any, group: str, index: int) -> str:
    """生成便于定位的 bench 标签，例如 ``benchlist[0] aime26``。"""
    name = bench.get("name") if isinstance(bench, dict) else None
    return f"{group}[{index}]" + (f" {name}" if name else "")


def _problem_path_dump_hint(task_type: Any, source: Dict[str, Any], path: str) -> str:
    """problem_path 不存在时，给 code bench 补一句「怎么导出官方数据集」。

    其他 task_type 没有标准数据集，返回空串（措辞留在调用方）。
    """
    if task_type != "code":
        return ""
    try:
        from .utils.evaluate_code import dataset_dump_hint, resolve_code_task
        task = resolve_code_task({"format_type": source.get("format_type")})
    except Exception:
        return ""
    return (f"（可用 `{dataset_dump_hint(task, path, lcb_scenario=source.get('lcb_scenario') or '')}`"
            " 导出官方数据集）")


def _collect_bench_problems(
    bench: Any, label: str = "bench", *, check_problem_path: bool = False
) -> List[str]:
    """收集单个 bench entry 的配置问题，不抛错，便于一次性汇总所有 bench。"""
    if not isinstance(bench, dict):
        return [f"{label}: 应为 JSON 对象，实际是 {type(bench).__name__}"]

    problems: List[str] = []
    for field in _BENCH_REQUIRED_FIELDS:
        value = bench.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"{label}: 缺少必填字段 {field}")

    task_type = bench.get("task_type")
    if isinstance(task_type, str) and task_type.strip() and task_type not in _JUDGER_TASK_TYPES:
        problems.append(
            f"{label}: 未知 task_type {task_type!r}，可选值: {', '.join(_JUDGER_TASK_TYPES)}")

    for field in _BENCH_TASK_TYPE_REQUIRED_FIELDS.get(task_type, ()):
        if not bench.get(field):
            problems.append(f"{label}: task_type={task_type} 需要字段 {field}")

    if task_type == "code":
        problems.extend(_collect_code_bench_problems(bench, label))

    # 与 _step_validate 的落地检查保持一致：都用原始路径，不额外展开 ~，
    # 否则预检放行而 validate 失败，反而更难排查。
    problem_path = bench.get("problem_path")
    if check_problem_path and isinstance(problem_path, str) and problem_path.strip():
        if not os.path.exists(problem_path):
            hint = _problem_path_dump_hint(task_type, bench, problem_path)
            problems.append(f"{label}: problem_path 不存在: {problem_path}{hint}")

    return problems


def _emit_bench_config_error(problems: List[str], writer, message: str) -> None:
    """bench 配置错误的统一出口，避免 code / recoverable 在多处漂移。"""
    emit_error(
        ValueError("; ".join(problems)),
        code=ErrorCode.CONFIG_ERROR, recoverable=True,
        stream_writer=writer, message=message,
    )


def _validate_bench(bench: Any, writer=None) -> None:
    """校验单个 bench entry 的结构；有问题时 emit_error（会退出进程）。"""
    problems = _collect_bench_problems(bench)
    if problems:
        _emit_bench_config_error(problems, writer, "评测集配置有误，请修正后重试。")


def _preflight_benches(benches: Any, group: str) -> Tuple[List[Any], List[str]]:
    """按组校验 bench entry，返回 (通过的 bench, 问题列表)。

    连数据文件是否存在一起校验，让配置问题在启动任何 vLLM 之前全部暴露。
    """
    usable: List[Any] = []
    problems: List[str] = []
    for index, bench in enumerate(benches):
        found = _collect_bench_problems(
            bench, _bench_label(bench, group, index), check_problem_path=True)
        problems.extend(found)
        if not found:
            usable.append(bench)
    return usable, problems


def _step_validate(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """验证步骤：检查必填字段、文件存在性和 JSONL 字段结构。"""
    from loopai.schema.states import get_missing_fields

    judger = state.get("judger", {})
    task_type = judger.get("eval_task_type","")

    writer(StreamEvent(
        current=state.get("current"), progress=0.0, message="开始校验配置参数"))

    # 1. 检查通用必填字段
    # eval_problem_path 不在这里：它是 bench.problem_path 的派生字段（见
    # _apply_bench_to_state），无论 bench 有没有配都会拿到一个值（哪怕是空串），
    # 放进必填里查不到任何东西。它的落地检查放在步骤 4，由 bench 结构预检兜底。
    required_fields = {
        "judger": [
            "eval_temperature", "eval_top_p",
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

    # 3. 特定任务类型的额外检查
    # text2sql 的 text2sql_dir / general_text 的 eval_type 已由 bench 预检
    # （_preflight_benches）按 bench entry 校验，且规则更强（空串也算缺失），
    # 这里不再重复一份。
    if not missing and task_type == "math":
        checks = (
            ("eval_case_num", lambda value: int(value) > 0),
            ("eval_max_tokens", lambda value: int(value) > 0),
            ("eval_temperature", lambda value: float(value) >= 0),
            ("eval_top_p", lambda value: 0 < float(value) <= 1),
        )
        for key, predicate in checks:
            try:
                valid = predicate(judger.get(key))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                missing.setdefault("judger", []).append(key)

    # 4. 问题文件：未配置与文件不存在分开报，避免「缺字段」掩盖真正原因
    problem_path = judger.get("eval_problem_path", "")
    if not problem_path:
        missing.setdefault("judger", []).append("eval_problem_path")

    if missing:
        emit_error(
            ValueError(f"Missing required fields: "
                       f"{json.dumps({'missing_fields': missing}, ensure_ascii=False)}"),
            code=ErrorCode.CONFIG_ERROR, recoverable=True,
            message="Judger configuration is incomplete.",
            stream_writer=writer,
        )

    if not os.path.exists(problem_path):
        bench_name = judger.get("bench_name", "")
        prefix = f"评测集 {bench_name} 的" if bench_name else ""
        hint = _problem_path_dump_hint(task_type, judger, problem_path)
        emit_error(
            FileNotFoundError(f"Problem file does not exist: {problem_path}"),
            code=ErrorCode.INVALID_INPUT, recoverable=True,
            stream_writer=writer,
            message=f"{prefix}problem_path 不存在: {problem_path}{hint}",
        )

    # 5. JSONL 字段校验
    from loopai.skills.Judger.utils.data import check_jsonl_fields

    if task_type == "code":
        # 判分在容器里跑，题目必须是判分侧认的那份数据集：evalplus 要求 task_id 前缀、
        # 必需字段、题目数量都对（任何一条不对，判分都只会以 assert 收场），
        # LiveCodeBench 只查字段（题数随 release 版本变）。按 bench 指定的数据集分别检查。
        from loopai.skills.Judger.utils.evaluate_code import (
            check_problem_file, dataset_dump_hint, dataset_label, resolve_code_task)
        try:
            code_dataset = resolve_code_task(judger)
        except ValueError as exc:
            emit_error(
                exc, code=ErrorCode.INVALID_INPUT, recoverable=True, stream_writer=writer,
                message=("code bench 需要指定评测数据集：bench.format_type"
                         "（humaneval+ / mbpp+ / livecodebench）。"),
            )

        problems = check_problem_file(
            problem_path, code_dataset, judger.get("lcb_scenario") or "")
        if problems:
            emit_error(
                ValueError(f"code problem file validation failed: {problems}"),
                code=ErrorCode.INVALID_INPUT, recoverable=True,
                stream_writer=writer,
                message=(f"评测集 {judger.get('bench_name', '')} 的 problem_path 不是 "
                         f"{dataset_label(code_dataset)} 数据集格式：{'；'.join(problems)}。"
                         f"可用 `{dataset_dump_hint(code_dataset, lcb_scenario=judger.get('lcb_scenario') or '')}`"
                         " 生成一份。"),
            )
    elif task_type == "text2sql":
        required = ["task_id", "prompt", "db_id", "question", "ground_truth"]
        ok, details = check_jsonl_fields(problem_path, required)
        if not ok:
            emit_error(
                ValueError(f"JSONL field validation failed: {json.dumps(details, ensure_ascii=False, indent=2)}"),
                code=ErrorCode.INVALID_INPUT, recoverable=True,
                stream_writer=writer,
                message=f"Problem file {problem_path} has invalid fields for task type {task_type}.",
            )
    elif task_type == "math":
        # AIME exports use problem/answer; MATH-style exports commonly use
        # question/target or problem/solution. Validate aliases per row.
        try:
            suffix = os.path.splitext(problem_path)[1].lower()
            if suffix == ".parquet":
                import pyarrow.parquet as pq
                columns = {name.lower() for name in pq.read_schema(problem_path).names}
                rows = None
            elif suffix in {".json", ".jsonl"}:
                with open(problem_path, "r", encoding="utf-8") as handle:
                    if suffix == ".json":
                        payload = json.load(handle)
                        if isinstance(payload, list):
                            rows = payload
                        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
                            rows = payload["data"]
                        elif isinstance(payload, dict):
                            rows = [payload]
                        else:
                            raise ValueError("JSON dataset must be an array of objects")
                    else:
                        # 必须立即求值：惰性生成器会在 with 退出、文件关闭后才被迭代
                        rows = [json.loads(line) for line in handle if line.strip()]
                columns = None
            else:
                raise ValueError(f"unsupported math dataset file type: {suffix}")

            if columns is not None:
                if not columns.intersection({"problem", "question", "prompt", "query", "input"}):
                    raise ValueError("dataset is missing problem/question/prompt/query/input")
                if not columns.intersection({"answer", "target", "final_answer", "solution"}):
                    raise ValueError("dataset is missing answer/target/final_answer/solution")
            else:
                for row_no, row in enumerate(rows, 1):
                    if not isinstance(row, dict):
                        raise ValueError(f"row {row_no} is not a JSON object")
                    row_keys = {str(key).lower() for key in row}
                    if not row_keys.intersection({"problem", "question", "prompt", "query", "input"}):
                        raise ValueError(f"row {row_no} is missing problem/question/prompt/query/input")
                    if not row_keys.intersection({"answer", "target", "final_answer", "solution"}):
                        raise ValueError(f"row {row_no} is missing answer/target/final_answer/solution")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            emit_error(
                exc, code=ErrorCode.INVALID_INPUT, recoverable=True,
                stream_writer=writer,
                message=f"Problem file {problem_path} has invalid math JSONL fields.",
            )

    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="配置校验通过",
        data={"task_type": task_type, "problem_path": problem_path}))
    return state


def _step_kill_vllm(state: Dict[str, Any], writer) -> Dict[str, Any]:

    """关闭本地 vLLM 进程（端口 8911）。"""

    from loopai.skills.Judger.utils.vllm_killer import kill_vllm_openai_api_server
    from loopai.skills.Judger.utils.vllm_starter import DEFAULT_VLLM_PORT

    writer(StreamEvent(current=state.get("current"), progress=0.0, message="正在关闭本地 vLLM 服务"))
    kill_vllm_openai_api_server(DEFAULT_VLLM_PORT)
    state["judger"]["eval_base_url"] = None
    writer(StreamEvent(current=state.get("current"), progress=1.0, message="vLLM 服务已关闭"))
    return state


def _cleanup_vllm(state: Dict[str, Any]) -> None:
    """无条件收掉本次运行启动的 vLLM（成功、失败、emit_error 退出都要走到）。

    ``kill_vllm_cleanup`` 只是流水线里的一个普通步骤，异常一抛就再也轮不到它，
    vLLM 于是变成孤儿：占着 GPU 显存和 8911 端口，直到下一次运行开头的
    ``kill_vllm`` 才被顺手清掉。这里把 ``_vllm_handle`` 用起来，在 finally 里收。
    """
    from loopai.skills.Judger.utils.vllm_killer import kill_vllm_openai_api_server
    from loopai.skills.Judger.utils.vllm_starter import (
        DEFAULT_VLLM_PORT, is_port_open, stop_vllm_server,
    )

    handle = state.pop("_vllm_handle", None)
    if handle is None:
        # 本次运行没起过 vLLM，8911 上可能是别人（或上一轮手动）起的服务，不能碰。
        return

    proc, stop_event = handle
    try:
        stop_vllm_server(proc, stop_event)
    except Exception:
        logger.exception("[Judger] 按进程句柄关闭 vLLM 失败，改用按进程名清理")

    # vLLM 是用 shell=True 起的，terminate 打到的可能是那层 shell；端口还开着
    # 就说明真身没死，退回按进程名清理。
    if is_port_open("localhost", DEFAULT_VLLM_PORT):
        logger.warning(f"[Judger] 端口 {DEFAULT_VLLM_PORT} 仍被占用，按进程名强制清理")
        kill_vllm_openai_api_server(DEFAULT_VLLM_PORT)


def _step_start_vllm(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """启动本地 vLLM 服务。"""
    from loopai.skills.Judger.utils.vllm_starter import (
        start_vllm_openai_api_server, DEFAULT_VLLM_PORT,
    )

    judger = state.get("judger", {})
    os.environ["CUDA_VISIBLE_DEVICES"] = judger.get("cuda_visible_devices", "0")

    tensor_parallel_size = judger.get("eval_vllm_tensor_parallel_size", 1)
    gpu_memory_utilization = judger.get("eval_vllm_gpu_memory_utilization", 0.9)
    model_path = judger.get("eval_model_path")
    # 与调用方约定的模型名（留空时由 runtime_config 取路径最后一段），
    # 必须传给 vLLM，否则它会把整条路径当成对外模型名。
    served_model_name = judger.get("eval_model_name")

    if not model_path:
        emit_error(
            ValueError("eval_model_path is required for local vLLM startup"),
            code=ErrorCode.CONFIG_ERROR, recoverable=True,
            stream_writer=writer,
            message="Missing eval_model_path for vLLM startup.",
        )

    # vLLM 的 stdout 走管道，Judger 一退出就再也读不到（进程崩没崩、有没有 OOM
    # 都查不到），所以同时落盘一份到本次运行的输出目录。
    vllm_log_path = (
        Path(str(state.get("output_dir") or "./outputs")).expanduser().resolve()
        / str(state.get("task_id") or "task")
        / "judger"
        / str(getattr(writer, "version_id", None) or "run")
        / "vllm.log"
    )

    writer(StreamEvent(
        current=state.get("current"), progress=0.0, message="正在启动本地 vLLM 服务",
        data={"model_path": model_path, "served_model_name": served_model_name,
              "tensor_parallel_size": tensor_parallel_size,
              "vllm_log_path": str(vllm_log_path)}))
    try:
        proc, stop_event = start_vllm_openai_api_server(
            tensor_parallel_size, gpu_memory_utilization, model_path,
            vllm_served_model_name=served_model_name, log_path=vllm_log_path)
    except Exception as exc:
        logger.exception(f"[Judger] vLLM 启动失败")
        emit_error(
            exc,
            code=ErrorCode.EXTERNAL_SERVICE_ERROR, recoverable=True,
            stream_writer=writer,
            message=f"vLLM startup failed: model={model_path}, tp_size={tensor_parallel_size}",
        )
    # 存下进程句柄。异常路径下没人持有它，vLLM 就成了孤儿进程，一直占着 GPU
    # 和 8911 端口，只能等下一次运行开头的 kill_vllm 顺手清（见 _cleanup_vllm）。
    state["_vllm_handle"] = (proc, stop_event)
    state["judger"]["eval_base_url"] = f"http://localhost:{DEFAULT_VLLM_PORT}/v1"
    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="vLLM 服务已启动",
        data={"base_url": state["judger"]["eval_base_url"]}))
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
            stream_writer=writer,
            message=f"Task type '{task_type}' is not supported for sample generation.",
        )

    state["judger"]["output_case_path"] = result_path
    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="样本生成完成",
        data={"output_case_path": result_path}))
    return state


def _step_sanitize(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """从模型输出里提取可执行代码（仅 code）。

    单独成一步而不是塞进 evaluate，是为了让"原始输出"和"提取后"都留档：排查
    "评测挂掉到底是模型写错还是我们提错"时，这两个文件对比着看就够了 ——
    不用像以前那样手动复现（线上 task 201 就是这么查的）。
    """
    from loopai.skills.Judger.utils.data import read_problems, stream_jsonl, write_jsonl
    from loopai.skills.Judger.utils.sanitize import sanitize

    judger = state.get("judger", {})
    bench_name = judger.get("bench_name", "bench")
    problem_path = judger["eval_problem_path"]
    sample_path = judger["output_case_path"]

    out_dir = (Path(str(state.get("output_dir", "."))) / str(state.get("task_id"))
               / "judger" / writer.version_id / bench_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    sanitized_path = str(out_dir / f"{bench_name}_sanitized.jsonl")

    problems = read_problems(problem_path)
    rows = []
    method_counts: Dict[str, int] = {}
    dropped_total = 0
    for sample in stream_jsonl(sample_path):
        problem = problems.get(sample["task_id"], {})
        result = sanitize(
            sample["completion"],
            entry_point=problem.get("entry_point"),
            prompt=problem.get("prompt"),
        )
        method_counts[result["extract_method"]] = method_counts.get(result["extract_method"], 0) + 1
        dropped_total += result["dropped_statements"]
        rows.append({
            "task_id": sample["task_id"],
            # 原始输出留着，方便和 solution 对比 —— 这正是这一步存在的意义
            "completion": sample["completion"],
            **result,
        })

    write_jsonl(sanitized_path, rows)
    state["judger"]["output_sanitized_path"] = sanitized_path

    logger.info(f"[Judger] sanitize: {len(rows)} 条，提取方式 {method_counts}，"
                f"丢掉顶层语句 {dropped_total} 条")
    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="代码提取完成",
        data={"output_sanitized_path": sanitized_path,
              "extract_methods": method_counts,
              "dropped_statements": dropped_total}))
    return state


def _step_evaluate(state: Dict[str, Any], writer) -> Dict[str, Any]:

    """样本评测步骤：执行代码/执行 SQL，计算 pass@k。"""

    from loopai.skills.Judger.utils.evaluate import run_evaluate_text2sql
    # code 的判分在 evalplus 官方镜像里跑（见 utils/evaluate_code.py）；
    # text2sql 仍是进程内判分。
    from loopai.skills.Judger.utils.evaluate_code import run_evaluate_code

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
            stream_writer=writer,
            message=f"Task type '{task_type}' is not supported for evaluation.",
        )

    state["judger"]["output_result_path"] = result.get("result_path", "")
    if result.get("summary_path"):
        state["judger"]["output_summary_path"] = result["summary_path"]
    # code 走 evalplus 容器，指标已经算好（百分数口径）；text2sql 还是老的
    # pass_at_k 字典（小数口径），两条路径在这里统一收口。
    metrics = result.get("metrics") or dict(result.get("pass_at_k", {}))
    state["judger"]["metrics"] = metrics
    writer(StreamEvent(
        current=state.get("current"), progress=1.0, message="评测完成",
        data={
            "output_result_path": state["judger"]["output_result_path"],
            "metrics": json.dumps(metrics, ensure_ascii=False) if metrics else "",
        }))
    return state


def _step_eval_general_text(state: Dict[str, Any], writer) -> Dict[str, Any]:

    """通用文本评测：One-Eval DataFlowEvalTool 子进程评测。

    逻辑来自 ``loopai.skills.Judger.utils.eval_general_text``，
    已去除 LangGraph 依赖，进度事件直接走传入的 ``writer``。
    """

    from loopai.skills.Judger.utils.eval_general_text import run_eval_general_text
    return run_eval_general_text(state, writer)


def _step_evaluate_math(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """Run the math evaluator container and publish its metrics."""
    from loopai.skills.Judger.utils.evaluate_math import run_evaluate_math

    result = run_evaluate_math(state, writer)
    state["judger"]["output_result_path"] = result.get("result_path", "")
    state["judger"]["metrics"] = result.get("metrics", {})
    return state


def _step_evaluate_livecodebench(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """LiveCodeBench 评测步骤：生成和判分都在容器里完成（见 utils/evaluate_code.py）。

    容器通过 ``--vllm_base_url`` 回调本机 vLLM，所以这里没有宿主机的 generate /
    sanitize 两步；``<bench>_sample.jsonl`` 是容器跑完后从 LCB 的产物转出来的。
    """
    from loopai.skills.Judger.utils.evaluate_code import run_evaluate_livecodebench

    writer(StreamEvent(
        current=state.get("current"), progress=0.0,
        message="开始评测样本 [task_type=code, format_type=livecodebench]"))

    result = run_evaluate_livecodebench(state, writer)
    state["judger"]["output_case_path"] = result.get("sample_path", "")
    state["judger"]["output_sanitized_path"] = result.get("sanitized_path", "")
    state["judger"]["output_result_path"] = result.get("result_path", "")
    state["judger"]["output_summary_path"] = result.get("summary_path", "")
    state["judger"]["metrics"] = result.get("metrics", {})
    return state


def _run_step(step_name: str, state: Dict[str, Any], writer) -> Dict[str, Any]:
    """分发执行单个流水线步骤。异常先写事件流再抛，确保错误不丢失。"""
    step = normalize_judger_step(step_name)
    dispatch = {
        "validate": _step_validate,
        "kill_vllm": _step_kill_vllm,
        "start_vllm": _step_start_vllm,
        "generate": _step_generate,
        "sanitize": _step_sanitize,
        "evaluate": _step_evaluate,
        "evaluate_livecodebench": _step_evaluate_livecodebench,
        "kill_vllm_cleanup": _step_kill_vllm,
        "eval_general_text": _step_eval_general_text,
        "evaluate_math": _step_evaluate_math,
    }
    if step in dispatch:
        return dispatch[step](state, writer)
    if step == "finish":
        return state
    emit_error(
        ValueError(f"Unknown executable Judger step: {step_name}"),
        code=ErrorCode.INVALID_INPUT, recoverable=True,
        stream_writer=writer,
        message=f"Step '{step_name}' is not a recognized Judger pipeline step.",
    )


# bench 字段 -> judger 字段的「可选覆盖」映射。
# bench 里设置这些字段会覆盖全局默认值；未设置时回落到全局默认（避免多 bench 之间值泄漏）。
_BENCH_OVERRIDE_MAP = {
    "case_num": "eval_case_num",
    "batch_size": "eval_batch_size",
    "temperature": "eval_temperature",
    "top_p": "eval_top_p",
    "max_tokens": "eval_max_tokens",
    "enable_thinking": "eval_enable_thinking",
    "top_k": "eval_top_k",
    "min_p": "eval_min_p",
    "presence_penalty": "eval_presence_penalty",
    "model": "eval_model_name",
}


def _apply_bench_to_state(state: Dict[str, Any], bench: Dict[str, Any]) -> None:
    """将 bench entry 的字段注入到 state["judger"]，使标准 pipeline 可直接运行。

    支持 per-bench 可选覆盖：case_num / batch_size / temperature / top_p /
    top_k / min_p / max_tokens / enable_thinking 在 bench 里设置时覆盖全局默认值，
    方便单个 bench 的特殊需求（例如某个评测集需要更低的 temperature 或关闭思考模式）。
    """
    judger = state.setdefault("judger", {})

    # 1. 重置可覆盖字段到全局默认值（避免上一个 bench 的值残留到下一个）
    defaults = state.get("_judger_override_defaults") or {}
    for _, judger_key in _BENCH_OVERRIDE_MAP.items():
        default_val = defaults.get(judger_key)
        if default_val is not None:
            judger[judger_key] = default_val
        else:
            judger.pop(judger_key, None)

    # 2. 清除 bench 特有字段，避免残留
    for k in ("format_type", "lcb_scenario", "eval_text2sql_dir",
              "bench_dataflow_eval_type", "key_mapping"):
        judger.pop(k, None)

    # 3. 必填字段（每个 bench 都必须有）
    judger["eval_task_type"] = bench.get("task_type", "code")
    judger["eval_problem_path"] = bench.get("problem_path", "")
    # 题目文件现在就是 bench 给的这一份（不再有中间转换产物）；留着给 CLI 显示用。
    judger["output_problem_path"] = judger["eval_problem_path"]
    judger["bench_name"] = bench.get("name", "")

    # 4. bench 特有字段（可选）
    if bench.get("format_type"):
        # 原样搬到 judger 上（步骤只拿到 state，拿不到 bench 本身）：code 靠它选判分
        # 后端和数据集（humaneval+ / mbpp+ / livecodebench）。
        judger["format_type"] = bench["format_type"]
    if bench.get("lcb_scenario"):
        # LiveCodeBench 的三个 scenario（codegeneration / selfrepair /
        # testoutputprediction）题目文件和产物字段都不一样，单独一个字段传下去。
        judger["lcb_scenario"] = bench["lcb_scenario"]
    if bench.get("text2sql_dir"):
        judger["eval_text2sql_dir"] = bench["text2sql_dir"]
    if bench.get("eval_type"):
        judger["bench_dataflow_eval_type"] = bench["eval_type"]
    if bench.get("key_mapping"):
        judger["key_mapping"] = bench["key_mapping"]
    # 5. 可选覆盖字段（bench 里设置则覆盖全局，未设置保持全局默认）
    for bench_key, judger_key in _BENCH_OVERRIDE_MAP.items():
        if bench_key in bench and bench[bench_key] is not None:
            judger[judger_key] = bench[bench_key]


def _run_single_bench(
    state: Dict[str, Any],
    bench: Dict[str, Any],
    writer,
) -> Dict[str, Any]:
    """运行单个 bench 的完整流水线，返回 bench result dict。"""
    _apply_bench_to_state(state, bench)

    task_type = bench["task_type"]
    steps = _pipeline_for(task_type, bench.get("format_type") or "")

    bench_name = bench["name"]
    logger.info(f"[Judger] bench {bench_name} (task_type={task_type}) starting...")
    writer(StreamEvent(
        current="judger", progress=0.0,
        message=f"Bench 开始: {bench_name}",
        data={"bench_name": bench_name, "task_type": task_type}))

    for step_name in steps:
        state["current"] = f"{bench_name}.{step_name}"
        logger.info(f"[Judger] [{bench_name}] step {step_name}")
        if step_name == "finish":
            break
        state = _run_step(step_name, state, writer)

    # 收集结果
    judger = state.get("judger", {})
    if task_type == "general_text":
        bench_data = judger.get("bench") or {}
        result = {
            "bench_name": bench_name,
            "task_type": task_type,
            "output_result_path": judger.get("output_result_path", ""),
            "output_pred_path": judger.get("output_pred_path", ""),
            "eval_status": bench_data.get("eval_status", "success"),
            "meta": bench_data.get("meta", {}),
            "key_mapping": bench_data.get("key_mapping", {}),
            "metrics": (bench_data.get("meta", {})).get("eval_result", {}),
        }
    elif task_type == "math":
        result = {
            "bench_name": bench_name,
            "task_type": task_type,
            "output_result_path": judger.get("output_result_path", ""),
            "metrics": judger.get("metrics", {}),
            "eval_status": "success",
        }
    else:
        result = {
            "bench_name": bench_name,
            "task_type": task_type,
            "output_case_path": judger.get("output_case_path", ""),
            "output_result_path": judger.get("output_result_path", ""),
            "metrics": judger.get("metrics", {}),
            "eval_status": "success",
        }

    writer(StreamEvent(
        current="judger", progress=1.0,
        message=f"Bench 完成: {bench_name}",
        data={"bench_name": bench_name, "result": result}))
    logger.info(f"[Judger] bench {bench_name} done")
    return result


# ---------------------------------------------------------------------------
# Main pipeline runner / 主流水线执行器
# ---------------------------------------------------------------------------

def run_judger_pipeline(
    state: Optional[Dict[str, Any]],
    task_id: Optional[str] = None,
    resume: bool = False,
    # from_step 暂时注释：bench 循环目前总是跑完整条流水线，接了断点续跑再放出来
    # from_step: Optional[str] = None,
    writer: Any = None,
) -> Dict[str, Any]:
    """执行 Judger 独立函数流水线（无需 LangGraph）。

    根据 task_type 自动选择流水线路径（code/text2sql、general_text 或 math）。

    事件通过 ``loopai.common.event_tool.get_event_writer`` 持久化到
    ``<output_dir>/<task_id>/judger.pkl``，事后可用 ``load_events()`` 读取。

    Args:
        state: 包含 ``state["judger"]`` 配置的状态字典。
        task_id: 任务唯一标识，用于读写 state、事件流和输出目录。
        resume: 从 checkpoint 恢复执行。

    Returns:
        最终状态字典。
    """
    from .runtime_config import resolve_judger_runtime_config
    from loopai.skills.Configer import get_configer_state_config

    # 加载或初始化 state
    if resume:
        state = _load_task_state(task_id)
    elif state is not None:
        state = dict(state)
        # If state from starter yaml is incomplete (e.g. missing benchlist),
        # load full judger config from DB (task model state)
        try:
            db_state = _load_task_state(task_id)
            db_judger = db_state.get("judger") or {}
            if db_judger:
                # Merge DB judger fields into state, but keep output_dir from yaml
                db_judger.pop("_last_completed", None)
                db_judger.pop("_current", None)
                for k, v in db_judger.items():
                    if v is not None and v != "":
                        state["judger"][k] = v
        except Exception:
            pass
    else:
        state = _load_task_state(task_id)
        if not state.get("judger"):
            # 任务无 state → 回退到全局默认配置
            cfg = get_configer_state_config(section_name="judger")
            if cfg and cfg.get("data"):
                state["judger"] = _unwrap_configer(cfg["data"].get("config", {}))
            cfg = get_configer_state_config(section_name="default")
            if cfg and cfg.get("data"):
                defaults = _unwrap_configer(cfg["data"].get("config", {}))
                state["task_id"] = defaults.get("task_id", task_id)
                state["output_dir"] = defaults.get("output_dir", "./outputs")

    state.setdefault("judger", {})
    resolve_judger_runtime_config(state, task_id=task_id)

    # 捕获全局默认值，供每个 bench 重置可覆盖字段（见 _apply_bench_to_state）
    state["_judger_override_defaults"] = {
        judger_key: state.get("judger", {}).get(judger_key)
        for _, judger_key in _BENCH_OVERRIDE_MAP.items()
    }

    # task_id 优先用 state["task_id"]，回退到显式传参
    task_id = state.get("task_id") or task_id or ""
    if not task_id:
        emit_error(
            ValueError("task_id is required but was not provided."),
            code=ErrorCode.CONFIG_ERROR, recoverable=True,
            stream_writer=writer,
            message="Please provide --task-id or set TASK_ID env var.",
        )

    output_dir = state.get("output_dir", "./outputs")

    judger_cfg = state.get("judger") or {}

    def _parse_benchlist(val, field_name):
        """将 textarea 传来的 JSON 字符串（整体数组或每行一个对象）解析为 list。

        解析失败一律报错：静默丢弃会让配错的行直接消失，最终表现为
        「少跑了一个 bench」这种很难排查的问题。
        """
        def fail(technical: str, user_message: str) -> None:
            emit_error(
                ValueError(technical),
                code=ErrorCode.CONFIG_ERROR, recoverable=True,
                stream_writer=writer, message=user_message,
            )

        if val is None:
            return []
        if isinstance(val, list):
            return val
        if not isinstance(val, str):
            fail(f"{field_name} must be a JSON array, got {type(val).__name__}",
                 f"{field_name} 格式错误：应为 JSON 数组。")

        text = val.strip()
        if not text:
            return []

        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            parsed = None

        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            fail(f"{field_name} must be a JSON array, got a JSON object",
                 f"{field_name} 格式错误：应为 JSON 数组（[{{...}}]），而不是单个对象。")

        # 整体解析失败 → 按行解析（每行一个 JSON 对象）；解析失败的行要报出来
        items: List[Any] = []
        failures: List[str] = []
        for lineno, line in enumerate(text.split("\n"), 1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                failures.append(f"第 {lineno} 行 ({exc})")
        if failures:
            fail(f"{field_name} has unparsable lines: " + "; ".join(failures),
                 f"{field_name} 有 {len(failures)} 行无法解析为 JSON，请检查格式。")
        return items

    benchlist = _parse_benchlist(judger_cfg.get("benchlist"), "benchlist")
    extra_benchlist = _parse_benchlist(judger_cfg.get("extra_benchlist"), "extra_benchlist")

    if not benchlist and not extra_benchlist:
        emit_error(
            ValueError("benchlist 和 extra_benchlist 都为空，请至少配置一个评测集"),
            code=ErrorCode.CONFIG_ERROR, recoverable=True,
            stream_writer=writer,
            message="Both benchlist and extra_benchlist are empty. Please configure at least one bench.",
        )

    # Bench 预检：在启动任何 vLLM 之前一次性发现所有配置问题，避免跑到第 N 个
    # bench 才发现配错（每个 bench 都要起一次 vLLM 并生成，代价很高）。
    # 主任务配置有误直接失败；附加任务按「失败只记录、不影响主任务」的既有约定，
    # 降级为告警并跳过该 bench。
    _, primary_problems = _preflight_benches(benchlist, "benchlist")
    if primary_problems:
        _emit_bench_config_error(
            primary_problems, writer, "主任务评测集配置有误，请修正后重试。")

    usable_extra, extra_problems = _preflight_benches(extra_benchlist, "extra_benchlist")
    if extra_problems:
        logger.warning(f"[Judger] 跳过配置有误的附加评测集: {extra_problems}")
        writer(StreamEvent(
            current="judger", progress=0.0,
            message=f"跳过配置有误的附加评测集: {'; '.join(extra_problems)}",
            data={"problems": extra_problems}))
        extra_benchlist = usable_extra

    writer(StreamEvent(
        current="judger", progress=0.0, message="Judger pipeline started",
        data={"task_id": task_id, "resume": resume}))

    logger.info(f"[Judger] task_id={task_id} "
                f"benchlist={[b.get('name') for b in benchlist]} "
                f"extra_benchlist={[b.get('name') for b in extra_benchlist]}")

    bench_results: List[Dict[str, Any]] = []
    secondary_results: List[Dict[str, Any]] = []
    state["judger"]["bench_result"] = bench_results
    state["judger"]["extra_bench_result"] = secondary_results

    # vLLM 的清理放在 finally：异常会一路 raise 出去（主任务失败）或直接被
    # emit_error 带出进程，两种情况下 _MATH_STEPS 里的 kill_vllm_cleanup 都轮不到，
    # vLLM 就留在那儿占着 GPU 和端口。
    try:
        # 主任务（失败记录后退出）
        for bench in benchlist:
            try:
                result = _run_single_bench(state, bench, writer)
                bench_results.append(result)
                _save_task_progress(state, task_id)
            except SystemExit:
                bench_results.append({
                    "bench_name": bench.get("name", "unknown"),
                    "eval_status": "failed",
                    "meta": {"error": "Bench evaluation failed"},
                })
                _save_task_progress(state, task_id)
                raise
            except Exception as exc:
                bench_results.append({
                    "bench_name": bench.get("name", "unknown"),
                    "eval_status": "failed",
                    "meta": {"error": str(exc)},
                })
                _save_task_progress(state, task_id)
                raise

        # 附加任务（失败记录后继续）
        for bench in extra_benchlist:
            try:
                result = _run_single_bench(state, bench, writer)
                secondary_results.append(result)
                _save_task_progress(state, task_id)
            except SystemExit:
                secondary_results.append({
                    "bench_name": bench.get("name", "unknown"),
                    "eval_status": "failed",
                    "meta": {"error": "Bench evaluation failed"},
                })
            except Exception as exc:
                secondary_results.append({
                    "bench_name": bench.get("name", "unknown"),
                    "eval_status": "failed",
                    "meta": {"error": str(exc)},
                })
    finally:
        _cleanup_vllm(state)

    state["last_completed"] = "finish"
    _save_task_progress(state, task_id)
    writer(StreamEvent(
        current="finish", progress=1.0, message="流水线完成"))
    logger.info(f"[Judger] pipeline finished")
    return state
