from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from tortoise import Tortoise


def sqlite_db_url(db_path: str | os.PathLike[str]) -> str:
    return f"sqlite://{Path(db_path).resolve()}"


def _sqlite_connect(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    return sqlite3.connect(Path(db_path).resolve(), timeout=5)


def require_db_path() -> str:
    db_path = os.getenv("DB_PATH")
    if not db_path:
        raise ValueError("DB_PATH environment variable is required")
    return db_path


async def init_sqlite_db(db_path: str | os.PathLike[str]) -> None:
    await Tortoise.init(
        db_url=sqlite_db_url(db_path),
        modules={"models": ["api.app.models.db_models"]},
    )


async def close_db() -> None:
    await Tortoise.close_connections()


@asynccontextmanager
async def sqlite_db_session(db_path: str | os.PathLike[str]) -> AsyncIterator[None]:
    await init_sqlite_db(db_path)
    try:
        yield
    finally:
        await close_db()
