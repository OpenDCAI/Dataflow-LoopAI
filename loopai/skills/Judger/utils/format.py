# -*- coding: utf-8 -*-
"""Standalone data format conversion — no LangGraph dependency.

Extracted from ``loopai.agents.Judger.utils.oj.format``,
replaced ``get_stream_writer()`` with a passed-in ``writer`` parameter.
"""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loopai.common.event_tool import StreamEvent
from loopai.logger import get_logger

logger = get_logger()


def _extract_function_names(code_text: str) -> str:
    pattern = r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
    names = list(dict.fromkeys(re.findall(pattern, code_text, re.MULTILINE)))
    return names[0] if names else ""


def _extract_before_and_func_def(code_text: str) -> str:
    pattern = r"^(.*?def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(.*?\)\s*:)"
    m = re.search(pattern, code_text, re.DOTALL | re.MULTILINE)
    return m.group(1).rstrip() if m else ""


def _mbpp_format(line: dict) -> dict:
    if not isinstance(line, dict):
        return {}
    line["canonical_solution"] = line["code"]
    line["test_list"] = list(dict.fromkeys(line["test_list"] + line["challenge_test_list"]))
    line["prompt"] = (
        _extract_before_and_func_def(line["code"])
        + '\n    """'
        + line["text"]
        + '\n    """'
    )
    line["entry_point"] = _extract_function_names(line["code"])
    for k in ("challenge_test_list", "code", "text", "test_setup_code"):
        line.pop(k, None)
    return line


def _human_eval_format(line: dict) -> dict:
    if not isinstance(line, dict):
        return {}
    line["canonical_solution"] = line["prompt"] + line["canonical_solution"]
    parts = line["test"].split("assert ")
    result = [parts[0]] + [f"assert {p}" for p in parts[1:]]
    cleaned = [item.rstrip(" \n\r") for item in result]
    filtered = [
        ln.replace("candidate", line["entry_point"])
        for ln in cleaned
        if ln.startswith("assert ")
    ]
    filtered = [ln for ln in filtered if ln.strip().startswith("assert ")]
    line["test_list"] = filtered
    del line["test"]
    return line


def _preprocess_json_file(
    input_path: str,
    output_path: str,
    line_processor: Optional[Callable[[dict], Optional[dict]]],
    writer,
) -> None:
    use_temp_file = input_path == output_path
    temp_file = None
    total_lines = 0
    success_lines = 0
    error_lines = 0
    filtered_lines = 0
    cnt_lines = 0

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            total_lines = sum(1 for _ in f)

        if use_temp_file:
            temp_dir = os.path.dirname(os.path.abspath(input_path))
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=temp_dir, delete=False
            )
            outfile = temp_file
        else:
            outfile = open(output_path, "w", encoding="utf-8")

        with open(input_path, "r", encoding="utf-8") as infile, outfile:
            for line in infile:
                cnt_lines += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        error_lines += 1
                        continue
                except json.JSONDecodeError:
                    error_lines += 1
                    continue

                processed = line_processor(data) if line_processor else data
                if processed is None:
                    filtered_lines += 1
                else:
                    json.dump(processed, outfile, ensure_ascii=False)
                    outfile.write("\n")
                    success_lines += 1

                writer(StreamEvent(
                    current=state.get("current", "judger"),
                    progress=round(cnt_lines / total_lines, 1),
                    message="任务数据格式化中",
                    data={"success": success_lines, "filtered": filtered_lines,
                          "error": error_lines, "progress": f"{cnt_lines}/{total_lines}"}))

        if use_temp_file and temp_file:
            if os.path.exists(output_path):
                os.remove(output_path)
            shutil.move(temp_file.name, output_path)

        logger.info(f"预处理完成 total={total_lines} success={success_lines} "
                    f"error={error_lines} filtered={filtered_lines} -> {output_path}")

    except FileNotFoundError:
        logger.error(f"文件不存在: {input_path}")
        writer(StreamEvent(
            current=state.get("current", "judger"), progress=1.0, message="任务数据格式化出错",
            data={"msg": f"文件不存在: {input_path}"}))
    except Exception as e:
        if use_temp_file and temp_file and os.path.exists(temp_file.name):
            os.remove(temp_file.name)
        logger.error(f"格式化意外错误: {e}")
        writer(StreamEvent(
            current=state.get("current", "judger"), progress=1.0, message="任务数据格式化出错",
            data={"msg": str(e)}))
    finally:
        if temp_file:
            temp_file.close()


def run_format_data(state: Dict[str, Any], writer):
    """数据格式转换入口，进度事件通过 ``writer`` 发送。"""
    judger_state = state.get("judger", {})
    method = judger_state.get("eval_format_type") or "human-eval"
    state_task_id = state.get("task_id")
    problem_path = judger_state["eval_problem_path"]
    output_dir = Path(state.get("output_dir", "."))
    problem_file_name = Path(problem_path).stem
    target_format_path = str(
        output_dir / str(state_task_id) / "judger" / f"{problem_file_name}_format.jsonl"
    )

    formatter = {"human-eval": _human_eval_format, "mbpp": _mbpp_format}.get(
        method, _human_eval_format
    )
    _preprocess_json_file(problem_path, target_format_path, formatter, writer)
    state["judger"]["eval_problem_path"] = target_format_path
