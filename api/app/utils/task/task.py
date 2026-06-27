import json
from ...models.db_models import TaskModel
from ...utils.config.config import format_value, wrap_attr
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
    for key in system_config:
        format_item = format_value(system_config[key])
        result.setdefault('system', {})[key] = format_item["value"]
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


def build_task_state_config(state_data: dict, language: str | None = None):
    language = language or state_data.get("language", "zh")
    nested_states_schema = get_state_config_schema(language)
    default_schema = nested_states_schema.get("default", {})
    nested_keys = list(nested_states_schema.keys())
    nested_keys.remove("default")

    result = {}
    for series_key in dict.fromkeys(list(state_data.keys()) + list(default_schema.keys())):
        if series_key in nested_keys:
            continue
        schema_val = default_schema.get(series_key, {})
        if series_key in state_data:
            cur_val = wrap_attr(state_data[series_key])
        elif "default" in schema_val:
            cur_val = wrap_attr(schema_val["default"])
        else:
            cur_val = {
                "value": None,
                "default_value": None,
                "type": "none",
            }
        result.setdefault("default", {})[series_key] = {
            **schema_val,
            **cur_val,
        }

    for series_key in nested_keys:
        result.setdefault(series_key, {})
        section_state = state_data.get(series_key, {})
        if not isinstance(section_state, dict):
            section_state = {}
        for key in nested_states_schema.get(series_key, {}):
            schema_val = nested_states_schema[series_key].get(key, {})
            if key in section_state:
                cur_val = wrap_attr(section_state[key])
            elif "default" in schema_val:
                cur_val = wrap_attr(schema_val["default"])
            else:
                cur_val = {
                    "value": None,
                    "default_value": None,
                    "type": "none",
                }
            result[series_key][key] = {
                **schema_val,
                **cur_val,
            }
    return result


def apply_state_config_updates(state: dict, states_config: dict):
    for series_key, series_value in states_config.items():
        if not isinstance(series_value, dict):
            continue
        if series_key == "default":
            for key, value in series_value.items():
                format_item = format_value(dict(value) if isinstance(value, dict) else {"value": value})
                state[key] = format_item["value"]
            continue

        nested_state = state.setdefault(series_key, {})
        if not isinstance(nested_state, dict):
            nested_state = {}
            state[series_key] = nested_state
        for key, value in series_value.items():
            format_item = format_value(dict(value) if isinstance(value, dict) else {"value": value})
            nested_state[key] = format_item["value"]
