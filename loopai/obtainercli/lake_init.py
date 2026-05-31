from __future__ import annotations

from pathlib import Path

from .config import write_lake_config
from .models import utc_now
from .tables import ensure_tables


def init_lake(
    *,
    root: str | Path,
    link_path: str | Path | None = None,
    if_not_exists: bool = False,
) -> dict:
    lake_root = Path(root).expanduser().resolve()
    if lake_root.exists() and any(lake_root.iterdir()) and not if_not_exists:
        raise FileExistsError(f"Lake root already exists: {lake_root}")
    lake_root.mkdir(parents=True, exist_ok=True)
    ensure_tables(lake_root)
    write_lake_config(lake_root / "lake.yaml", root=lake_root)
    if link_path is not None:
        write_lake_config(Path(link_path), root=lake_root)
    return {
        "ok": True,
        "command": "lake init",
        "lake_root": str(lake_root),
        "lake_config": str(link_path) if link_path else str(lake_root / "lake.yaml"),
        "status": "success",
        "warnings": [],
        "created_at": utc_now(),
    }
