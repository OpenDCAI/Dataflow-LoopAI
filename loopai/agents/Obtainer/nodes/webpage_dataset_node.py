import json
import asyncio
import os
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.agents.Obtainer.utils import WebTools
from loopai.common.prompts import PromptLoader
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

logger = get_logger()


def webpage_dataset_node(state: LoopAIState) -> LoopAIState:
    """
    Webpage Dataset Node that:
    1. Reads collected webpage content
    2. Uses LLM to extract and structure data according to PT/SFT schema
    3. Saves structured data as JSONL
    """
    logger.info("=== WebPage Dataset Node: Starting ===")
    
    # Get user query from state
    user_query = ""
    
    if state.get("automated_query"):
        user_query = state.get("automated_query")
    else:
        if state.get("messages") and len(state["messages"]) > 0:
            from langchain_core.messages import HumanMessage
            
            for message in reversed(state["messages"]):
                if isinstance(message, HumanMessage):
                    if hasattr(message, "content"):
                        user_query = message.content
                        break
                elif isinstance(message, dict):
                    msg_type = message.get("type", "")
                    msg_role = message.get("role", "")
                    if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                        user_query = message.get("content", "")
                        if user_query:
                            break
                elif hasattr(message, "type"):
                    if message.type == "human":
                        if hasattr(message, "content"):
                            user_query = message.content
                            break
    
    if not user_query:
        logger.warning("No user query found in state")
        state["exception"] = "No user query provided"
        return state
    
    logger.info(f"User query: {user_query}")
    
    # Initialize components
    try:
        # Get configuration from state or use defaults
        model_name = state.get("obtainer", {}).get("model_path") or state.get("analyze_model_path")
        base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
        api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
        temperature = state.get("obtainer", {}).get("temperature", 0.7)
        
        if not model_name or not base_url or not api_key:
            logger.error("Missing required configuration for webpage dataset node")
            state["exception"] = "Missing model configuration (model_name, base_url, api_key)"
            return state
        
        # Initialize prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # Get category (PT or SFT)
        category = state.get("obtainer", {}).get("category", "PT").upper()
        
        # Output directory
        output_dir = state.get("output_dir", "./output")
        dataset_dir = os.path.join(output_dir, "webpage_dataset")
        os.makedirs(dataset_dir, exist_ok=True)
        
        # Get webpage data source (from webpage_collect_node or directly from URLs)
        webpage_data_path = state.get("obtainer", {}).get("webpage_collect_jsonl_path", "")
        webpage_urls = state.get("obtainer", {}).get("webpage_collect_urls_visited", [])
        
        # Run async workflow
        debug_mode = state.get("obtainer_debug", False)
        result = asyncio.run(_webpage_dataset_workflow(
            user_query=user_query,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            category=category,
            output_dir=dataset_dir,
            webpage_data_path=webpage_data_path,
            webpage_urls=webpage_urls,
            max_records_per_page=state.get("obtainer_max_records_per_page", 10),  # These might not be in obtainer dict
            min_relevance_score=state.get("obtainer_min_relevance_score", 0.7),  # These might not be in obtainer dict
            dataset_concurrent_limit=state.get("obtainer_dataset_concurrent_limit", 5),  # These might not be in obtainer dict
            debug_mode=debug_mode,
        ))
        
        # Update state with results
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            state.setdefault("obtainer", {})["webpage_dataset_summary"] = result.get("summary", "")
            state.setdefault("obtainer", {})["webpage_dataset_count"] = result.get("dataset_count", 0)
            state.setdefault("obtainer", {})["webpage_dataset_jsonl_path"] = result.get("jsonl_path", "")
            logger.info(
                f"WebPage Dataset completed: {result.get('dataset_count', 0)} records generated, "
                f"saved to {result.get('jsonl_path', '')}"
            )
        
        # Send custom stream event if debug mode is enabled
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        message="WebPage Dataset node completed",
                        data={
                            'user_query': user_query,
                            'dataset_count': result.get("dataset_count", 0),
                            'category': category,
                            'has_exception': "exception" in result,
                        }
                    ).json())
            except Exception as e:
                logger.debug(f"Could not send stream event: {e}")
        
    except Exception as e:
        logger.error(f"WebPage Dataset node error: {e}", exc_info=True)
        state["exception"] = f"WebPage Dataset error: {str(e)}"
    
    logger.info("=== WebPage Dataset Node: Completed ===")
    return state


