import os
import json
import uuid
from fastapi import APIRouter
from omegaconf import OmegaConf
from tortoise.expressions import Q
from ..models.body import response_body, ConfigModel
from ..models.db_models import StarterConfig
from ..utils.config.config import get_system_config, get_state_config, format_value

router = APIRouter(tags=["config"])

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))

@router.get("/config", operation_id='getConfig', summary="获取Starter配置")
async def get_config():
    """获取配置"""
    system_config = await get_system_config(BASE_DIR)
    state_config = await get_state_config(BASE_DIR)
    res = {
        'id': system_config['id'],
        'name': system_config['name'],
        'system': system_config['config'],
        'states': state_config['config'],
    }
    return response_body(data=res)()

@router.post("/config", operation_id='updateConfig', summary="更新Starter配置")
async def update_config(config: ConfigModel):
    """更新配置"""
    try:
        config_obj = json.loads(config.config)
    except:
        return response_body(code=400, status='error', message='config格式错误')()
    
    original_config = await StarterConfig.filter(Q(id=config.id)).first()
    original_config_obj = json.loads(original_config.config)
    if not original_config:
        return response_body(code=400, status='error', message='config不存在')()
    system_config = config_obj.get('system', {})
    states_config = config_obj.get('states', {})
    for series_key in system_config:
        for key in system_config[series_key]:
            format_item = format_value(system_config[series_key][key])
            original_config_obj.setdefault(series_key, {})[key] = format_item["value"]
    for series_key in states_config:
        if series_key == 'default':
            for key in states_config[series_key]:
                format_item = format_value(states_config[series_key][key])
                original_config_obj.setdefault('default_states', {})[key] = format_item["value"]
        else:
            for key in states_config[series_key]:
                format_item = format_value(states_config[series_key][key])
                original_config_obj.setdefault('default_states', {}).setdefault(series_key, {})[key] = format_item["value"]
    original_config.config = json.dumps(original_config_obj)
    await original_config.save()
    return response_body(data={
        'id': original_config.id,
        'name': original_config.name,
        'config': original_config_obj,
    })()


@router.get("/list_dir", operation_id='listDir', summary="列出目录下的文件, 且判定是否为文件夹")
async def list_dir(path: str):
    """列出目录下的文件, 且判定是否为文件夹"""
    if not os.path.exists(path):
        return response_body(code=400, status='error', message='目录不存在')()
    files = os.listdir(path)
    res = []
    for file in files:
        res.append({
            'name': file,
            'is_dir': os.path.isdir(os.path.join(path, file)),
        })
    res = sorted(
        res,
        key=lambda x: (not x['is_dir'], x['name'])
    )
    return response_body(data=res)()
