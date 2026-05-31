from __future__ import annotations

from pathlib import Path
from typing import Dict


def _parse_simple_yaml(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def write_lake_config(path: Path, *, root: Path, warehouse: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    warehouse = warehouse or root / "warehouse"
    path.write_text(
        "\n".join(
            [
                "# LoopAI ObtainerCLI lake pointer",
                f"root: {root}",
                f"warehouse: {warehouse}",
                "catalog: local-jsonl",
                "namespace: loopai",
                "",
            ]
        ),
        encoding="utf-8",
    )


def resolve_lake_root(lake: str | Path) -> Path:
    path = Path(lake)
    if path.is_file():
        values = _parse_simple_yaml(path)
        root = values.get("root")
        if not root:
            raise ValueError(f"Lake config missing root: {path}")
        return Path(root).expanduser().resolve()
    return path.expanduser().resolve()