async def _webpage_dataset_workflow(
    user_query: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: PromptLoader,
    category: str,
    output_dir: str,
    webpage_data_path: Optional[str],
    webpage_urls: List[str],
    max_records_per_page: int = 10,
    min_relevance_score: float = 0.7,
    dataset_concurrent_limit: int = 5,
    debug_mode: bool = False,
) -> Dict[str, Any]:
    """
    Main workflow for generating dataset from webpage content
    
    Steps:
    1. Load webpage content (from JSONL or fetch from URLs)
    2. For each webpage, use LLM to extract structured data
    3. Filter by relevance score
    4. Save as JSONL
    """
    try:
        # Initialize LLM
        llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        
        # Load webpage content
        webpage_contents = []
        
        if webpage_data_path and os.path.exists(webpage_data_path):
            logger.info(f"Loading webpage data from: {webpage_data_path}")
            with open(webpage_data_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            data = json.loads(line)
                            webpage_contents.append(data)
                        except json.JSONDecodeError:
                            continue
            logger.info(f"Loaded {len(webpage_contents)} webpages from JSONL")
        
        # If no data from JSONL, try to fetch from URLs
        if not webpage_contents and webpage_urls:
            logger.info(f"Fetching content from {len(webpage_urls)} URLs using Jina")
            for url in webpage_urls[:20]:  # Limit to 20 URLs
                try:
                    page_content = await WebTools.read_with_jina_reader(url)
                    structured_content = page_content.get("structured_content", {})
                    markdown_content = page_content.get("text", "")
                    
                    webpage_contents.append({
                        "url": url,
                        "title": structured_content.get("title", ""),
                        "content": markdown_content,
                        "structured_content": structured_content,
                    })
                except Exception as e:
                    logger.warning(f"Error fetching {url}: {e}")
                    continue
        
        if not webpage_contents:
            return {
                "exception": "No webpage content available. Please run webpage_collect_node first or provide webpage URLs.",
                "summary": "",
                "dataset_count": 0,
                "jsonl_path": "",
            }
        
        logger.info(f"Processing {len(webpage_contents)} webpages to generate {category} dataset")
        
        # Concurrent limit for dataset generation
        dataset_semaphore = asyncio.Semaphore(dataset_concurrent_limit)
        all_records = []
        
        async def process_webpage(webpage: Dict[str, Any], index: int) -> List[Dict[str, Any]]:
            """Process a single webpage to generate dataset records"""
            async with dataset_semaphore:
                try:
                    logger.info(f"Processing webpage {index}/{len(webpage_contents)}: {webpage.get('url', 'N/A')}")
                    
                    # Extract content
                    content = webpage.get("content", "")
                    title = webpage.get("title", "")
                    url = webpage.get("url", "")
                    
                    if not content or len(content.strip()) < 100:
                        logger.warning(f"Skipping webpage with insufficient content: {url}")
                        return []
                    
                    # Limit content length for LLM
                    content_preview = content[:8000] if len(content) > 8000 else content
                    
                    # Generate structured data using LLM
                    result = await _generate_dataset_records(
                        llm=llm,
                        prompt_loader=prompt_loader,
                        user_query=user_query,
                        category=category,
                        webpage_title=title,
                        webpage_content=content_preview,
                        webpage_url=url,
                        max_records=max_records_per_page,
                        min_relevance_score=min_relevance_score,
                    )
                    
                    records = result.get("records", [])
                    reason = result.get("reason", "")
                    
                    if records:
                        logger.info(f"Generated {len(records)} records from webpage {index}")
                        return records
                    else:
                        # Log reason when no records generated
                        logger.warning(f"No records generated from webpage {index} ({url})")
                        if reason:
                            logger.warning(f"Reason: {reason}")
                            print(f"\n[Warning] No records generated from webpage: {url}")
                            print(f"Reason: {reason}\n")
                        return []
                    
                except Exception as e:
                    logger.error(f"Error processing webpage {index}: {e}")
                    return []
        
        # Process all webpages concurrently
        logger.info(f"Processing {len(webpage_contents)} webpages with {dataset_concurrent_limit} concurrent workers...")
        dataset_tasks = [process_webpage(webpage, i+1) for i, webpage in enumerate(webpage_contents)]
        dataset_results = await asyncio.gather(*dataset_tasks, return_exceptions=True)
        
        # Collect all records
        for result in dataset_results:
            if isinstance(result, Exception):
                logger.error(f"Exception in dataset generation: {result}")
                continue
            if result:
                all_records.extend(result)
        
        # Save to JSONL
        jsonl_filename = f"webpage_dataset_{category.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        jsonl_path = os.path.join(output_dir, jsonl_filename)
        
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for record in all_records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        
        summary = (
            f"Generated {len(all_records)} {category} dataset records from {len(webpage_contents)} webpages. "
            f"Saved to: {jsonl_path}"
        )
        
        return {
            "summary": summary,
            "dataset_count": len(all_records),
            "jsonl_path": jsonl_path,
        }
        
    except Exception as e:
        logger.error(f"WebPage Dataset workflow error: {e}", exc_info=True)
        return {
            "exception": f"WebPage Dataset workflow error: {str(e)}",
            "summary": "",
            "dataset_count": 0,
            "jsonl_path": "",
        }


async def _generate_dataset_records(
    llm: ChatOpenAI,
    prompt_loader: PromptLoader,
    user_query: str,
    category: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    max_records: int = 10,
    min_relevance_score: float = 0.7,
) -> Dict[str, Any]:
    """
    Generate dataset records from webpage content using LLM
    
    Returns dict with:
    - records: List of structured records according to PT/SFT schema
    - reason: Reason string if no records generated
    """
    try:
        # Get prompt based on category
        if category == "PT":
            try:
                system_prompt = prompt_loader("system", "webpage_dataset_pt_prompt")
                task_prompt_template = prompt_loader("task", "webpage_dataset_pt_prompt")
                task_prompt = task_prompt_template.format(
                    user_query=user_query,
                    webpage_title=webpage_title,
                    webpage_content=webpage_content[:8000],
                    webpage_url=webpage_url,
                    max_records=max_records,
                )
            except Exception as e:
                logger.warning(f"Failed to load PT prompt, using default: {e}")
                system_prompt = _get_default_pt_system_prompt()
                task_prompt = _get_default_pt_task_prompt(user_query, webpage_title, webpage_content, webpage_url, max_records)
        else:  # SFT
            try:
                system_prompt = prompt_loader("system", "webpage_dataset_sft_prompt")
                task_prompt_template = prompt_loader("task", "webpage_dataset_sft_prompt")
                task_prompt = task_prompt_template.format(
                    user_query=user_query,
                    webpage_title=webpage_title,
                    webpage_content=webpage_content[:8000],
                    webpage_url=webpage_url,
                    max_records=max_records,
                )
            except Exception as e:
                logger.warning(f"Failed to load SFT prompt, using default: {e}")
                system_prompt = _get_default_sft_system_prompt()
                task_prompt = _get_default_sft_task_prompt(user_query, webpage_title, webpage_content, webpage_url, max_records)
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_prompt),
        ]
        
        # Invoke LLM
        response = await llm.ainvoke(messages)
        response_content = response.content if hasattr(response, 'content') else str(response)
        
        # Parse JSON response
        clean_response = response_content.strip().replace("```json", "").replace("```", "")
        
        try:
            result = json.loads(clean_response)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise
        
        # Extract records and reason from model response
        records = []
        model_reason = ""
        
        # Check if result is in new format with "records" and "reason" keys
        if isinstance(result, dict) and "records" in result:
            records = result.get("records", [])
            model_reason = result.get("reason", "")
        elif isinstance(result, list):
            # Legacy format: direct list of records
            records = result
        elif category == "PT" and isinstance(result, dict) and "text" in result:
            # Single PT record
            records = [result]
        elif category == "SFT" and isinstance(result, dict) and "messages" in result:
            # Single SFT record
            records = [result]
        else:
            logger.warning(f"Unexpected {category} result format: {type(result)}")
            return {
                "records": [],
                "reason": f"Unexpected {category} result format: {type(result)}. Expected dict with 'records' key or list of records.",
            }
        
        # Filter by relevance and validate
        valid_records = []
        filtered_count = 0
        for record in records:
            # Check relevance score if present
            if "relevance_score" in record:
                if record["relevance_score"] < min_relevance_score:
                    filtered_count += 1
                    continue
            
            # Validate record structure
            if category == "PT":
                if "text" in record and record["text"]:
                    valid_records.append(record)
                else:
                    filtered_count += 1
            else:  # SFT
                if "messages" in record and record["messages"] and len(record["messages"]) > 0:
                    valid_records.append(record)
                else:
                    filtered_count += 1
        
        # Add metadata to each record
        for record in valid_records:
            if "meta" not in record:
                record["meta"] = {}
            record["meta"]["source"] = record["meta"].get("source", webpage_url)
            record["meta"]["webpage_title"] = webpage_title
            record["meta"]["webpage_url"] = webpage_url
            record["meta"]["generated_at"] = datetime.utcnow().isoformat() + "Z"
        
        final_records = valid_records[:max_records]
        
        # Use model's reason if provided, otherwise generate default reason
        reason = model_reason
        if not final_records:
            if model_reason:
                # Use model's reason if provided
                reason = model_reason
            elif not records:
                reason = "LLM returned no records. The webpage content may not be relevant to the user's objective."
            elif filtered_count > 0:
                reason = f"All {len(records)} generated records were filtered out due to low relevance (score < {min_relevance_score}) or invalid structure."
            else:
                reason = "No valid records could be extracted from the generated data."
        
        return {
            "records": final_records,
            "reason": reason,
        }
        
    except Exception as e:
        logger.error(f"Error generating dataset records: {e}", exc_info=True)
        return {
            "records": [],
            "reason": f"Error during record generation: {str(e)}",
        }


