import os
import json
from tortoise.expressions import Q
from ...models.db_models import StarterConfig
from omegaconf import OmegaConf
from loopai.schema.states import get_state_config_schema

async def check_config_from_db(base_dir):
    # 判断sqliter中是否有config记录，如果一条也没有，读取./examples/starter.yaml转化为json然后存到数据库
    config = await StarterConfig.filter(Q(name='starter')).first()
    if not config:
        cfg = OmegaConf.load(os.path.join(base_dir, "starter.yaml"))
        config_obj = OmegaConf.to_container(cfg, resolve=True)
        await StarterConfig.create(name='starter', config=json.dumps(config_obj))
        config = await StarterConfig.filter(Q(name='starter')).first()
    return config

def wrap_attr(val):
    type_name = 'str'
    if type(val) == int:
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
    system_config = config_data.get('system', {})
    for key in system_config:
        system_config[key] = wrap_attr(system_config[key])
    res = {
        'id': config.id,
        'name': config.name,
        'config': system_config,
    }
    return res

async def get_state_config(base_dir):
    """获取Starter状态配置"""
    config = await check_config_from_db(base_dir)
    config_data = json.loads(config.config)
    states_data = config_data.get('default_states', {})
    language = states_data.get('language', 'zh')
    nested_states_schema = get_state_config_schema(language)
    default_schema = nested_states_schema.get('default', {})
    nested_keys = list(nested_states_schema.keys())
    nested_keys.remove('default')
    result = {}
    for series_key in dict.fromkeys(list(states_data.keys()) + list(default_schema.keys())):
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
