"""
Format mapping tools for data post-processing.
These tools allow users to select or customize output formats for their data.
"""
import json
from typing import Dict, Any, List, Optional


# 预设格式定义
PRESET_FORMATS = {
    "alpaca": {
        "name": "Alpaca格式",
        "description": "Alpaca微调格式，包含instruction、input、output字段",
        "schema": {
            "instruction": "string",
            "input": "string (optional)",
            "output": "string"
        },
        "example": {
            "instruction": "请回答以下问题",
            "input": "什么是人工智能？",
            "output": "人工智能是..."
        }
    },
    "chatml": {
        "name": "ChatML格式",
        "description": "OpenAI ChatML格式，用于对话微调",
        "schema": {
            "messages": [
                {
                    "role": "system|user|assistant",
                    "content": "string"
                }
            ]
        },
        "example": {
            "messages": [
                {"role": "system", "content": "你是一个有用的助手"},
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮助你的吗？"}
            ]
        }
    },
    "jsonl_pt": {
        "name": "JSONL预训练格式",
        "description": "简单的JSONL格式，每行一个文本记录，用于预训练",
        "schema": {
            "text": "string"
        },
        "example": {
            "text": "这是一段用于预训练的文本内容..."
        }
    },
    "jsonl_sft": {
        "name": "JSONL微调格式",
        "description": "JSONL格式，包含对话或问答对，用于微调",
        "schema": {
            "conversation": [
                {
                    "role": "user|assistant",
                    "content": "string"
                }
            ]
        },
        "example": {
            "conversation": [
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "回答"}
            ]
        }
    },
    "openai_fine_tune": {
        "name": "OpenAI微调格式",
        "description": "OpenAI官方微调格式",
        "schema": {
            "messages": [
                {
                    "role": "system|user|assistant",
                    "content": "string"
                }
            ]
        },
        "example": {
            "messages": [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "用户消息"},
                {"role": "assistant", "content": "助手回复"}
            ]
        }
    },
    "llama2_chat": {
        "name": "Llama2对话格式",
        "description": "Meta Llama2对话格式",
        "schema": {
            "text": "string (包含特殊标记如 <s>[INST] ... [/INST] ... </s>)"
        },
        "example": {
            "text": "<s>[INST] 用户问题 [/INST] 助手回答 </s>"
        }
    }
}


def list_preset_formats() -> str:
    """
    列出所有预设的数据格式选项。
    
    Returns:
        格式化的字符串，包含所有预设格式的列表和描述
    """
    result = ["可用的预设格式：\n"]
    
    for format_id, format_info in PRESET_FORMATS.items():
        result.append(f"  {format_id}: {format_info['name']}")
        result.append(f"    描述: {format_info['description']}")
        result.append(f"    示例: {json.dumps(format_info['example'], ensure_ascii=False, indent=2)}")
        result.append("")
    
    return "\n".join(result)


def select_format(format_id: str) -> str:
    """
    选择预设格式。
    
    Args:
        format_id: 格式ID（如 'alpaca', 'chatml', 'jsonl_pt' 等）
    
    Returns:
        JSON字符串，包含格式信息（包括schema和example）
    """
    if format_id not in PRESET_FORMATS:
        available = ", ".join(PRESET_FORMATS.keys())
        result = {
            "success": False,
            "error": f"格式 '{format_id}' 不存在。可用格式: {available}",
            "available_formats": list(PRESET_FORMATS.keys())
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    format_info = PRESET_FORMATS[format_id]
    result = {
        "success": True,
        "format_id": format_id,
        "format_name": format_info["name"],
        "description": format_info["description"],
        "schema": format_info["schema"],
        "example": format_info["example"]
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def customize_format(description: str, sample_data: Optional[Dict[str, Any]] = None) -> str:
    """
    请求生成自定义格式的schema。
    这个工具会返回一个建议的格式结构，需要用户确认。
    
    Args:
        description: 用户对自定义格式的描述
        sample_data: 可选的示例数据，用于帮助生成schema
    
    Returns:
        JSON字符串，包含待确认的格式请求信息
    """
    # 这个函数返回一个待确认的格式结构
    # 实际的schema生成会在mapping_node中通过LLM完成
    result = {
        "status": "pending_confirmation",
        "description": description,
        "sample_data": sample_data,
        "message": "已记录您的自定义格式需求。系统将生成格式结构供您确认。"
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def confirm_format(format_config: Dict[str, Any]) -> str:
    """
    确认使用指定的格式配置。
    
    Args:
        format_config: 格式配置字典，包含schema和example
    
    Returns:
        JSON字符串，包含确认结果
    """
    if not isinstance(format_config, dict):
        result = {
            "success": False,
            "error": "格式配置必须是字典类型"
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    required_keys = ["schema"]
    if not all(key in format_config for key in required_keys):
        result = {
            "success": False,
            "error": f"格式配置缺少必需字段: {required_keys}",
            "provided_keys": list(format_config.keys())
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    result = {
        "success": True,
        "message": "格式已确认，将用于数据映射",
        "format_config": format_config
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def get_format_schema(format_id: Optional[str] = None, format_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    获取格式的schema定义。
    
    Args:
        format_id: 预设格式ID（如果使用预设格式）
        format_config: 自定义格式配置（如果使用自定义格式）
    
    Returns:
        格式schema字典
    """
    if format_id:
        if format_id in PRESET_FORMATS:
            return {
                "success": True,
                "schema": PRESET_FORMATS[format_id]["schema"],
                "example": PRESET_FORMATS[format_id]["example"]
            }
        else:
            return {
                "success": False,
                "error": f"格式 '{format_id}' 不存在"
            }
    
    if format_config and "schema" in format_config:
        return {
            "success": True,
            "schema": format_config["schema"],
            "example": format_config.get("example", {})
        }
    
    return {
        "success": False,
        "error": "必须提供format_id或format_config"
    }