def _get_default_pt_system_prompt() -> str:
    """Default system prompt for PT dataset generation"""
    return """You are a data extraction expert for Pre-training (PT) datasets. Your task is to extract structured text data from webpage content that is highly relevant to the user's objective.

You must return data in the following JSON Schema format:

{
  "text": "string | array<string> | null",
  "meta": {
    "source": "string | null",
    "language": "string | null",
    "timestamp": "string | null",
    "token_count": "string | null",
    "quality_score": "string | null",
    "original_id": "string | null"
  }
}

Key requirements:
1. **High Relevance**: Only extract content that is highly relevant to the user's objective. If content is not relevant, return an empty list.
2. **Text Extraction**: Extract continuous, coherent text suitable for language model pre-training.
3. **Multiple Records**: You can extract multiple records from a single webpage if it contains multiple relevant sections.
4. **Field Paths**: For meta fields, you can use field paths or direct string values.
5. **Quality**: Prioritize high-quality, well-structured text content."""


def _get_default_pt_task_prompt(
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    max_records: int,
) -> str:
    """Default task prompt for PT dataset generation"""
    return f"""User Objective: {user_query}

Webpage Information:
- Title: {webpage_title}
- URL: {webpage_url}
- Content (first 8000 chars): {webpage_content[:8000]}

Task: Extract up to {max_records} high-quality text records from this webpage that are highly relevant to the user's objective.

Requirements:
1. Extract only content that is DIRECTLY relevant to: {user_query}
2. Each record should contain continuous, coherent text suitable for pre-training
3. If the webpage contains multiple relevant sections, create separate records for each
4. If content is not relevant, return an empty array: []
5. Include metadata (source, language, etc.) when available

Return a JSON object with the following structure:
{{
  "records": [
    {{
      "text": "extracted text content (string, not field path)",
      "meta": {{
        "source": "{webpage_url}",
        "language": "detected language code (zh/en/mix) or null",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "Explanation of why records were or were not generated. If records array is empty, this field is REQUIRED."
}}

If no relevant content found, return: {{"records": [], "reason": "详细说明为什么没有找到相关内容"}}"""


