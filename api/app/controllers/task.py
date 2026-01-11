import os
import json
import uuid
from fastapi import APIRouter
from omegaconf import OmegaConf
from tortoise.expressions import Q
from ..models.body import response_body, TaskItem
from ..models.db_models import Task
from ..utils.config.config import format_value

router = APIRouter(tags=["task"])

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))

def config_format(config: dict):
    system_config = config.get('system', {})
    states_config = config.get('states', {})
    result = {}
    for series_key in system_config:
        for key in system_config[series_key]:
            format_item = format_value(system_config[series_key][key])
            result.setdefault(series_key, {})[key] = format_item["value"]
    for series_key in states_config:
        if series_key == 'default':
            for key in states_config[series_key]:
                format_item = format_value(states_config[series_key][key])
                result.setdefault('default_states', {})[key] = format_item["value"]
        else:
            for key in states_config[series_key]:
                format_item = format_value(states_config[series_key][key])
                result.setdefault('default_states', {}).setdefault(series_key, {})[key] = format_item["value"]
    return result

@router.post("/task", operation_id='createTask', summary='创建任务项')
async def create_task(taskItem: TaskItem):
    """创建任务项"""
    task_id = str(uuid.uuid4())
    taskItem.task_id = task_id
    try:
        config = json.loads(taskItem.config)
    except:
        return response_body(code=400, status='error', message='config格式错误')()
    config = config_format(config)
    task = Task(
        task_id=task_id,
        name=taskItem.name,
        config=json.dumps(config),
        state=taskItem.state,
    )
    await task.save()
    return response_body(data={
        'id': task.id,
        'task_id': task.task_id,
        'name': task.name,
        'config': task.config,
        'state': task.state,
        'createdAt': task.createdAt,
        'updatedAt': task.updatedAt,
    })()

@router.get("/task/{task_id}", operation_id='getTask', summary='获取任务项')
async def get_task(task_id: str):
    """获取任务项"""
    task = await Task.get_or_none(task_id=task_id)
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()
    return response_body(data={
        'id': task.id,
        'task_id': task.task_id,
        'name': task.name,
        'config': task.config,
        'state': task.state,
        'createdAt': task.createdAt,
        'updatedAt': task.updatedAt,
    })()

@router.get("/list_tasks", operation_id='getTasks', summary='获取所有任务项')
async def get_tasks(search: str = None, offset: int = 0, limit: int = 50):
    """获取所有任务项"""
    qs = Task.all()

    if search:
        qs = qs.filter(
            Q(name__icontains=search) |
            Q(task_id__icontains=search)
        )

    qs = qs.offset(offset).limit(limit)

    tasks = await qs

    return response_body(data=[{
        "id": t.id,
        "task_id": t.task_id,
        "name": t.name,
        "createdAt": t.createdAt,
        "updatedAt": t.updatedAt,
    } for t in tasks])()

@router.put("/task", operation_id='updateTask', summary='更新任务项')
async def update_task(taskItem: TaskItem):
    """更新任务项"""
    task = await Task.get_or_none(id=taskItem.id)
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()
    task.name = taskItem.name
    if taskItem.config:
        try:
            config = json.loads(taskItem.config)
        except:
            return response_body(code=400, status='error', message='config格式错误')()
        config = config_format(config)
        task.config = json.dumps(config)
    if taskItem.state:
        task.state = taskItem.state
    await task.save()
    return response_body(data={
        'id': task.id,
        'task_id': task.task_id,
        'name': task.name,
        'config': task.config,
        'state': task.state,
        'createdAt': task.createdAt,
        'updatedAt': task.updatedAt,
    })()

@router.delete("/task/{id}", operation_id='delTask', summary='删除任务项')
async def del_task(id: str):
    """删除任务项"""
    task = await Task.get_or_none(id=id)
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()
    await task.delete()
    return response_body(code=200, status='success', message='任务项删除成功')()
