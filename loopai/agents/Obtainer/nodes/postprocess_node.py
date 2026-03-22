import asyncio
import os
import json
from typing import Dict, Any, List, Optional, Tuple, Set

from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.agents.Obtainer.utils.data_convertor import DataConvertor, _ensure_hf_cache_env
from loopai.common.prompts import PromptLoader

logger = get_logger()


def postprocess_node(state: LoopAIState) -> LoopAIState:
    """Post-process node that converts downloaded datasets to PT/SFT format"""
    logger.info("=== Post-process Node: Starting ===")
    
    # Check if there are any successful downloads
    subtasks = state.get("obtainer", {}).get("subtasks", [])
    successful_downloads = [
        task for task in subtasks 
        if task.get("type") == "download" and task.get("status") == "completed_successfully"
    ]
    
    # Get download directory - prioritize environment variable, then state, then default
    download_dir = os.getenv("DOWNLOAD_DIR")
    if not download_dir:
        # Try to get from state
        download_dir = state.get("download_dir")
        if not download_dir:
            # Fallback to output_dir/downloads
            output_dir = state.get("output_dir", "./output")
            download_dir = os.path.join(output_dir, "downloads")
    
    has_download_files = os.path.exists(download_dir) and any(
        os.path.isfile(os.path.join(download_dir, f)) or os.path.isdir(os.path.join(download_dir, f))
        for f in os.listdir(download_dir)
        if not f.startswith('.') and f not in ['processed_output', '.tmp', '.cache']
    )
    
    if not successful_downloads and not has_download_files:
        logger.info("No successful downloads found and no files in download directory, skipping post-process node")
        return state
    
    if successful_downloads:
        logger.info(f"Found {len(successful_downloads)} successful downloads to post-process")
    elif has_download_files:
        logger.info("No download tasks found, but download directory exists with files. Processing files in download directory.")
    
    # Get user query for context
    user_query = ""
    
    if state.get("obtainer_subtask_query"):
        user_query = state.get("obtainer_subtask_query")
    elif state.get("automated_query"):
        user_query = state.get("automated_query")
    else:
        # Extract user message from messages list
        # Look for the last HumanMessage in the messages list
        if state.get("messages") and len(state["messages"]) > 0:
            from langchain_core.messages import HumanMessage
            
            # Search backwards for the last HumanMessage
            for message in reversed(state["messages"]):
                # Check if it's a HumanMessage
                if isinstance(message, HumanMessage):
                    if hasattr(message, "content"):
                        user_query = message.content
                        break
                # Also check dict format
                elif isinstance(message, dict):
                    # Check if it's a human message by type or role
                    msg_type = message.get("type", "")
                    msg_role = message.get("role", "")
                    if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                        user_query = message.get("content", "")
                        if user_query:
                            break
                # Fallback: check if message has content and looks like user input
                elif hasattr(message, "type"):
                    if message.type == "human":
                        if hasattr(message, "content"):
                            user_query = message.content
                            break
    
    # Get category (PT or SFT) - default to PT if not specified
    category = state.get("obtainer", {}).get("category", "PT").upper()
    if category not in ["PT", "SFT"]:
        logger.warning(f"Invalid category '{category}', defaulting to PT")
        category = "PT"
    
    # Initialize components
    try:
        model_name = state.get("obtainer", {}).get("model_path") or state.get("analyze_model_path")
        base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
        api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
        temperature = state.get("obtainer", {}).get("temperature", 0.0)
        
        if not model_name or not base_url or not api_key:
            logger.error("Missing required configuration for post-process node")
            state["exception"] = "Missing model configuration for post-process node"
            return state
        
        # Initialize prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # Get download directory - prioritize environment variable, then state, then default
        download_dir = os.getenv("DOWNLOAD_DIR")
        if not download_dir:
            # Try to get from state
            download_dir = state.get("download_dir")
            if not download_dir:
                # Fallback to output_dir/downloads
                output_dir = state.get("output_dir", "./output")
                download_dir = os.path.join(output_dir, "downloads")
        
        if not os.path.exists(download_dir):
            logger.error(f"Download directory does not exist: {download_dir}")
            state["exception"] = f"Download directory does not exist: {download_dir}"
            return state
        
        # Get additional configuration from state or environment
        llm_timeout = state.get("obtainer_llm_timeout", 120.0)  # This might not be in obtainer dict
        max_retries = state.get("obtainer_max_retries", 3)  # This might not be in obtainer dict
        max_concurrent_mapping = state.get("obtainer_max_concurrent_mapping", 10)  # This might not be in obtainer dict
        
        # Run async workflow
        result = asyncio.run(_postprocess_workflow(
            download_dir=download_dir,
            user_query=user_query,
            category=category,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            llm_timeout=llm_timeout,
            max_retries=max_retries,
            max_concurrent_mapping=max_concurrent_mapping,
        ))
        
        # Update state with results
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            state.setdefault("obtainer", {})["postprocess_results"] = {
                "total_records_processed": result.get("total_records_processed", 0),
                "processed_sources_count": result.get("processed_sources_count", 0),
                "output_dir": result.get("output_dir", ""),
            }
            # Save intermediate format path for mapping node
            output_dir = result.get("output_dir", "")
            if output_dir and os.path.exists(output_dir):
                state.setdefault("obtainer", {})["intermediate_data_path"] = output_dir
                logger.info(f"Intermediate format data saved at: {output_dir}")
            logger.info(
                f"Post-process node completed: {result.get('total_records_processed', 0)} records processed."
            )
            
            # Send custom stream event if debug mode is enabled
            debug_mode = state.get("obtainer_debug", True)
            if debug_mode:
                try:
                    writer = get_stream_writer()
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            message="Post-process node completed",
                            data={
                                'category': category,
                                'total_records_processed': result.get('total_records_processed', 0),
                                'processed_sources_count': result.get('processed_sources_count', 0),
                                'output_dir': result.get('output_dir', ''),
                                'user_query': user_query[:100] if user_query else ''
                            }
                        ).json())
                except Exception as e:
                    logger.debug(f"Could not send stream event: {e}")
            
    except Exception as e:
        logger.error(f"Post-process node error: {e}", exc_info=True)
        state["exception"] = f"Post-process error: {str(e)}"
    
    logger.info("=== Post-process Node: Completed ===")
    return state


