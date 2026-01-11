import os
import json
import uuid
import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import message_to_dict
from tortoise.expressions import Q
from ..models.body import response_body, ConfigModel
from ..models.db_models import StarterConfig, TaskModel
from ..utils.starter import StarterManager
from ..utils.task.task import update_task_state, get_task_state
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
LoopAI_DIR = os.path.dirname(BASE_DIR)

router = APIRouter(tags=["starter"])


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

manager = None
default_states = {}
_task_id = None


async def init_manager(task_id):
    global manager
    global default_states
    global _task_id

    _task_id = task_id

    config = await load_config(task_id)
    if not config:
        return

    with open(os.path.join(LoopAI_DIR, config['starter']['api_key_path']), 'r') as f:
        api_key = f.read().strip()

    # Read Tavily API key
    tavily_api_key = None
    if 'tavily_api_key_path' in config['starter'] and os.path.exists(config['starter']['tavily_api_key_path']):
        with open(config['starter']['tavily_api_key_path'], 'r') as f:
            tavily_api_key = f.read().strip()
            os.environ['TAVILY_API_KEY'] = tavily_api_key

    rag_api_key = None
    if 'rag' in config and 'api_key_path' in config['rag'] and os.path.exists(config['rag']['api_key_path']):
        with open(config['rag']['api_key_path'], 'r') as f:
            rag_api_key = f.read().strip()

    kaggle_username = config['starter'].get('kaggle_username', '') or ''
    kaggle_key = config['starter'].get('kaggle_key', '') or ''

    config['default_states']['obtainer']['api_key'] = api_key
    config['default_states']['obtainer']['category'] = config['default_states']['obtainer']['category'].upper()
    config['default_states']['obtainer']['tavily_api_key'] = tavily_api_key if tavily_api_key else ''
    config['default_states']['obtainer']['kaggle_username'] = kaggle_username
    config['default_states']['obtainer']['kaggle_key'] = kaggle_key

    if 'reset' in config['rag']:
        config['default_states']['obtainer']['reset_rag'] = config['rag']['reset']
    if 'embed_model' in config['rag']:
        embed_model = config['rag']['embed_model']
        if embed_model:  # Only set if not empty
            config['default_states']['obtainer']['rag_embed_model'] = embed_model
    if 'collection_name' in config['rag']:
        config['default_states']['obtainer']['rag_collection_name'] = config['rag']['collection_name']
    if 'api_base_url' in config['rag']:
        if config['rag']['api_base_url']:  # Only set if not empty
            config['default_states']['obtainer']['rag_api_base_url'] = config['rag']['api_base_url']
    if rag_api_key:
        config['default_states']['obtainer']['rag_api_key'] = rag_api_key

    manager = StarterManager(sg_init_args={
        'tools': [check_motivation],
        'model_name': config['starter']['model_name'],
        'base_url': config['starter']['base_url'],
        'api_key': api_key,
        'checkpointer': checkpointer,
        'store': store
    })

    default_states = config['default_states']


@router.post("/agent/start", operation_id='startAgent', summary="Start the agent")
async def start_agent(task_id: str):
    if not manager:
        await init_manager(task_id)
    state = await get_task_state(task_id, default_states)
    manager.start(default_state=state)
    return response_body(message="Agent started")


@router.post("/agent/input", operation_id='agentInput', summary="Send input to the agent")
async def agent_input(text: str):
    if not manager:
        return response_body(code=400, message="Starter manager not initialized")
    manager.send_input(text)
    return response_body(message="Input sent")


@router.post("/agent/stop", operation_id='stopAgent', summary="Stop the agent")
async def stop_agent():
    if not manager:
        return response_body(code=400, message="Starter manager not initialized")
    data = manager.poll_state()
    save_status = await update_task_state(_task_id, data.get('state', {}))
    manager.stop()
    return response_body(message="Agent stopped, the state is saved: {}".format(save_status))


@router.get("/agent/status", operation_id='getAgentStatus', summary="Get the agent status")
async def get_status():
    if not manager:
        return response_body(code=400, message="Starter manager not initialized")
    data = manager.poll_state()
    save_status = await update_task_state(_task_id, data.get('state', {}))
    return response_body(message="Agent state saved: {}".format(save_status), data=data)


@router.get("/agent/messages", operation_id='getAgentMessages', summary="Get the agent messages")
def get_state_messages():
    if not manager:
        return response_body(code=400, message="Starter manager not initialized").stream()
    data = manager.poll_state()
    if "state" not in data or not data['state']:
        return response_body(code=400, message="No messages available").stream()
    if "messages" not in data["state"] or not data["state"]["messages"]:
        return response_body(code=400, message="No messages available").stream()

    def decode_msg(msg):
        if type(msg) != dict:
            return message_to_dict(msg)
        return msg
    messages = [decode_msg(item) for item in data["state"]["messages"]]
    return response_body(message="Agent messages", data=messages)


async def get_message_call():
    if not manager:
        yield response_body(code=400, message="Starter manager not initialized").stream()
        return

    def decode_msg(msg):
        if type(msg) != dict:
            return message_to_dict(msg)
        return msg

    while True:
        await asyncio.sleep(0.1)
        data = manager.poll_state()
        if "state" not in data:
            yield response_body(code=401, message="No messages available").stream()
            continue
        if not data["state"] or "messages" not in data.get("state", {}) or not data["state"]["messages"]:
            yield response_body(code=401, message="No messages available").stream()
            continue
        messages = []
        if "stream_message" in data and data["stream_message"]:
            messages.append(decode_msg(data["stream_message"]))
        msg_state = data["event_streaming"]
        if msg_state == 'not_ready':
            yield response_body(code=401, message="wait for message").stream()
        else:
            yield response_body(message="Agent messages", status='loading' if msg_state == 'start' else 'success', data=messages).stream()
        if msg_state == 'finished':
            return


@router.get("/agent/message/stream", operation_id='getAgentMessageStream', summary="Get the agent message stream")
def get_message():
    return StreamingResponse(get_message_call(), media_type="text/event-stream")
