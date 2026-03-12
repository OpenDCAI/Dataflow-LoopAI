import json
import asyncio
import os
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime, timezone

from langgraph.config import get_stream_writer

from loopai.agents.Constructor.mapping.script_mapping_node import script_mapping_node
from loopai.agents.WebCrawler.utils.dataset_generator import (
    generate_sft_records,
    generate_pt_records,
    generate_webpage_summary_and_relevance,
)
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
    writer = get_stream_writer()
    writer(StreamEvent(
        current=state['current'],
        message="开始执行数据集构建任务",
        progress=0
    ).json())
    
    
    # Check if there's an exception from previous node
    if state.get("exception"):
        logger.error(f"Skipping due to previous exception: {state['exception']}")
        return state
    
    # 获取 webcrawler 配置
    webcrawler = state.get("webcrawler", {}) or {}
    
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
        # Get configuration from webcrawler dict, with fallback to analyzer config
        analyzer = state.get("analyzer", {}) or {}
        model_name = webcrawler.get("model") or analyzer.get("analyze_model_path")
        base_url = webcrawler.get("deepseek_api_base") or analyzer.get("analyze_base_url")
        api_key = webcrawler.get("deepseek_api_key") or analyzer.get("analyze_api_key")
        temperature = webcrawler.get("temperature", 0.7)
        
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
        
        # Get WebCrawler output data from webcrawler dict
        webcrawler_result = webcrawler.get("output_result", {})
        crawled_data = webcrawler_result.get("crawled_data", [])
        
        if not crawled_data:
            logger.warning("No crawled data found from WebCrawler")
            state["exception"] = "No crawled data available. Please run crawl_node first."
            return state
        
        logger.info(f"Found {len(crawled_data)} crawled pages to process")
        
        # Run async workflow
        debug_mode = webcrawler.get("debug", False)
        result = asyncio.run(_webcrawler_dataset_workflow(
            user_query=user_query,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            output_dir=dataset_dir,
            crawled_data=crawled_data,
            max_records_per_page=webcrawler.get("max_records_per_page", 100),
            min_relevance_score=webcrawler.get("min_relevance_score", 0.6),
            dataset_concurrent_limit=webcrawler.get("dataset_concurrent_limit", 50),
            max_content_length=webcrawler.get("max_content_length", 50000),
            debug_mode=debug_mode,
        ))
        
        # Update state with intermediate-format results (store in webcrawler dict)
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            webcrawler["dataset_summary"] = result.get("summary", "")
            webcrawler["dataset_sft_count"] = result.get("sft_count", 0)
            webcrawler["dataset_pt_count"] = result.get("pt_count", 0)
            webcrawler["dataset_sft_path"] = result.get("sft_jsonl_path", "")
            webcrawler["dataset_pt_path"] = result.get("pt_jsonl_path", "")
            logger.info(
                f"WebCrawler Dataset completed: {result.get('sft_count', 0)} SFT records, "
                f"{result.get('pt_count', 0)} PT records generated"
            )

            # === Use Constructor mapping (script_mapping_node) to convert intermediate SFT/PT to final dataset formats ===
            try:
                mapping_results = {}

                # SFT -> dataset (default format: jsonl_sft)
                sft_path = result.get("sft_jsonl_path") or ""
                if sft_path:
                    sft_format = webcrawler.get("sft_mapping_format", "jsonl_sft")
                    logger.info(
                        f"Running script_mapping_node for WebCrawler SFT data: "
                        f"path={sft_path}, format={sft_format}"
                    )
                    # 确保 constructor 字典存在
                    if "constructor" not in state:
                        state["constructor"] = {}
                    state["constructor"]["intermediate_data_path"] = sft_path
                    state["constructor"]["category"] = "SFT"
                    state["constructor"]["confirmed_format"] = {
                        "format_id": sft_format,
                        "format_name": sft_format,
                        "description": "Auto-selected by WebCrawler for SFT mapping",
                        "schema": {},
                        "example": {},
                        "is_preset": True,
                    }
                    state = script_mapping_node(state)

                    if state.get("constructor", {}).get("mapping_results"):
                        sft_mapping = dict(state["constructor"]["mapping_results"])
                        mapping_results["sft"] = sft_mapping
                        webcrawler["dataset_sft_mapped_path"] = sft_mapping.get(
                            "output_file", ""
                        )

                # PT -> dataset (default format: jsonl_pt)
                pt_path = result.get("pt_jsonl_path") or ""
                if pt_path:
                    pt_format = webcrawler.get("pt_mapping_format", "jsonl_pt")
                    logger.info(
                        f"Running script_mapping_node for WebCrawler PT data: "
                        f"path={pt_path}, format={pt_format}"
                    )
                    state["constructor"]["intermediate_data_path"] = pt_path
                    state["constructor"]["category"] = "PT"
                    state["constructor"]["confirmed_format"] = {
                        "format_id": pt_format,
                        "format_name": pt_format,
                        "description": "Auto-selected by WebCrawler for PT mapping",
                        "schema": {},
                        "example": {},
                        "is_preset": True,
                    }
                    state = script_mapping_node(state)

                    if state.get("constructor", {}).get("mapping_results"):
                        pt_mapping = dict(state["constructor"]["mapping_results"])
                        mapping_results["pt"] = pt_mapping
                        webcrawler["dataset_pt_mapped_path"] = pt_mapping.get(
                            "output_file", ""
                        )

                if mapping_results:
                    webcrawler["dataset_mapping_results"] = mapping_results

            except Exception as map_err:
                logger.error(f"Error when mapping WebCrawler dataset via Constructor: {map_err}", exc_info=True)
        
        writer(StreamEvent(
            current=state['current'],
            message="数据集收集完成",
            progress=1,
            data={
                'user_query': user_query,
                'sft_count': result.get("sft_count", 0),
                'pt_count': result.get("pt_count", 0),
                'has_exception': "exception" in result,
            }
        ).json())
        
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
    1. For each webpage, directly generate SFT/PT from markdown content
    2. For webpages that generated SFT records, generate summary and relevance score
    3. Save as JSONL in intermediate format
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
        webpages_with_sft = []  # 记录生成了SFT的网页，后续需要生成摘要
        
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
                    
                    # 步骤1: 先尝试生成SFT（如果有代码块）
                    sft_records = []
                    if code_blocks and len(code_blocks) > 0:
                        logger.info(f"Found {len(code_blocks)} code blocks, attempting SFT generation")
                        sft_result = await generate_sft_records(
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
                    
                    # 步骤2: 如果没有生成SFT，生成PT
                    pt_records = []
                    if not sft_records:
                        logger.info(f"Generating PT format for webpage {index}")
                        pt_result = await generate_pt_records(
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
                    
                    return {
                        "sft_records": sft_records, 
                        "pt_records": pt_records,
                        "webpage_info": {
                            "url": url,
                            "title": title,
                            "content": content_preview,
                            "has_sft": len(sft_records) > 0
                        }
                    }
                    
                except Exception as e:
                    logger.error(f"Error processing webpage {index}: {e}")
                    return {"sft_records": [], "pt_records": []}
        
        # Process all webpages concurrently
        logger.info(f"Processing {len(crawled_data)} webpages with {dataset_concurrent_limit} concurrent workers...")
        dataset_tasks = [process_webpage(webpage, i+1) for i, webpage in enumerate(crawled_data)]
        dataset_results = await asyncio.gather(*dataset_tasks, return_exceptions=True)
        
        # Collect all records and track webpages with SFT
        for result in dataset_results:
            if isinstance(result, Exception):
                logger.error(f"Exception in dataset generation: {result}")
                continue
            if result:
                all_sft_records.extend(result.get("sft_records", []))
                all_pt_records.extend(result.get("pt_records", []))
                # 记录生成了SFT的网页
                webpage_info = result.get("webpage_info", {})
                if webpage_info.get("has_sft", False):
                    webpages_with_sft.append(webpage_info)

        # 步骤3: 对生成了SFT的网页，生成摘要和相关性评分 
        if webpages_with_sft:
            logger.info(f"\n开始为 {len(webpages_with_sft)} 个生成了SFT的网页生成摘要和相关性评分...")
            
            async def generate_summary_for_webpage(webpage_info: Dict[str, Any]) -> Dict[str, Any]:
                """为单个网页生成摘要和相关性评分"""
                try:
                    url = webpage_info.get("url", "")
                    title = webpage_info.get("title", "")
                    content = webpage_info.get("content", "")
                    
                    logger.info(f"  生成摘要: {url}")
                    
                    # 生成摘要和相关性评分
                    summary_result = await generate_webpage_summary_and_relevance(
                        llm=llm,
                        user_query=user_query,
                        webpage_title=title,
                        webpage_content=content,
                        webpage_url=url,
                    )
                    
                    return {
                        "url": url,
                        "title": title,
                        "summary": summary_result.get("summary", ""),
                        "relevance_score": summary_result.get("relevance_score", 0),
                    }
                except Exception as e:
                    logger.error(f"  生成摘要失败 {url}: {e}")
                    return {
                        "url": url,
                        "title": title,
                        "summary": "摘要生成失败",
                        "relevance_score": 0,
                    }
            
            # 并发生成摘要
            summary_tasks = [generate_summary_for_webpage(info) for info in webpages_with_sft]
            webpage_summaries = await asyncio.gather(*summary_tasks, return_exceptions=True)
            
            # 记录摘要结果
            valid_summaries = [s for s in webpage_summaries if not isinstance(s, Exception)]
            logger.info(f"成功生成 {len(valid_summaries)} 个网页摘要")
            
            # 保存摘要到输出目录
            if valid_summaries:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                summary_file = os.path.join(output_dir, f"webpage_summaries_{timestamp}.jsonl")
                with open(summary_file, 'w', encoding='utf-8') as f:
                    for summary in valid_summaries:
                        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
                logger.info(f"网页摘要已保存至: {summary_file}")
        
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
