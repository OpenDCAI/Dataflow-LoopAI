#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数学评测容器的增量落盘测试。

背景：评测跑完后容器才写一次 ``result.json``，中途任何失败（某道题请求超时、
被 Judger 杀掉、容器 OOM）都会让已经算出来的结果全部丢失 —— 挂在一道题上，
前面几十道的完整结果也跟着没了。现在改成每道题跑完写一次，并且用
"先写 .tmp 再 os.replace" 保证任何时刻文件都是完整的合法 JSON。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CONTAINER_SCRIPT = (
    _REPO_ROOT / "loopai" / "skills" / "Judger" / "docker" / "math_eval" / "evaluate_math.py"
)


def _load_container_module():
    """容器脚本不在包内，按文件路径加载。"""
    spec = importlib.util.spec_from_file_location("math_eval_container", _CONTAINER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


container = _load_container_module()

_CONFIG = {
    "base_model": "Qwen3-8B",
    "dataset": "aime26",
    "enable_thinking": True,
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": -1,
    "min_p": 0.0,
    "presence_penalty": 0.0,
    "max_new_tokens": 38912,
    "max_model_len": 40960,
    "checkpoint_dir": None,
    "key_mapping": {},
    "val_n": 4,
}


def _problem(idx: int, correct: bool) -> dict:
    return {
        "problem_id": idx,
        "ground_truth": "2",
        "val_n": 4,
        "generations": [{"output_token_count": 100, "finish_reason": "stop"}],
        "num_correct": 1 if correct else 0,
        "majority_vote_correct": correct,
    }


def _write(tmp_path, *, results, num_problems, **overrides):
    kwargs = dict(
        pass_at_n=sum(1 for r in results if r["majority_vote_correct"]),
        total_correct_per_problem=sum(r["num_correct"] for r in results),
        majority_vote_correct_count=sum(1 for r in results if r["majority_vote_correct"]),
        formatted_count=len(results),
        truncated_count=0,
        total=len(results) * 4,
    )
    kwargs.update(overrides)
    path = tmp_path / "result.json"
    container._write_summary(
        str(path), config=_CONFIG, results=results, num_problems=num_problems, **kwargs)
    return path


def test_partial_progress_keeps_finished_problems(tmp_path):
    """跑到第 2 题崩了 —— 前 2 题的结果必须留下，而且能看出总共该跑几题。"""
    results = [_problem(0, True), _problem(1, False)]

    payload = json.loads(_write(tmp_path, results=results, num_problems=30).read_text("utf-8"))

    assert payload["num_problems"] == 30
    assert payload["completed_problems"] == 2
    assert len(payload["results"]) == 2
    assert payload["total_solutions"] == 8


def test_metrics_are_relative_to_work_done(tmp_path):
    results = [_problem(0, True), _problem(1, True), _problem(2, False), _problem(3, False)]

    payload = json.loads(_write(tmp_path, results=results, num_problems=4).read_text("utf-8"))

    assert payload["pass_at_n"] == 2
    assert payload["pass_at_n_pct"] == pytest.approx(50.0)
    # average@n 是「答对的样本数 / 总样本数」：4 题 × 4 样本 = 16 个样本里对 2 个
    assert payload["average_at_n"] == 2
    assert payload["average_at_n_pct"] == pytest.approx(12.5)
    # format_rate 同样按总样本数算：16 个样本里抽出答案的有 4 个
    assert payload["formatted_count"] == 4
    assert payload["format_rate"] == pytest.approx(25.0)
    assert payload["truncation_rate"] == pytest.approx(0.0)


def test_config_is_carried_in_every_write(tmp_path):
    """部分结果也要能自证是哪次运行的参数 —— 事后复现靠它。"""
    payload = json.loads(
        _write(tmp_path, results=[_problem(0, True)], num_problems=30).read_text("utf-8"))

    for key, value in _CONFIG.items():
        assert payload[key] == value
    assert payload["max_new_tokens"] == 38912


def test_rewrite_replaces_previous_partial(tmp_path):
    """后一次写要整体覆盖前一次，不能把旧结果和新结果混在一起。"""
    path = _write(tmp_path, results=[_problem(0, True)], num_problems=30)
    assert json.loads(path.read_text("utf-8"))["completed_problems"] == 1

    path = _write(tmp_path, results=[_problem(0, True), _problem(1, True)], num_problems=30)
    assert json.loads(path.read_text("utf-8"))["completed_problems"] == 2


def test_no_temp_file_left_behind(tmp_path):
    _write(tmp_path, results=[_problem(0, True)], num_problems=30)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["result.json", "summary.json"]


# ---------------------------------------------------------------------------
# 只有指标的 summary.json
# ---------------------------------------------------------------------------

def test_summary_file_has_metrics_without_generations(tmp_path):
    """result.json 带着每题全文（几十 MB），看指标不该被迫解析它。"""
    _write(tmp_path, results=[_problem(0, True), _problem(1, False)], num_problems=30)

    summary = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    full = json.loads((tmp_path / "result.json").read_text("utf-8"))

    assert "results" not in summary
    assert len(full["results"]) == 2
    # 两份文件的指标部分必须一致，不能各算各的
    assert {k: v for k, v in full.items() if k != "results"} == summary


def test_summary_file_tracks_partial_progress(tmp_path):
    """崩在第 2 题时，summary.json 也要能看出跑到哪了。"""
    _write(tmp_path, results=[_problem(0, True), _problem(1, True)], num_problems=30)

    summary = json.loads((tmp_path / "summary.json").read_text("utf-8"))

    assert summary["completed_problems"] == 2
    assert summary["num_problems"] == 30
    assert summary["pass_at_n"] == 2


def test_both_files_are_rewritten_together(tmp_path):
    _write(tmp_path, results=[_problem(0, True)], num_problems=30)
    assert json.loads((tmp_path / "summary.json").read_text("utf-8"))["completed_problems"] == 1

    _write(tmp_path, results=[_problem(0, True), _problem(1, True)], num_problems=30)
    assert json.loads((tmp_path / "summary.json").read_text("utf-8"))["completed_problems"] == 2
    assert len(json.loads((tmp_path / "result.json").read_text("utf-8"))["results"]) == 2


# ---------------------------------------------------------------------------
# 宿主侧改名
# ---------------------------------------------------------------------------

class _Writer:
    version_id = "v1"

    def __call__(self, event):
        pass


def test_host_renames_both_files_into_bench_dir(monkeypatch, tmp_path):
    """容器把两份文件写到挂载点 /outputs；宿主侧两个都要跟着改成带 bench 名的。"""
    from loopai.skills.Judger.utils import evaluate_math as host

    dataset = tmp_path / "aime26.jsonl"
    dataset.write_text('{"problem": "1+1=?", "answer": "2"}\n', encoding="utf-8")
    out_root = tmp_path / "outputs"

    monkeypatch.setattr(host, "_ensure_math_eval_image", lambda writer=None: None)
    monkeypatch.setattr(host, "_assert_model_is_served", lambda name: None)

    def _fake_docker(command, check=False):
        mount = next(str(a).split(":")[0] for a in command if str(a).endswith(":/outputs"))
        body = json.dumps({"pass_at_n_pct": 50.0})
        Path(mount, "result.json").write_text(body, encoding="utf-8")
        Path(mount, "summary.json").write_text(body, encoding="utf-8")

    monkeypatch.setattr(host.subprocess, "run", _fake_docker)

    state = {
        "task_id": "t1",
        "output_dir": str(out_root),
        "judger": {
            "eval_problem_path": str(dataset),
            "eval_model_name": "Qwen3-8B",
            "bench_name": "aime26",
            "eval_case_num": 4,
        },
    }

    host.run_evaluate_math(state, _Writer())

    bench_dir = out_root / "t1" / "judger" / "v1" / "aime26"
    assert (bench_dir / "aime26_result.json").is_file()
    assert (bench_dir / "aime26_summary.json").is_file()
    # 通用名不该残留
    assert not (bench_dir / "result.json").exists()
    assert not (bench_dir / "summary.json").exists()


def test_creates_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "result.json"

    container._write_summary(
        str(path), config=_CONFIG, results=[], num_problems=3,
        pass_at_n=0, total_correct_per_problem=0, majority_vote_correct_count=0,
        formatted_count=0, truncated_count=0, total=0)

    assert json.loads(path.read_text("utf-8"))["completed_problems"] == 0


def test_empty_results_do_not_divide_by_zero(tmp_path):
    """数据集为空、或循环一次都没进时，百分比不能炸。"""
    payload = json.loads(
        _write(tmp_path, results=[], num_problems=0).read_text("utf-8"))

    assert payload["pass_at_n_pct"] == 0.0
    assert payload["average_at_n_pct"] == 0.0
    assert payload["format_rate"] == 0.0


def test_no_output_file_is_a_noop(tmp_path):
    container._write_summary(
        None, config=_CONFIG, results=[], num_problems=3,
        pass_at_n=0, total_correct_per_problem=0, majority_vote_correct_count=0,
        formatted_count=0, truncated_count=0, total=0)  # 不应抛错

    assert list(tmp_path.iterdir()) == []
