#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""examples/scripts/create_task.py 的测试。

背景：``loopai-judger`` 只 UPDATE 任务、从不创建 —— 整个 loopai 包里没有
``INSERT INTO taskmodel``。想在命令行上单独跑 Judger，必须先有任务。这个脚本
补上这一步，所以它建出来的行必须真的能被 Judger 的后续流程用起来。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SCRIPT = _REPO_ROOT / "examples" / "scripts" / "create_task.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("create_task_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def _row(db_path, task_id):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "select name, state from taskmodel where task_id=?", (task_id,)).fetchone()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# state 构造
# ---------------------------------------------------------------------------

def test_build_state_seeds_from_starter_and_sets_task_id():
    state = script.build_state("t-1")

    assert state["task_id"] == "t-1"           # 覆盖 starter 里的 "default"
    assert state["messages"] == []
    assert state["output_dir"]
    # starter.yaml 的 default_states 段应该被摊平进来
    for section in ("judger", "analyzer", "obtainer", "trainer"):
        assert section in state


def test_build_state_survives_missing_starter(tmp_path):
    state = script.build_state("t-1", starter_path=tmp_path / "nope.yaml")

    assert state["task_id"] == "t-1"
    assert state["output_dir"] == "./outputs"


# ---------------------------------------------------------------------------
# 建任务
# ---------------------------------------------------------------------------

def test_create_task_inserts_row(tmp_path):
    db = str(tmp_path / "db.sqlite3")

    result = script.create_task(db, task_id="t-1", name="demo")

    assert result == {"task_id": "t-1", "name": "demo", "created": True}
    name, raw_state = _row(db, "t-1")
    assert name == "demo"
    assert json.loads(raw_state)["task_id"] == "t-1"


def test_create_task_generates_task_id_when_absent(tmp_path):
    result = script.create_task(str(tmp_path / "db.sqlite3"))

    assert result["task_id"]
    assert result["name"] == result["task_id"]   # name 缺省跟着 task_id


def test_create_task_refuses_duplicate(tmp_path):
    db = str(tmp_path / "db.sqlite3")
    script.create_task(db, task_id="t-1")

    with pytest.raises(ValueError) as excinfo:
        script.create_task(db, task_id="t-1")

    assert "t-1" in str(excinfo.value)


def test_create_task_overwrite_replaces_row(tmp_path):
    db = str(tmp_path / "db.sqlite3")
    script.create_task(db, task_id="t-1", name="first")

    result = script.create_task(db, task_id="t-1", name="second", overwrite=True)

    assert result["created"] is False
    assert _row(db, "t-1")[0] == "second"
    # 只能有一行
    con = sqlite3.connect(db)
    count = con.execute("select count(*) from taskmodel").fetchone()[0]
    con.close()
    assert count == 1


def test_create_task_creates_parent_directory(tmp_path):
    db = tmp_path / "a" / "b" / "db.sqlite3"

    script.create_task(str(db), task_id="t-1")

    assert db.is_file()


def test_create_task_is_idempotent_about_schema(tmp_path):
    """重复跑不能因为表已存在而炸。"""
    db = str(tmp_path / "db.sqlite3")
    script.create_task(db, task_id="t-1")
    script.create_task(db, task_id="t-2")

    con = sqlite3.connect(db)
    tasks = sorted(r[0] for r in con.execute("select task_id from taskmodel"))
    con.close()
    assert tasks == ["t-1", "t-2"]


# ---------------------------------------------------------------------------
# 建出来的任务要真的能用
# ---------------------------------------------------------------------------

def test_created_task_can_receive_judger_config(tmp_path):
    """关键契约：脚本建完任务后，--config-path 必须能写进去 —— 这是它存在的意义。"""
    from loopai.skills.Judger.cli import _apply_config_to_task

    db = str(tmp_path / "db.sqlite3")
    script.create_task(db, task_id="t-1")

    applied = _apply_config_to_task(db, "t-1", {"judger": {
        "eval_model_path": "/models/Qwen3-8B",
        "eval_request_timeout": 3600,
        "benchlist": [{"name": "aime26", "task_type": "math",
                       "problem_path": "/data/aime26_test.jsonl"}],
    }})

    assert "eval_model_path" in applied["judger_fields"]
    state = json.loads(_row(db, "t-1")[1])
    assert state["judger"]["eval_model_path"] == "/models/Qwen3-8B"
    # _apply_config_to_task 用到了 updatedAt 列，脚本建表时必须带上
    assert state["judger"]["eval_request_timeout"] == 3600
