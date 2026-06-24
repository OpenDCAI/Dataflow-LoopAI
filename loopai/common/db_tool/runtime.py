from __future__ import annotations

import os
from typing import Any

from .base import _sqlite_connect, require_db_path


def _serialize_task_runtime_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "task_id": row[1],
        "node_name": row[2],
        "version": row[3],
        "status": row[4],
        "createdAt": row[5],
        "updatedAt": row[6],
    }


def create_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    con = _sqlite_connect(db_path)
    try:
        cur = con.execute(
            """
            insert into taskruntime(task_id, node_name, version, status, createdAt, updatedAt)
            values(?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (task_id, node_name, version, status),
        )
        con.commit()
        row = con.execute(
            """
            select id, task_id, node_name, version, status, createdAt, updatedAt
            from taskruntime
            where id=?
            """,
            (int(cur.lastrowid),),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise RuntimeError("failed to create task runtime")
    return _serialize_task_runtime_row(row)


def update_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any] | None:
    con = _sqlite_connect(db_path)
    try:
        existing = con.execute(
            """
            select id
            from taskruntime
            where task_id=? and node_name=? and version=?
            order by updatedAt desc, id desc
            limit 1
            """,
            (task_id, node_name, version),
        ).fetchone()
        if existing is None:
            return None

        con.execute(
            """
            update taskruntime
            set status=?, updatedAt=datetime('now')
            where id=?
            """,
            (status, existing[0]),
        )
        con.commit()
        row = con.execute(
            """
            select id, task_id, node_name, version, status, createdAt, updatedAt
            from taskruntime
            where id=?
            """,
            (existing[0],),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return _serialize_task_runtime_row(row)


def upsert_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    runtime = update_task_runtime_sync(db_path, task_id, node_name, version, status)
    if runtime is not None:
        return runtime
    return create_task_runtime_sync(db_path, task_id, node_name, version, status)


def get_latest_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
) -> dict[str, Any] | None:
    con = _sqlite_connect(db_path)
    try:
        row = con.execute(
            """
            select id, task_id, node_name, version, status, createdAt, updatedAt
            from taskruntime
            where task_id=? and node_name=?
            order by updatedAt desc, id desc
            limit 1
            """,
            (task_id, node_name),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return _serialize_task_runtime_row(row)


def list_task_runtime_history_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
) -> list[dict[str, Any]]:
    con = _sqlite_connect(db_path)
    try:
        rows = con.execute(
            """
            select id, task_id, node_name, version, status, createdAt, updatedAt
            from taskruntime
            where task_id=? and node_name=?
            order by updatedAt desc, id desc
            """,
            (task_id, node_name),
        ).fetchall()
    finally:
        con.close()
    return [_serialize_task_runtime_row(row) for row in rows]


def list_latest_task_runtimes_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
) -> list[dict[str, Any]]:
    con = _sqlite_connect(db_path)
    try:
        rows = con.execute(
            """
            select id, task_id, node_name, version, status, createdAt, updatedAt
            from taskruntime
            where task_id=?
            order by node_name asc, updatedAt desc, id desc
            """,
            (task_id,),
        ).fetchall()
    finally:
        con.close()

    latest_runtimes: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    for row in rows:
        node_name = row[2] or ""
        if node_name in seen_nodes:
            continue
        seen_nodes.add(node_name)
        latest_runtimes.append(_serialize_task_runtime_row(row))
    return latest_runtimes


async def create_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    return create_task_runtime_sync(require_db_path(), task_id, node_name, version, status)


async def update_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any] | None:
    return update_task_runtime_sync(require_db_path(), task_id, node_name, version, status)


async def upsert_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    return upsert_task_runtime_sync(require_db_path(), task_id, node_name, version, status)


async def get_latest_task_runtime(
    task_id: str,
    node_name: str,
) -> dict[str, Any] | None:
    return get_latest_task_runtime_sync(require_db_path(), task_id, node_name)


async def list_task_runtime_history(
    task_id: str,
    node_name: str,
) -> list[dict[str, Any]]:
    return list_task_runtime_history_sync(require_db_path(), task_id, node_name)


async def list_latest_task_runtimes(task_id: str) -> list[dict[str, Any]]:
    return list_latest_task_runtimes_sync(require_db_path(), task_id)
