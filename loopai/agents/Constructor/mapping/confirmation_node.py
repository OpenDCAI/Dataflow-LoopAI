"""
Confirmation Node

Displays pending format and waits for user confirmation
Uses LLM to analyze user feedback (confirm/modify/restart)
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


def confirmation_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    Confirmation node - Display format and wait for user confirmation
    """
    logger.info("=== Confirmation Node: Starting ===")
    
    # 确保 constructor 字典存在
    if "constructor" not in state:
        state["constructor"] = {}
    
    constructor_state = state.get("constructor", {})
    pending_format = constructor_state.get("pending_format")
    
    if not pending_format:
        logger.error("No pending format found")
        state["exception"] = "No pending format found"
        return state
    
    confirm_message = _build_confirmation_message(pending_format)
    
    if "messages" not in state:
        state["messages"] = []
    state["messages"].append({
        "type": "ai",
        "role": "assistant",
        "content": confirm_message,
    })
    
    logger.info("Waiting for user confirmation...")
    user_input = interrupt(confirm_message)
    
    logger.info(f"User confirmation input: {user_input}")
    
    if user_input:
        state["messages"].append({
            "type": "human",
            "role": "user",
            "content": str(user_input),
        })
    
    return _analyze_confirmation_intent(state, str(user_input) if user_input else "", pending_format, store)


def _build_confirmation_message(pending_format: Dict[str, Any]) -> str:
    """Build confirmation message"""
    format_name = pending_format.get("format_name", "Selected format")
    format_id = pending_format.get("format_id", "")
    description = pending_format.get("description", "")
    schema = pending_format.get("schema", {})
    example = pending_format.get("example", {})
    is_preset = pending_format.get("is_preset", True)
    
    format_type = "Preset format" if is_preset else "Custom format"
    
    confirm_message = f"""Selected {format_type}: {format_name}
{"Format ID: " + format_id if format_id else ""}
{("Description: " + description) if description else ""}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 Format Structure (Schema):
{json.dumps(schema, ensure_ascii=False, indent=2)}

📝 Sample Data:
{json.dumps(example, ensure_ascii=False, indent=2)}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Please confirm whether to use this format:
  • Enter "confirm" or "yes" to use this format for data mapping
  • Enter "modify" or "change" to modify format requirements
  • Enter "restart" or "reselect" to reselect format
  • Or directly enter another format ID to switch format"""
    
    return confirm_message


