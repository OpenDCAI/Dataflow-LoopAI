import json
from ...models.db_models import TaskModel
from ...utils.config.config import format_value
from langchain_core.messages import BaseMessage
from loopai.schema.states import get_state_config_schema
from copy import deepcopy

def config_format(config: dict, task_id: str):
    """
    config转化, 传入的config包含{"system": {...}, "states": {...}}
    对齐starter中的config格式
    """
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
    result.setdefault('default_states', {})['task_id'] = task_id
    return result

async def update_task_state(task_id: str, state: dict):
    task = await TaskModel.get_or_none(task_id=task_id)
    if not task:
        return False
    def decode_msg(msg):
        if isinstance(msg, BaseMessage):
            return msg.model_dump()
        return msg
    if not state:
        state = {}
    state = deepcopy(state)
    messages = [decode_msg(item) for item in state.get("messages", [])]
    state["messages"] = messages
    task.state = json.dumps(state)
    await task.save()
    return True

async def get_task_state(task_id: str, default_state: dict):
    task = await TaskModel.get_or_none(task_id=task_id)
    state = {}
    if not task or not task.state:
        return default_state
    try:
        state = json.loads(task.state)
    except:
        state = {}
    nested_states_schema = get_state_config_schema()
    nested_keys = nested_states_schema.keys()
    for series_key in default_state:
        if series_key not in nested_keys:
            if series_key not in state:
                state[series_key] = default_state[series_key]
        else:
            for key in default_state[series_key]:
                if key not in state.get(series_key, {}):
                    state.setdefault(series_key, {})[key] = default_state[series_key][key]
    return state
