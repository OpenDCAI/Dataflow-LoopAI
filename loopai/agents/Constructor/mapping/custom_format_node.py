"""
Custom Format Node (LLM)

Generates or modifies custom format schema using LLM
Supports two modes:
1. Create new format from scratch
2. Modify existing format based on user description
"""
import json
import re
from typing import Dict, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from .__mapping_prompts import get_prompt

logger = get_logger()


def custom_format_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    Custom format node - Generate or modify custom format schema using LLM
    """
    logger.info("=== Custom Format Node: Starting ===")
    
    # 确保 obtainer 字典存在
    if "obtainer" not in state:
        state["obtainer"] = {}
    
    obtainer_state = state.get("obtainer", {})
    description = obtainer_state.get("mapping_custom_description", "")
    
    previous_format = obtainer_state.get("confirmed_format") or obtainer_state.get("pending_format")
    is_modification = previous_format is not None and description
    
    if is_modification:
        logger.info(f"Modification mode: modifying existing format based on '{description}'")
    
    if not description:
        logger.info("No custom format description, asking user")
        
        if previous_format:
            previous_schema = json.dumps(previous_format.get("schema", {}), ensure_ascii=False, indent=2)
            ask_message = f"""Current format schema:
{previous_schema}

Please describe your modification to this format:
  • Example: "Change instruction field to system"
  • Example: "Add a meta field containing source and language"
  • Example: "Remove input field"

Enter your modification requirements:"""
        else:
            ask_message = """Please describe the custom data format you need.

Examples:
  • "I need a Q&A format with question and answer fields"
  • "I need a conversation format with multi-turn user and assistant messages"
  • "I need an article format with title, content, tags fields"

Please describe the fields and data structure you need:"""
        
        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(AIMessage(content=ask_message))
        
        user_input = interrupt(ask_message)
        
        if user_input:
            description = str(user_input)
            state["obtainer"]["mapping_custom_description"] = description
            state["messages"].append(HumanMessage(content=description))
            logger.info(f"User provided description: {description}")
        else:
            logger.warning("No description provided")
            state["exception"] = "No custom format description provided"
            return state
    
    obtainer = state.get("obtainer", {})
    model_name = obtainer.get("model_path")
    base_url = obtainer.get("base_url")
    api_key = obtainer.get("api_key")
    temperature = obtainer.get("temperature", 0.7)
    
    if not model_name or not base_url or not api_key:
        logger.error("Missing LLM configuration")
        state["exception"] = "Missing LLM configuration"
        return state
    
    try:
        llm = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            temperature=temperature
        )
        
        if previous_format and is_modification:
            previous_schema = json.dumps(previous_format.get("schema", {}), ensure_ascii=False, indent=2)
            previous_example = json.dumps(previous_format.get("example", {}), ensure_ascii=False, indent=2)
            
            try:
                system_prompt = get_prompt("system", "custom_format_modifier_prompt")
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = "You are a data format expert. Modify the existing format based on user requirements."
            
            user_prompt = f"""Existing format schema:
{previous_schema}

Existing format example:
{previous_example}

User modification requirements: {description}

Based on user modification requirements, generate modified schema and example data. Only output JSON object."""
        else:
            try:
                system_prompt = get_prompt("system", "custom_format_generator_prompt")
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = "You are a data format expert. Generate JSON format schema definition based on user description."
            
            user_prompt = f"""User requirements: {description}

Generate corresponding schema and example data. Only output JSON object."""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        response = llm.invoke(messages)
        response_text = response.content.strip()
        
        logger.debug(f"LLM response: {response_text}")
        
        format_config = _parse_json_response(response_text)
        
        if format_config is None:
            raise ValueError("Unable to parse LLM response JSON")
        
        format_name = "Modified format" if is_modification else "Custom format"
        pending_format = {
            "format_id": "custom",
            "format_name": format_name,
            "description": description,
            "schema": format_config.get("schema", {}),
            "example": format_config.get("example", {}),
            "is_preset": False
        }
        state["obtainer"]["pending_format"] = pending_format
        
        state["obtainer"]["confirmed_format"] = None
        
        logger.info("Custom format generated successfully")
        
        _save_to_store(state, store, pending_format, description)
        
    except Exception as e:
        logger.error(f"Error generating custom format: {e}")
        
        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(AIMessage(
            content=f"Error generating custom format: {str(e)}\n\nPlease try describing your format more clearly, or select a preset format."
        ))
        
        state["obtainer"]["mapping_user_intent"] = ""
        state["obtainer"]["mapping_custom_description"] = ""
    
    logger.info("=== Custom Format Node: Completed ===")
    return state


def _parse_json_response(response_text: str) -> Dict[str, Any]:
    """Parse LLM response JSON"""
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass
    
    pattern = r'```(?:json)?\s*([\s\S]*?)```'
    match = re.search(pattern, response_text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    
    start_idx = response_text.find('{')
    end_idx = response_text.rfind('}')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            return json.loads(response_text[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            pass
    
    return None


def _save_to_store(state: LoopAIState, store: BaseStore, pending_format: Dict[str, Any], description: str):
    """Save operation record to store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        data = {
            "event_type": "custom_format_generated",
            "timestamp": datetime.datetime.now().isoformat(),
            "description": description[:200],
            "schema": pending_format.get("schema")
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "custom_format_event", data)
        logger.debug(f"Saved custom_format event to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")
