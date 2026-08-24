from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from tortoise.contrib.fastapi import register_tortoise
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from loopai.schema.system_runtime import (
    export_system_integrations_to_env,
    load_runtime_system_config,
)
from .utils.config.credential_migration import migrate_persisted_credentials

from .controllers.config import router as config_router
from .controllers.response_proxy import router as response_proxy_router
from .controllers.starter import router as starter_router
from .controllers.task import router as task_router
from .controllers.resource import router as resource_router
from .controllers.obtainer import router as obtainer_router

import os

# 配置目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
DB_PATH = os.path.join(BASE_DIR, "db", "db.sqlite3")
DIST_DIR = Path(BASE_DIR) / "dist"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# 创建FastAPI应用
app = FastAPI(
    title="LoopAI Server",
    description="LoopAI server with APIs for managing training tasks",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许的前端地址
    allow_credentials=True,  # 是否允许发送 Cookie
    allow_methods=["*"],  # 允许的 HTTP 方法
    allow_headers=["*"],  # 允许的请求头
)


@app.on_event("startup")
async def load_system_integrations() -> None:
    migrate_persisted_credentials(DB_PATH)
    export_system_integrations_to_env(load_runtime_system_config())

register_tortoise(
    app,
    db_url=f"sqlite://{DB_PATH}",
    modules={"models": ["api.app.models.db_models"]},
    generate_schemas=True,
    add_exception_handlers=True,
)

app.include_router(config_router, prefix="/config", tags=["config"])
app.include_router(response_proxy_router, prefix="/responseProxy", tags=["responseProxy"])
app.include_router(starter_router, prefix="/starter", tags=["starter"])
app.include_router(task_router, prefix="/task", tags=["task"])
app.include_router(resource_router, prefix="/resource", tags=["resource"])
app.include_router(obtainer_router, prefix="/obtainer", tags=["obtainer"])

app.mount(
    "/assets",
    StaticFiles(directory=DIST_DIR / "assets", check_dir=False),
    name="frontend-assets",
)


@app.get("/info")
async def root():
    """根路径"""
    return {
        "message": "LoopAI Server",
        "version": "1.0.0",
        "endpoints": {
            "task-train-status": "GET /task/train_status - 获取 Trainer 指标文件",
            "codex-submit": "POST /starter/codex/stream - 提交 Codex SDK 任务",
            "codex-session": "GET /starter/codex/session/{session_id} - 获取 Codex 会话状态"
        }
    }


@app.get("/", include_in_schema=False)
async def frontend_root():
    return _frontend_index_response()


def _frontend_index_response():
    index_path = DIST_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return JSONResponse(
        status_code=404,
        content={
            "message": "Frontend dist is not installed.",
            "hint": "Build ui/ or run scripts/download_ui_release.py to populate api/dist.",
        },
    )


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "loopai-service",
        "directories": {
            "configs": os.path.exists(CONFIGS_DIR),
            "logs": os.path.exists(LOGS_DIR),
            "runs": os.path.exists(RUNS_DIR)
        }
    }


@app.get("/{full_path:path}", include_in_schema=False)
async def frontend_fallback(full_path: str):
    if full_path == "m" or full_path.startswith("m/"):
        return RedirectResponse(url=f"/#/{full_path}", status_code=307)
    return _frontend_index_response()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8855)
