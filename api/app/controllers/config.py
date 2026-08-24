from fastapi import APIRouter
from ..models.body import response_body, ConfigModel
from ..services.config import (
    ConfigServiceError,
    get_starter_config,
    list_directory,
    update_starter_config,
)
from loopai.schema.states import get_state_config_schema

router = APIRouter(tags=["config"])


@router.get("/config", operation_id='getConfig', summary="获取Starter配置")
async def get_config():
    """获取配置"""
    return response_body(data=await get_starter_config())()


@router.post("/config", operation_id='updateConfig', summary="更新Starter配置")
async def update_config(config: ConfigModel):
    """更新配置"""
    try:
        return response_body(data=await update_starter_config(config))()
    except ConfigServiceError as exc:
        return response_body(code=exc.code, status='error', message=exc.message)()


@router.get("/state_schema", operation_id='getStateSchema', summary="获取State的配置Schema")
async def get_state_schema(language: str="zh"):
    """获取State的配置Schema"""
    schema = get_state_config_schema(language)
    return response_body(data=schema)()


@router.get("/list_dir", operation_id='listDir', summary="列出目录下的文件, 且判定是否为文件夹")
async def list_dir(path: str):
    """列出目录下的文件, 且判定是否为文件夹"""
    try:
        return response_body(data=list_directory(path))()
    except ConfigServiceError as exc:
        return response_body(code=exc.code, status='error', message=exc.message)()
