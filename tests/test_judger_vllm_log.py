#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vLLM 输出落盘测试。

背景：vLLM 子进程的 stdout 是管道，只由 Judger 进程里的消费线程读取并转进
logger（控制台）。Judger 一退出，管道读端消失，vLLM 的日志就彻底没了 ——
事后连它崩没崩、有没有 OOM 都查不到。这里锁住"同步写一份到文件"的行为，
以及"子进程退出后要把管道里剩余输出读干净"（崩溃前的最后几行最关键）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger.utils import vllm_starter


def _spawn_printer(lines: int) -> subprocess.Popen:
    code = "for i in range(%d): print('line-%%03d' %% i)" % lines
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )


# ---------------------------------------------------------------------------
# 消费线程的落盘行为
# ---------------------------------------------------------------------------

def test_output_is_written_to_log_file_with_timestamps(tmp_path):
    log_path = tmp_path / "vllm.log"
    proc = _spawn_printer(3)

    with open(log_path, "w", encoding="utf-8", buffering=1) as log_file:
        vllm_starter._consume_subprocess_output(proc, threading.Event(), log_file)

    content = log_path.read_text(encoding="utf-8")
    for index in range(3):
        assert f"line-{index:03d}" in content
    # 每行都带时间戳，方便对齐故障时间点
    assert content.splitlines()[0][:4].isdigit()


def test_tail_is_not_lost_when_child_exits(tmp_path):
    """子进程退出后，管道里剩余的输出必须读完 —— 崩溃现场就在这里。"""
    log_path = tmp_path / "vllm.log"
    proc = _spawn_printer(200)

    with open(log_path, "w", encoding="utf-8", buffering=1) as log_file:
        vllm_starter._consume_subprocess_output(proc, threading.Event(), log_file)

    assert "line-199" in log_path.read_text(encoding="utf-8")


def test_consumer_still_works_without_log_file():
    proc = _spawn_printer(3)

    vllm_starter._consume_subprocess_output(proc, threading.Event(), None)  # 不应抛错


# ---------------------------------------------------------------------------
# 启动时写头部
# ---------------------------------------------------------------------------

class _ImmediatelyDeadProc:
    """poll() 立刻返回退出码，避免 start_vllm 真去等端口。"""

    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 1

    def poll(self):
        return 1

    def terminate(self):
        pass


def test_start_writes_header_with_served_model_name(tmp_path, monkeypatch):
    log_path = tmp_path / "nested" / "vllm.log"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"engine died\n")
    os.close(write_fd)

    class _Completed:
        returncode = 0

    monkeypatch.setattr(vllm_starter.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(
        vllm_starter.subprocess, "Popen",
        lambda *a, **k: _ImmediatelyDeadProc(os.fdopen(read_fd)))

    with pytest.raises(Exception):  # 进程"已退出" → 启动失败
        vllm_starter.start_vllm_openai_api_server(
            1, 0.9, "/models/Qwen3-8B",
            vllm_served_model_name="Qwen3-8B", log_path=log_path)

    content = log_path.read_text(encoding="utf-8")
    # 头部记录了实际启动命令，模型名对不上时一眼能看出来
    assert "--served-model-name Qwen3-8B" in content
    assert "--model /models/Qwen3-8B" in content
    assert "served_model_name=Qwen3-8B" in content


def test_start_creates_parent_directory(tmp_path, monkeypatch):
    log_path = tmp_path / "a" / "b" / "vllm.log"
    read_fd, write_fd = os.pipe()
    os.close(write_fd)

    class _Completed:
        returncode = 0

    monkeypatch.setattr(vllm_starter.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(
        vllm_starter.subprocess, "Popen",
        lambda *a, **k: _ImmediatelyDeadProc(os.fdopen(read_fd)))

    with pytest.raises(Exception):
        vllm_starter.start_vllm_openai_api_server(
            1, 0.9, "/models/Qwen3-8B", log_path=log_path)

    assert log_path.is_file()
