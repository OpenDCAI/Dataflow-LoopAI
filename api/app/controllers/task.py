import os
from fastapi import APIRouter
from ..models.body import response_body, TaskItem, TaskStateConfigModel
from ..services.task import (
    TaskServiceError,
    create_task as create_task_service,
    delete_task as delete_task_service,
    get_latest_task_runtime,
    get_task as get_task_service,
    get_task_state_config as get_task_state_config_service,
    get_train_status as get_train_status_service,
    list_latest_task_runtimes,
    list_task_runtime_history,
    list_tasks as list_tasks_service,
    update_task as update_task_service,
    update_task_state_config as update_task_state_config_service,
)

router = APIRouter(tags=["task"])

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
LoopAI_DIR = os.path.dirname(BASE_DIR)


def _resolve_output_dir(output_dir: str) -> str:
    output_dir = (output_dir or "outputs").strip() or "outputs"
    output_dir = os.path.expanduser(output_dir)
    if os.path.isabs(output_dir):
        return output_dir
    return os.path.abspath(os.path.join(LoopAI_DIR, output_dir))

@router.post("/task", operation_id='createTask', summary='创建任务项')
async def create_task(taskItem: TaskItem):
    """创建任务项"""
    try:
        return response_body(data=await create_task_service(taskItem))()
    except TaskServiceError as exc:
        return response_body(code=exc.code, status='error', message=exc.message)()

@router.get("/task/{task_id}", operation_id='getTask', summary='获取任务项')
async def get_task(task_id: str):
    """获取任务项"""
    task = await get_task_service(task_id)
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()
    return response_body(data=task)()

@router.get("/list_tasks", operation_id='getTasks', summary='获取所有任务项')
async def get_tasks(search: str = None, offset: int = 0, limit: int = 50):
    """获取所有任务项"""
    return response_body(data=await list_tasks_service(search, offset, limit))()

@router.put("/task", operation_id='updateTask', summary='更新任务项')
async def update_task(taskItem: TaskItem):
    """更新任务项"""
    try:
        task = await update_task_service(taskItem)
    except TaskServiceError as exc:
        return response_body(code=exc.code, status='error', message=exc.message)()
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()
    return response_body(data=task)()


@router.get("/task/{task_id}/state_config", operation_id='getTaskStateConfig', summary='获取任务State配置')
async def get_task_state_config(task_id: str):
    """获取任务State配置"""
    task_state_config = await get_task_state_config_service(task_id)
    if not task_state_config:
        return response_body(code=404, status='error', message='任务项不存在')()
    return response_body(data=task_state_config)()


@router.post("/task/{task_id}/state_config", operation_id='updateTaskStateConfig', summary='更新任务State配置')
async def update_task_state_config(task_id: str, state_config: TaskStateConfigModel):
    """更新任务State配置"""
    try:
        return response_body(data=await update_task_state_config_service(task_id, state_config.states or {}))()
    except TaskServiceError as exc:
        return response_body(code=exc.code, status='error', message=exc.message)()

@router.delete("/task/{id}", operation_id='delTask', summary='删除任务项')
async def del_task(id: str):
    """删除任务项"""
    deleted = await delete_task_service(id)
    if not deleted:
        return response_body(code=404, status='error', message='任务项不存在')()
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
    output_dir = _resolve_output_dir(output_dir)
    try:
        metrics = get_train_status_service(output_dir, task_id, train_task_id)
        return response_body(data=metrics)()
    except TaskServiceError as exc:
        return response_body(code=exc.code, status='error', message=exc.message)()
