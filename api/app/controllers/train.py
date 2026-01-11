import os
import signal
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from ..models.task_models import TrainRequest, TrainResponse, TaskStatusResponse, LogResponse, TaskStatus, SwanLabLogResponse, AllSwanLabLogsResponse
from ..utils.train import TaskManager, generate_task_id, save_yaml_config, read_log_file, validate_yaml_content

router = APIRouter(tags=["train"])

# 配置目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
RUNS_DIR = os.path.join(BASE_DIR, "runs")

# 创建任务管理器
task_manager = TaskManager(CONFIGS_DIR, LOGS_DIR, RUNS_DIR)


@router.post("/", response_model=TrainResponse)
async def start_training(request: TrainRequest):
    """启动训练任务 - JSON配置方式"""
    try:
        # 验证YAML配置
        if not validate_yaml_content(request.config):
            raise HTTPException(
                status_code=400, detail="Invalid YAML configuration")

        # 生成任务ID
        task_id = generate_task_id()

        # 保存配置文件
        config_path = save_yaml_config(task_id, request.config, CONFIGS_DIR)

        # 创建训练任务
        task_info = task_manager.create_task(
            task_id, config_path, request.task_name)

        # 启动训练
        if task_manager.start_training(task_id):
            return TrainResponse(
                task_id=task_id,
                status=TaskStatus.RUNNING,
                message="Training task started successfully"
            )
        else:
            raise HTTPException(
                status_code=500, detail="Failed to start training task")

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/upload", response_model=TrainResponse)
async def start_training_upload(
    file: UploadFile = File(...),
    task_name: Optional[str] = Form(None)
):
    """启动训练任务 - 文件上传方式"""
    try:
        # 检查文件类型
        if not file.filename.endswith(('.yaml', '.yml')):
            raise HTTPException(
                status_code=400, detail="Only YAML files are supported")

        # 读取文件内容
        content = await file.read()
        config_content = content.decode('utf-8')

        # 验证YAML配置
        if not validate_yaml_content(config_content):
            raise HTTPException(
                status_code=400, detail="Invalid YAML configuration")

        # 生成任务ID
        task_id = generate_task_id()

        # 保存配置文件
        config_path = save_yaml_config(task_id, config_content, CONFIGS_DIR)

        # 创建训练任务
        task_info = task_manager.create_task(task_id, config_path, task_name)

        # 启动训练
        if task_manager.start_training(task_id):
            return TrainResponse(
                task_id=task_id,
                status=TaskStatus.RUNNING,
                message="Training task started successfully"
            )
        else:
            raise HTTPException(
                status_code=500, detail="Failed to start training task")

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """获取任务状态"""
    task_info = task_manager.get_task_status(task_id)

    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStatusResponse(
        task_id=task_info['task_id'],
        status=task_info['status'],
        created_at=task_info['created_at'],
        started_at=task_info.get('started_at'),
        completed_at=task_info.get('completed_at'),
        error_message=task_info.get('error_message')
    )


@router.get("/logs/{task_id}", response_model=LogResponse)
async def get_task_logs(task_id: str, max_lines: Optional[int] = None):
    """获取任务日志"""
    task_info = task_manager.get_task_status(task_id)

    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")

    log_path = os.path.join(LOGS_DIR, f"{task_id}.log")
    logs, total_lines = read_log_file(log_path, max_lines)

    return LogResponse(
        task_id=task_id,
        logs=logs,
        total_lines=total_lines
    )


@router.get("/tasks")
async def get_all_tasks():
    """获取所有任务列表"""
    tasks = task_manager.get_all_tasks()
    return {
        "total": len(tasks),
        "tasks": [
            {
                "task_id": task_id,
                "task_name": task_info.get('task_name'),
                "status": task_info['status'],
                "created_at": task_info['created_at'],
                "started_at": task_info.get('started_at'),
                "completed_at": task_info.get('completed_at')
            }
            for task_id, task_info in tasks.items()
        ]
    }


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    """取消任务"""
    if task_manager.cancel_task(task_id):
        return {"message": f"Task {task_id} cancelled successfully"}
    else:
        raise HTTPException(status_code=400, detail="Cannot cancel task")


@router.get("/swanlab-logs/{task_id}", response_model=SwanLabLogResponse)
async def get_train_output_swanlab_log_path(task_id: str):
    """获取指定任务的SwanLab日志文件夹路径"""
    task_info = task_manager.get_task_status(task_id)

    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")

    log_path = task_manager.get_train_output_swanlab_log_path(task_id)

    if log_path:
        return SwanLabLogResponse(
            task_id=task_id,
            log_path=log_path,
            message="SwanLab log folder found"
        )
    else:
        return SwanLabLogResponse(
            task_id=task_id,
            log_path=None,
            message="SwanLab log folder not found or task not started yet"
        )


@router.get("/swanlab-logs", response_model=AllSwanLabLogsResponse)
async def get_all_swanlab_logs():
    """获取所有SwanLab日志文件夹"""
    logs = task_manager.get_all_swanlab_logs()

    return AllSwanLabLogsResponse(
        total=len(logs),
        logs=logs
    )
