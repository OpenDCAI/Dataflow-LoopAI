from __future__ import annotations

import shutil
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import OBTAINER_ACTIVE_BINDING_KEYS, obtainer_context, read_lake_config, write_lake_config
from .datamixer_adapter import warehouse_root
from .errors import ObtainerCliError
from .monitor_state import read_monitor_state


def _resolve_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _bool_from_config(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _existing_config(link: Path) -> dict[str, str]:
    if link.is_file():
        return read_lake_config(link)
    return {}


def _write_pointer(
    link: Path,
    *,
    lake_root: Path,
    warehouse: Path,
    previous: dict[str, str],
    context_values: dict[str, str] | None = None,
) -> None:
    write_lake_config(
        link,
        root=lake_root,
        warehouse=warehouse,
        catalog=previous.get("catalog") or "datamixer",
        backend="datamixer",
        auto_embed=_bool_from_config(previous.get("auto_embed"), True),
        embedding_provider=previous.get("embedding_provider") or "openai-compatible",
        embedding_base_url=previous.get("embedding_base_url") or "http://127.0.0.1:8000/v1",
        embedding_api_key=previous.get("embedding_api_key") or "",
        embedding_model=previous.get("embedding_model") or "BAAI/bge-small-zh-v1.5",
        embedding_backend=previous.get("embedding_backend") or "local-jsonl",
        embedding_text_field=previous.get("embedding_text_field") or "text",
        auto_embed_async=_bool_from_config(previous.get("auto_embed_async"), True),
        auto_embed_batch_size=int(previous.get("auto_embed_batch_size") or 512),
        obtainer_context_values=context_values or previous,
    )


def _iso_mtime(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _candidate_id(warehouse: Path, source_path: Path) -> str:
    digest = hashlib.sha256(f"{warehouse}:{source_path}".encode("utf-8")).hexdigest()[:16]
    return f"lake_{digest}"


def _walk_named_files(root: Path, names: set[str], *, max_depth: int = 6) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    root = root.resolve()
    ignored = {".git", "node_modules", "__pycache__", ".pytest_cache", "dist"}
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            rel_depth = len(current.relative_to(root).parts)
        except ValueError:
            rel_depth = 0
        if rel_depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [name for name in dirnames if name not in ignored and not name.startswith(".venv")]
        for filename in filenames:
            if filename in names:
                found.append(current / filename)
    return found


def _scan_roots(project_root: str | Path | None, extra_roots: list[str | Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    if project_root:
        project = Path(project_root).expanduser().resolve()
        roots.extend([project / ".datamixer", project / "outputs"])
    for env_name in ("LOOPAI_CACHE_DIR", "OBTAINER_CACHE_DIR", "DATAMIXER_CACHE_DIR"):
        value = os.getenv(env_name)
        if value:
            roots.append(Path(value).expanduser().resolve())
    xdg = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()
    roots.extend([xdg / "loopai", Path.home() / ".cache" / "loopai"])
    for raw in extra_roots or []:
        if raw:
            roots.append(Path(raw).expanduser().resolve())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _warehouse_stats(warehouse: Path) -> dict[str, Any]:
    try:
        from loopai.agents.Obtainer.datamixer.store import DataStore

        store = DataStore.open(warehouse)
        samples = store.catalog.count()
        datasets = store.catalog.list_datasets()
        catalog_bytes = store.catalog.db_size()
        store.close()
        return {
            "status": "ready",
            "samples": samples,
            "datasets": len(datasets),
            "total_on_disk_bytes": catalog_bytes,
            "unique_blobs": 0,
            "storage_summary": "catalog_only",
            "warnings": [],
        }
    except Exception as exc:
        return {
            "status": "error",
            "samples": 0,
            "datasets": 0,
            "total_on_disk_bytes": 0,
            "unique_blobs": 0,
            "warnings": [{"code": "DATAMIXER_STATS_ERROR", "message": str(exc)}],
        }


def _candidate_from_lake_config(path: Path) -> dict[str, Any] | None:
    try:
        config = read_lake_config(path)
        warehouse = warehouse_root(path)
    except Exception:
        return None
    if not warehouse:
        return None
    stats = _warehouse_stats(warehouse) if (warehouse / "datamixer.toml").exists() else {
        "status": "missing",
        "samples": 0,
        "datasets": 0,
        "total_on_disk_bytes": 0,
        "unique_blobs": 0,
        "warnings": [{"code": "DATAMIXER_WAREHOUSE_MISSING", "message": f"Warehouse is missing: {warehouse}"}],
    }
    return {
        "id": _candidate_id(warehouse, path),
        "name": warehouse.parent.name or warehouse.name,
        "source_type": "lake_config",
        "source_path": str(path),
        "lake_config": str(path),
        "lake_root": config.get("root") or str(warehouse.parent),
        "warehouse": str(warehouse),
        "warehouse_exists": (warehouse / "datamixer.toml").exists(),
        "modified_at": _iso_mtime(path),
        **stats,
    }


def _candidate_from_warehouse_marker(path: Path) -> dict[str, Any] | None:
    warehouse = path.parent.resolve()
    if not (warehouse / "datamixer.toml").exists():
        return None
    stats = _warehouse_stats(warehouse)
    return {
        "id": _candidate_id(warehouse, path),
        "name": warehouse.parent.name or warehouse.name,
        "source_type": "warehouse",
        "source_path": str(path),
        "lake_config": "",
        "lake_root": str(warehouse.parent),
        "warehouse": str(warehouse),
        "warehouse_exists": True,
        "modified_at": _iso_mtime(path),
        **stats,
    }


def scan_lake_candidates(
    *,
    project_root: str | Path | None = None,
    active_link: str | Path = ".datamixer/lake.yaml",
    extra_roots: list[str | Path] | None = None,
    max_depth: int = 6,
) -> dict[str, Any]:
    active = current_lake_pointer(link_path=active_link)
    active_warehouse = str(active.get("warehouse") or "")
    roots = _scan_roots(project_root, extra_roots)
    candidates_by_warehouse: dict[str, dict[str, Any]] = {}
    for root in roots:
        for path in _walk_named_files(root, {"lake.yaml", "datamixer.toml"}, max_depth=max_depth):
            candidate = (
                _candidate_from_lake_config(path)
                if path.name == "lake.yaml"
                else _candidate_from_warehouse_marker(path)
            )
            if not candidate:
                continue
            warehouse = candidate["warehouse"]
            previous = candidates_by_warehouse.get(warehouse)
            if previous and previous.get("source_type") == "lake_config":
                continue
            candidates_by_warehouse[warehouse] = candidate
    if active_warehouse and active_warehouse not in candidates_by_warehouse and active.get("warehouse_exists"):
        warehouse = Path(active_warehouse)
        stats = _warehouse_stats(warehouse)
        candidates_by_warehouse[active_warehouse] = {
            "id": _candidate_id(warehouse, Path(str(active.get("lake_config") or warehouse))),
            "name": warehouse.parent.name or warehouse.name,
            "source_type": "active_pointer",
            "source_path": str(active.get("lake_config") or ""),
            "lake_config": str(active.get("lake_config") or ""),
            "lake_root": str(active.get("lake_root") or warehouse.parent),
            "warehouse": active_warehouse,
            "warehouse_exists": True,
            "modified_at": _iso_mtime(Path(str(active.get("lake_config") or warehouse))),
            **stats,
        }
    candidates = sorted(
        candidates_by_warehouse.values(),
        key=lambda item: (item.get("warehouse") != active_warehouse, item.get("modified_at") or ""),
        reverse=False,
    )
    for item in candidates:
        item["active"] = bool(active_warehouse and item.get("warehouse") == active_warehouse)
    return {
        "ok": True,
        "command": "dm lake scan",
        "status": "success",
        "active": active,
        "scan_roots": [str(root) for root in roots if root.exists()],
        "lakes": candidates,
        "count": len(candidates),
        "warnings": [],
    }


def current_lake_pointer(*, link_path: str | Path = ".datamixer/lake.yaml") -> dict[str, Any]:
    link = Path(link_path).expanduser()
    if not link.is_absolute():
        link = Path.cwd() / link
    link = link.resolve()
    exists = link.is_file()
    config = read_lake_config(link) if exists else {}
    warehouse = ""
    warehouse_exists = False
    if exists:
        try:
            resolved = warehouse_root(link)
            warehouse = str(resolved)
            warehouse_exists = (resolved / "datamixer.toml").exists()
        except Exception:
            warehouse = config.get("warehouse", "")
    return {
        "ok": True,
        "command": "dm lake current",
        "status": "loaded" if exists else "missing",
        "lake_config": str(link),
        "lake_root": config.get("root", ""),
        "warehouse": warehouse,
        "warehouse_exists": warehouse_exists,
        "backend": config.get("backend", ""),
        "catalog": config.get("catalog", ""),
        "config": config,
        "obtainer_context": obtainer_context(config),
        "warnings": []
        if exists
        else [
            {
                "code": "LAKE_POINTER_MISSING",
                "message": f"Lake pointer does not exist: {link}",
            }
        ],
    }


def load_lake_pointer(
    *,
    warehouse: str | Path,
    link_path: str | Path = ".datamixer/lake.yaml",
    lake_root: str | Path | None = None,
) -> dict[str, Any]:
    warehouse_path = _resolve_path(warehouse)
    if not warehouse_path.is_dir() or not (warehouse_path / "datamixer.toml").is_file():
        raise ObtainerCliError(
            "DATAMIXER_WAREHOUSE_NOT_FOUND",
            f"DataMixer warehouse is not initialized: {warehouse_path}",
            hint="Run `loopai-obtainercli dm --root <warehouse> init --json` first, then load that warehouse.",
            exit_code=2,
            details={"warehouse": str(warehouse_path)},
        )
    link = Path(link_path).expanduser()
    if not link.is_absolute():
        link = Path.cwd() / link
    link = link.resolve()
    root = _resolve_path(lake_root) if lake_root else warehouse_path.parent
    previous = _existing_config(link)
    source_config_path = root / "lake.yaml"
    source_context = (
        read_lake_config(source_config_path)
        if source_config_path.is_file()
        else {}
    )
    context_values = {**previous, **source_context}
    previous_warehouse = previous.get("warehouse", "")
    _write_pointer(
        link,
        lake_root=root,
        warehouse=warehouse_path,
        previous=previous,
        context_values=context_values,
    )
    monitor = read_monitor_state(warehouse_path, lake=link)
    return {
        "ok": True,
        "command": "dm lake load",
        "status": "success",
        "lake_config": str(link),
        "lake_root": str(root),
        "warehouse": str(warehouse_path),
        "previous_warehouse": previous_warehouse,
        "pointer_written": True,
        "warehouse_deleted": False,
        "monitor": {
            "cache_status": monitor.get("cache_status", "cache_missing"),
            "stale": bool(monitor.get("stale")),
            "stale_reason": monitor.get("stale_reason", ""),
            "updated_at": monitor.get("updated_at", ""),
            "rebuild": monitor.get("rebuild", {}),
        },
        "warnings": [],
    }


def update_lake_obtainer_context(
    *,
    link_path: str | Path = ".datamixer/lake.yaml",
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Persist operational defaults/current runs on the active lake pointer."""
    link = Path(link_path).expanduser()
    if not link.is_absolute():
        link = Path.cwd() / link
    link = link.resolve()
    if not link.is_file():
        raise ObtainerCliError(
            "LAKE_POINTER_MISSING",
            f"Lake pointer does not exist: {link}",
            hint="Load or initialize a DataMixer lake before starting an Obtainer worker.",
            exit_code=2,
        )
    config = read_lake_config(link)
    context = obtainer_context(config)
    invalid = sorted(set(updates) - set(context))
    if invalid:
        raise ValueError(f"Unsupported Obtainer lake context keys: {', '.join(invalid)}")
    for key, value in updates.items():
        context[key] = "" if value is None else str(value).strip()
    root_value = str(config.get("root") or "").strip()
    warehouse_value = str(config.get("warehouse") or "").strip()
    if not root_value or not warehouse_value:
        raise ValueError(f"Lake pointer is missing root or warehouse: {link}")
    root = Path(root_value).expanduser()
    warehouse = Path(warehouse_value).expanduser()

    def write_context(path: Path) -> None:
        write_lake_config(
            path,
            root=root,
            warehouse=warehouse,
            catalog=config.get("catalog") or "datamixer",
            backend=config.get("backend") or "datamixer",
            auto_embed=_bool_from_config(config.get("auto_embed"), True),
            embedding_provider=config.get("embedding_provider") or "openai-compatible",
            embedding_base_url=config.get("embedding_base_url") or "http://127.0.0.1:8000/v1",
            embedding_api_key=config.get("embedding_api_key") or "",
            embedding_model=config.get("embedding_model") or "BAAI/bge-small-zh-v1.5",
            embedding_backend=config.get("embedding_backend") or "local-jsonl",
            embedding_text_field=config.get("embedding_text_field") or "text",
            auto_embed_async=_bool_from_config(config.get("auto_embed_async"), True),
            auto_embed_batch_size=int(config.get("auto_embed_batch_size") or 512),
            obtainer_context_values=context,
        )

    write_context(link)
    canonical_config = root.resolve() / "lake.yaml"
    if canonical_config != link:
        write_context(canonical_config)
    return {
        "ok": True,
        "command": "dm lake context",
        "status": "success",
        "lake_config": str(link),
        "warehouse": str(warehouse.resolve()),
        "obtainer_context": context,
        "warnings": [],
    }


def unbind_lake_obtainer_context(
    *,
    link_path: str | Path = ".datamixer/lake.yaml",
) -> dict[str, Any]:
    """Clear active task/run bindings so a stale task_id never leaks into a
    new task. WebAgent defaults (model, workers, ...) are intentionally kept."""
    return update_lake_obtainer_context(
        link_path=link_path,
        updates={key: "" for key in OBTAINER_ACTIVE_BINDING_KEYS},
    )


def delete_lake_pointer(
    *,
    link_path: str | Path = ".datamixer/lake.yaml",
    delete_warehouse: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    link = Path(link_path).expanduser()
    if not link.is_absolute():
        link = Path.cwd() / link
    link = link.resolve()
    if not link.exists():
        return {
            "ok": True,
            "command": "dm lake delete",
            "status": "success",
            "lake_config": str(link),
            "pointer_removed": False,
            "warehouse": "",
            "warehouse_deleted": False,
            "warnings": [
                {
                    "code": "LAKE_POINTER_ALREADY_MISSING",
                    "message": f"Lake pointer does not exist: {link}",
                }
            ],
        }

    warehouse = warehouse_root(link)
    if delete_warehouse and not yes:
        raise ObtainerCliError(
            "CONFIRMATION_REQUIRED",
            "Deleting the DataMixer warehouse requires --yes.",
            hint="Use `dm lake delete --delete-warehouse --yes` only when the reusable warehouse should be removed.",
            exit_code=2,
            details={"lake_config": str(link), "warehouse": str(warehouse)},
        )

    link.unlink()
    warehouse_deleted = False
    if delete_warehouse:
        if not (warehouse / "datamixer.toml").is_file():
            raise ObtainerCliError(
                "DATAMIXER_WAREHOUSE_NOT_FOUND",
                f"Refusing to delete a path that is not a DataMixer warehouse: {warehouse}",
                hint="The pointer was removed. Re-check the warehouse path before deleting files.",
                exit_code=2,
                details={"warehouse": str(warehouse), "lake_config": str(link)},
            )
        shutil.rmtree(warehouse)
        warehouse_deleted = True

    return {
        "ok": True,
        "command": "dm lake delete",
        "status": "success",
        "lake_config": str(link),
        "pointer_removed": True,
        "warehouse": str(warehouse),
        "warehouse_deleted": warehouse_deleted,
        "warnings": []
        if warehouse_deleted
        else [
            {
                "code": "WAREHOUSE_PRESERVED",
                "message": "Only the lake pointer was removed; the DataMixer warehouse was preserved for reuse.",
            }
        ],
    }
