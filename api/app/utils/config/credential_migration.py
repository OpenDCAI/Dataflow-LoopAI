from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from loopai.common.tracking import strip_retired_tracking_fields
from loopai.schema.system_runtime import (
    migrate_legacy_credentials,
    strip_legacy_state_credentials,
)


def _migrated_json(raw: str | None, migrate) -> tuple[str | None, bool]:
    if not raw:
        return raw, False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, False
    if not isinstance(payload, dict):
        return raw, False
    before = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    migrated = migrate(payload)
    if isinstance(migrated, dict):
        payload = migrated
    after = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False), before != after


def _normalize_stored_config(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = strip_retired_tracking_fields(payload)
    return migrate_legacy_credentials(cleaned)


def _normalize_stored_state(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = strip_retired_tracking_fields(payload)
    return strip_legacy_state_credentials(cleaned)


def migrate_persisted_credentials(db_path: str | Path) -> dict[str, int]:
    """Migrate stored credentials and remove retired tracker fields transactionally."""
    path = Path(db_path)
    result = {"starter_rows": 0, "task_config_rows": 0, "task_state_rows": 0}
    if not path.exists():
        return result

    con = sqlite3.connect(path, timeout=5)
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
        }
        if "starterconfig" in tables:
            for row_id, raw_config in con.execute("select id, config from starterconfig").fetchall():
                migrated, changed = _migrated_json(raw_config, _normalize_stored_config)
                if changed:
                    con.execute("update starterconfig set config=? where id=?", (migrated, row_id))
                    result["starter_rows"] += 1

        if "taskmodel" in tables:
            rows = con.execute("select id, config, state from taskmodel").fetchall()
            for row_id, raw_config, raw_state in rows:
                migrated_config, config_changed = _migrated_json(
                    raw_config, _normalize_stored_config
                )
                migrated_state, state_changed = _migrated_json(
                    raw_state, _normalize_stored_state
                )
                if config_changed:
                    con.execute("update taskmodel set config=? where id=?", (migrated_config, row_id))
                    result["task_config_rows"] += 1
                if state_changed:
                    con.execute("update taskmodel set state=? where id=?", (migrated_state, row_id))
                    result["task_state_rows"] += 1
        con.commit()
    finally:
        con.close()
    return result
