from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..models.body import response_body
from ..utils.obtainer.monitor import probe_embedding_health
from loopai.skills.ObtainerCLI.monitor_state import read_monitor_state, start_background_rebuild
from loopai.agents.Obtainer.datamixer.console import run_cli as run_datamixer_console_cli
from loopai.skills.ObtainerCLI.datamixer_adapter import warehouse_root
from loopai.skills.ObtainerCLI.lake_manager import (
    current_lake_pointer,
    delete_lake_pointer,
    load_lake_pointer,
    scan_lake_candidates,
)

router = APIRouter(tags=["obtainer"])

REPO_ROOT = Path(__file__).resolve().parents[3]


class DataMixerCliRequest(BaseModel):
    argv: list[str] | None = None
    line: str | None = None
    files: dict[str, str] | None = None
    lake: str | None = None
    root: str | None = None


class DataMixerLakeLoadRequest(BaseModel):
    warehouse: str
    link: str | None = None
    lake_root: str | None = None


class DataMixerLakeDeleteRequest(BaseModel):
    link: str | None = None
    delete_warehouse: bool = False
    yes: bool = False


def _resolve_repo_path(raw: str | None, default: str) -> Path:
    path = Path(raw or default).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _resolve_lake_path(lake: str | None) -> Path:
    path = _resolve_repo_path(lake, ".loopai/lake.yaml")
    if path.suffix in {".yaml", ".yml"} and not path.exists():
        raise FileNotFoundError(f"Lake config not found: {path}")
    return path


def _resolve_datamixer_root(*, lake: str | None = None, root: str | None = None) -> Path:
    if root:
        path = Path(root).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path
    return warehouse_root(_resolve_lake_path(lake))


def _datamixer_command_payload() -> dict[str, Any]:
    return {
        "entrypoint": "loopai-obtainercli dm",
        "backend": "datamixer",
        "groups": [
            {
                "key": "lake",
                "label": "Lake Management",
                "commands": ["lake scan", "lake current", "lake load --warehouse /path/to/warehouse", "lake delete"],
            },
            {
                "key": "catalog",
                "label": "Catalog",
                "commands": ["status", "stats", "schema", "columns", "dataset list", "query"],
            },
            {
                "key": "ingest",
                "label": "Ingest",
                "commands": ["ingest", "agent-ingest", "dataset-acquisition-agent start"],
            },
            {
                "key": "operators",
                "label": "Processing",
                "commands": ["op list", "op run", "pipeline run", "dataflow agent-run"],
            },
            {
                "key": "index",
                "label": "Index & Recall",
                "commands": ["index stats", "index build", "recall"],
            },
            {
                "key": "recipe",
                "label": "Recipe Export",
                "commands": ["recipe validate", "recipe plan", "recipe preview", "recipe export --snapshot"],
            },
            {
                "key": "lineage",
                "label": "Lineage",
                "commands": ["snapshot list", "snapshot create", "lineage list"],
            },
        ],
    }


@router.get("/lake/monitor", operation_id="getObtainerLakeMonitor", summary="获取 Obtainer 数据湖监控数据")
async def get_lake_monitor(lake: str | None = None):
    try:
        lake_path = _resolve_lake_path(lake)
        data = read_monitor_state(warehouse_root(lake_path), lake=lake_path)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.post("/lake/monitor/rebuild", operation_id="rebuildObtainerLakeMonitor", summary="异步重建 Obtainer 数据湖监控 cache")
async def rebuild_lake_monitor(lake: str | None = None):
    try:
        lake_path = _resolve_lake_path(lake)
        data = start_background_rebuild(warehouse_root(lake_path), lake=lake_path)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get("/lake/embedding_health", operation_id="getObtainerEmbeddingHealth", summary="探测 Obtainer embedding 服务状态")
async def get_embedding_health(lake: str | None = None, timeout_seconds: float = 3.0):
    try:
        data = probe_embedding_health(lake=_resolve_lake_path(lake), timeout_seconds=timeout_seconds)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get(
    "/datamixer/lake/current",
    operation_id="getObtainerDataMixerLakeCurrent",
    summary="获取当前 DataMixer 数据湖指针",
)
async def get_datamixer_lake_current(lake: str | None = None):
    try:
        data = current_lake_pointer(link_path=_resolve_repo_path(lake, ".loopai/lake.yaml"))
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get(
    "/datamixer/lake/scan",
    operation_id="scanObtainerDataMixerLakes",
    summary="扫描项目与缓存目录中的 DataMixer 数据湖",
)
async def scan_datamixer_lakes(lake: str | None = None, max_depth: int = 6):
    try:
        data = scan_lake_candidates(
            project_root=REPO_ROOT,
            active_link=_resolve_repo_path(lake, ".loopai/lake.yaml"),
            max_depth=max_depth,
        )
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.post(
    "/datamixer/lake/load",
    operation_id="loadObtainerDataMixerLake",
    summary="加载已有 DataMixer warehouse 为当前数据湖",
)
async def load_datamixer_lake(request: DataMixerLakeLoadRequest):
    try:
        warehouse = _resolve_repo_path(request.warehouse, request.warehouse)
        link = _resolve_repo_path(request.link, ".loopai/lake.yaml")
        lake_root = _resolve_repo_path(request.lake_root, request.lake_root) if request.lake_root else None
        data = load_lake_pointer(warehouse=warehouse, link_path=link, lake_root=lake_root)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.post(
    "/datamixer/lake/delete",
    operation_id="deleteObtainerDataMixerLake",
    summary="卸载当前 DataMixer 数据湖指针",
)
async def delete_datamixer_lake(request: DataMixerLakeDeleteRequest):
    try:
        link = _resolve_repo_path(request.link, ".loopai/lake.yaml")
        data = delete_lake_pointer(
            link_path=link,
            delete_warehouse=request.delete_warehouse,
            yes=request.yes,
        )
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get(
    "/datamixer/commands",
    operation_id="getObtainerDataMixerCommands",
    summary="获取 DataMixer 命令面",
)
async def get_datamixer_commands():
    return response_body(data=_datamixer_command_payload())()


@router.post(
    "/datamixer/cli",
    operation_id="runObtainerDataMixerCli",
    summary="通过 DataMixer 命令面执行数据湖操作",
)
async def run_datamixer_cli(request: DataMixerCliRequest):
    try:
        root = _resolve_datamixer_root(lake=request.lake, root=request.root)
        result = run_datamixer_console_cli(
            root,
            argv=request.argv,
            line=request.line,
            files=request.files,
        )
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=result)()
