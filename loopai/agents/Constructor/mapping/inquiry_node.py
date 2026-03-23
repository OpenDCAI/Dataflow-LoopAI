"""
Inquiry Node

Interacts with user to determine desired output format
Uses LLM to analyze user input and determine intent
"""
import json
from typing import Dict, Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from loopai.agents.Constructor.tools.format_mapping_tools import PRESET_FORMATS
from .__mapping_prompts import get_prompt

logger = get_logger()


def inquiry_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    Inquiry node - Ask user what format they need, use LLM to analyze intent
    """
    logger.info("=== Inquiry Node: Starting ===")
    
    intermediate_path = state.get("constructor", {}).get("intermediate_data_path", "")
    if not intermediate_path:
        logger.warning("No intermediate data path found")
        state["exception"] = "Intermediate data path not found"
        return state
    
    import os
    if not os.path.exists(intermediate_path):
        logger.warning(f"Intermediate data path does not exist: {intermediate_path}")
        state["exception"] = f"Intermediate data path does not exist: {intermediate_path}"
        return state
    
    welcome_message = _build_welcome_message()
    
    if "messages" not in state:
        state["messages"] = []
    state["messages"].append({
        "type": "ai",
        "role": "assistant",
        "content": welcome_message,
    })
    
    logger.info("Showing welcome message and waiting for user input...")
    user_input = interrupt(welcome_message)
    
    logger.info(f"User input received: {user_input}")
    
    if user_input:
        state["messages"].append({
            "type": "human",
            "role": "user",
            "content": str(user_input),
        })
    
    return _analyze_user_intent(state, str(user_input) if user_input else "", store)


def _build_welcome_message() -> str:
    """Build welcome message"""
    format_list = []
    for format_id, format_info in PRESET_FORMATS.items():
        format_list.append(f"  • {format_id}: {format_info['name']}")
        format_list.append(f"    {format_info['description']}")
    
    welcome_message = f"""Data has been processed to intermediate format, now need to convert to target format.

📋 Available preset formats:
{chr(10).join(format_list)}

Please select the format you need:
  • Enter format ID (e.g., alpaca, chatml, jsonl_pt, etc.) to select preset format
  • Enter 'list' to view detailed information and examples of all formats
  • Or describe your custom format (e.g., I need a format with question and answer fields)"""
    
    return welcome_message


def _analyze_user_intent(state: LoopAIState, user_input: str, store: BaseStore = None) -> LoopAIState:
    """Analyze user intent"""
    logger.info(f"Analyzing user intent: {user_input}")
    
    # 确保 constructor 字典存在
    if "constructor" not in state:
        state["constructor"] = {}
    
    if not user_input or not user_input.strip():
        state["constructor"]["mapping_user_intent"] = "unclear"
        logger.info("Empty input, intent: unclear")
        return state
    
    user_input_lower = user_input.lower().strip()
    
    if user_input_lower == "list":
        state["constructor"]["mapping_user_intent"] = "list_formats"
        logger.info("Quick match: list_formats")
        _save_to_store(state, store)
        return state
    
    if user_input_lower in PRESET_FORMATS:
        state["constructor"]["mapping_user_intent"] = "preset_format"
        state["constructor"]["mapping_selected_format_id"] = user_input_lower
        logger.info(f"Quick match: preset_format ({user_input_lower})")
        _save_to_store(state, store)
        return state
    
    for format_id in PRESET_FORMATS.keys():
        if format_id in user_input_lower:
            state["constructor"]["mapping_user_intent"] = "preset_format"
            state["constructor"]["mapping_selected_format_id"] = format_id
            logger.info(f"Found preset format in input: {format_id}")
            _save_to_store(state, store)
            return state
    
    constructor = state.get("constructor", {})
    model_name = constructor.get("model_path")
    base_url = constructor.get("base_url")
    api_key = constructor.get("api_key")
    
    if not model_name or not base_url or not api_key:
        logger.warning("No LLM config, using simple rules")
        return _simple_intent_analysis(state, user_input, store)
    
    try:
        llm = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            temperature=0.0
        )
        
        preset_format_ids = list(PRESET_FORMATS.keys())
        
        try:
            system_prompt_template = get_prompt("system", "format_inquiry_intent_analyzer_prompt")
            system_prompt = f"""{system_prompt_template}

Available preset format IDs: {preset_format_ids}"""
        except Exception as e:
            logger.warning(f"Failed to load prompt, using default: {e}")
            system_prompt = "You are an intent analysis assistant. Analyze user input and determine intent."
        
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"User input: {user_input}")
        ])
        
        result = _parse_llm_response(response.content)
        
        if result:
            action = result.get("action", "custom_format")
            format_id = result.get("format_id", "")
            custom_desc = result.get("custom_description", "")
            
            state["constructor"]["mapping_user_intent"] = action
            
            if action == "preset_format" and format_id:
                state["constructor"]["mapping_selected_format_id"] = format_id.lower()
            elif action == "custom_format":
                state["constructor"]["mapping_custom_description"] = custom_desc or user_input
            
            logger.info(f"LLM analysis result: action={action}, format_id={format_id}")
        else:
            state["constructor"]["mapping_user_intent"] = "custom_format"
            state["constructor"]["mapping_custom_description"] = user_input
            logger.info("LLM response parse failed, defaulting to custom_format")
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        state["constructor"]["mapping_user_intent"] = "custom_format"
        state["constructor"]["mapping_custom_description"] = user_input
    
    _save_to_store(state, store)
    logger.info("=== Inquiry Node: Completed ===")
    return state


def _simple_intent_analysis(state: LoopAIState, user_input: str, store: BaseStore = None) -> LoopAIState:
    """Simple intent analysis (fallback when no LLM)"""
    logger.info("Using simple intent analysis")
    
    # 确保 constructor 字典存在
    if "constructor" not in state:
        state["constructor"] = {}
    
    user_input_lower = user_input.lower().strip()
    
    list_keywords = ["list", "view", "show", "available", "options"]
    if any(kw in user_input_lower for kw in list_keywords):
        state["constructor"]["mapping_user_intent"] = "list_formats"
        logger.info("Simple match: list_formats")
        _save_to_store(state, store)
        return state
    
    state["constructor"]["mapping_user_intent"] = "custom_format"
    state["constructor"]["mapping_custom_description"] = user_input
    logger.info(f"Simple match: custom_format")
    _save_to_store(state, store)
    return state


def _parse_llm_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM response"""
    import re
    
    try:
        return json.loads(response_text.strip())
    except:
        pass
    
    pattern = r'```(?:json)?\s*([\s\S]*?)```'
    match = re.search(pattern, response_text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except:
            pass
    
    start = response_text.find('{')
    end = response_text.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(response_text[start:end+1])
        except:
            pass
    
    return None


def _save_to_store(state: LoopAIState, store: BaseStore):
    """Save to store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        constructor_state = state.get("constructor", {})
        data = {
            "event_type": "inquiry_completed",
            "timestamp": datetime.datetime.now().isoformat(),
            "user_intent": constructor_state.get("mapping_user_intent", ""),
            "selected_format_id": constructor_state.get("mapping_selected_format_id", ""),
            "custom_description": constructor_state.get("mapping_custom_description", "")[:200]
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "inquiry_event", data)
        logger.debug("Saved inquiry event to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")
