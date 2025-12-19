import os
import json
import uuid
from fastapi import APIRouter
from omegaconf import OmegaConf
from tortoise.expressions import Q
from ..models.body import response_body, ConfigModel
from ..models.db_models import StarterConfig
from ..utils.starter import StarterManager
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
LoopAI_DIR = os.path.dirname(BASE_DIR)

router = APIRouter(tags=["starter"])


async def load_config():
    config = await StarterConfig.filter(Q(name='starter')).first()
    if not config:
        return None
    return json.loads(config.config)

manager = None
default_states = {}

async def init_manager():
    global manager
    global default_states

    config = await load_config()
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

    config['default_states']['obtainer_api_key'] = api_key
    config['default_states']['obtainer_category'] = config['default_states']['obtainer_category'].upper()
    config['default_states']['obtainer_tavily_api_key'] = tavily_api_key if tavily_api_key else ''
    config['default_states']['obtainer_kaggle_username'] = kaggle_username
    config['default_states']['obtainer_kaggle_key'] = kaggle_key

    if 'reset' in config['rag']:
        config['default_states']['obtainer_reset_rag'] = config['rag']['reset']
    if 'embed_model' in config['rag']:
        embed_model = config['rag']['embed_model']
        if embed_model:  # Only set if not empty
            config['default_states']['obtainer_rag_embed_model'] = embed_model
    if 'collection_name' in config['rag']:
        config['default_states']['obtainer_rag_collection_name'] = config['rag']['collection_name']
    if 'api_base_url' in config['rag']:
        if config['rag']['api_base_url']:  # Only set if not empty
            config['default_states']['obtainer_rag_api_base_url'] = config['rag']['api_base_url']
    if rag_api_key:
        config['default_states']['obtainer_rag_api_key'] = rag_api_key

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
async def start_agent():
    if not manager:
        await init_manager()
    manager.start(default_state=default_states)
    return response_body(message="Agent started")

@router.post("/agent/input", operation_id='agentInput', summary="Send input to the agent")
async def agent_input(text: str):
    if not manager:
        return response_body(code=400, message="Starter manager not initialized")
    manager.send_input(text)
    return response_body(message="Input sent")


@router.post("/agent/stop", operation_id='stopAgent', summary="Stop the agent")
def stop_agent():
    if not manager:
        return response_body(code=400, message="Starter manager not initialized")
    manager.stop()
    return response_body(message="Agent stopped")


@router.get("/agent/status", operation_id='getAgentStatus', summary="Get the agent status")
def get_status():
    if not manager:
        return response_body(code=400, message="Starter manager not initialized")
    data = manager.poll_state()
    return response_body(message="Agent status", data=data)

@router.get("/agent/messages", operation_id='getAgentMessages', summary="Get the agent messages")
def get_messages():
    if not manager:
        return response_body(code=400, message="Starter manager not initialized")
    data = manager.poll_state()
    if "state" not in data:
        return response_body(code=400, message="No messages available")
    if "messages" not in data["state"]:
        return response_body(code=400, message="No messages available")
    messages = data["state"]["messages"]
    if "stream_message" in data:
        messages.append(data["stream_message"])
    return response_body(message="Agent messages", data=messages)

