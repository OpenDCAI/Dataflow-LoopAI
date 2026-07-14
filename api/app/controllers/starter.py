import os
import json
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from tortoise.expressions import Q
from ..models.body import (
    response_body,
    StarterCodexRequest,
)
from ..models.db_models import StarterConfig, TaskModel
from ..services.starter import (
    CodexStarterService,
    build_custom_info,
    codex_session_store,
    load_starter_system_config,
    parse_task_state,
)
from ..services.task import build_initial_task_state, list_latest_task_runtimes
from ..utils.monitor.hw_stat import get_nvidia_gpu_usage, get_huawei_npu_usage, get_cpu_usage, get_memory_usage

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
LoopAI_DIR = os.path.dirname(BASE_DIR)
DB_PATH = os.path.join(BASE_DIR, "db", "db.sqlite3")
DEFAULT_OUTPUTS_DIR = os.path.join(LoopAI_DIR, "outputs")

router = APIRouter(tags=["starter"])


@router.post("/codex/stream", operation_id="starterCodexStream", summary="Submit codex prompt")
async def starter_codex_stream(req: StarterCodexRequest):
    system_config = await load_starter_system_config()
    service = CodexStarterService(
        system_config=system_config, session_store=codex_session_store)
    payload = await service.submit(
        prompt=req.prompt,
        workspace=req.workspace,
        session_id=req.session_id,
        env_overrides={
            'DB_PATH': DB_PATH,
            'TASK_ID': req.session_id,
        },
    )
    message = "Codex prompt queued" if payload.get("type") == "queued" else "Codex prompt submitted"
    return response_body(message=message, data=payload)()


@router.get("/codex/session/{session_id}", operation_id="starterCodexSession", summary="Get codex session state")
async def starter_codex_session(session_id: str):
    system_config = await load_starter_system_config()
    service = CodexStarterService(system_config=system_config, session_store=codex_session_store)
    session = codex_session_store.get(session_id)
    task = await TaskModel.get_or_none(task_id=session_id)
    if session is None:
        restored = await service.restore_session_snapshot(session_id)
        if restored is not None:
            payload = dict(restored)
            payload["ai_thread_id"] = task.ai_thread_id if task else payload.get("codex_thread_id")
            payload["conversation_source"] = "starter"
            return response_body(message="Codex session restored", data=payload)()
        return response_body(
            message="Codex session not started",
            data={
                "session_id": session_id,
                "status": "not_started",
                "ai_thread_id": task.ai_thread_id if task else None,
                "conversation": [],
                "conversation_source": "starter",
                "conversation_path": None,
                "events": [],
                "pending_prompts": [],
                "active_prompt": None,
                "final_result": None,
                "last_error": None,
            },
        )()
    payload = dict(session)
    payload["ai_thread_id"] = task.ai_thread_id if task else payload.get("codex_thread_id")
    payload["conversation_source"] = "starter"
    payload["conversation_path"] = None
    return response_body(data=payload)()


@router.get("/codex/session/{session_id}/stream", operation_id="starterCodexSessionStream", summary="Stream codex session events")
async def starter_codex_session_stream(session_id: str, request: Request):
    session = codex_session_store.get(session_id)
    if session is None:
        return response_body(
            message="Codex session not started",
            data={
                "session_id": session_id,
                "status": "not_started",
            },
        )()
    async def event_stream():
        last_event_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
        try:
            last_index = max(0, int(last_event_id) + 1) if last_event_id is not None else 0
        except (TypeError, ValueError):
            last_index = 0

        def wrap_sse_data(data, event_id: int | None = None):
            payload = dict(data or {})
            if event_id is not None:
                payload.setdefault("_event_index", event_id)
            encoded = json.dumps(response_body(data=payload)(), ensure_ascii=False)
            if event_id is None:
                return f"data: {encoded}\n\n"
            return f"id: {event_id}\ndata: {encoded}\n\n"

        while True:
            current = codex_session_store.get(session_id)
            if current is None:
                yield response_body(
                    code=404,
                    status="error",
                    message="Codex session not found",
                ).stream()
                return

            events = current.get("events", [])
            while last_index < len(events):
                payload = events[last_index].get("payload", {})
                yield wrap_sse_data(payload, last_index)
                last_index += 1

            if current.get("status") not in {"submitted", "running", "finishing"}:
                yield wrap_sse_data({
                    "type": "session.snapshot",
                    "session_id": session_id,
                    "session": current,
                }, len(events))
                return

            await asyncio.sleep(0.5)

    if session.get("status") not in {"submitted", "running", "finishing"}:
        return response_body(
            message="Codex session is not running",
            data={
                "session_id": session_id,
                "status": session.get("status") or "not_started",
            },
        )()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/codex/session/{session_id}/reset", operation_id="starterCodexSessionReset", summary="Reset codex session history")
async def starter_codex_session_reset(session_id: str):
    session = codex_session_store.get(session_id)
    task = await TaskModel.get_or_none(task_id=session_id)
    if session is None and task is None:
        return response_body(code=404, status="error", message="Codex session not found")()
    if session is not None and session.get("status") in {"submitted", "running", "finishing"}:
        return response_body(code=409, status="error", message="Codex session is still running and cannot be reset")()
    if task is not None:
        task.ai_thread_id = None
        await task.save()
    if session is not None:
        session = codex_session_store.reset(session_id)
    else:
        session = {
            "session_id": session_id,
            "status": "created",
            "conversation": [],
            "events": [],
            "pending_prompts": [],
            "active_prompt": None,
            "final_result": None,
            "last_error": None,
            "ai_thread_id": None,
        }
    if isinstance(session, dict):
        session["ai_thread_id"] = None
    return response_body(message="Codex session reset", data=session)()


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

    state = parse_task_state(task.state)
    task_runtimes = await list_latest_task_runtimes(task_id)

    if not isinstance(state, dict):
        state = await build_initial_task_state(task_id)

    custom_info = build_custom_info(task_id, state, DEFAULT_OUTPUTS_DIR, LoopAI_DIR)
    state["task_id"] = task_id
    state.setdefault("messages", [])

    return response_body(data={
        "running": False,
        "event_streaming": "not_ready",
        "waiting_llm": False,
        "current": state.get("current"),
        "node_status": task_runtimes,
        "interrupt_value": None,
        "state": state,
        "custom_info": custom_info,
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
