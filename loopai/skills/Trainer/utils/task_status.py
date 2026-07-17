"""
训练任务状态枚举
从 api/app/models/task_models.py 迁移
"""

from enum import Enum


class TaskStatus(str, Enum):
    """训练任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
