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
        cfg = OmegaConf.load(os.path.join(base_dir, "examples", "starter.yaml"))
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
    if 'default_states' in config_data:
        del config_data['default_states']
    for series_key in config_data:
        for key in config_data[series_key]:
            config_data[series_key][key] = wrap_attr(config_data[series_key][key])
    res = {
        'id': config.id,
        'name': config.name,
        'config': config_data,
    }
    return res

async def get_state_config(base_dir):
    """获取Starter状态配置"""
    config = await check_config_from_db(base_dir)
    config_data = json.loads(config.config)
    states_data = config_data.get('default_states', {})
    nested_states_schema = get_state_config_schema()
    nested_keys = nested_states_schema.keys()
    result = {}
    for series_key in states_data:
        if series_key not in nested_keys:
            result.setdefault('default', {})[series_key] = wrap_attr(states_data[series_key])
    for series_key in nested_keys:
        result.setdefault(series_key, {})
        for key in nested_states_schema.get(series_key, {}):
            if key in states_data.get(series_key, {}):
                cur_val = wrap_attr(states_data.get(series_key, {})[key])
            else:
                cur_val = {
                    'value': None,
                    'default_value': None,
                }
            schema_val = nested_states_schema[series_key].get(key, {})
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