def _get_default_sft_system_prompt() -> str:
    """Default system prompt for SFT dataset generation"""
    return """You are a data extraction expert for Supervised Fine-Tuning (SFT) datasets. Your task is to extract question-answer pairs or instruction-following data from webpage content that is highly relevant to the user's objective.

You must return data in the following JSON Schema format:

{
  "messages": [
    {{
      "role": "user | assistant | system | tool",
      "content": "string | array<string> | null",
      "loss_mask": true | false | null
    }}
  ],
  "system": "string | null",
  "meta": {{
    "source": "string | null",
    "language": "string | null",
    "timestamp": "string | null",
    "token_count": "string | null",
    "quality_score": "string | null",
    "original_id": "string | null"
  }}
}

Key requirements:
1. **High Relevance**: Only extract content that is highly relevant to the user's objective. If content is not relevant, return an empty list.
2. **Message Structure**: Create proper message sequences with user/assistant roles.
3. **Multiple Records**: You can extract multiple records from a single webpage if it contains multiple relevant Q&A pairs or instruction examples.
4. **Quality**: Prioritize high-quality, well-structured instruction-following content."""


def _get_default_sft_task_prompt(
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    max_records: int,
) -> str:
    """Default task prompt for SFT dataset generation"""
    return f"""User Objective: {user_query}

Webpage Information:
- Title: {webpage_title}
- URL: {webpage_url}
- Content (first 8000 chars): {webpage_content[:8000]}

Task: Extract up to {max_records} high-quality instruction-following records (question-answer pairs or instruction-response pairs) from this webpage that are highly relevant to the user's objective.

Requirements:
1. Extract only content that is DIRECTLY relevant to: {user_query}
2. Each record should contain a proper message sequence (user question/instruction → assistant response)
3. If the webpage contains multiple relevant Q&A pairs, create separate records for each
4. If content is not relevant, return an empty array: []
5. Include metadata (source, language, etc.) when available

Return a JSON object with the following structure:
{{
  "records": [
    {{
      "messages": [
        {{
          "role": "user",
          "content": "question or instruction (actual text, not field path)",
          "loss_mask": false
        }},
        {{
          "role": "assistant",
          "content": "answer or response (actual text, not field path)",
          "loss_mask": true
        }}
      ],
      "system": null,
      "meta": {{
        "source": "{webpage_url}",
        "language": "detected language code (zh/en/mix) or null",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "Explanation of why records were or were not generated. If records array is empty, this field is REQUIRED."
}}

If no relevant content found, return: {{"records": [], "reason": "详细说明为什么没有找到相关内容"}}"""

