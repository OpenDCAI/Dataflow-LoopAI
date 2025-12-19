import json
import asyncio
import os
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime, timezone

from langgraph.config import get_stream_writer

from loopai.agents.Obtainer.mapping.script_mapping_node import script_mapping_node
from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

logger = get_logger()


def webcrawler_dataset_node(state: LoopAIState) -> LoopAIState:
    """
    WebCrawler Dataset Node that:
    1. Reads WebCrawler collected content
    2. Uses LLM to extract and structure data according to PT/SFT schema
    3. For code blocks: generate SFT format (question-code pairs)
    4. If no SFT can be generated: fallback to PT format (markdown content)
    5. Saves structured data as JSONL in intermediate format
    """
    logger.info("=== WebCrawler Dataset Node: Starting ===")
    
    # Check if there's an exception from previous node
    if state.get("exception"):
        logger.error(f"Skipping due to previous exception: {state['exception']}")
        return state
    
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
        user_query = "收集相关技术内容和代码示例"
    
    logger.info(f"User query: {user_query}")
    
    # Initialize components
    try:
        # Get configuration from state or use defaults
        model_name = state.get("webcrawler_model") or state.get("analyze_model_path")
        base_url = state.get("webcrawler_deepseek_api_base") or state.get("analyze_base_url")
        api_key = state.get("webcrawler_deepseek_api_key") or state.get("analyze_api_key")
        temperature = state.get("webcrawler_temperature", 0.7)
        
        if not model_name or not base_url or not api_key:
            logger.error("Missing required configuration for webcrawler dataset node")
            state["exception"] = "Missing model configuration (model_name, base_url, api_key)"
            return state
        
        # Initialize prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # Output directory
        output_dir = state.get("output_dir", "./output")
        dataset_dir = os.path.join(output_dir, "webcrawler_dataset")
        os.makedirs(dataset_dir, exist_ok=True)
        
        # Get WebCrawler output data
        webcrawler_result = state.get("webcrawler_output_result", {})
        crawled_data = webcrawler_result.get("crawled_data", [])
        
        if not crawled_data:
            logger.warning("No crawled data found from WebCrawler")
            state["exception"] = "No crawled data available. Please run crawl_node first."
            return state
        
        logger.info(f"Found {len(crawled_data)} crawled pages to process")
        
        # Run async workflow
        debug_mode = state.get("webcrawler_debug", False)
        result = asyncio.run(_webcrawler_dataset_workflow(
            user_query=user_query,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            output_dir=dataset_dir,
            crawled_data=crawled_data,
            max_records_per_page=state.get("webcrawler_max_records_per_page", 100),
            min_relevance_score=state.get("webcrawler_min_relevance_score", 0.6),
            dataset_concurrent_limit=state.get("webcrawler_dataset_concurrent_limit", 50),
            max_content_length=state.get("webcrawler_max_content_length", 50000),
            debug_mode=debug_mode,
        ))
        
        # Update state with intermediate-format results
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            state["webcrawler_dataset_summary"] = result.get("summary", "")
            state["webcrawler_dataset_sft_count"] = result.get("sft_count", 0)
            state["webcrawler_dataset_pt_count"] = result.get("pt_count", 0)
            state["webcrawler_dataset_sft_path"] = result.get("sft_jsonl_path", "")
            state["webcrawler_dataset_pt_path"] = result.get("pt_jsonl_path", "")
            logger.info(
                f"WebCrawler Dataset completed: {result.get('sft_count', 0)} SFT records, "
                f"{result.get('pt_count', 0)} PT records generated"
            )

            # === Use Obtainer mapping (script_mapping_node) to convert intermediate SFT/PT to final dataset formats ===
            try:
                mapping_results = {}

                # SFT -> dataset (default format: jsonl_sft)
                sft_path = result.get("sft_jsonl_path") or ""
                if sft_path:
                    sft_format = state.get("webcrawler_sft_mapping_format", "jsonl_sft")
                    logger.info(
                        f"Running script_mapping_node for WebCrawler SFT data: "
                        f"path={sft_path}, format={sft_format}"
                    )
                    state["obtainer_intermediate_data_path"] = sft_path
                    state["obtainer_category"] = "SFT"
                    state["obtainer_confirmed_format"] = {
                        "format_id": sft_format,
                        "format_name": sft_format,
                        "description": "Auto-selected by WebCrawler for SFT mapping",
                        "schema": {},
                        "example": {},
                        "is_preset": True,
                    }
                    state = script_mapping_node(state)

                    if state.get("obtainer_mapping_results"):
                        sft_mapping = dict(state["obtainer_mapping_results"])
                        mapping_results["sft"] = sft_mapping
                        state["webcrawler_dataset_sft_mapped_path"] = sft_mapping.get(
                            "output_file", ""
                        )

                # PT -> dataset (default format: jsonl_pt)
                pt_path = result.get("pt_jsonl_path") or ""
                if pt_path:
                    pt_format = state.get("webcrawler_pt_mapping_format", "jsonl_pt")
                    logger.info(
                        f"Running script_mapping_node for WebCrawler PT data: "
                        f"path={pt_path}, format={pt_format}"
                    )
                    state["obtainer_intermediate_data_path"] = pt_path
                    state["obtainer_category"] = "PT"
                    state["obtainer_confirmed_format"] = {
                        "format_id": pt_format,
                        "format_name": pt_format,
                        "description": "Auto-selected by WebCrawler for PT mapping",
                        "schema": {},
                        "example": {},
                        "is_preset": True,
                    }
                    state = script_mapping_node(state)

                    if state.get("obtainer_mapping_results"):
                        pt_mapping = dict(state["obtainer_mapping_results"])
                        mapping_results["pt"] = pt_mapping
                        state["webcrawler_dataset_pt_mapped_path"] = pt_mapping.get(
                            "output_file", ""
                        )

                if mapping_results:
                    state["webcrawler_dataset_mapping_results"] = mapping_results

            except Exception as map_err:
                logger.error(f"Error when mapping WebCrawler dataset via Obtainer: {map_err}", exc_info=True)
        
        # Send custom stream event if debug mode is enabled
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current=state.get('current', 'webcrawler_dataset_node'),
                        message="WebCrawler Dataset node completed",
                        data={
                            'user_query': user_query,
                            'sft_count': result.get("sft_count", 0),
                            'pt_count': result.get("pt_count", 0),
                            'has_exception': "exception" in result,
                        }
                    ).json())
            except Exception as e:
                logger.debug(f"Could not send stream event: {e}")
        
    except Exception as e:
        logger.error(f"WebCrawler Dataset node error: {e}", exc_info=True)
        state["exception"] = f"WebCrawler Dataset error: {str(e)}"
    
    logger.info("=== WebCrawler Dataset Node: Completed ===")
    return state


