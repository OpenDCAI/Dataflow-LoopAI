from tortoise import fields, models
from tortoise.fields.base import CASCADE


class BaseModel(models.Model):
    """基础模型类，提供通用字段"""
    
    class Meta:
        abstract = True

class StarterConfig(BaseModel):
    """启动器配置模型"""
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=255)
    config = fields.TextField(null=True)

class TaskModel(BaseModel):
    """任务项模型"""
    id = fields.IntField(pk=True)
    task_id = fields.CharField(max_length=255)
    name = fields.CharField(max_length=255)
    config = fields.TextField(null=True)
    state = fields.TextField(null=True)
    ai_thread_id = fields.CharField(max_length=255, null=True)
    createdAt = fields.DatetimeField(auto_now_add=True)
    updatedAt = fields.DatetimeField(auto_now=True)

class TaskRuntime(BaseModel):
    """任务运行时模型"""
    id = fields.IntField(pk=True)
    task_id = fields.CharField(max_length=255)
    node_name = fields.CharField(max_length=255, null=True)
    version = fields.CharField(max_length=255, null=True)
    state = fields.TextField(null=True)
    status = fields.TextField(null=True)
    createdAt = fields.DatetimeField(auto_now_add=True)
    updatedAt = fields.DatetimeField(auto_now=True)


class ThreadHistory(BaseModel):
    """Codex thread 历史快照"""
    id = fields.IntField(pk=True)
    task_id = fields.CharField(max_length=255)
    session_id = fields.CharField(max_length=255, null=True)
    thread_id = fields.CharField(max_length=255, null=True)
    prompt = fields.TextField(null=True)
    workspace = fields.TextField(null=True)
    status = fields.TextField(null=True)
    env_overrides = fields.TextField(null=True)
    inputs = fields.TextField(null=True)
    pending_request = fields.TextField(null=True)
    pending_prompts = fields.TextField(null=True)
    active_prompt = fields.TextField(null=True)
    conversation = fields.TextField(null=True)
    events = fields.TextField(null=True)
    final_result = fields.TextField(null=True)
    last_error = fields.TextField(null=True)
    createdAt = fields.DatetimeField(auto_now_add=True)
    updatedAt = fields.DatetimeField(auto_now=True)

class ResourceModel(BaseModel):
    """资源模型"""
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=255)
    description = fields.TextField(null=True)
    path = fields.TextField(null=True)
    file_type = fields.TextField(null=True)
    res_type = fields.TextField(null=True)
    size = fields.IntField(null=True)
    status = fields.TextField(null=True)
    createdAt = fields.DatetimeField(auto_now_add=True)
    updatedAt = fields.DatetimeField(auto_now=True)
