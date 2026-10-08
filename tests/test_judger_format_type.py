#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""code bench 的 ``format_type`` 与题目校验测试。

``format_type`` 现在只有一个用途：给 code bench 选评测数据集（``humaneval+`` /
``mbpp+``），值原样从 bench 搬到 ``judger["format_type"]``。这条链路曾经断过
（加「每个 bench 前清掉 format_type」的同时把「从 bench 写进 judger」删了），
所以这里既测搬运也测清理。

题目文件必须是 evalplus 官方数据集格式（判分在官方镜像里跑），
``validate`` 负责把原始 MBPP / HumanEval、挂错数据集这些情况当场拦下。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger import runner


def _write_jsonl(path: Path, *rows: dict) -> Path:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


class _Writer:
    """产物路径依赖 writer.version_id（和 generate / evaluate 一致），所以不能传 lambda。"""

    version_id = "v1"

    def __init__(self):
        self.events = []
        self.failed: dict | None = None

    def __call__(self, event):
        self.events.append(event)

    def set_failed(self, payload):
        """emit_error 会把结构化 payload 交回来，测试里用它断言报错信息。"""
        self.failed = payload


def _state(tmp_path, **judger_overrides) -> dict:
    """造一个和 run_judger_pipeline 同构的 state：
    先有全局值，再按 _BENCH_OVERRIDE_MAP 捕获 override 默认值。
    （少了这一步，_apply_bench_to_state 的「重置」会把这些键 pop 掉。）
    """
    judger = {"eval_model_path": "/models/Qwen3-8B", "eval_temperature": 0.0,
              "eval_top_p": 0.95, "eval_case_num": 1}
    judger.update(judger_overrides)
    return {
        "task_id": "t", "output_dir": str(tmp_path), "judger": dict(judger),
        "_judger_override_defaults": {
            v: judger.get(v) for v in runner._BENCH_OVERRIDE_MAP.values()},
    }


# ---------------------------------------------------------------------------
# format_type 要能从 bench 传到 judger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("format_type", ["humaneval+", "mbpp+", "livecodebench"])
def test_format_type_is_carried_from_bench(tmp_path, format_type):
    state = _state(tmp_path)

    runner._apply_bench_to_state(state, {
        "name": "b", "task_type": "code", "problem_path": "/tmp/p.jsonl",
        "format_type": format_type})

    assert state["judger"]["format_type"] == format_type


def test_format_type_is_cleared_between_benches(tmp_path):
    """上一个 bench 的 format_type 不能漏到下一个，否则校验分支会跟着错。"""
    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "b1", "task_type": "code", "problem_path": "/tmp/p.jsonl",
        "format_type": "mbpp+"})

    runner._apply_bench_to_state(state, {
        "name": "b2", "task_type": "code", "problem_path": "/tmp/p.jsonl"})

    assert "format_type" not in state["judger"]


# ---------------------------------------------------------------------------
# validate 认的是 evalplus 数据集格式（判分在官方镜像里跑）
# ---------------------------------------------------------------------------

def _evalplus_row(task_id="HumanEval/0"):
    return {"task_id": task_id, "prompt": "def f():\n", "entry_point": "f",
            "canonical_solution": "    return 1\n",
            "base_input": [[]], "plus_input": [[]], "atol": 1e-6,
            "test": "def check(candidate):\n    assert candidate() == 1"}


def test_validate_accepts_evalplus_rows(tmp_path):
    # 官方 HumanEval+ 是 164 题，数量对不上判分也会失败，所以这里按真实数量造
    raw = _write_jsonl(tmp_path / "human_eval_plus.jsonl",
                       *[_evalplus_row(f"HumanEval/{i}") for i in range(164)])

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "human_eval", "task_type": "code",
        "problem_path": str(raw), "format_type": "humaneval+"})

    runner._step_validate(state, lambda event: None)  # 不抛 SystemExit 即通过


def test_validate_rejects_raw_dataset_with_actionable_message(tmp_path):
    """原始 MBPP / HumanEval 缺 base_input/plus_input/atol，判分用不了 —— 要当场拦住，
    并且报错里要点名缺了哪些字段、怎么生成正确的数据。"""
    raw = _write_jsonl(tmp_path / "mbpp.jsonl", {
        "task_id": 1, "text": "add two numbers",
        "code": "def add(a, b):\n    return a + b",
        "test_list": ["assert add(1,2)==3"], "challenge_test_list": ["assert add(0,0)==0"]})

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "mbpp", "task_type": "code",
        "problem_path": str(raw), "format_type": "mbpp+"})

    writer = _Writer()
    with pytest.raises(SystemExit):
        runner._step_validate(state, writer)

    message = writer.failed["message"]
    for field in ("base_input", "plus_input", "atol", "assertion"):   # MBPP+ 的字段表
        assert field in message
    assert "MBPP+" in message
    assert "_ready_mbpp_plus_path" in message  # 直接给出导出命令（复制官方原始 jsonl）


def test_validate_flags_dataset_mismatch(tmp_path):
    """挂错数据集（MBPP+ 给成 humaneval）→ 报错要说清是 task_id 前缀不对。"""
    raw = _write_jsonl(tmp_path / "human_eval_plus.jsonl", {
        "task_id": "HumanEval/0", "prompt": "def f():\n", "entry_point": "f",
        "canonical_solution": "    return 1\n", "base_input": [[]], "plus_input": [[]],
        "atol": 1e-6, "test": "def check(candidate):\n    pass"})

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "mbpp", "task_type": "code",
        "problem_path": str(raw), "format_type": "mbpp+"})

    writer = _Writer()
    with pytest.raises(SystemExit):
        runner._step_validate(state, writer)

    message = writer.failed["message"]
    assert "MBPP+" in message and "Mbpp/" in message


