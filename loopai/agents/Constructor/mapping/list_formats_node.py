"""
List Formats Node - 列表节点 (非LLM)

功能:
1. 直接调用 list_preset_formats() tool
2. 格式化输出所有可用格式的详细信息
3. 返回到 inquiry_node 继续等待用户选择
"""
import json
from typing import Dict, Any

from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from loopai.agents.Constructor.tools.format_mapping_tools import PRESET_FORMATS

logger = get_logger()


def list_formats_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    列表节点 - 显示所有格式的详细信息
    
    这是一个非LLM节点，直接调用 tools 获取格式列表
    
    Args:
        state: 当前状态
        store: LangGraph store
    
    Returns:
        更新后的状态，设置为返回 inquiry_node
    """
    logger.info("=== List Formats Node: Starting ===")
    
    # 构建详细格式列表
    format_details = []
    
    for format_id, format_info in PRESET_FORMATS.items():
        format_details.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        format_details.append(f"📋 {format_id}: {format_info['name']}")
        format_details.append(f"   描述: {format_info['description']}")
        format_details.append(f"   ")
        format_details.append(f"   Schema结构:")
        schema_str = json.dumps(format_info['schema'], ensure_ascii=False, indent=6)
        for line in schema_str.split('\n'):
            format_details.append(f"      {line}")
        format_details.append(f"   ")
        format_details.append(f"   示例数据:")
        example_str = json.dumps(format_info['example'], ensure_ascii=False, indent=6)
        for line in example_str.split('\n'):
            format_details.append(f"      {line}")
        format_details.append("")
    
    format_details.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    message_content = f"""以下是所有可用格式的详细信息：

{chr(10).join(format_details)}

请输入您想使用的格式ID（如 alpaca, chatml, jsonl_pt 等），
或描述您需要的自定义格式。"""
    
    # 添加 AI 消息
    if "messages" not in state:
        state["messages"] = []
    state["messages"].append({
        "type": "ai",
        "role": "assistant",
        "content": message_content,
    })
    
    # 确保 constructor 字典存在
    if "constructor" not in state:
        state["constructor"] = {}
    
    # 重置意图，让 inquiry_node 重新等待用户输入
    state["constructor"]["mapping_user_intent"] = ""
    
    # 保存到 store
    _save_to_store(state, store)
    
    logger.info("=== List Formats Node: Completed ===")
    return state


def _save_to_store(state: LoopAIState, store: BaseStore):
    """保存操作记录到 store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        data = {
            "event_type": "list_formats",
            "timestamp": datetime.datetime.now().isoformat(),
            "formats_count": len(PRESET_FORMATS)
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "list_formats_event", data)
        logger.debug(f"Saved list_formats event to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")

