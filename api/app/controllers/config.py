import os
import json
import uuid
from fastapi import APIRouter
from omegaconf import OmegaConf
from tortoise.expressions import Q
from ..models.body import response_body, ConfigModel
from ..models.db_models import StarterConfig

router = APIRouter(tags=["config"])

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))

@router.get("/config", operation_id='getConfig', summary="获取Starter配置")
async def get_config():
    """获取配置"""
    # 判断sqliter中是否有config记录，如果一条也没有，读取./examples/starter.yaml转化为json然后存到数据库
    config = await StarterConfig.filter(Q(name='starter')).first()
    if not config:
        cfg = OmegaConf.load(os.path.join(BASE_DIR, "examples", "starter.yaml"))
        config_obj = OmegaConf.to_container(cfg, resolve=True)
        await StarterConfig.create(name='starter', config=json.dumps(config_obj))
        config = await StarterConfig.filter(Q(name='starter')).first()
    config_data = json.loads(config.config)
    for series_key in config_data:
        for key in config_data[series_key]:
            type_name = 'str'
            if type(config_data[series_key][key]) == int:
                type_name = 'int'
            elif type(config_data[series_key][key]) == bool:
                type_name = 'bool'
            elif type(config_data[series_key][key]) == float:
                type_name = 'float'
            elif config_data[series_key][key] is None:
                type_name = 'none'
            config_data[series_key][key] = {
                'value': config_data[series_key][key],
                'default_value': config_data[series_key][key],
                'type': type_name,
            }
    res = {
        'id': config.id,
        'name': config.name,
        'config': config_data,
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
    for series_key in config_obj:
        for key in config_obj[series_key]:
            if key not in original_config_obj[series_key]:
                continue
            type_name = config_obj[series_key][key].get('type', 'str')
            if type_name == 'int':
                try:
                    config_obj[series_key][key]['value'] = int(config_obj[series_key][key]['value'])
                except:
                    config_obj[series_key][key]['value'] = float(config_obj[series_key][key]['value'])
            elif type_name == 'bool':
                config_obj[series_key][key]['value'] = bool(config_obj[series_key][key]['value'])
            elif type_name == 'float':
                config_obj[series_key][key]['value'] = float(config_obj[series_key][key]['value'])
            else:
                config_obj[series_key][key]['value'] = str(config_obj[series_key][key]['value'])
            original_config_obj[series_key][key] = config_obj[series_key][key]['value']
    original_config.config = json.dumps(original_config_obj)
    await original_config.save()
    return response_body(data={
        'id': original_config.id,
        'name': original_config.name,
        'config': original_config_obj,
    })()
