from pydantic import BaseModel
from typing import Optional, Dict, Any, List, List
from enum import Enum


class TaskStatus(str, Enum):
    """训练任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrainRequest(BaseModel):
    """训练请求模型"""
    framework: str
    config_path: str
    task_name: Optional[str] = None


class TrainResponse(BaseModel):
    """训练响应模型"""
    task_id: str
    status: TaskStatus
    message: Optional[str] = None


class TaskStatusResponse(BaseModel):
    """任务状态响应模型"""
    task_id: str
    status: TaskStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class LogResponse(BaseModel):
    """日志响应模型"""
    task_id: str
    logs: str
    total_lines: int


class SwanLabLogResponse(BaseModel):
    """SwanLab日志响应模型"""
    task_id: str
    log_path: Optional[str] = None
    message: Optional[str] = None


class SwanLabLogFolder(BaseModel):
    """SwanLab日志文件夹信息"""
    folder_name: str
    folder_path: str
    created_at: str


class AllSwanLabLogsResponse(BaseModel):
    """所有SwanLab日志响应模型"""
    total: int
    logs: List[SwanLabLogFolder]


class MetricsResponse(BaseModel):
    """训练指标响应模型"""
    task_id: str
    summary: Dict[str, Any]
    latest_metrics: List[Dict[str, Any]]
