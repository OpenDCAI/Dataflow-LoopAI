# -*- coding: utf-8 -*-
"""Judger CLI entry point — ``loopai-judger`` command."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def _load_config(path: str) -> dict[str, Any]:
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file does not exist: {config_path}")
    if config_path.suffix.lower() == ".json":
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    elif config_path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("YAML config requires PyYAML in the active environment") from exc
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    else:
        raise ValueError("Config file must be .json, .yaml, or .yml")
    if not isinstance(payload, dict):
        raise ValueError("Config root must be a JSON/YAML object")
    return payload


def _unwrap_config_values(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and set(value).issubset(
        {"value", "default", "default_value", "type", "description", "title"}
    ):
        return value["value"]
    if isinstance(value, dict):
        return {key: _unwrap_config_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap_config_values(item) for item in value]
    return value


def _config_sections(config: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Extract Judger overrides and optional output directory from both config shapes."""
    judger = config.get("judger")
    if not isinstance(judger, dict):
        default_states = config.get("default_states")
        judger = default_states.get("judger") if isinstance(default_states, dict) else None
    if not isinstance(judger, dict):
        judger = config.get("states", {}).get("judger") if isinstance(config.get("states"), dict) else {}
    if not isinstance(judger, dict):
        raise ValueError("Config must contain a 'judger' section or 'default_states.judger'")

    output_dir = config.get("output_dir")
    if output_dir is None and isinstance(config.get("default_states"), dict):
        output_dir = config["default_states"].get("output_dir")
    return _unwrap_config_values(judger), output_dir


def _fail(exc: BaseException, message: str) -> None:
    """CLI 层校验失败的统一出口。

    这里直接 raise 的话，调用方（Codex / 前端）拿到的是裸 traceback，和流水线
    里其它失败的 ``{"ok": false, ...}`` 对不上。emit_error 内部会 sys.exit，
    所以调用之后的代码不可达。
    """
    from loopai.common.exception import ErrorCode, emit_error

    emit_error(exc, code=ErrorCode.CONFIG_ERROR, recoverable=True, message=message)


def _apply_config_to_task(db_path: str, task_id: str, config: dict[str, Any]) -> Any:
    """Persist config-file overrides into taskmodel.state before running Judger."""
    judger_overrides, output_dir = _config_sections(config)
    db = Path(db_path).expanduser().resolve()
    with sqlite3.connect(db) as connection:
        row = connection.execute("SELECT state FROM taskmodel WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"Task not found in database: {task_id}")
        state = json.loads(row[0] or "{}")
        if not isinstance(state, dict):
            state = {}
        current_judger = state.setdefault("judger", {})
        if not isinstance(current_judger, dict):
            current_judger = {}
            state["judger"] = current_judger
        current_judger.update(judger_overrides)
        if output_dir is not None:
            state["output_dir"] = output_dir
        connection.execute(
            "UPDATE taskmodel SET state=?, updatedAt=datetime('now') WHERE task_id=?",
            (json.dumps(state, ensure_ascii=False), task_id),
        )
    return {"judger_fields": sorted(judger_overrides), "output_dir": output_dir}


def main():
    parser = argparse.ArgumentParser(
        description="Run LoopAI Judger evaluation pipeline (standalone, no LangGraph)",
    )
    parser.add_argument(
        "--resume", action="store_true", default=False,
        help="Resume from last checkpoint",
    )
    parser.add_argument(
        "--from-step", type=str, default=None,
        help="Force start from a specific pipeline step",
    )
    parser.add_argument("--task-id", help="Task id to load from Configer database")
    parser.add_argument("--db-path", help="SQLite database path")
    parser.add_argument("--output-dir", help="Override output root directory")
    parser.add_argument(
        "--config-path", help="JSON/YAML config; merge its judger section into the database task before running",
    )
    parser.add_argument(
        "--problem-path", "--dataset-path", dest="problem_path",
        help="Override the task's local evaluation dataset path",
    )

    # Runtime overrides map to the existing JUDGER_* environment settings.
    overrides = (
        ("model_path", "JUDGER_MODEL_PATH", "Model path for vLLM"),
        ("model_name", "JUDGER_MODEL_NAME", "Model id exposed by vLLM"),
        ("temperature", "JUDGER_TEMPERATURE", "Sampling temperature"),
        ("top_p", "JUDGER_TOP_P", "Top-p sampling value"),
        ("top_k", "JUDGER_TOP_K", "Top-k sampling value"),
        ("min_p", "JUDGER_MIN_P", "Min-p sampling value"),
        ("presence_penalty", "JUDGER_PRESENCE_PENALTY", "Presence penalty"),
        ("batch_size", "JUDGER_BATCH_SIZE", "Evaluation batch size"),
        ("case_num", "JUDGER_CASE_NUM", "Solutions per problem; math val_n"),
        ("max_tokens", "JUDGER_MAX_TOKENS", "Maximum generated tokens"),
        ("tensor_parallel_size", "JUDGER_TENSOR_PARALLEL_SIZE", "vLLM tensor parallel size"),
        ("gpu_memory_utilization", "JUDGER_GPU_MEMORY_UTILIZATION", "vLLM GPU memory utilization"),
    )
    for dest, _, help_text in overrides:
        parser.add_argument(f"--{dest.replace('_', '-')}", dest=dest, default=None, help=help_text)
    parser.add_argument("--cuda-visible-devices", default=None, help="GPU ids for vLLM, e.g. 4")
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--enable-thinking", dest="enable_thinking", action="store_true")
    thinking.add_argument("--no-thinking", dest="enable_thinking", action="store_false")
    parser.set_defaults(enable_thinking=None)

    args = parser.parse_args()

    if args.db_path:
        os.environ["DB_PATH"] = args.db_path

    config = _load_config(args.config_path) if args.config_path else None
    config_task_id = None
    if config:
        config_task_id = config.get("task_id")
        if not config_task_id and isinstance(config.get("default_states"), dict):
            config_task_id = config["default_states"].get("task_id")
    task_id = args.task_id or os.getenv("TASK_ID") or config_task_id
    if task_id:
        os.environ["TASK_ID"] = str(task_id)
    if config:
        db_path = os.getenv("DB_PATH")
        if not db_path:
            _fail(ValueError("--config-path requires --db-path or DB_PATH"),
                  "--config-path 需要同时提供 --db-path，或设置 DB_PATH 环境变量。")
        if not task_id:
            _fail(ValueError("--config-path requires --task-id, TASK_ID, or task_id in the config"),
                  "--config-path 需要 --task-id，或 TASK_ID 环境变量，或配置文件里的 task_id。")
        try:
            result = _apply_config_to_task(db_path, str(task_id), config)
        except Exception as exc:
            _fail(exc, f"无法把配置写入任务 {task_id}：{exc}")
        if result["output_dir"] is not None and not args.output_dir:
            os.environ["OUTPUT_DIR"] = str(result["output_dir"])
    if args.output_dir:
        os.environ["OUTPUT_DIR"] = args.output_dir
    if args.problem_path:
        os.environ["JUDGER_PROBLEM_PATH"] = args.problem_path
    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.cuda_visible_devices
    if args.enable_thinking is not None:
        os.environ["JUDGER_ENABLE_THINKING"] = str(args.enable_thinking).lower()
    for dest, env_name, _ in overrides:
        value = getattr(args, dest)
        if value is not None:
            os.environ[env_name] = str(value)

    from loopai.skills.Judger import run
    run(resume=args.resume, from_step=args.from_step)


if __name__ == "__main__":
    main()
