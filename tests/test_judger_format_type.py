#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bench 的 ``format_type`` 链路测试。

`bench["format_type"]` 一度是个死字段：[`c882362`] 那个 commit 一边加了
"每个 bench 开始前清掉 eval_format_type" 的循环，一边把
"从 bench 取 format_type 写进 eval_format_type" 那两行删了 —— 加「清」的同时
删了「设」。后果是 mbpp / human-eval 这类**需要转换**的数据集全都卡在 validate：

- validate 按 format_type 选该检查哪套字段，选不中就走 else 分支，
  要求 `prompt/entry_point/canonical_solution/test_list`
- 而 mbpp 原始数据只有 `text/code/test_list/challenge_test_list` → 报"缺 prompt"

`_preprocess_json_file` 同期也是坏的（函数体里引用不在作用域的 `state`），
所以只把 format_type 接回去还不够 —— 转换那一步照样会 NameError。
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
from loopai.skills.Judger.utils import format as fmt


def _write_jsonl(path: Path, *rows: dict) -> Path:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


class _Writer:
    """产物路径依赖 writer.version_id（和 generate / evaluate 一致），所以不能传 lambda。"""

    version_id = "v1"

    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


def _state(tmp_path, *, make_output_dir: bool = True, **judger_overrides) -> dict:
    """造一个和 run_judger_pipeline 同构的 state：
    先有全局值，再按 _BENCH_OVERRIDE_MAP 捕获 override 默认值。
    （少了这一步，_apply_bench_to_state 的「重置」会把这些键 pop 掉。）

    ``make_output_dir=False`` 用来验证格式转换不再依赖事件流 writer 顺手建目录。
    """
    judger = {"eval_model_path": "/models/Qwen3-8B", "eval_temperature": 0.0,
              "eval_top_p": 0.95, "eval_case_num": 1}
    judger.update(judger_overrides)
    if make_output_dir:
        (tmp_path / "t" / "judger").mkdir(parents=True, exist_ok=True)
    return {
        "task_id": "t", "output_dir": str(tmp_path), "judger": dict(judger),
        "_judger_override_defaults": {
            v: judger.get(v) for v in runner._BENCH_OVERRIDE_MAP.values()},
    }


# ---------------------------------------------------------------------------
# format_type 要能从 bench 传到 judger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("format_type", ["mbpp", "human-eval"])
def test_format_type_is_carried_from_bench(tmp_path, format_type):
    state = _state(tmp_path)

    runner._apply_bench_to_state(state, {
        "name": "b", "task_type": "code", "problem_path": "/tmp/p.jsonl",
        "format_type": format_type})

    assert state["judger"]["eval_format_type"] == format_type


def test_format_type_is_cleared_between_benches(tmp_path):
    """上一个 bench 的 format_type 不能漏到下一个，否则校验分支会跟着错。"""
    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "b1", "task_type": "code", "problem_path": "/tmp/p.jsonl",
        "format_type": "mbpp"})

    runner._apply_bench_to_state(state, {
        "name": "b2", "task_type": "code", "problem_path": "/tmp/p.jsonl"})

    assert "eval_format_type" not in state["judger"]


# ---------------------------------------------------------------------------
# validate 按 format_type 选对分支（检查的是**原始**数据集）
# ---------------------------------------------------------------------------

def test_validate_accepts_raw_mbpp_fields(tmp_path):
    """mbpp 原始数据没有 prompt/entry_point —— 走错分支就会误报缺失。"""
    raw = _write_jsonl(tmp_path / "mbpp.jsonl", {
        "task_id": 1, "text": "add two numbers",
        "code": "def add(a, b):\n    return a + b",
        "test_list": ["assert add(1,2)==3"], "challenge_test_list": ["assert add(0,0)==0"]})

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "mbpp", "task_type": "code",
        "problem_path": str(raw), "format_type": "mbpp"})

    runner._step_validate(state, lambda event: None)  # 不抛 SystemExit 即通过


def test_validate_accepts_raw_humaneval_fields(tmp_path):
    raw = _write_jsonl(tmp_path / "human_eval.jsonl", {
        "task_id": "HumanEval/0", "prompt": "def f():\n", "entry_point": "f",
        "canonical_solution": "    return 1\n", "test": "def check(candidate):\n    assert candidate()==1"})

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "human_eval", "task_type": "code",
        "problem_path": str(raw), "format_type": "human-eval"})

    runner._step_validate(state, lambda event: None)


