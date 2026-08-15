import os
import json
from tortoise.expressions import Q
from ...models.db_models import StarterConfig
from omegaconf import OmegaConf
from loopai.common.tracking import strip_retired_tracking_fields
from loopai.schema.states import RETIRED_DATA_AGENT_STATE_SECTIONS, get_state_config_schema
from loopai.schema.system import get_system_config_schema
from loopai.schema.system_runtime import migrate_legacy_credentials

async def check_config_from_db(base_dir):
    # 判断sqliter中是否有config记录，如果一条也没有，读取./examples/starter.yaml转化为json然后存到数据库
    config = await StarterConfig.filter(Q(name='starter')).first()
    if not config:
        cfg = OmegaConf.load(os.path.join(base_dir, "starter.yaml"))
        config_obj = strip_retired_tracking_fields(
            OmegaConf.to_container(cfg, resolve=True)
        )
        await StarterConfig.create(name='starter', config=json.dumps(config_obj))
        config = await StarterConfig.filter(Q(name='starter')).first()
    if config and config.config:
        # Normalize legacy credentials and remove retired tracker fields for
        # both newly created and existing StarterConfig rows.
        try:
            original = json.loads(config.config)
            cleaned = strip_retired_tracking_fields(original)
            migrate_legacy_credentials(cleaned)
            if cleaned != original:
                config.config = json.dumps(cleaned, ensure_ascii=False)
                await config.save(update_fields=["config"])
        except Exception:
            # Preserve the existing validation/error behavior for malformed DB
            # rows; callers will surface the parse failure in the normal path.
            pass
    return config

def wrap_attr(val):
    type_name = 'str'
    if isinstance(val, dict):
        type_name = 'dict'
    elif isinstance(val, list):
        type_name = 'list'
    elif type(val) == int:
        type_name = 'int'
    elif type(val) == bool:
        type_name = 'bool'
    elif type(val) == float:
        type_name = 'float'
    elif val is None:
        type_name = 'none'
    return {
        'value': val,
        'default_value': val,
        'type': type_name,
    }

async def get_system_config(base_dir):
    """获取配置"""
    config = await check_config_from_db(base_dir)
    config_data = json.loads(config.config)
    system_config = strip_retired_tracking_fields(config_data.get('system', {}))
    states_data = strip_retired_tracking_fields(config_data.get('default_states', {}))
    language = states_data.get('language', 'zh') if isinstance(states_data, dict) else 'zh'
    system_schema = get_system_config_schema(language)
    result = {}
    for key in dict.fromkeys(list(system_config.keys()) + list(system_schema.keys())):
        schema_val = system_schema.get(key, {})
        if key in system_config:
            cur_val = wrap_attr(system_config[key])
        elif 'default' in schema_val:
            cur_val = wrap_attr(schema_val['default'])
        else:
            cur_val = {
                'value': None,
                'default_value': None,
                'type': 'none',
            }
        result[key] = {
            **schema_val,
            **cur_val,
        }
    res = {
        'id': config.id,
        'name': config.name,
        'config': result,
    }
    return res

async def get_state_config(base_dir):
    """获取Starter状态配置"""
    config = await check_config_from_db(base_dir)
    config_data = json.loads(config.config)
    states_data = strip_retired_tracking_fields(config_data.get('default_states', {}))
    language = states_data.get('language', 'zh')
    nested_states_schema = get_state_config_schema(language)
    default_schema = nested_states_schema.get('default', {})
    nested_keys = list(nested_states_schema.keys())
    nested_keys.remove('default')
    result = {}
    for series_key in dict.fromkeys(list(states_data.keys()) + list(default_schema.keys())):
        if series_key in RETIRED_DATA_AGENT_STATE_SECTIONS:
            continue
        if series_key not in nested_keys:
            key = series_key
            schema_val = default_schema.get(key, {})
            if key in states_data:
                cur_val = wrap_attr(states_data[key])
            elif "default" in schema_val:
                cur_val = wrap_attr(schema_val["default"])
            else:
                cur_val = {
                    'value': None,
                    'default_value': None,
                    'type': 'none',
                }
            result.setdefault('default', {})[key] = {
                **schema_val,
                **cur_val,
            }
    for series_key in nested_keys:
        result.setdefault(series_key, {})
        for key in nested_states_schema.get(series_key, {}):
            schema_val = nested_states_schema[series_key].get(key, {})
            if key in states_data.get(series_key, {}):
                cur_val = wrap_attr(states_data.get(series_key, {})[key])
            elif "default" in schema_val:
                # 与 Pydantic model_json_schema 对齐：缺失键时用字段默认值，避免前端把 value=null 渲染成不可编辑的 None
                cur_val = wrap_attr(schema_val["default"])
            else:
                cur_val = {
                    'value': None,
                    'default_value': None,
                    'type': 'none',
                }
            result[series_key][key] = {
                **schema_val,
                **cur_val,
            }
                
    res = {
        'id': config.id,
        'name': config.name,
        'config': result,
    }
    return res

def format_value(item):
    type_name = item.get('type', 'str')
    # Use 'value' if present, otherwise fallback to 'default'
    value = item.get('value')
    if value is None:
        value = item.get('default')
    if value is None:
        item['value'] = None
        return item
    if type_name in {'dict', 'list', 'json'} or isinstance(value, (dict, list)):
        item['value'] = value
        return item
    item['value'] = value
    if type_name == 'bool':
        item['value'] = bool(item['value'])
    else:
        if type(item['value']) == int or type(item['value']) == float:
            return item
        if type_name == 'int':
            try:
                item['value'] = int(item['value'])
            except:
                item['value'] = float(item['value'])
        elif type_name == 'float':
            item['value'] = float(item['value'])
        else:
            item['value'] = str(item['value'])
    return item
