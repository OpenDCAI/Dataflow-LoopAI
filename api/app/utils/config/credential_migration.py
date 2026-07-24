from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

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
    migrate(payload)
    after = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False), before != after


def migrate_persisted_credentials(db_path: str | Path) -> dict[str, int]:
    """Migrate starter/task JSON transactionally without touching Trainer secrets."""
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
                migrated, changed = _migrated_json(raw_config, migrate_legacy_credentials)
                if changed:
                    con.execute("update starterconfig set config=? where id=?", (migrated, row_id))
                    result["starter_rows"] += 1

        if "taskmodel" in tables:
            rows = con.execute("select id, config, state from taskmodel").fetchall()
            for row_id, raw_config, raw_state in rows:
                migrated_config, config_changed = _migrated_json(
                    raw_config, migrate_legacy_credentials
                )
                migrated_state, state_changed = _migrated_json(
                    raw_state, strip_legacy_state_credentials
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
