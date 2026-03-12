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