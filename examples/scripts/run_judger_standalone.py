#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone Judger CLI entry point.

Usage:
    # 直接用 starter.yaml（推荐）
    python examples/scripts/run_judger_standalone.py \
        --config-path examples/config/starter.yaml \
        --print-result

    # 也可以用 JSON 配置文件
    python examples/scripts/run_judger_standalone.py \
        --config-path /tmp/judger_config.json \
        --print-result

    # 断点续跑
    python examples/scripts/run_judger_standalone.py \
        --config-path examples/config/starter.yaml \
        --resume

    python examples/scripts/run_judger_standalone.py --list-steps

配置文件支持两种格式：
1. starter.yaml — 自动从 default_states.judger 提取 judger 配置
2. JSON — {"judger": {...}, "task_id": "...", "output_dir": "..."}

环境变量可覆盖配置文件中的值：
    JUDGER_MODEL_PATH, JUDGER_TASK_TYPE, JUDGER_TEMPERATURE,
    JUDGER_TOP_P, JUDGER_PROBLEM_PATH, JUDGER_BATCH_SIZE,
    JUDGER_CASE_NUM, TASK_ID, OUTPUT_DIR, CUDA_VISIBLE_DEVICES,
    JUDGER_CHECKPOINT_PATH
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，无需 PYTHONPATH
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from typing import Any, Dict, Optional


def _load_yaml(path: str) -> Dict[str, Any]:
    """加载 YAML 文件（优先 yaml 库，回退到简单解析）。"""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass
    # 回退：简单 YAML 解析（支持 starter.yaml 的两层嵌套结构）
    return _parse_simple_yaml(path)


def _parse_simple_yaml(path: str) -> Dict[str, Any]:
    """极简 YAML 解析器，只处理 starter.yaml 这种两层缩进结构。"""
    import re
    result: Dict[str, Any] = {}
    stack: list = [(result, -1)]
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            # 匹配 key: value
            m = re.match(r"^(\s*)([\w_-]+):\s*(.*)", stripped)
            if not m:
                continue
            key = m.group(2)
            value = m.group(3).strip()
            indent = len(m.group(1))
            # 弹出比当前缩进深的层级
            while stack and stack[-1][1] >= indent:
                stack.pop()
            parent = stack[-1][0]
            if value in ("", "{}", "[]"):
                # 嵌套对象或空值
                child: Dict[str, Any] = {}
                parent[key] = child
                stack.append((child, indent))
            elif value.startswith('"') and value.endswith('"'):
                parent[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                parent[key] = value[1:-1]
            else:
                # 尝试类型推断
                parent[key] = _coerce_yaml_value(value)
    return result


def _coerce_yaml_value(value: str) -> Any:
    v = value.strip()
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if v.lower() in ("null", "~"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _load_config(config_path: str) -> Dict[str, Any]:
    """加载配置文件，自动识别 YAML/JSON。"""
    ext = Path(config_path).suffix.lower()
    if ext in (".yaml", ".yml"):
        return _load_yaml(config_path)
    elif ext == ".json":
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        # 尝试 YAML 优先
        try:
            return _load_yaml(config_path)
        except Exception:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)


def _extract_state_from_starter_yaml(config: Dict[str, Any]) -> Dict[str, Any]:
    """从 starter.yaml 格式提取 state 字典。

    仅提取 task_id 和 output_dir，不提取 judger 配置。
    Judger 配置优先从 DB (taskmodel) 读取。
    """
    defaults = config.get("default_states", {})

    return {
        "judger": {},
        "task_id": defaults.get("task_id", ""),
        "output_dir": defaults.get("output_dir", "./outputs"),
    }


def _print_result(state: Dict[str, Any]):
    judger = state.get("judger", {})
    print("\n=== Judger Pipeline Complete ===")
    print(f"Task Type:     {judger.get('eval_task_type', 'N/A')}")
    print(f"Task ID:       {state.get('task_id', 'N/A')}")
    print(f"Result Path:   {judger.get('output_result_path', 'N/A')}")
    print(f"Sample Path:   {judger.get('output_case_path', 'N/A')}")
    print(f"Problem Path:  {judger.get('output_problem_path', 'N/A')}")
    pred_path = judger.get("output_pred_path", "")
    if pred_path:
        print(f"Pred Path:     {pred_path}")
    bench = judger.get("bench")
    if bench and isinstance(bench, dict):
        print(f"Bench Name:    {bench.get('bench_name', 'N/A')}")
        print(f"Eval Status:   {bench.get('eval_status', 'N/A')}")
        meta = bench.get("meta") or {}
        stats = meta.get("eval_result") or {}
        if stats:
            print("Stats:")
            for k, v in stats.items():
                print(f"  {k}: {v}")


def _list_steps():
    from loopai.skills.Judger.runner import JUDGER_PIPELINE_STEPS

    print("Available Judger pipeline steps:")
    for step in JUDGER_PIPELINE_STEPS:
        print(f"  - {step}")
    print()
    print("code/text2sql pipeline:")
    print("  validate -> kill_vllm -> start_vllm -> format_data -> generate -> evaluate -> kill_vllm_cleanup -> finish")
    print()
    print("general_text pipeline:")
    print("  validate -> eval_general_text -> finish")


def main():
    parser = argparse.ArgumentParser(
        description="Run LoopAI Judger standalone (no LangGraph required)",
    )
    parser.add_argument(
        "--config-path",
        type=str,
        default=None,
        help="Config file: starter.yaml or JSON",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume from last checkpoint",
    )
    parser.add_argument(
        "--from-step",
        type=str,
        default=None,
        help="Force start from a specific pipeline step",
    )
    parser.add_argument(
        "--task-id",
        type=str,
        default=None,
        help="Override task id",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Override output directory",
    )
    parser.add_argument(
        "--print-result",
        action="store_true",
        default=False,
        help="Print result paths to stderr",
    )
    parser.add_argument(
        "--print-events",
        action="store_true",
        default=False,
        help="Print event list from judger.pkl after execution",
    )
    parser.add_argument(
        "--list-steps",
        action="store_true",
        default=False,
        help="Print available pipeline steps and exit",
    )

    args = parser.parse_args()

    if args.list_steps:
        _list_steps()
        return

    from loopai.skills.Judger import run

    # 从配置文件构建 state（可选）
    state: Optional[Dict[str, Any]] = None
    if args.config_path:
        config = _load_config(args.config_path)
        if "default_states" in config:
            state = _extract_state_from_starter_yaml(config)
        else:
            state = {
                "judger": config.get("judger", {}),
                "output_dir": args.output_dir or config.get("output_dir", "./outputs"),
            }

    # task_id 通过环境变量传递，run() 自动读取
    task_id = args.task_id or os.getenv("TASK_ID")
    if task_id:
        os.environ["TASK_ID"] = task_id
    if args.output_dir:
        os.environ["OUTPUT_DIR"] = args.output_dir

    # run() 内部处理一切：env 校验、writer 创建、emit_success/emit_error
    run(
        state=state,
        resume=args.resume,
        from_step=args.from_step,
    )


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
