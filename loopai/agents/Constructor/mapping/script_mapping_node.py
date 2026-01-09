"""
Script Mapping Node (Non-LLM)

Maps data from intermediate format to preset target formats using predefined rules
Fast, low-cost, and deterministic

Intermediate Format Schema (LLM_Data_Mapping_Schema v1.0):
- PT mode: {"text": "...", "meta": {...}}
- SFT mode: {"messages": [...], "system": "...", "meta": {...}}
"""
import os
import json
from typing import Dict, Any, List, Callable, Union

from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

logger = get_logger()


def _extract_text_from_intermediate(record: Dict[str, Any]) -> str:
    """
    Extract text content from intermediate format (PT mode)
    """
    text = record.get("text")
    
    if text is None:
        return ""
    
    if isinstance(text, list):
        return "\n".join(str(t) for t in text if t)
    
    return str(text) if text else ""


def _extract_messages_from_intermediate(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract message list from intermediate format (SFT mode)
    """
    messages = record.get("messages", [])
    
    if not isinstance(messages, list):
        return []
    
    result = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        
        role = msg.get("role", "user")
        content = msg.get("content")
        
        # content 可能是字符串或数组
        if isinstance(content, list):
            content = "\n".join(str(c) for c in content if c)
        elif content is None:
            content = ""
        else:
            content = str(content)
        
        result.append({
            "role": role,
            "content": content
        })
    
    return result


def _get_system_prompt(record: Dict[str, Any]) -> str:
    """Get system prompt from record if exists"""
    messages = record.get("messages", [])
    if isinstance(messages, list):
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, list):
                    return "\n".join(str(c) for c in content if c)
                return str(content) if content else ""
    
    system = record.get("system")
    if system:
        if isinstance(system, list):
            return "\n".join(str(s) for s in system if s)
        return str(system)
    
    return ""


def _map_to_alpaca(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map to Alpaca format
    
    Target: {"instruction": "...", "input": "...", "output": "..."}
    """
    messages = _extract_messages_from_intermediate(record)
    
    if messages:
        # SFT 模式: 从 messages 提取
        instruction = ""
        output = ""
        input_text = ""  # 默认将 system 填入 Alpaca input
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user" and not instruction:
                instruction = content
            elif role == "assistant" and not output:
                output = content
            elif role =="system" and not input_text:
                input_text= content
            
        
        return {
            "instruction": instruction,
            "input": input_text,
            "output": output
        }
    else:
        # PT 模式: text 作为 output
        text = _extract_text_from_intermediate(record)
        return {
            "instruction": "",
            "input": "",
            "output": text
        }


def _map_to_chatml(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map to ChatML format
    
    Target: {"messages": [{"role": "...", "content": "..."}]}
    """
    messages = _extract_messages_from_intermediate(record)
    result_messages = []
    
    if messages:
        for msg in messages:
            role = msg.get("role", "user")
            # 处理 role 转换：tool -> assistant，其他保持原样（system/user/assistant）
            if role == "tool":
                role = "assistant"
            result_messages.append({
                "role": role,
                "content": msg.get("content", "")
            })
    else:
        # PT 模式：从 text 获取
        text = _extract_text_from_intermediate(record)
        if text:
            result_messages.append({
                "role": "user",
                "content": text
            })
    
    return {"messages": result_messages}


def _map_to_jsonl_pt(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map to JSONL pre-training format
    
    Target: {"text": "..."}
    """
    # 优先使用 PT 模式的 text
    text = _extract_text_from_intermediate(record)
    
    if not text:
        # 尝试从 SFT messages 构建文本
        messages = _extract_messages_from_intermediate(record)
        if messages:
            parts = []
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if content:
                    if role == "system":
                        parts.append(f"System: {content}")
                    elif role == "user":
                        parts.append(f"User: {content}")
                    elif role == "assistant":
                        parts.append(f"Assistant: {content}")
                    else:
                        parts.append(content)
            text = "\n\n".join(parts)
    
    return {"text": text}


def _map_to_jsonl_sft(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map to JSONL SFT format (conversation format)
    
    Target: {"conversation": [{"role": "...", "content": "..."}]}
    """
    messages = _extract_messages_from_intermediate(record)
    conversation = []
    
    if messages:
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            # SFT conversation 通常不包含 system
            if role == "system":
                continue
            
            # 标准化 role
            if role not in ["user", "assistant"]:
                role = "assistant" if role == "tool" else "user"
            
            conversation.append({
                "role": role,
                "content": content
            })
    else:
        # PT 模式: text 作为 assistant response
        text = _extract_text_from_intermediate(record)
        if text:
            conversation.append({
                "role": "assistant",
                "content": text
            })
    
    return {"conversation": conversation}


def _map_to_openai_fine_tune(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map to OpenAI fine-tuning format (same as ChatML)
    """
    return _map_to_chatml(record)


def _map_to_llama2_chat(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map to Llama2 chat format
    
    Target: {"text": "<s>[INST] ... [/INST] ... </s>"}
    """
    messages = _extract_messages_from_intermediate(record)
    
    if messages:
        # 获取 system prompt
        system_prompt = _get_system_prompt(record)
        
        # 构建 Llama2 格式
        parts = []
        current_user = ""
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role == "system":
                continue
            elif role == "user":
                current_user = content
            elif role == "assistant":
                if system_prompt and not parts:
                    inst_content = f"<<SYS>>\n{system_prompt}\n<</SYS>>\n\n{current_user}"
                else:
                    inst_content = current_user
                
                parts.append(f"<s>[INST] {inst_content} [/INST] {content} </s>")
                current_user = ""
        
        text = "".join(parts) if parts else ""
    else:
        text = _extract_text_from_intermediate(record)
    
    return {"text": text}


FORMAT_MAPPERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "alpaca": _map_to_alpaca,
    "chatml": _map_to_chatml,
    "jsonl_pt": _map_to_jsonl_pt,
    "jsonl_sft": _map_to_jsonl_sft,
    "openai_fine_tune": _map_to_openai_fine_tune,
    "llama2_chat": _map_to_llama2_chat,
}


def script_mapping_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    Script mapping node - Use predefined rules for data mapping (Non-LLM)
    """
    logger.info("=== Script Mapping Node: Starting ===")
    
    confirmed_format = state.get("obtainer_confirmed_format", {})
    format_id = confirmed_format.get("format_id", "")
    
    if not format_id or format_id not in FORMAT_MAPPERS:
        logger.error(f"Invalid format_id for script mapping: {format_id}")
        state["exception"] = f"Invalid format ID: {format_id}"
        return state
    
    intermediate_path = state.get("obtainer_intermediate_data_path", "")
    if not intermediate_path or not os.path.exists(intermediate_path):
        logger.error(f"Intermediate data path not found: {intermediate_path}")
        state["exception"] = f"Intermediate data path does not exist: {intermediate_path}"
        return state
    
    output_dir = state.get("output_dir", "./output")
    mapping_output_dir = os.path.join(output_dir, "mapped_output")
    os.makedirs(mapping_output_dir, exist_ok=True)
    
    mapper = FORMAT_MAPPERS[format_id]
    
    try:
        records = _read_intermediate_data(intermediate_path)
        
        if not records:
            logger.warning("No records found in intermediate data")
            state["obtainer_mapping_results"] = {
                "total_records": 0,
                "mapped_records": 0,
                "output_dir": mapping_output_dir,
                "output_file": ""
            }
            return state
        
        logger.info(f"Read {len(records)} records from intermediate data")
        
        category = state.get("obtainer_category", "PT")
        output_file = os.path.join(mapping_output_dir, f"mapped_{format_id}_{category}.jsonl")
        
        mapped_count = 0
        failed_count = 0
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for idx, record in enumerate(records):
                try:
                    mapped_record = mapper(record)
                    
                    if _is_valid_record(mapped_record, format_id):
                        f.write(json.dumps(mapped_record, ensure_ascii=False) + '\n')
                        mapped_count += 1
                    else:
                        failed_count += 1
                        logger.debug(f"Record {idx} mapped to empty/invalid result, skipping")
                    
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"Error mapping record {idx}: {e}")
                
                if (idx + 1) % 1000 == 0:
                    logger.info(f"Processed {idx + 1} records (mapped: {mapped_count}, failed: {failed_count})")
        
        logger.info(f"Script mapping completed: {mapped_count} records mapped, {failed_count} failed")
        
        state["obtainer_mapping_results"] = {
            "total_records": len(records),
            "mapped_records": mapped_count,
            "failed_records": failed_count,
            "output_dir": mapping_output_dir,
            "output_file": output_file,
            "format_id": format_id,
            "mapping_type": "script"
        }
        
        _save_to_store(state, store, state["obtainer_mapping_results"])
        
    except Exception as e:
        logger.error(f"Error in script mapping: {e}", exc_info=True)
        state["exception"] = f"Script mapping error: {str(e)}"
    
    logger.info("=== Script Mapping Node: Completed ===")
    return state


def _read_intermediate_data(path: str) -> List[Dict[str, Any]]:
    """Read intermediate format data"""
    records = []
    
    if os.path.isfile(path):
        records.extend(_read_jsonl_file(path))
    elif os.path.isdir(path):
        for filename in os.listdir(path):
            if filename.endswith('.jsonl'):
                filepath = os.path.join(path, filename)
                records.extend(_read_jsonl_file(filepath))
    
    return records


def _read_jsonl_file(filepath: str) -> List[Dict[str, Any]]:
    """Read single JSONL file"""
    records = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON line in {filepath}: {e}")
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {e}")
    return records


def _is_valid_record(record: Dict[str, Any], format_id: str) -> bool:
    """
    Check if mapping result is valid
    """
    if not record:
        return False
    
    if format_id == "alpaca":
        instruction = record.get("instruction", "")
        output = record.get("output", "")
        return bool((instruction and instruction.strip()) or (output and output.strip()))
    
    elif format_id in ["chatml", "openai_fine_tune"]:
        messages = record.get("messages", [])
        if not messages:
            return False
        return any(
            m.get("content", "").strip() 
            for m in messages 
            if isinstance(m, dict)
        )
    
    elif format_id == "jsonl_pt":
        text = record.get("text", "")
        return bool(text and text.strip())
    
    elif format_id == "jsonl_sft":
        conversation = record.get("conversation", [])
        if not conversation:
            return False
        return any(
            c.get("content", "").strip() 
            for c in conversation 
            if isinstance(c, dict)
        )
    
    elif format_id == "llama2_chat":
        text = record.get("text", "")
        return bool(text and text.strip())
    
    return True


def _save_to_store(state: LoopAIState, store: BaseStore, results: Dict[str, Any]):
    """Save operation record to store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        data = {
            "event_type": "script_mapping_completed",
            "timestamp": datetime.datetime.now().isoformat(),
            "results": results
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "script_mapping_event", data)
        logger.debug(f"Saved script_mapping event to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")