async def _postprocess_workflow(
    download_dir: str,
    user_query: str,
    category: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: Optional[PromptLoader] = None,
    llm_timeout: float = 120.0,
    max_retries: int = 3,
    max_concurrent_mapping: int = 10,
) -> Dict[str, Any]:
    """Async workflow for post-processing downloaded datasets"""
    try:
        _ensure_hf_cache_env(download_dir)
        
        # Initialize data convertor with timeout and retry settings
        convertor = DataConvertor(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            timeout=llm_timeout,
            max_retries=max_retries,
        )
        
        # Set up controlled temp directory
        try:
            controlled_tmp = os.getenv("DF_TEMP_DIR") or os.path.join(download_dir, ".tmp")
            os.makedirs(controlled_tmp, exist_ok=True)
            os.environ.setdefault("TMPDIR", controlled_tmp)
        except Exception as e:
            logger.warning(f"Failed to set up controlled temp directory (can be ignored): {e}")
        
        if not os.path.exists(download_dir):
            logger.error(f"Download directory does not exist: {download_dir}")
            return {"exception": f"Download directory does not exist: {download_dir}"}
        
        # Step 1: File discovery (LLM-driven)
        logger.info(f"Scanning download directory: {download_dir}")
        exclude_files = [
            'PT.jsonl', 'SFT.jsonl', 'summary.txt',
            'chroma.sqlite3', 'data_level0.bin', 'header.bin', 
            'length.bin', 'link_lists.bin'
        ]
        file_list_str = convertor._get_file_list_string(download_dir, exclude_files=exclude_files)
        
        if file_list_str == "This directory is empty.":
            logger.warning(f"Directory {download_dir} is empty, no files to process.")
            return {
                "total_records_processed": 0,
                "processed_sources_count": 0,
                "output_dir": "",
            }
        
        logger.debug(f"File list:\n{file_list_str}")
        
        chunked_file_lists = convertor._chunk_file_list_for_llm(file_list_str)
        total_chunks = len(chunked_file_lists)
        logger.info(f"File list will be split into {total_chunks} chunks for LLM file discovery.")
        
        # Process file discovery chunks with 5 concurrent tasks
        data_file_list: List[str] = []
        seen_paths: Set[str] = set()
        failed_chunks = 0
        
        async def process_chunk(idx: int, chunk_str: str) -> Tuple[int, List[str], Optional[Exception]]:
            """Process a single chunk"""
            try:
                logger.info(
                    f"Processing file discovery chunk {idx}/{total_chunks}, approximately {len(chunk_str)} characters."
                )
                chunk_result = await convertor.invoke_file_discovery(chunk_str)
                logger.info(
                    f"Chunk {idx}/{total_chunks} returned {len(chunk_result)} candidate files."
                )
                return (idx, chunk_result, None)
            except Exception as e:
                logger.error(f"LLM file discovery chunk {idx}/{total_chunks} failed: {e}")
                return (idx, [], e)
        
        # Use semaphore to limit concurrent file discovery tasks to 5
        semaphore = asyncio.Semaphore(5)
        
        async def process_chunk_with_semaphore(idx: int, chunk_str: str):
            """Process chunk with semaphore control"""
            async with semaphore:
                return await process_chunk(idx, chunk_str)
        
        # Create tasks for all chunks
        tasks = [
            process_chunk_with_semaphore(idx, chunk_str)
            for idx, chunk_str in enumerate(chunked_file_lists, start=1)
        ]
        
        # Execute all tasks concurrently (max 5 at a time)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for result in results:
            if isinstance(result, Exception):
                failed_chunks += 1
                logger.error(f"File discovery chunk processing failed: {result}")
            else:
                idx, chunk_result, error = result
                if error:
                    failed_chunks += 1
                else:
                    for candidate in chunk_result:
                        if isinstance(candidate, str) and candidate not in seen_paths:
                            seen_paths.add(candidate)
                            data_file_list.append(candidate)
        
        if not data_file_list:
            if failed_chunks == total_chunks:
                logger.error("All file discovery chunks failed, cannot continue.")
            else:
                logger.warning(f"LLM did not find any data files in {download_dir}.")
            return {
                "total_records_processed": 0,
                "processed_sources_count": 0,
                "output_dir": "",
            }
        
        if failed_chunks:
            logger.warning(
                f"File discovery process had {failed_chunks}/{total_chunks} chunks fail, results may be incomplete."
            )
        logger.info(f"LLM identified {len(data_file_list)} data files: {data_file_list}")
        
        # Step 2 & 3: Data conversion and merging
        output_dir = os.path.join(download_dir, "processed_output")
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Output directory: {os.path.abspath(output_dir)}")
        
        output_jsonl_prefix = os.path.join(output_dir, f"{category.upper()}")
        logger.info(f"========================================")
        logger.info(f"Output file prefix (absolute path), will split every 10000 records:")
        logger.info(f"   {os.path.abspath(output_jsonl_prefix)}_00001.jsonl ...")
        logger.info(f"========================================")
        
        processed_sources_list: List[Tuple[str, int]] = []
        
        for relative_file_path in data_file_list:
            absolute_file_path = os.path.join(download_dir, relative_file_path)
            
            if not os.path.exists(absolute_file_path):
                logger.warning(f"LLM returned non-existent file path '{relative_file_path}', skipping.")
                continue
            
            logger.info(f"--- Processing file: {absolute_file_path} ---")
            
            files_to_process = []
            
            if convertor._is_compressed_file(absolute_file_path):
                logger.info(f"Detected compressed file: {absolute_file_path}")
                extracted_dir = convertor._extract_compressed_file(absolute_file_path)
                
                if not extracted_dir:
                    logger.error(f"Extraction failed, skipping file: {absolute_file_path}")
                    continue
                
                for root, dirs, files in os.walk(extracted_dir):
                    for f in files:
                        full_path = os.path.join(root, f)
                        if any(full_path.lower().endswith(ext) for ext in 
                               ['.json', '.jsonl', '.csv', '.parquet', '.arrow', '.txt']):
                            files_to_process.append(full_path)
                
                if not files_to_process:
                    logger.warning(f"No data files found after extraction: {absolute_file_path}")
                    continue
                
                logger.info(f"Found {len(files_to_process)} data files after extraction")
            else:
                files_to_process = [absolute_file_path]
            
            # Collect all data files and their datasets first
            file_data_pairs: List[Tuple[str, Any]] = []
            
            for file_path in files_to_process:
                logger.info(f"--- Loading data file: {file_path} ---")
                builder_type = convertor._get_builder_type(file_path)
                if not builder_type:
                    logger.warning(f"Cannot determine builder type, skipping file: {file_path}")
                    continue
                
                # Try multiple loading strategies
                data = None
                load_strategies = [
                    {"name": "load_dataset", "func": convertor._load_with_datasets},
                    {"name": "fallback", "func": convertor._load_with_fallback},
                ]
                
                for strategy in load_strategies:
                    try:
                        logger.info(f"Trying load strategy: {strategy['name']}")
                        data = await strategy['func'](builder_type, file_path)
                        if data is not None:
                            logger.info(f"Load strategy '{strategy['name']}' succeeded!")
                            break
                    except Exception as e:
                        logger.warning(f"Load strategy '{strategy['name']}' failed: {e}")
                        continue
                
                if data is None:
                    logger.error(f"All load strategies failed, skipping file: {file_path}")
                    continue
                
                file_data_pairs.append((file_path, data))
            
            # Collect all splits that need mapping
            mapping_tasks: List[Dict[str, Any]] = []
            for file_path, data in file_data_pairs:
                file_name = os.path.basename(file_path)
                for split_name, data_content in data.items():
                    if len(data_content) == 0:
                        continue
                    column_names = data_content.column_names
                    sample_record = data_content[0]
                    mapping_tasks.append({
                        "file_path": file_path,
                        "file_name": file_name,
                        "split_name": split_name,
                        "data_content": data_content,
                        "column_names": column_names,
                        "sample_record": sample_record,
                    })
            
            # Process all mapping tasks with limited concurrency (reduced from 50 to 10 for stability)
            logger.info(f"Found {len(mapping_tasks)} splits to process, starting concurrent mapping ({max_concurrent_mapping} concurrent)...")
            
            async def process_mapping_task(task: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Exception]]:
                """Process a single mapping task"""
                try:
                    logger.info(f"Starting LLM mapping for {task['file_name']} ({task['split_name']})...")
                    annotation_result = await convertor.invoke_data_mapping(
                        column_names=task["column_names"],
                        sample_record=task["sample_record"],
                        dataset=task["data_content"],
                        user_target=user_query,
                        category=category
                    )
                    logger.info(f"✓ LLM mapping succeeded for {task['file_name']} ({task['split_name']}): {annotation_result}")
                    return (task, annotation_result, None)
                except Exception as e:
                    logger.error(f"✗ LLM data mapping failed for {task['file_name']} ({task['split_name']}): {e}")
                    return (task, None, e)
            
            # Use semaphore to limit concurrent mapping tasks
            mapping_semaphore = asyncio.Semaphore(max_concurrent_mapping)
            
            async def process_mapping_with_semaphore(task: Dict[str, Any]):
                """Process mapping task with semaphore control"""
                async with mapping_semaphore:
                    return await process_mapping_task(task)
            
            # Create tasks for all mappings
            mapping_tasks_list = [
                process_mapping_with_semaphore(task)
                for task in mapping_tasks
            ]
            
            # Execute all mapping tasks concurrently (limited by semaphore)
            logger.info(f"Executing {len(mapping_tasks_list)} mapping tasks with max {max_concurrent_mapping} concurrent...")
            mapping_results = await asyncio.gather(*mapping_tasks_list, return_exceptions=True)
            logger.info(f"Completed {len(mapping_results)} mapping tasks")
            
            # Process datasets with mapping results concurrently
            # Filter out failed mappings first
            valid_mapping_results = []
            for mapping_result in mapping_results:
                if isinstance(mapping_result, Exception):
                    logger.error(f"Mapping task processing failed: {mapping_result}")
                    continue
                
                task, annotation_result, error = mapping_result
                if error or annotation_result is None:
                    logger.warning(f"Skipping {task['file_name']} ({task['split_name']}) due to mapping failure")
                    continue
                
                valid_mapping_results.append((task, annotation_result))
            
            # Process all valid splits concurrently
            if valid_mapping_results:
                logger.info(f"Processing {len(valid_mapping_results)} splits with mapping results concurrently...")
                
                async def process_split_with_mapping(task_and_result: Tuple[Dict[str, Any], Dict[str, Any]]):
                    """Process a single split with its mapping result"""
                    task, annotation_result = task_and_result
                    try:
                        await convertor._process_dataset_with_mapping(
                            task["data_content"],
                            task["file_path"],
                            task["file_name"],
                            task["split_name"],
                            annotation_result,
                            category,
                            output_jsonl_prefix,
                            processed_sources_list
                        )
                    except Exception as e:
                        logger.error(f"Error processing {task['file_name']} ({task['split_name']}): {e}")
                
                # Execute all data processing tasks concurrently
                await asyncio.gather(*[process_split_with_mapping(result) for result in valid_mapping_results], return_exceptions=True)
        
        # File processing loop complete
        total_records_processed = sum(count for _, count in processed_sources_list)
        logger.info(f"Download directory processing complete. Total extracted {total_records_processed} records.")
        
        # Output file location info
        if total_records_processed > 0:
            logger.info(f"========================================")
            logger.info(f"Data successfully written to multiple chunk files, prefix as follows:")
            logger.info(f"Prefix path: {os.path.abspath(output_jsonl_prefix)}_*.jsonl")
            logger.info(f"Total records: {total_records_processed}")
            logger.info(f"========================================")
        else:
            logger.warning(f"No valid records extracted, output files may be empty or non-existent.")
        
        # Step 4: Clean up temporary directories
        logger.info("Cleaning up temporary extraction directories...")
        convertor._cleanup_temp_dirs()
        
        return {
            "total_records_processed": total_records_processed,
            "processed_sources_count": len(processed_sources_list),
            "output_dir": os.path.abspath(output_dir),
        }
        
    except Exception as e:
        logger.error(f"Post-process workflow error: {e}", exc_info=True)
        return {"exception": f"Post-process workflow error: {str(e)}"}

