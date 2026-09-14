#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Judger 模型名一致性测试。

背景：不传 ``--served-model-name`` 时，vLLM 会把 ``--model`` 的原值（一串绝对
路径）当成对外模型名（vllm/config.py: get_served_model_name），而 vLLM 校验时
是精确字符串比较（entrypoints/openai/serving_models.py: is_base_model）。调用方
若拿路径去猜短名，vLLM 会对每一条请求回 404。

因此约定：名字只在 ``resolve_judger_runtime_config`` 产生一处，``vllm_starter``
和评测容器用同一个值。这些测试锁住这个约定。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger import runtime_config
from loopai.skills.Judger.utils import evaluate_math, generate, vllm_starter


def _resolve_model_name(judger: dict) -> str:
    state = {"task_id": "t", "output_dir": "/tmp/o", "judger": dict(judger)}
    return runtime_config.resolve_judger_runtime_config(state, task_id="t")["model_name"]


# ---------------------------------------------------------------------------
# 名字的产生（runtime_config）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model_path, expected",
    [
        ("/jizhicfs/hymiezhao/lpc/repos/zx/models/Qwen3-8B", "Qwen3-8B"),
        ("/models/Qwen3-8B/", "Qwen3-8B"),
        ("Qwen/Qwen3-8B", "Qwen3-8B"),
        ("Qwen3-8B", "Qwen3-8B"),
    ],
)
def test_model_name_defaults_to_path_basename(model_path, expected):
    assert _resolve_model_name({"eval_model_path": model_path}) == expected


def test_explicit_model_name_wins_over_basename():
    assert _resolve_model_name(
        {"eval_model_path": "/models/Qwen3-8B", "eval_model_name": "my-served-name"}
    ) == "my-served-name"


def test_model_name_env_override_wins(monkeypatch):
    monkeypatch.setenv("JUDGER_MODEL_NAME", "from-env")
    assert _resolve_model_name({"eval_model_path": "/models/Qwen3-8B"}) == "from-env"


# ---------------------------------------------------------------------------
# 名字的消费（评测容器侧的一致性校验）
# ---------------------------------------------------------------------------

def _stub_vllm_models(monkeypatch, payload):
    response_body = json.dumps(payload).encode("utf-8")

    class _Response:
        def read(self):
            return response_body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        evaluate_math.urllib.request, "urlopen",
        lambda url, timeout=None: _Response())


def test_assert_model_is_served_accepts_matching_name(monkeypatch):
    _stub_vllm_models(monkeypatch, {"data": [{"id": "Qwen3-8B"}]})

    evaluate_math._assert_model_is_served("Qwen3-8B")


def test_assert_model_is_served_rejects_name_vllm_does_not_serve(monkeypatch):
    # 线上那次 404 的形态：vLLM 上架的是完整路径，调用方发的是短名
    _stub_vllm_models(monkeypatch, {"data": [{"id": "/models/Qwen3-8B"}]})

    with pytest.raises(ValueError) as excinfo:
        evaluate_math._assert_model_is_served("Qwen3-8B")
    assert "Qwen3-8B" in str(excinfo.value)
    assert "/models/Qwen3-8B" in str(excinfo.value)


def test_assert_model_is_served_tolerates_unreachable_vllm(monkeypatch):
    def _refuse(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(evaluate_math.urllib.request, "urlopen", _refuse)

    evaluate_math._assert_model_is_served("anything")  # 不应抛错


# ---------------------------------------------------------------------------
# 名字的消费（vLLM 启动侧）
# ---------------------------------------------------------------------------

def _capture_vllm_command(monkeypatch, served_name) -> str:
    class _Completed:
        returncode = 0

    # 骗过 "能否 import vllm" 的预检，并拦截真正的进程启动
    monkeypatch.setattr(vllm_starter.subprocess, "run", lambda *a, **k: _Completed())
    captured: dict = {}

    def _popen(command, **kwargs):
        captured["command"] = command
        raise RuntimeError("stop before launching vllm")

    monkeypatch.setattr(vllm_starter.subprocess, "Popen", _popen)

    with pytest.raises(RuntimeError):
        vllm_starter.start_vllm_openai_api_server(
            1, 0.9, "/models/Qwen3-8B", vllm_served_model_name=served_name)
    return captured.get("command", "")


def test_vllm_command_pins_served_model_name(monkeypatch):
    command = _capture_vllm_command(monkeypatch, "Qwen3-8B")

    assert "--served-model-name Qwen3-8B" in command
    assert "--model /models/Qwen3-8B" in command


def test_vllm_command_omits_flag_when_name_absent(monkeypatch):
    command = _capture_vllm_command(monkeypatch, None)

    assert "--served-model-name" not in command


# ---------------------------------------------------------------------------
# 名字的消费（code/text2sql 生成侧）
# ---------------------------------------------------------------------------

class _Writer:
    version_id = "v1"

    def __call__(self, event):
        pass


class _FakeModel:
    def batch(self, prompts):
        return [type("R", (), {"content": "SELECT 1"})() for _ in prompts]


def _capture_generate_call(monkeypatch, tmp_path, runner_fn, judger) -> dict:
    captured: dict = {}

    def _fake_init(**kwargs):
        captured.update(kwargs)
        return _FakeModel()

    monkeypatch.setattr(generate, "_init_model", _fake_init)
    monkeypatch.setattr(generate, "read_problems", lambda path: {
        "q1": {"prompt": "p", "db_id": "db", "question": "q", "ground_truth": "g"}})
    monkeypatch.setattr(generate, "write_jsonl", lambda path, rows: None)

    state = {"task_id": "t1", "output_dir": str(tmp_path), "judger": dict(judger)}
    runner_fn(state, _Writer())
    return captured


_JUDGER = {
    "eval_model_path": "/models/Qwen3-8B",
    "eval_model_name": "Qwen3-8B",
    "eval_base_url": "http://127.0.0.1:8911/v1",
    "eval_temperature": 0.0,
    "eval_top_p": 0.95,
    "eval_problem_path": "/tmp/problems.jsonl",
    "bench_name": "bird_dev",
    "eval_task_type": "text2sql",
    "eval_case_num": 1,
    "eval_batch_size": 10,
    "eval_text2sql_dir": "/tmp/dbs",
}


@pytest.mark.parametrize("runner_fn", [generate.run_generate_code, generate.run_generate_text2sql])
def test_generate_sends_served_name_not_path(monkeypatch, tmp_path, runner_fn):
    """生成侧必须发 vLLM 上架的名字。

    vllm_starter 用 --served-model-name 把名字钉成 eval_model_name（默认取路径
    最后一段），发完整路径会被 vLLM 判成 404。这个 bug 真的发生过：改成
    --served-model-name 之后，math 路径跟着改了，code/text2sql 漏了。
    """
    captured = _capture_generate_call(monkeypatch, tmp_path, runner_fn, _JUDGER)

    assert captured["model_name"] == "Qwen3-8B"
    assert "/models/Qwen3-8B" not in captured["model_name"]
