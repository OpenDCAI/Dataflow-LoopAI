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
        "state": row[4],
        "status": row[5],
        "createdAt": row[6],
        "updatedAt": row[7],
    }


def create_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
    version: str,
    status: str,
    state: str | None = None,
) -> dict[str, Any]:
    con = _sqlite_connect(db_path)
    try:
        cur = con.execute(
            """
            insert into taskruntime(task_id, node_name, version, state, status, createdAt, updatedAt)
            values(?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (task_id, node_name, version, state, status),
        )
        con.commit()
        row = con.execute(
            """
            select id, task_id, node_name, version, state, status, createdAt, updatedAt
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
    state: str | None = None,
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
            set state=?, status=?, updatedAt=datetime('now')
            where id=?
            """,
            (state, status, existing[0]),
        )
        con.commit()
        row = con.execute(
            """
            select id, task_id, node_name, version, state, status, createdAt, updatedAt
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
    state: str | None = None,
) -> dict[str, Any]:
    runtime = update_task_runtime_sync(
        db_path,
        task_id,
        node_name,
        version,
        status,
        state,
    )
    if runtime is not None:
        return runtime
    return create_task_runtime_sync(db_path, task_id, node_name, version, status, state)


def sync_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
    version: str | None,
    status: str,
    state: str | None = None,
) -> dict[str, Any]:
    normalized_version = (version or "").strip()
    if normalized_version:
        runtime = update_task_runtime_sync(
            db_path=db_path,
            task_id=task_id,
            node_name=node_name,
            version=normalized_version,
            status=status,
            state=state,
        )
        if runtime is not None:
            return runtime
    return create_task_runtime_sync(
        db_path=db_path,
        task_id=task_id,
        node_name=node_name,
        version=normalized_version,
        status=status,
        state=state,
    )


def get_latest_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    node_name: str,
) -> dict[str, Any] | None:
    con = _sqlite_connect(db_path)
    try:
        row = con.execute(
            """
            select id, task_id, node_name, version, state, status, createdAt, updatedAt
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


def get_current_task_runtime_sync(
    db_path: str | os.PathLike[str],
    task_id: str,
    version: str,
) -> dict[str, Any] | None:
    con = _sqlite_connect(db_path)
    try:
        row = con.execute(
            """
            select id, task_id, node_name, version, state, status, createdAt, updatedAt
            from taskruntime
            where task_id=? and version=?
            order by updatedAt desc, id desc
            limit 1
            """,
            (task_id, version),
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
            select id, task_id, node_name, version, state, status, createdAt, updatedAt
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
            select id, task_id, node_name, version, state, status, createdAt, updatedAt
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
    state: str | None = None,
) -> dict[str, Any]:
    return create_task_runtime_sync(
        require_db_path(),
        task_id,
        node_name,
        version,
        status,
        state,
    )


async def update_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
    state: str | None = None,
) -> dict[str, Any] | None:
    return update_task_runtime_sync(
        require_db_path(),
        task_id,
        node_name,
        version,
        status,
        state,
    )


async def upsert_task_runtime(
    task_id: str,
    node_name: str,
    version: str,
    status: str,
    state: str | None = None,
) -> dict[str, Any]:
    return upsert_task_runtime_sync(
        require_db_path(),
        task_id,
        node_name,
        version,
        status,
        state,
    )


async def sync_task_runtime(
    task_id: str,
    node_name: str,
    version: str | None,
    status: str,
    state: str | None = None,
) -> dict[str, Any]:
    return sync_task_runtime_sync(
        require_db_path(),
        task_id,
        node_name,
        version,
        status,
        state,
    )


async def get_latest_task_runtime(
    task_id: str,
    node_name: str,
) -> dict[str, Any] | None:
    return get_latest_task_runtime_sync(require_db_path(), task_id, node_name)


async def get_current_task_runtime(
    task_id: str,
    version: str,
) -> dict[str, Any] | None:
    return get_current_task_runtime_sync(require_db_path(), task_id, version)


async def list_task_runtime_history(
    task_id: str,
    node_name: str,
) -> list[dict[str, Any]]:
    return list_task_runtime_history_sync(require_db_path(), task_id, node_name)


async def list_latest_task_runtimes(task_id: str) -> list[dict[str, Any]]:
    return list_latest_task_runtimes_sync(require_db_path(), task_id)