def _analyze_confirmation_intent(
    state: LoopAIState, 
    user_input: str, 
    pending_format: Dict[str, Any],
    store: BaseStore = None
) -> LoopAIState:
    """Analyze user confirmation intent"""
    logger.info(f"Analyzing confirmation intent: {user_input}")
    
    # 确保 constructor 字典存在
    if "constructor" not in state:
        state["constructor"] = {}
    
    if not user_input or not user_input.strip():
        state["constructor"]["confirmation_result"] = "confirmed"
        state["constructor"]["confirmed_format"] = pending_format
        logger.info("Empty input, defaulting to confirmed")
        _save_to_store(state, store, user_input)
        return state
    
    user_input_lower = user_input.lower().strip()
    
    confirm_keywords = ["confirm", "yes", "ok", "y"]
    if user_input_lower in confirm_keywords or user_input_lower in [kw + " format" for kw in confirm_keywords]:
        state["constructor"]["confirmation_result"] = "confirmed"
        state["constructor"]["confirmed_format"] = pending_format
        logger.info("Quick match: confirmed")
        _save_to_store(state, store, user_input)
        return state
    
    if user_input_lower in PRESET_FORMATS:
        state["constructor"]["confirmation_result"] = "restart"
        state["constructor"]["mapping_user_intent"] = "preset_format"
        state["constructor"]["mapping_selected_format_id"] = user_input_lower
        state["constructor"]["pending_format"] = None
        logger.info(f"Quick match: switch to preset format {user_input_lower}")
        _save_to_store(state, store, user_input)
        return state
    
    restart_exact_keywords = ["restart", "reselect", "cancel", "list"]
    if user_input_lower in restart_exact_keywords:
        state["constructor"]["confirmation_result"] = "restart"
        state["constructor"]["pending_format"] = None
        state["constructor"]["confirmed_format"] = None
        state["constructor"]["mapping_user_intent"] = ""
        state["constructor"]["mapping_selected_format_id"] = ""
        state["constructor"]["mapping_custom_description"] = ""
        logger.info("Quick match: restart - exact keyword match")
        _save_to_store(state, store, user_input)
        return state
    
    modify_exact_keywords = ["modify", "change"]
    if user_input_lower in modify_exact_keywords:
        state["constructor"]["confirmation_result"] = "modify"
        state["constructor"]["pending_format"] = None
        state["constructor"]["mapping_custom_description"] = ""
        logger.info("Quick match: modify - exact keyword match")
        _save_to_store(state, store, user_input)
        return state
    
    if len(user_input) > 5:
        logger.info("Input is long enough, using LLM analysis")
    
    constructor = state.get("constructor", {})
    model_name = constructor.get("model_path")
    base_url = constructor.get("base_url")
    api_key = constructor.get("api_key")
    
    if not model_name or not base_url or not api_key:
        logger.warning("No LLM config, treating as modify request")
        state["constructor"]["confirmation_result"] = "modify"
        state["constructor"]["mapping_custom_description"] = user_input
        _save_to_store(state, store, user_input)
        return state
    
    try:
        llm = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            temperature=0.0
        )
        
        preset_format_ids = list(PRESET_FORMATS.keys())
        current_format_id = pending_format.get("format_id", "")
        current_schema = json.dumps(pending_format.get("schema", {}), ensure_ascii=False)
        
        try:
            system_prompt_template = get_prompt("system", "format_confirmation_intent_analyzer_prompt")
            system_prompt = f"""{system_prompt_template}

Current selected format: {current_format_id}
Current format schema: {current_schema}

Available preset format IDs: {preset_format_ids}"""
        except Exception as e:
            logger.warning(f"Failed to load prompt, using default: {e}")
            system_prompt = "You are an intent analysis assistant. Analyze user's confirmation intent."
        
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"User input: {user_input}")
        ])
        
        result = _parse_llm_response(response.content)
        
        if result:
            action = result.get("action", "confirmed")
            target_format = result.get("target_format_id", "")
            modify_desc = result.get("modify_description", "")
            
            if action == "confirmed":
                state["constructor"]["confirmation_result"] = "confirmed"
                state["constructor"]["confirmed_format"] = pending_format
            elif action == "switch_preset" and target_format:
                state["constructor"]["confirmation_result"] = "restart"
                state["constructor"]["mapping_user_intent"] = "preset_format"
                state["constructor"]["mapping_selected_format_id"] = target_format.lower()
                state["constructor"]["pending_format"] = None
            elif action == "modify":
                state["constructor"]["confirmation_result"] = "modify"
                state["constructor"]["mapping_custom_description"] = modify_desc or user_input
            elif action == "restart":
                state["constructor"]["confirmation_result"] = "restart"
                state["constructor"]["pending_format"] = None
                state["constructor"]["mapping_user_intent"] = ""
            else:
                state["constructor"]["confirmation_result"] = "confirmed"
                state["constructor"]["confirmed_format"] = pending_format
            
            logger.info(f"LLM analysis: action={action}")
        else:
            state["constructor"]["confirmation_result"] = "confirmed"
            state["constructor"]["confirmed_format"] = pending_format
            
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        state["constructor"]["confirmation_result"] = "confirmed"
        state["constructor"]["confirmed_format"] = pending_format
    
    _save_to_store(state, store, user_input)
    logger.info("=== Confirmation Node: Completed ===")
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


def _save_to_store(state: LoopAIState, store: BaseStore, user_input: str):
    """Save to store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        data = {
            "event_type": "confirmation_completed",
            "timestamp": datetime.datetime.now().isoformat(),
            "user_input": user_input,
            "result": state.get("constructor", {}).get("confirmation_result", "")
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "confirmation_event", data)
        logger.debug("Saved confirmation event to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")