def test_preflight_missing_code_dataset_gives_dump_command(tmp_path):
    """code bench 的 problem_path 不存在时，报错要顺带给出导出官方数据集的命令。

    这个错误发生在起 vLLM 之前，用户第一眼看到的就是它，不能只丢一个路径。
    """
    missing = tmp_path / "data" / "humaneval_plus.jsonl"
    usable, problems = runner._preflight_benches(
        [{"name": "humaneval", "task_type": "code", "format_type": "humaneval+",
          "problem_path": str(missing)}], "benchlist")

    assert usable == []
    assert len(problems) == 1
    assert str(missing) in problems[0]
    assert "_ready_human_eval_plus_path" in problems[0]


def test_preflight_missing_dataset_hint_skips_unknown_task_type(tmp_path):
    """非 code（或 format_type 认不出来）时不硬塞导出命令，保持原样报错。"""
    missing = tmp_path / "questions.jsonl"
    _, problems = runner._preflight_benches(
        [{"name": "text2sql", "task_type": "text2sql", "text2sql_dir": str(tmp_path),
          "problem_path": str(missing)}], "benchlist")

    assert str(missing) in problems[0]
    assert "_ready_" not in problems[0]


# ---------------------------------------------------------------------------
# LiveCodeBench：自己的流水线 + 认 LCB 的题目文件
# ---------------------------------------------------------------------------

def _lcb_row(question_id: str = "1873_A") -> dict:
    return {"question_id": question_id, "question_content": "cards ...",
            "platform": "codeforces", "contest_date": "2023-08-21T00:00:00",
            "difficulty": "easy", "starter_code": "",
            "public_test_cases": "[]", "private_test_cases": "gASV...", "metadata": "{}"}


def test_livecodebench_pipeline_has_no_host_generation():
    """LCB 的生成在容器里（容器回调本机 vLLM），宿主机就没有 generate / sanitize。"""
    steps = runner._pipeline_for("code", "livecodebench")

    assert "evaluate_livecodebench" in steps
    assert "generate" not in steps and "sanitize" not in steps
    assert steps[0] == "validate"                      # 校验仍然在起 vLLM 之前
    assert steps.index("start_vllm") < steps.index("evaluate_livecodebench")


def test_evalplus_pipeline_is_untouched():
    assert runner._pipeline_for("code", "humaneval+") == runner._CODE_STEPS
    assert runner._pipeline_for("code", "mbpp+") == runner._CODE_STEPS


def test_livecodebench_resume_uses_its_own_pipeline(tmp_path):
    """断点续跑也要按 format_type 选流水线，不能落回 evalplus 那条。"""
    state = _state(tmp_path, eval_task_type="code", format_type="livecodebench")
    state["last_completed"] = "start_vllm"

    assert runner._resume_step_from_state(state) == "evaluate_livecodebench"


def test_run_step_dispatches_livecodebench_to_its_own_step(tmp_path, monkeypatch):
    """步骤名和实现要对上：LCB 走专门那一步，不是 evalplus 的 evaluate。"""
    seen = []

    def fake_lcb(state, writer):
        seen.append("lcb")
        return state

    monkeypatch.setattr(runner, "_step_evaluate_livecodebench", fake_lcb)
    monkeypatch.setattr(runner, "_step_evaluate",
                        lambda state, writer: seen.append("evalplus") or state)

    runner._run_step("evaluate_livecodebench", _state(tmp_path), lambda event: None)

    assert seen == ["lcb"]


def test_validate_accepts_livecodebench_rows(tmp_path):
    """LCB 题目文件就是上游的 test.jsonl（含私有用例），题数不卡死。"""
    raw = _write_jsonl(tmp_path / "test.jsonl",
                       *[_lcb_row(f"1873_{chr(ord('A') + i)}") for i in range(3)])

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "livecodebench", "task_type": "code",
        "problem_path": str(raw), "format_type": "livecodebench",
        "lcb_scenario": "codegeneration"})

    runner._step_validate(state, lambda event: None)   # 不抛 SystemExit 即通过


def test_validate_rejects_non_livecodebench_rows(tmp_path):
    """把 evalplus 的题目挂到 LCB bench 上：字段对不上要当场报出来。"""
    raw = _write_jsonl(tmp_path / "humaneval_plus.jsonl", _evalplus_row())

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "livecodebench", "task_type": "code",
        "problem_path": str(raw), "format_type": "livecodebench",
        "lcb_scenario": "codegeneration"})

    writer = _Writer()
    with pytest.raises(SystemExit):
        runner._step_validate(state, writer)

    message = writer.failed["message"]
    assert "LiveCodeBench" in message
    assert "question_id" in message and "private_test_cases" in message


def test_preflight_missing_livecodebench_dataset_gives_curl(tmp_path):
    """LCB 数据集要自己下载，报错里要直接给出 curl 命令。"""
    missing = tmp_path / "data" / "livecodebench" / "test.jsonl"

    usable, problems = runner._preflight_benches(
        [{"name": "livecodebench", "task_type": "code", "format_type": "livecodebench",
          "lcb_scenario": "codegeneration", "problem_path": str(missing)}], "benchlist")

    assert usable == []
    assert len(problems) == 1
    assert "curl" in problems[0] and "code_generation_lite" in problems[0]
