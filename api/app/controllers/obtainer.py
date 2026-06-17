from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ..models.body import response_body
from ..utils.obtainer.monitor import build_lake_monitor, probe_embedding_health

router = APIRouter(tags=["obtainer"])

REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_lake_path(lake: str | None) -> Path:
    raw = lake or ".loopai/lake.yaml"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    if path.suffix in {".yaml", ".yml"} and not path.exists():
        raise FileNotFoundError(f"Lake config not found: {path}")
    return path


@router.get("/lake/monitor", operation_id="getObtainerLakeMonitor", summary="获取 Obtainer 数据湖监控数据")
async def get_lake_monitor(lake: str | None = None):
    try:
        data = build_lake_monitor(lake=_resolve_lake_path(lake))
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