async def _webcrawler_dataset_workflow(
    user_query: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: PromptLoader,
    output_dir: str,
    crawled_data: List[Dict[str, Any]],
    max_records_per_page: int = 10,
    min_relevance_score: float = 0.6,
    dataset_concurrent_limit: int = 5,
    max_content_length: int = 50000,
    debug_mode: bool = False,
) -> Dict[str, Any]:
    """
    Main workflow for generating dataset from WebCrawler content
    
    Steps:
    1. For each webpage, check if it has code blocks
    2. If has code blocks: Try to generate SFT format (question-code pairs)
    3. If SFT generation fails or no code blocks: Generate PT format (markdown content)
    4. Save as JSONL in intermediate format
    """
    try:
        # Initialize LLM
        llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        
        logger.info(f"Processing {len(crawled_data)} webpages to generate intermediate format dataset")
        
        # Concurrent limit for dataset generation
        dataset_semaphore = asyncio.Semaphore(dataset_concurrent_limit)
        all_sft_records = []
        all_pt_records = []
        
        async def process_webpage(webpage: Dict[str, Any], index: int) -> Dict[str, Any]:
            """Process a single webpage to generate dataset records"""
            async with dataset_semaphore:
                try:
                    logger.info(f"Processing webpage {index}/{len(crawled_data)}: {webpage.get('url', 'N/A')}")
                    
                    # Extract content
                    content = webpage.get("content", "")
                    title = webpage.get("title", "")
                    url = webpage.get("url", "")
                    code_blocks = webpage.get("code_blocks", [])
                    
                    if not content or len(content.strip()) < 100:
                        logger.warning(f"Skipping webpage with insufficient content: {url}")
                        return {"sft_records": [], "pt_records": []}
                    
                    # Limit content length for LLM
                    content_preview = content[:max_content_length] if len(content) > max_content_length else content
                    
                    # Try to generate SFT records if there are code blocks
                    sft_records = []
                    if code_blocks and len(code_blocks) > 0:
                        logger.info(f"Found {len(code_blocks)} code blocks, attempting SFT generation")
                        sft_result = await _generate_sft_records(
                            llm=llm,
                            prompt_loader=prompt_loader,
                            user_query=user_query,
                            webpage_title=title,
                            webpage_content=content_preview,
                            webpage_url=url,
                            code_blocks=code_blocks,
                            max_records=max_records_per_page,
                            min_relevance_score=min_relevance_score,
                            max_content_length=max_content_length,
                        )
                        sft_records = sft_result.get("records", [])
                        reason = sft_result.get("reason", "")
                        
                        if sft_records:
                            logger.info(f"Generated {len(sft_records)} SFT records from webpage {index}")
                        else:
                            logger.warning(f"Failed to generate SFT records: {reason}")
                    
                    # If no SFT records generated, fallback to PT format
                    pt_records = []
                    if not sft_records:
                        logger.info(f"Generating PT format for webpage {index}")
                        pt_result = await _generate_pt_records(
                            llm=llm,
                            prompt_loader=prompt_loader,
                            user_query=user_query,
                            webpage_title=title,
                            webpage_content=content_preview,
                            webpage_url=url,
                            max_records=max_records_per_page,
                            min_relevance_score=min_relevance_score,
                            max_content_length=max_content_length,
                        )
                        pt_records = pt_result.get("records", [])
                        reason = pt_result.get("reason", "")
                        
                        if pt_records:
                            logger.info(f"Generated {len(pt_records)} PT records from webpage {index}")
                        else:
                            logger.warning(f"Failed to generate PT records: {reason}")
                    
                    return {"sft_records": sft_records, "pt_records": pt_records}
                    
                except Exception as e:
                    logger.error(f"Error processing webpage {index}: {e}")
                    return {"sft_records": [], "pt_records": []}
        
        # Process all webpages concurrently
        logger.info(f"Processing {len(crawled_data)} webpages with {dataset_concurrent_limit} concurrent workers...")
        dataset_tasks = [process_webpage(webpage, i+1) for i, webpage in enumerate(crawled_data)]
        dataset_results = await asyncio.gather(*dataset_tasks, return_exceptions=True)
        
        # Collect all records
        for result in dataset_results:
            if isinstance(result, Exception):
                logger.error(f"Exception in dataset generation: {result}")
                continue
            if result:
                all_sft_records.extend(result.get("sft_records", []))
                all_pt_records.extend(result.get("pt_records", []))

        # === Post-process SFT records ===
        # Requirement: after SFT generation is complete, remove fields whose value is null,
        # specifically the top-level "system" field when it is null,
        # but DO NOT drop the whole record (to avoid losing valid SFT samples).
        if all_sft_records:
            removed_system_null_count = 0
            for record in all_sft_records:
                # Only touch top-level "system" key
                if "system" in record and record["system"] is None:
                    removed_system_null_count += 1
                    del record["system"]

            if removed_system_null_count > 0:
                logger.info(
                    f"Removed null 'system' field from {removed_system_null_count} SFT records"
                )

        # Save to JSONL files
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        sft_jsonl_path = ""
        if all_sft_records:
            sft_jsonl_filename = f"webcrawler_dataset_sft_{timestamp}.jsonl"
            sft_jsonl_path = os.path.join(output_dir, sft_jsonl_filename)
            with open(sft_jsonl_path, 'w', encoding='utf-8') as f:
                for record in all_sft_records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"Saved {len(all_sft_records)} SFT records to {sft_jsonl_path}")
        
        pt_jsonl_path = ""
        if all_pt_records:
            pt_jsonl_filename = f"webcrawler_dataset_pt_{timestamp}.jsonl"
            pt_jsonl_path = os.path.join(output_dir, pt_jsonl_filename)
            with open(pt_jsonl_path, 'w', encoding='utf-8') as f:
                for record in all_pt_records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"Saved {len(all_pt_records)} PT records to {pt_jsonl_path}")
        
        summary = (
            f"Generated {len(all_sft_records)} SFT records and {len(all_pt_records)} PT records "
            f"from {len(crawled_data)} webpages in intermediate format."
        )
        
        return {
            "summary": summary,
            "sft_count": len(all_sft_records),
            "pt_count": len(all_pt_records),
            "sft_jsonl_path": sft_jsonl_path,
            "pt_jsonl_path": pt_jsonl_path,
        }
        
    except Exception as e:
        logger.error(f"WebCrawler Dataset workflow error: {e}", exc_info=True)
        return {
            "exception": f"WebCrawler Dataset workflow error: {str(e)}",
            "summary": "",
            "sft_count": 0,
            "pt_count": 0,
            "sft_jsonl_path": "",
            "pt_jsonl_path": "",
        }


