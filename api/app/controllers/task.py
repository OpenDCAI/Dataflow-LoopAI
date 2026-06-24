import os
import json
import uuid
from fastapi import APIRouter
from tortoise.expressions import Q
from ..models.body import response_body, TaskItem, TaskRuntimeItem
from ..models.db_models import TaskModel
from ..services.task import (
    build_initial_task_state,
    create_task_runtime,
    get_latest_task_runtime,
    list_latest_task_runtimes,
    list_task_runtime_history,
    parse_task_state_overrides,
    update_task_runtime,
    upsert_task_runtime,
)
from ..utils.task.task import config_format

router = APIRouter(tags=["task"])

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))

@router.post("/task", operation_id='createTask', summary='创建任务项')
async def create_task(taskItem: TaskItem):
    """创建任务项"""
    task_id = str(uuid.uuid4())
    taskItem.task_id = task_id
    try:
        config = json.loads(taskItem.config)
    except Exception:
        return response_body(code=400, status='error', message='config格式错误')()
    try:
        state_overrides = parse_task_state_overrides(taskItem.state)
        initial_state = await build_initial_task_state(task_id, state_overrides=state_overrides)
    except Exception as exc:
        return response_body(code=400, status='error', message=f'state格式错误: {exc}')()
    config = config_format(config, task_id)
    task = TaskModel(
        task_id=task_id,
        name=taskItem.name,
        config=json.dumps(config),
        state=json.dumps(initial_state, ensure_ascii=False),
    )
    await task.save()
    return response_body(data={
        'id': task.id,
        'task_id': task.task_id,
        'name': task.name,
        'config': task.config,
        'state': task.state,
        'ai_thread_id': task.ai_thread_id,
        'createdAt': task.createdAt,
        'updatedAt': task.updatedAt,
    })()

@router.get("/task/{task_id}", operation_id='getTask', summary='获取任务项')
async def get_task(task_id: str):
    """获取任务项"""
    task = await TaskModel.get_or_none(task_id=task_id)
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()
    return response_body(data={
        'id': task.id,
        'task_id': task.task_id,
        'name': task.name,
        'config': task.config,
        'state': task.state,
        'ai_thread_id': task.ai_thread_id,
        'createdAt': task.createdAt,
        'updatedAt': task.updatedAt,
    })()

@router.get("/list_tasks", operation_id='getTasks', summary='获取所有任务项')
async def get_tasks(search: str = None, offset: int = 0, limit: int = 50):
    """获取所有任务项"""
    qs = TaskModel.all()

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
        "ai_thread_id": t.ai_thread_id,
        "createdAt": t.createdAt,
        "updatedAt": t.updatedAt,
    } for t in tasks])()

@router.put("/task", operation_id='updateTask', summary='更新任务项')
async def update_task(taskItem: TaskItem):
    """更新任务项"""
    task = await TaskModel.get_or_none(id=taskItem.id)
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
    await task.save()
    return response_body(data={
        'id': task.id,
        'task_id': task.task_id,
        'name': task.name,
        'config': task.config,
        'state': task.state,
        'ai_thread_id': task.ai_thread_id,
        'createdAt': task.createdAt,
        'updatedAt': task.updatedAt,
    })()

@router.delete("/task/{id}", operation_id='delTask', summary='删除任务项')
async def del_task(id: str):
    """删除任务项"""
    task = await TaskModel.get_or_none(id=id)
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()
    await task.delete()
    return response_body(code=200, status='success', message='任务项删除成功')()

@router.get("/task/runtime/{task_id}/{node_name}/latest", operation_id="getLatestTaskRuntime", summary="获取节点最新运行状态")
async def get_latest_runtime(task_id: str, node_name: str):
    runtime = await get_latest_task_runtime(task_id=task_id, node_name=node_name)
    if not runtime:
        return response_body(code=404, status="error", message="任务运行时状态不存在")()
    return response_body(data=runtime)()


@router.get("/task/runtime/{task_id}/{node_name}/history", operation_id="getTaskRuntimeHistory", summary="获取节点历史运行状态")
async def get_runtime_history(task_id: str, node_name: str):
    runtimes = await list_task_runtime_history(task_id=task_id, node_name=node_name)
    return response_body(data=runtimes)()


@router.get("/task/runtime/{task_id}/latest", operation_id="getLatestTaskRuntimes", summary="获取任务下所有节点最新运行状态")
async def get_latest_runtimes(task_id: str):
    runtimes = await list_latest_task_runtimes(task_id=task_id)
    return response_body(data=runtimes)()

@router.get("/train_status", operation_id='getTrainStatus', summary='获取训练状态')
async def get_train_status(output_dir: str, task_id: str, train_task_id: str):
    """获取训练状态"""
    watch_path = os.path.join(output_dir, task_id, 'trainer', train_task_id)
    final_path = os.path.join(watch_path, 'metrics', 'metrics.json')
    if os.path.exists(final_path):
        with open(final_path, 'r') as f:
            metrics = json.load(f)
            return response_body(data=metrics)()
    else:
        return response_body(code=404, status='error', message='训练状态文件不存在:' + final_path)()
