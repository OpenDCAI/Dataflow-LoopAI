from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from tortoise.contrib.fastapi import register_tortoise
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pathlib import Path

from .utils.starter.starter import starter_process

from .controllers.config import router as config_router
from .controllers.starter import router as starter_router
from .controllers.task import router as task_router
from .controllers.train import router as train_router
from .controllers.resource import router as resource_router

import os
import signal

# 配置目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
DB_PATH = os.path.join(BASE_DIR, "db", "db.sqlite3")
DIST_DIR = Path(BASE_DIR) / "dist"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # 在 yield 前的代码会在应用 启动时执行，在 yield 后的代码会在应用 关闭时执行。
    for p in starter_process:
        print(f"terminate process {p.pid}")
        if p.is_alive():
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)

# 创建FastAPI应用
app = FastAPI(
    title="LLaMA Factory Remote Training Service",
    description="远程训练服务，支持通过API触发LLaMA Factory训练任务",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许的前端地址
    allow_credentials=True,  # 是否允许发送 Cookie
    allow_methods=["*"],  # 允许的 HTTP 方法
    allow_headers=["*"],  # 允许的请求头
)

register_tortoise(
    app,
    db_url=f"sqlite://{DB_PATH}",
    modules={"models": ["api.app.models.db_models"]},
    generate_schemas=True,
    add_exception_handlers=True,
)

app.include_router(config_router, prefix="/config", tags=["config"])
app.include_router(starter_router, prefix="/starter", tags=["starter"])
app.include_router(task_router, prefix="/task", tags=["task"])
app.include_router(train_router, prefix="/train", tags=["train"])
app.include_router(resource_router, prefix="/resource", tags=["resource"])

app.mount(
    "/assets",
    StaticFiles(directory=DIST_DIR / "assets", check_dir=False),
    name="frontend-assets",
)


@app.get("/info")
async def root():
    """根路径"""
    return {
        "message": "LLaMA Factory Remote Training Service",
        "version": "1.0.0",
        "endpoints": {
            "train": "POST /train - 启动训练任务",
            "status": "GET /train/status/{task_id} - 查询任务状态",
            "logs": "GET /train/logs/{task_id} - 获取任务日志",
            "tasks": "GET /train/tasks - 获取所有任务列表",
            "swanlab-logs-task": "GET /train/swanlab-logs/{task_id} - 获取指定任务的SwanLab日志文件夹路径",
            "swanlab-logs-all": "GET /train/swanlab-logs - 获取所有SwanLab日志文件夹"
        }
    }


@app.get("/", include_in_schema=False)
async def frontend_root():
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8855)
