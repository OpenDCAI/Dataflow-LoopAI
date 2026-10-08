#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""创建一个新任务，供 loopai-judger 等 skill 通过 task_id 使用。

**为什么需要它**：``loopai-judger`` 只 UPDATE 任务、从不创建 —— 整个 loopai 包里
没有任何 ``INSERT INTO taskmodel``。任务原本只能由 API
(``api/app/services/task/service.py::create_task``) 或前端建。想在命令行上单独
跑 Judger，就得先有个任务。

这个脚本直接写 sqlite，不依赖 API / Tortoise / FastAPI，所以能在任何有 yaml
的环境里跑。代价是它构造的 state 比较朴素（只从 starter.yaml 的 default_states
摊平而来）；够 ``loopai-judger`` 用，但不要指望它和 API 建的 state 逐字段一致。

用法::

    # 建新任务（task_id 自动生成），并打印后续命令
    python examples/scripts/create_task.py --db-path api/db/db.sqlite3 --name my-aime-test

    # 指定 task_id，并顺手把 judger 配置写进去
    python examples/scripts/create_task.py --db-path api/db/db.sqlite3 \\
        --task-id my-task-001 --config-path examples/config/math_bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_STARTER = _REPO_ROOT / "examples" / "config" / "starter.yaml"

# 和 api/app/models/db_models.py 的 TaskModel / TaskRuntime 对齐。
# 用 IF NOT EXISTS，避免和 API 自己建的 schema 打架。
_SCHEMA = (
    """
    create table if not exists taskmodel (
        id integer primary key autoincrement,
        task_id varchar(255) not null,
        name varchar(255) not null,
        config text,
        state text,
        ai_thread_id varchar(255),
        createdAt datetime,
        updatedAt datetime
    )
    """,
    """
    create table if not exists taskruntime (
        id integer primary key autoincrement,
        task_id varchar(255) not null,
        node_name varchar(255),
        version varchar(255),
        state text,
        status text,
        createdAt datetime,
        updatedAt datetime
    )
    """,
)


def build_state(task_id: str, starter_path: Path = DEFAULT_STARTER) -> Dict[str, Any]:
    """按 starter.yaml 的 default_states 摊平出一个初始 state。

    API 走的 ``build_initial_task_state`` 需要 Tortoise 连上库、还要 Configer 的
    包装格式，这里不引入那套依赖，够 Judger 用就行。
    """
    import yaml

    state: Dict[str, Any] = {"messages": []}
    if starter_path.is_file():
        payload = yaml.safe_load(starter_path.read_text(encoding="utf-8")) or {}
        default_states = payload.get("default_states") or {}
        if isinstance(default_states, dict):
            state.update(default_states)
    state["task_id"] = task_id
    state.setdefault("output_dir", "./outputs")
    return state


def create_task(
    db_path: str,
    task_id: Optional[str] = None,
    name: Optional[str] = None,
    starter_path: Path = DEFAULT_STARTER,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """插入一行 taskmodel，返回 {"task_id", "name", "created"}。"""
    task_id = str(task_id or uuid.uuid4())
    name = name or task_id

    db = Path(db_path).expanduser().resolve()
    db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db)
    try:
        for statement in _SCHEMA:
            connection.execute(statement)

        existing = connection.execute(
            "select id from taskmodel where task_id=?", (task_id,)).fetchone()
        if existing and not overwrite:
            raise ValueError(
                f"任务已存在: {task_id}（要覆盖请加 --overwrite）")

        state = build_state(task_id, starter_path)
        payload = (
            name,
            "{}",
            json.dumps(state, ensure_ascii=False),
            task_id,
        )
        if existing:
            connection.execute(
                "update taskmodel set name=?, config=?, state=?, updatedAt=datetime('now') "
                "where task_id=?",
                payload,
            )
        else:
            connection.execute(
                "insert into taskmodel (name, config, state, task_id, createdAt, updatedAt) "
                "values (?, ?, ?, ?, datetime('now'), datetime('now'))",
                payload,
            )
        connection.commit()
    finally:
        connection.close()

    return {"task_id": task_id, "name": name, "created": not existing}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a LoopAI task row for standalone skill runs")
    parser.add_argument("--db-path", help="SQLite database path (or set DB_PATH)")
    parser.add_argument("--task-id", help="Explicit task id; default: a new uuid4")
    parser.add_argument("--name", help="Human-readable task name; default: the task id")
    parser.add_argument("--starter", default=str(DEFAULT_STARTER),
                        help="starter.yaml to seed default_states from")
    parser.add_argument("--config-path",
                        help="Optional judger JSON/YAML config to write into the task right away")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite the task if it already exists")
    args = parser.parse_args()

    db_path = args.db_path or os.getenv("DB_PATH")
    if not db_path:
        parser.error("--db-path is required (or set DB_PATH)")

    try:
        result = create_task(
            db_path, task_id=args.task_id, name=args.name,
            starter_path=Path(args.starter), overwrite=args.overwrite)
    except Exception as exc:
        print(f"创建任务失败: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print(f"task_id: {result['task_id']}")
    print(f"name   : {result['name']}")

    if args.config_path:
        from loopai.skills.Judger.cli import _apply_config_to_task, _load_config

        applied = _apply_config_to_task(
            db_path, result["task_id"], _load_config(args.config_path))
        print(f"已写入 judger 配置字段: {', '.join(applied['judger_fields'])}")

    print()
    print("下一步：")
    print(f"  DB_PATH={db_path} loopai-judger --task-id {result['task_id']}"
          + (f" --config-path {args.config_path}" if args.config_path else ""))


if __name__ == "__main__":
    main()