async def _generate_sft_records(
    llm: ChatOpenAI,
    prompt_loader: PromptLoader,
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    code_blocks: List[Dict[str, str]],
    max_records: int = 10,
    min_relevance_score: float = 0.6,
    max_content_length: int = 50000,
) -> Dict[str, Any]:
    """
    Generate SFT records from webpage code blocks using LLM
    
    For each code block, generate:
    - user message: description of what the code does (question)
    - assistant message: the code block itself (answer)
    
    Returns dict with:
    - records: List of SFT records in intermediate format
    - reason: Reason string if no records generated
    """
    try:
        # Get prompt
        try:
            system_prompt = prompt_loader("system", "webcrawler_dataset_sft_prompt")
            task_prompt_template = prompt_loader("task", "webcrawler_dataset_sft_prompt")
            
            # Format code blocks info for prompt
            code_blocks_info = []
            for i, block in enumerate(code_blocks[:max_records], 1):
                code_preview = block.get("code", "")[:500] if len(block.get("code", "")) > 500 else block.get("code", "")
                code_blocks_info.append(f"代码块 {i} ({block.get('language', 'unknown')}):\n```{block.get('language', '')}\n{code_preview}\n```")
            
            task_prompt = task_prompt_template.format(
                user_query=user_query,
                webpage_title=webpage_title,
                webpage_url=webpage_url,
                code_blocks_info="\n\n".join(code_blocks_info),
                max_records=max_records,
            )
        except Exception as e:
            logger.warning(f"Failed to load SFT prompt, using default: {e}")
            system_prompt = _get_default_sft_system_prompt()
            task_prompt = _get_default_sft_task_prompt(user_query, webpage_title, webpage_url, code_blocks, max_records)
        
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
        
        # Extract records and reason
        records = []
        model_reason = ""
        
        if isinstance(result, dict) and "records" in result:
            records = result.get("records", [])
            model_reason = result.get("reason", "")
        elif isinstance(result, list):
            records = result
        else:
            logger.warning(f"Unexpected SFT result format: {type(result)}")
            return {
                "records": [],
                "reason": f"Unexpected result format: {type(result)}",
            }
        
        # Filter by relevance and validate
        valid_records = []
        for record in records:
            # Check relevance score
            if "relevance_score" in record:
                if record["relevance_score"] < min_relevance_score:
                    continue
            
            # Validate SFT structure
            if "messages" in record and record["messages"] and len(record["messages"]) > 0:
                valid_records.append(record)
        
        # Add metadata to each record
        for record in valid_records:
            if "meta" not in record:
                record["meta"] = {}
            record["meta"]["source"] = record["meta"].get("source", webpage_url)
            record["meta"]["webpage_title"] = webpage_title
            record["meta"]["webpage_url"] = webpage_url
            record["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        final_records = valid_records[:max_records]
        
        reason = model_reason
        if not final_records and not model_reason:
            reason = "Failed to generate valid SFT records from code blocks"
        
        return {
            "records": final_records,
            "reason": reason,
        }
        
    except Exception as e:
        logger.error(f"Error generating SFT records: {e}", exc_info=True)
        return {
            "records": [],
            "reason": f"Error during SFT generation: {str(e)}",
        }


async def _generate_pt_records(
    llm: ChatOpenAI,
    prompt_loader: PromptLoader,
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    max_records: int = 10,
    min_relevance_score: float = 0.6,
    max_content_length: int = 50000,
) -> Dict[str, Any]:
    """
    Generate PT records from webpage markdown content using LLM
    
    Returns dict with:
    - records: List of PT records in intermediate format
    - reason: Reason string if no records generated
    """
    try:
        # Get prompt
        try:
            system_prompt = prompt_loader("system", "webcrawler_dataset_pt_prompt")
            task_prompt_template = prompt_loader("task", "webcrawler_dataset_pt_prompt")
            task_prompt = task_prompt_template.format(
                user_query=user_query,
                webpage_title=webpage_title,
                webpage_content=webpage_content[:max_content_length],  
                webpage_url=webpage_url,
                max_records=max_records,
            )
        except Exception as e:
            logger.warning(f"Failed to load PT prompt, using default: {e}")
            system_prompt = _get_default_pt_system_prompt()
            task_prompt = _get_default_pt_task_prompt(user_query, webpage_title, webpage_content, webpage_url, max_records, max_content_length)
        
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
        
        # Extract records and reason
        records = []
        model_reason = ""
        
        if isinstance(result, dict) and "records" in result:
            records = result.get("records", [])
            model_reason = result.get("reason", "")
        elif isinstance(result, list):
            records = result
        elif isinstance(result, dict) and "text" in result:
            records = [result]
        else:
            logger.warning(f"Unexpected PT result format: {type(result)}")
            return {
                "records": [],
                "reason": f"Unexpected result format: {type(result)}",
            }
        
        # Filter by relevance and validate
        valid_records = []
        for record in records:
            # Check relevance score
            if "relevance_score" in record:
                if record["relevance_score"] < min_relevance_score:
                    continue
            
            # Validate PT structure
            if "text" in record and record["text"]:
                valid_records.append(record)
        
        # Add metadata to each record
        for record in valid_records:
            if "meta" not in record:
                record["meta"] = {}
            record["meta"]["source"] = record["meta"].get("source", webpage_url)
            record["meta"]["webpage_title"] = webpage_title
            record["meta"]["webpage_url"] = webpage_url
            record["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        final_records = valid_records[:max_records]
        
        reason = model_reason
        if not final_records and not model_reason:
            reason = "Failed to generate valid PT records from webpage content"
        
        return {
            "records": final_records,
            "reason": reason,
        }
        
    except Exception as e:
        logger.error(f"Error generating PT records: {e}", exc_info=True)
        return {
            "records": [],
            "reason": f"Error during PT generation: {str(e)}",
        }


def _get_default_sft_system_prompt() -> str:
    """Default system prompt for SFT dataset generation from code blocks"""
    return """你是一个数据提取专家，专门从网页代码块中生成高质量的 SFT（监督微调）训练数据。

你的任务是分析网页中的代码块，为每个代码块生成一个问答对：
- system 消息（仅对text2sql任务有效）：根据SQL语句推断并生成对应的数据库Schema定义
- user 消息：对代码功能的描述（例如："生成能够实现XXX功能的Python代码"）
- assistant 消息：代码块本身

特殊处理 - SQL/数据库相关代码：
对于SQL查询、数据库操作等代码，你需要：
1. 分析SQL语句中涉及的表、字段、关联关系
2. 根据SQL语句推断并构造合理的数据库Schema，包括：
   - 语义直观的表名和字段命名
   - 显式定义的主键（PRIMARY KEY）
   - 外键关联关系（FOREIGN KEY REFERENCES）
   - 字段类型和注释说明
3. 将Schema放入 system 角色的 content 中

Schema示例格式：
```sql
-- Table storing user profile information
CREATE TABLE users (
    user_id INT PRIMARY KEY,
    full_name VARCHAR(50),
    -- Account status: 1=Active, 0=Inactive, -1=Banned
    account_status INT 
);

-- Table storing transaction history
CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    user_id INT,
    total_amount DECIMAL(10, 2), -- Amount in USD
    created_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

输出格式必须符合以下中间态 JSON Schema：

{
  "messages": [
    {
      "role": "user",
      "content": "string",
      "loss_mask": false
    },
    {
      "role": "assistant",
      "content": "string",
      "loss_mask": true
    },
        {
      "role": "system",
      "content": "数据库Schema定义（仅SQL类代码需要）",
      "loss_mask": false
    }
  ],
  "system": "string | null",
  "meta": {
    "source": "string | null",
    "language": "string | null",
    "timestamp": "string | null",
    "token_count": "string | null",
    "quality_score": "string | null",
    "original_id": "string | null"
  }
}

关键要求：
1. **高相关性**：只提取与用户目标高度相关的代码块
2. **准确的功能描述**：user 消息应准确描述代码的功能和用途
3. **完整的代码**：assistant 消息应包含完整的代码块
4. **SQL专项处理**：对于SQL代码，必须在system消息中提供推断出的数据库Schema
5. **多条记录**：一个网页如果有多个代码块，可以生成多条记录
6. **质量优先**：优先处理高质量、有实用价值的代码示例"""


def _get_default_sft_task_prompt(
    user_query: str,
    webpage_title: str,
    webpage_url: str,
    code_blocks: List[Dict[str, str]],
    max_records: int,
) -> str:
    """Default task prompt for SFT dataset generation"""
    # Format code blocks info
    code_blocks_info = []
    for i, block in enumerate(code_blocks[:max_records], 1):
        code_preview = block.get("code", "")[:500] if len(block.get("code", "")) > 500 else block.get("code", "")
        code_blocks_info.append(f"代码块 {i} ({block.get('language', 'unknown')}):\n```{block.get('language', '')}\n{code_preview}\n```")
    
    return f"""用户目标: {user_query}

网页信息:
- 标题: {webpage_title}
- URL: {webpage_url}

代码块信息:
{chr(10).join(code_blocks_info)}

任务: 从这些代码块中提取最多 {max_records} 个高质量的 SFT 训练样本。

要求:
1. 只提取与用户目标 "{user_query}" 直接相关的代码块
2. 为每个代码块生成一个问答对：
   - user 消息：描述代码的功能（例如："编写一个Python函数实现XXX功能"、"生成能够完成XXX任务的SQL语句"）
   - assistant 消息：完整的代码块
3. **SQL专项处理**：如果代码是SQL语句，必须额外添加一个 system 消息：
   - 分析SQL中涉及的所有表名、字段名、关联关系
   - **根据SQL推断并构造**合理的数据库Schema（CREATE TABLE语句）
   - Schema应包含：语义直观的命名、主键定义、外键关联、字段类型、必要的注释
   - 将Schema放入messages数组的第一个位置，role设为"system"
4. 如果代码块不相关或质量不高，可以跳过
5. loss_mask: system 和 user 消息设为 false, assistant 消息设为 true

返回 JSON 对象，格式如下：

**对于SQL类代码（必须包含system消息和推断的Schema）：**
{{
  "records": [
    {{
      "messages": [
        {{
          "role": "user",
          "content": "对SQL功能的描述",
          "loss_mask": false
        }},
        {{
          "role": "assistant",
          "content": "完整的SQL代码",
          "loss_mask": true
        }},
                {{
          "role": "system",
          "content": "根据SQL推断的数据库Schema，格式如下：\\n-- Table description\\nCREATE TABLE table_name (\\n    column_name DATA_TYPE PRIMARY KEY,\\n    ...\\n    FOREIGN KEY (col) REFERENCES other_table(col)\\n);",
          "loss_mask": false
        }}
      ],
      "system": null,
      "meta": {{
        "source": "{webpage_url}",
        "language": "sql",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "说明"
}}

**对于非SQL类代码（不需要system消息）：**
{{
  "records": [
    {{
      "messages": [
        {{
          "role": "user",
          "content": "对代码功能的描述",
          "loss_mask": false
        }},
        {{
          "role": "assistant",
          "content": "完整的代码块",
          "loss_mask": true
        }}
      ],
      "system": null,
      "meta": {{
        "source": "{webpage_url}",
        "language": "检测到的编程语言",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "说明"
}}

如果没有找到相关内容，返回: {{"records": [], "reason": "详细说明为什么没有找到相关代码"}}"""


def _get_default_pt_system_prompt() -> str:
    """Default system prompt for PT dataset generation"""
    return """你是一个数据提取专家，专门从网页内容中提取适合语言模型预训练（PT）的文本数据。

你的任务是从网页的 Markdown 内容中提取高质量、连贯的文本，用于预训练数据集。

输出格式必须符合以下中间态 JSON Schema：

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

关键要求：
1. **高相关性**：只提取与用户目标高度相关的内容
2. **文本提取**：提取连贯、完整的文本段落，适合语言模型预训练
3. **多条记录**：如果网页包含多个相关主题部分，可以拆分成多条记录
4. **质量优先**：优先提取结构良好、信息丰富的文本内容"""


def _get_default_pt_task_prompt(
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    max_records: int,
    max_content_length: int = 50000,
) -> str:
    """Default task prompt for PT dataset generation"""
    return f"""用户目标: {user_query}

网页信息:
- 标题: {webpage_title}
- URL: {webpage_url}
- 内容 (前 {max_content_length} 字符): {webpage_content[:max_content_length]}

任务: 从这个网页中提取最多 {max_records} 个高质量的 PT（预训练）文本记录。

要求:
1. 只提取与用户目标 "{user_query}" 直接相关的内容
2. 每条记录应包含连贯、完整的文本段落
3. 如果网页包含多个相关主题部分，可以拆分成多条记录
4. 如果内容不相关，返回空数组
5. 包含元数据（source、language 等）

返回 JSON 对象，格式如下：
{{
  "records": [
    {{
      "text": "提取的文本内容（实际文本，不是字段路径）",
      "meta": {{
        "source": "{webpage_url}",
        "language": "检测到的语言代码 (zh/en/mix) 或 null",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "生成或未生成记录的原因说明。如果 records 数组为空，此字段必填。"
}}

如果没有找到相关内容，返回: {{"records": [], "reason": "详细说明为什么没有找到相关内容"}}"""

