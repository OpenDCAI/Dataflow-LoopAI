"""
LLM Mapping Node

Generates mapping function using LLM, then batch processes all data
Significantly reduces token consumption compared to per-record LLM calls
"""
import os
import json
import re
from typing import Dict, Any, List, Optional, Callable

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from .__mapping_prompts import get_prompt

logger = get_logger()


def llm_mapping_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    LLM mapping node - Generate mapping rules with LLM, then batch process
    
    Process:
    1. LLM analyzes sample data and target format
    2. LLM generates Python mapping function code
    3. Execute mapping function to batch process all data
    """
    logger.info("=== LLM Mapping Node: Starting ===")
    
    confirmed_format = state.get("obtainer_confirmed_format", {})
    schema = confirmed_format.get("schema", {})
    example = confirmed_format.get("example", {})
    description = confirmed_format.get("description", "")
    
    if not schema:
        logger.error("No schema found in confirmed format")
        state["exception"] = "No schema in confirmed format"
        return state
    
    intermediate_path = state.get("obtainer_intermediate_data_path", "")
    if not intermediate_path or not os.path.exists(intermediate_path):
        logger.error(f"Intermediate data path not found: {intermediate_path}")
        state["exception"] = f"Intermediate data path does not exist: {intermediate_path}"
        return state
    
    model_name = state.get("obtainer_model_path") or state.get("analyze_model_path")
    base_url = state.get("obtainer_base_url") or state.get("analyze_base_url")
    api_key = state.get("obtainer_api_key") or state.get("analyze_api_key")
    temperature = state.get("obtainer_temperature", 0.0)
    
    if not model_name or not base_url or not api_key:
        logger.error("Missing LLM configuration")
        state["exception"] = "Missing LLM configuration"
        return state
    
    output_dir = state.get("output_dir", "./output")
    mapping_output_dir = os.path.join(output_dir, "mapped_output")
    os.makedirs(mapping_output_dir, exist_ok=True)
    
    try:
        records = _read_intermediate_data(intermediate_path)
        
        if not records:
            logger.warning("No records found in intermediate data")
            state["obtainer_mapping_results"] = {
                "total_records": 0,
                "mapped_records": 0,
                "output_dir": mapping_output_dir,
                "output_file": "",
                "mapping_type": "llm"
            }
            return state
        
        logger.info(f"Read {len(records)} records from intermediate data")
        
        sample_records = records[:min(3, len(records))]
        
        logger.info("Step 1: Generating mapping function using LLM...")
        llm = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            temperature=temperature
        )
        
        mapping_func = _generate_mapping_function(
            llm=llm,
            sample_records=sample_records,
            target_schema=schema,
            target_example=example,
            description=description
        )
        
        if mapping_func is None:
            raise ValueError("Failed to generate mapping function from LLM")
        
        logger.info("Mapping function generated successfully")
        
        logger.info("Step 2: Batch processing all records...")
        category = state.get("obtainer_category", "PT")
        output_file = os.path.join(mapping_output_dir, f"mapped_custom_{category}.jsonl")
        
        mapped_count = 0
        failed_count = 0
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for idx, record in enumerate(records):
                try:
                    mapped_record = mapping_func(record)
                    
                    if mapped_record and _is_valid_record(mapped_record):
                        f.write(json.dumps(mapped_record, ensure_ascii=False) + '\n')
                        mapped_count += 1
                    else:
                        failed_count += 1
                        logger.debug(f"Record {idx}: Mapped to empty/invalid result")
                
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"Record {idx}: Error during mapping: {e}")
                
                if (idx + 1) % 1000 == 0:
                    logger.info(f"Processed {idx + 1} records (mapped: {mapped_count}, failed: {failed_count})")
        
        logger.info(f"LLM mapping completed: {mapped_count} records mapped, {failed_count} failed")
        
        result = {
            "total_records": len(records),
            "mapped_records": mapped_count,
            "failed_records": failed_count,
            "output_dir": mapping_output_dir,
            "output_file": output_file,
            "format_id": "custom",
            "mapping_type": "llm"
        }
        
        state["obtainer_mapping_results"] = result
        _save_to_store(state, store, result)
        
    except Exception as e:
        logger.error(f"Error in LLM mapping: {e}", exc_info=True)
        state["exception"] = f"LLM mapping error: {str(e)}"
    
    logger.info("=== LLM Mapping Node: Completed ===")
    return state


def _generate_mapping_function(
    llm: ChatOpenAI,
    sample_records: List[Dict[str, Any]],
    target_schema: Dict[str, Any],
    target_example: Dict[str, Any],
    description: str
) -> Optional[Callable]:
    """
    Generate mapping function using LLM
    
    LLM analyzes sample data and target format, generates a Python function
    """
    
    try:
        system_prompt = get_prompt("system", "llm_mapping_function_generator_prompt")
    except Exception as e:
        logger.warning(f"Failed to load prompt, using default: {e}")
        system_prompt = "You are a Python expert. Generate a map_record function to transform data."
    
    samples_text = "\n\n".join([
        f"Sample {i+1}:\n{json.dumps(record, ensure_ascii=False, indent=2)}"
        for i, record in enumerate(sample_records)
    ])
    
    user_prompt = f"""Input data samples (intermediate format):
{samples_text}

