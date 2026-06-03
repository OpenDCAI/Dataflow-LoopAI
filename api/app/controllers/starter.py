import os
import json
import uuid
import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from tortoise.expressions import Q
from ..models.body import (
    response_body,
    ConfigModel,
    StarterCodexRequest,
    StarterCodexSessionInputRequest,
    StarterCodexSessionResumeRequest,
)
from ..models.db_models import StarterConfig, TaskModel
from ..services.starter import CodexStarterService, codex_session_store, load_starter_system_config
from ..services.task import build_initial_task_state
from ..utils.monitor.hw_stat import get_nvidia_gpu_usage, get_huawei_npu_usage, get_cpu_usage, get_memory_usage

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
LoopAI_DIR = os.path.dirname(BASE_DIR)
DB_PATH = os.path.join(BASE_DIR, "db", "db.sqlite3")

router = APIRouter(tags=["starter"])


@router.post("/codex/stream", operation_id="starterCodexStream", summary="Run codex-sdk with SSE streaming")
async def starter_codex_stream(req: StarterCodexRequest):
    system_config = await load_starter_system_config()
    service = CodexStarterService(
        system_config=system_config, session_store=codex_session_store)
    return StreamingResponse(
        service.stream(
            prompt=req.prompt,
            workspace=req.workspace,
            session_id=req.session_id,
            env_overrides={
                'DB_PATH': DB_PATH
            },
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/codex/session/{session_id}", operation_id="starterCodexSession", summary="Get codex session state")
async def starter_codex_session(session_id: str):
    session = codex_session_store.get(session_id)
    if session is None:
        return response_body(code=404, status="error", message="Codex session not found")()
    return response_body(data=session)()


@router.post("/codex/session/input", operation_id="starterCodexSessionInput", summary="Store codex session input")
async def starter_codex_session_input(req: StarterCodexSessionInputRequest):
    session = codex_session_store.merge_inputs(req.session_id, req.values)
    if session is None:
        return response_body(code=404, status="error", message="Codex session not found")()
    codex_session_store.update(req.session_id, status="input_received")
    return response_body(message="Codex session input stored", data=session)()


@router.post("/codex/session/resume", operation_id="starterCodexSessionResume", summary="Resume codex session")
async def starter_codex_session_resume(req: StarterCodexSessionResumeRequest):
    session = codex_session_store.get(req.session_id)
    if session is None:
        return response_body(code=404, status="error", message="Codex session not found")()

    system_config = await load_starter_system_config()
    service = CodexStarterService(
        system_config=system_config, session_store=codex_session_store)
    return StreamingResponse(
        service.stream(
            prompt=session.get("prompt") or "",
            workspace=session.get("workspace"),
            session_id=req.session_id,
            env_overrides=session.get("env_overrides"),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


async def load_config(task_id=None):
    if task_id is None:
        configItem = await StarterConfig.filter(Q(name='starter')).first()
        if not configItem:
            return None
        config = configItem.config
    else:
        taskItem = await TaskModel.get_or_none(task_id=task_id)
        if not taskItem:
            return None
        config = taskItem.config
    return json.loads(config)


@router.get("/agent/status", operation_id="getAgentStatus", summary="Get agent status by task_id")
async def get_agent_status(task_id: str):
    task = await TaskModel.get_or_none(task_id=task_id)
    if not task:
        return response_body(code=404, status='error', message='任务项不存在')()

    try:
        state = json.loads(task.state) if task.state else None
    except Exception:
        state = None

    if not isinstance(state, dict):
        state = await build_initial_task_state(task_id)

    state["task_id"] = task_id
    state.setdefault("messages", [])

    return response_body(data={
        "running": False,
        "event_streaming": "not_ready",
        "waiting_llm": False,
        "current": state.get("current"),
        "running_tasks": [],
        "interrupt_value": None,
        "state": state,
        "custom_info": None,
        "updated_custom_info": None,
        "stream_message": None,
    })()


@router.get("/agent/hardware_usage", operation_id='getHardwareUsage', summary="Get the hardware usage")
async def get_hw_usage():
    gpu_usage = []
    cpu_usage = {}
    mem_usage = {}
    res = get_nvidia_gpu_usage()
    if not res[0]:
        res = get_huawei_npu_usage()
    if res[0]:
        gpu_usage = res[1]
    cpu_usage = get_cpu_usage()
    mem_usage = get_memory_usage()
    return response_body(message="Hardware usage", data={
        "gpu_usage": gpu_usage,
        "cpu_usage": cpu_usage,
        "mem_usage": mem_usage
    })
