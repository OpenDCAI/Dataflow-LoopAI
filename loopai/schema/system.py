from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from loopai.common.i18n.i18n_loader import I18NLoader


def _default_embedding_config() -> dict[str, Any]:
    """Embedding 服务配置（与 .datamixer/lake.yaml 的 embedding_* 字段对应）。"""
    return {
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "",
        "model": "BAAI/bge-small-zh-v1.5",
        "backend": "local-jsonl",
        "text_field": "text",
    }


def _default_mineru_config() -> dict[str, Any]:
    """MinerU-HTML（Dripper）文档解析服务配置。"""
    return {
        "url": "http://127.0.0.1:7986",
        "python": "",
        "model": "",
        "gpu": "0",
        "transport": "http",
        "backend": "vllm",
    }


def _default_model_config() -> dict[str, Any]:
    return {
        "proxy_base_url": "",
        "proxy_api_key": "",
        "default_model": "starter",
        "codex_model": "codex",
        "looper_model": "starter",
        "default_tier": "medium",
        "embedding": _default_embedding_config(),
        "mineru": _default_mineru_config(),
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


def _default_integrations_config() -> dict[str, Any]:
    return {
        "tavily": {"api_key": "env:TAVILY_API_KEY"},
        "kaggle": {
            "username": "env:KAGGLE_USERNAME",
            "key": "env:KAGGLE_KEY",
        },
        "rag": {"base_url": "", "api_key": "env:RAG_API_KEY"},
    }


class SystemConfig(BaseModel):
    api_port: int = Field(
        default=8855,
        title="API端口",
        description="系统 API 服务监听端口",
        json_schema_extra={"ui_type": "number", "ui_group": "基础配置"},
    )
    model: dict[str, Any] = Field(
        default_factory=_default_model_config,
        title="模型配置",
        description="完整的模型代理与模型池配置",
        json_schema_extra={"ui_type": "model_pool", "ui_group": "模型配置"},
    )
    integrations: dict[str, Any] = Field(
        default_factory=_default_integrations_config,
        title="外部服务配置",
        description="Tavily、Kaggle 和 RAG 等外部服务的运行时凭据",
        json_schema_extra={"ui_group": "集成配置"},
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
