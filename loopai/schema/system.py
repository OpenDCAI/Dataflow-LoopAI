from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from loopai.common.i18n.i18n_loader import I18NLoader


def _default_model_config() -> dict[str, Any]:
    return {
        "proxy_base_url": "",
        "proxy_api_key": "",
        "default_model": "starter",
        "codex_model": "codex",
        "looper_model": "starter",
        "default_tier": "medium",
        "pool": [
            {
                "tier": "medium",
                "name": "starter",
                "api_key": "",
                "base_url": "https://api.deepseek.com",
                "model_name": "deepseek-v4-flash",
                "maxworker": 1,
                "wire_api": "chat",
                "response_format": "",
                "enabled": True,
            },
            {
                "tier": "medium",
                "name": "codex",
                "api_key": "",
                "base_url": "https://api.deepseek.com",
                "model_name": "deepseek-v4-flash",
                "maxworker": 1,
                "wire_api": "chat",
                "response_format": "",
                "enabled": True,
            },
        ],
    }


class SystemConfig(BaseModel):
    api_port: int = Field(
        default=8855,
        title="API端口",
        description="系统 API 服务监听端口",
        json_schema_extra={"ui_type": "number", "ui_group": "基础配置"},
    )
    tavily_api_key: str = Field(
        default="",
        title="Tavily API Key",
        description="Tavily 搜索服务的 API Key",
        json_schema_extra={"ui_type": "password", "ui_group": "基础配置"},
    )
    kaggle_username: str = Field(
        default="binrxu",
        title="Kaggle 用户名",
        description="Kaggle 账号用户名",
        json_schema_extra={"ui_type": "text", "ui_group": "基础配置"},
    )
    kaggle_key: str = Field(
        default="",
        title="Kaggle Key",
        description="Kaggle 账号访问密钥",
        json_schema_extra={"ui_type": "password", "ui_group": "基础配置"},
    )
    model: dict[str, Any] = Field(
        default_factory=_default_model_config,
        title="模型配置",
        description="完整的模型代理与模型池配置",
        json_schema_extra={"ui_type": "model_pool", "ui_group": "模型配置"},
    )
    codex_workspace: str = Field(
        default="/home/lpc/repos/Dataflow-LoopAI",
        title="Codex 工作区",
        description="Codex 执行时使用的项目工作目录",
        json_schema_extra={"ui_type": "file_path", "ui_group": "Codex配置"},
    )
    codex_home: str = Field(
        default="/home/lpc/repos/Dataflow-LoopAI/codex_home",
        title="Codex Home",
        description="Codex 运行时 HOME 目录",
        json_schema_extra={"ui_type": "file_path", "ui_group": "Codex配置"},
    )
    codex_run_timeout_ms: int = Field(
        default=300000,
        title="Codex 超时毫秒数",
        description="单次 Codex 执行的超时时间，单位毫秒",
        json_schema_extra={"ui_type": "number", "ui_group": "Codex配置"},
    )
    codex_use_project_config: bool = Field(
        default=False,
        title="启用项目配置",
        description="是否优先使用项目内的 Codex 配置",
        json_schema_extra={"ui_type": "switch", "ui_group": "Codex配置"},
    )


def get_system_config_schema(language: str = "zh") -> dict[str, Any]:
    i18n = I18NLoader(language)
    schema = SystemConfig.model_json_schema()
    properties = schema.get("properties", {})

    for field_info in properties.values():
        if "title" in field_info:
            field_info["title"] = i18n(field_info["title"])
        if "description" in field_info:
            field_info["description"] = i18n(field_info["description"])

    return properties
