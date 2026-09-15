#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Judger 失败路径的兜底测试。

崩溃时有两件事必须发生，之前一件都没发生：

1. **把 vLLM 收掉**。``kill_vllm_cleanup`` 只是流水线里的一个普通步骤，异常一
   抛就再也轮不到它，vLLM 于是变成孤儿：占着 GPU 显存和 8911 端口，只能等下一
   次运行开头的 ``kill_vllm`` 顺手清。
2. **产出结构化错误 payload**。``emit_error`` 只是个主动调用的函数，不是异常拦
   截器；容器 / 子进程抛上来的异常原本会直接穿到进程外，前端只看到一坨 traceback。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills import Judger as judger_pkg
from loopai.skills.Judger import runner
from loopai.skills.Judger.utils import vllm_killer, vllm_starter


# ---------------------------------------------------------------------------
# 收 vLLM
# ---------------------------------------------------------------------------

def _record_cleanup(monkeypatch, *, port_open: bool) -> list:
    """替换三个清理出口，返回记录调用的列表。"""
    calls: list = []
    monkeypatch.setattr(vllm_starter, "stop_vllm_server",
                        lambda proc, ev: calls.append(("stop", proc, ev)))
    monkeypatch.setattr(vllm_starter, "is_port_open",
                        lambda host, port: port_open)
    monkeypatch.setattr(vllm_killer, "kill_vllm_openai_api_server",
                        lambda port: calls.append(("pkill", port)))
    return calls


def test_cleanup_closes_vllm_by_process_handle(monkeypatch):
    calls = _record_cleanup(monkeypatch, port_open=False)
    state = {"_vllm_handle": ("PROC", "EVENT")}

    runner._cleanup_vllm(state)

    assert calls == [("stop", "PROC", "EVENT")]
    assert "_vllm_handle" not in state


def test_cleanup_falls_back_to_pkill_when_port_still_open(monkeypatch):
    """vLLM 是 shell=True 起的，terminate 可能只打到那层 shell。"""
    calls = _record_cleanup(monkeypatch, port_open=True)

    runner._cleanup_vllm({"_vllm_handle": ("PROC", "EVENT")})

    assert calls == [
        ("stop", "PROC", "EVENT"),
        ("pkill", vllm_starter.DEFAULT_VLLM_PORT),
    ]


def test_cleanup_does_not_touch_vllm_it_did_not_start(monkeypatch):
    """没起过 vLLM 就不该碰 8911 —— 那上面可能是别人或上一轮手动起的服务。"""
    calls = _record_cleanup(monkeypatch, port_open=True)

    runner._cleanup_vllm({})

    assert calls == []


def test_start_vllm_stores_process_handle(monkeypatch, tmp_path):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(vllm_starter, "start_vllm_openai_api_server",
                        lambda *a, **k: ("PROC", "EVENT"))
    state = {
        "judger": {"eval_model_path": "/models/Qwen3-8B"},
        "output_dir": str(tmp_path),
        "task_id": "t1",
    }

    runner._step_start_vllm(state, lambda event: None)

    assert state["_vllm_handle"] == ("PROC", "EVENT")


def test_pipeline_cleans_up_vllm_when_bench_fails(monkeypatch, tmp_path):
    """主任务 bench 抛异常时，finally 里的清理必须照样跑。"""
    cleaned: list = []
    monkeypatch.setattr(runner, "_cleanup_vllm", lambda state: cleaned.append(True))
    monkeypatch.setattr(runner, "_load_task_state", lambda task_id: {})
    monkeypatch.setattr(runner, "_save_task_progress", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "_run_single_bench",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("评测容器挂了")))

    problem_path = tmp_path / "aime26.jsonl"
    problem_path.write_text('{"problem": "1+1=?", "answer": "2"}\n', encoding="utf-8")

    state = {
        "task_id": "t1",
        "output_dir": str(tmp_path),
        "judger": {
            "eval_model_path": "/models/Qwen3-8B",
            "benchlist": [{"name": "aime26", "task_type": "math",
                           "problem_path": str(problem_path)}],
            "extra_benchlist": [],
        },
    }

    with pytest.raises(RuntimeError):
        runner.run_judger_pipeline(
            state=state, task_id="t1", writer=lambda event: None)

    assert cleaned == [True]


# ---------------------------------------------------------------------------
# 报错兜底
# ---------------------------------------------------------------------------

def _stub_run_entry(monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", "/tmp/db.sqlite3")
    monkeypatch.setenv("TASK_ID", "t1")
    monkeypatch.setattr(judger_pkg, "get_event_writer", lambda **kwargs: None)


def test_run_reports_unhandled_pipeline_exception(monkeypatch, capsys):
    """容器抛上来的异常必须变成结构化 payload，而不是裸 traceback。"""
    _stub_run_entry(monkeypatch)

    def _boom(**kwargs):
        raise RuntimeError("math evaluator exited with status 1")

    monkeypatch.setattr(judger_pkg, "run_judger_pipeline", _boom)

    with pytest.raises(SystemExit) as excinfo:
        judger_pkg.run(state={"task_id": "t1", "output_dir": "/tmp/o"})

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "UNHANDLED_EXCEPTION"
    assert "math evaluator exited with status 1" in payload["error"]["detail"]


def test_run_does_not_double_report_an_emit_error_exit(monkeypatch, capsys):
    """流水线内部已经 emit_error 过的失败（SystemExit）不该被再兜一次。"""
    _stub_run_entry(monkeypatch)

    def _already_reported(**kwargs):
        raise SystemExit(1)

    monkeypatch.setattr(judger_pkg, "run_judger_pipeline", _already_reported)

    with pytest.raises(SystemExit):
        judger_pkg.run(state={"task_id": "t1", "output_dir": "/tmp/o"})

    assert capsys.readouterr().out == ""
