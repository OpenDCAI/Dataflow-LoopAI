"""
Preset Format Node - 预设格式节点 (非LLM)

功能:
1. 调用 select_format(format_id) tool
2. 获取格式的 schema 和 example
3. 存储到 obtainer_pending_format
4. 生成确认消息
"""
import json
from typing import Dict, Any

from langchain_core.messages import AIMessage
from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from loopai.agents.Constructor.tools.format_mapping_tools import PRESET_FORMATS, select_format

logger = get_logger()


def preset_format_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    预设格式节点 - 处理用户选择的预设格式
    
    这是一个非LLM节点，直接调用 tools 获取格式信息
    
    Args:
        state: 当前状态
        store: LangGraph store
    
    Returns:
        更新后的状态，包含 pending_format
    """
    logger.info("=== Preset Format Node: Starting ===")
    
    # 确保 obtainer 字典存在
    if "obtainer" not in state:
        state["obtainer"] = {}
    
    obtainer_state = state.get("obtainer", {})
    
    # 获取用户选择的格式ID
    format_id = obtainer_state.get("mapping_selected_format_id", "")
    
    if not format_id:
        logger.error("No format_id found in state")
        state["exception"] = "未找到格式ID"
        return state
    
    # 调用 select_format tool
    result_str = select_format(format_id)
    result = json.loads(result_str)
    
    if not result.get("success"):
        logger.error(f"Format selection failed: {result.get('error')}")
        error_msg = result.get("error", "格式选择失败")
        available = result.get("available_formats", [])
        
        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(AIMessage(
            content=f"错误: {error_msg}\n\n可用格式: {', '.join(available)}\n\n请重新选择格式。"
        ))
        
        # 重置意图，返回 inquiry
        state["obtainer"]["mapping_user_intent"] = ""
        state["obtainer"]["mapping_selected_format_id"] = ""
        return state
    
    # 存储 pending format
    pending_format = {
        "format_id": format_id,
        "format_name": result.get("format_name", ""),
        "description": result.get("description", ""),
        "schema": result.get("schema", {}),
        "example": result.get("example", {}),
        "is_preset": True  # 标记为预设格式
    }
    state["obtainer"]["pending_format"] = pending_format
    
    logger.info(f"Selected preset format: {format_id}")
    
    # 保存到 store
    _save_to_store(state, store, pending_format)
    
    logger.info("=== Preset Format Node: Completed ===")
    return state


def _save_to_store(state: LoopAIState, store: BaseStore, pending_format: Dict[str, Any]):
    """保存操作记录到 store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        data = {
            "event_type": "preset_format_selected",
            "timestamp": datetime.datetime.now().isoformat(),
            "format_id": pending_format.get("format_id"),
            "format_name": pending_format.get("format_name")
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "preset_format_event", data)
        logger.debug(f"Saved preset_format event to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")