# ---------------------------------------------------------------------------
# 转换本身要能跑（以前会 NameError）
# ---------------------------------------------------------------------------

def test_run_format_data_converts_mbpp(tmp_path):
    raw = _write_jsonl(tmp_path / "mbpp.jsonl", {
        "task_id": 1, "text": "add two numbers",
        "code": "def add(a, b):\n    return a + b",
        "test_list": ["assert add(1,2)==3"], "challenge_test_list": ["assert add(0,0)==0"]})

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "mbpp", "task_type": "code",
        "problem_path": str(raw), "format_type": "mbpp"})

    writer = _Writer()
    fmt.run_format_data(state, writer)
    events = writer.events

    out = Path(state["judger"]["eval_problem_path"])
    # 产物要落在 .../judger/<version_id>/<bench_name>/ 下，和 generate / evaluate 同层。
    # 以前少了后两层，直接写在 .../judger/ 里，多个 bench 会互相混、也分不出哪次运行。
    assert out.parent == tmp_path / "t" / "judger" / "v1" / "mbpp"
    assert out.name == "mbpp_format.jsonl"
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["entry_point"] == "add"
    assert row["canonical_solution"] == "def add(a, b):\n    return a + b"
    assert "add two numbers" in row["prompt"]
    # test_list 和 challenge_test_list 合并
    assert row["test_list"] == ["assert add(1,2)==3", "assert add(0,0)==0"]
    # 原始字段清掉
    assert "code" not in row and "text" not in row
    assert events, "转换过程应该往事件流里发进度"


def test_run_format_data_converts_human_eval(tmp_path):
    raw = _write_jsonl(tmp_path / "human_eval.jsonl", {
        "task_id": "HumanEval/0", "prompt": "def f():\n", "entry_point": "f",
        "canonical_solution": "    return 1\n",
        "test": "def check(candidate):\n    assert candidate()==1\n    assert candidate()==1"})

    state = _state(tmp_path)
    runner._apply_bench_to_state(state, {
        "name": "human_eval", "task_type": "code",
        "problem_path": str(raw), "format_type": "human-eval"})

    fmt.run_format_data(state, _Writer())

    row = json.loads(Path(state["judger"]["eval_problem_path"])
                     .read_text(encoding="utf-8").splitlines()[0])
    assert row["test_list"] == ["assert f()==1", "assert f()==1"]   # candidate 换成 entry_point
    assert "test" not in row
    assert row["canonical_solution"].startswith("def f():")


# ---------------------------------------------------------------------------
# 输出目录 / 错误信息
# ---------------------------------------------------------------------------

def test_format_data_creates_its_own_output_dir(tmp_path):
    """以前靠事件流 writer 顺手 mkdir 才碰巧能写 —— 那是别人的副作用。"""
    raw = _write_jsonl(tmp_path / "mbpp.jsonl", {
        "task_id": 1, "text": "t", "code": "def f():\n    return 1",
        "test_list": ["assert f()==1"], "challenge_test_list": []})

    state = _state(tmp_path, make_output_dir=False)
    runner._apply_bench_to_state(state, {
        "name": "mbpp", "task_type": "code",
        "problem_path": str(raw), "format_type": "mbpp"})

    fmt.run_format_data(state, _Writer())

    assert Path(state["judger"]["eval_problem_path"]).is_file()


def test_missing_file_error_names_the_file_actually_missing(tmp_path):
    """输入和输出的 open() 都会抛 FileNotFoundError，报错要指向真正缺的那个。"""
    raw = _write_jsonl(tmp_path / "present.jsonl", {"task_id": 1})
    events = []

    # 输入在，输出目录不在 —— 缺的是输出
    fmt._preprocess_json_file(
        str(raw), str(tmp_path / "nope" / "out.jsonl"), None, events.append, "judger")

    msg = events[-1].data["msg"]
    assert "out.jsonl" in msg
    assert "present.jsonl" not in msg


def test_missing_input_error_names_the_input(tmp_path):
    events = []

    fmt._preprocess_json_file(
        str(tmp_path / "gone.jsonl"), str(tmp_path / "out.jsonl"),
        None, events.append, "judger")

    assert "gone.jsonl" in events[-1].data["msg"]