Target format Schema:
{json.dumps(target_schema, ensure_ascii=False, indent=2)}

Target format example:
{json.dumps(target_example, ensure_ascii=False, indent=2)}

User requirements: {description}

Please write map_record function to convert input format to target format. Only output function code."""
    
    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        response = llm.invoke(messages)
        code = response.content.strip()
        
        logger.debug(f"LLM generated code:\n{code}")
        
        func_code = _extract_function_code(code)
        
        if not func_code:
            logger.error("Failed to extract function code from LLM response")
            return None
        
        namespace = {}
        exec(func_code, namespace)
        
        if 'map_record' not in namespace:
            logger.error("Function 'map_record' not found in generated code")
            return None
        
        mapping_func = namespace['map_record']
        
        try:
            test_result = mapping_func(sample_records[0])
            if not isinstance(test_result, dict):
                logger.error(f"Mapping function returned non-dict: {type(test_result)}")
                return None
            logger.info(f"Function validation successful, test output: {json.dumps(test_result, ensure_ascii=False)[:200]}")
        except Exception as e:
            logger.error(f"Function validation failed: {e}")
            return None
        
        return mapping_func
        
    except Exception as e:
        logger.error(f"Error generating mapping function: {e}", exc_info=True)
        return None


def _extract_function_code(text: str) -> Optional[str]:
    """Extract function code from LLM response"""
    
    pattern = r'```(?:python)?\s*([\s\S]*?)```'
    match = re.search(pattern, text)
    if match:
        code = match.group(1).strip()
        if 'def map_record' in code:
            return code
    
    if 'def map_record' in text:
        start_idx = text.find('def map_record')
        if start_idx != -1:
            return text[start_idx:].strip()
    
    return None


def _is_valid_record(record: Dict[str, Any]) -> bool:
    """Check if mapping result is valid (at least one non-empty field)"""
    if not record:
        return False
    
    for value in record.values():
        if value:
            if isinstance(value, str) and value.strip():
                return True
            elif isinstance(value, (list, dict)) and value:
                return True
            elif isinstance(value, (int, float, bool)):
                return True
    
    return False


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


def _save_to_store(state: LoopAIState, store: BaseStore, results: Dict[str, Any]):
    """Save operation record to store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        data = {
            "event_type": "llm_mapping_completed",
            "timestamp": datetime.datetime.now().isoformat(),
            "results": results
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "llm_mapping_event", data)
        logger.debug(f"Saved llm_mapping event to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")
