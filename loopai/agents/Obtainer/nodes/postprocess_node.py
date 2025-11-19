import asyncio
import os
import json
from typing import Dict, Any, List, Optional, Tuple, Set
from pathlib import Path

from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.agents.Obtainer.utils.data_convertor import DataConvertor, _ensure_hf_cache_env
from loopai.agents.Obtainer.utils.postprocess_tools import get_tool_registry
from loopai.common.prompts import PromptLoader

logger = get_logger()


def postprocess_node(state: LoopAIState) -> LoopAIState:
    """后处理节点：读取文件抽样，根据格式调用后处理工具算子库进行处理"""
    logger.info("=== Post-process Node: Starting ===")
    
    # 检查是否有成功的下载
    subtasks = state.get("obtainer_subtasks", [])
    successful_downloads = [
        task for task in subtasks 
        if task.get("type") == "download" and task.get("status") == "completed_successfully"
    ]
    
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
    
    # 获取用户查询用于上下文
    user_query = ""
    
    # 首先尝试从 automated_query 获取（最高优先级）
    if state.get("automated_query"):
        user_query = state.get("automated_query")
    else:
        # 从消息列表中提取用户消息
        # 查找最后一个 HumanMessage
        if state.get("messages") and len(state["messages"]) > 0:
            from langchain_core.messages import HumanMessage
            
            # 反向搜索最后一个 HumanMessage
            for message in reversed(state["messages"]):
                # 检查是否是 HumanMessage
                if isinstance(message, HumanMessage):
                    if hasattr(message, "content"):
                        user_query = message.content
                        break
                # 也检查字典格式
                elif isinstance(message, dict):
                    # 通过 type 或 role 检查是否是用户消息
                    msg_type = message.get("type", "")
                    msg_role = message.get("role", "")
                    if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                        user_query = message.get("content", "")
                        if user_query:
                            break
                # 回退：检查消息是否有 content 且看起来像用户输入
                elif hasattr(message, "type"):
                    if message.type == "human":
                        if hasattr(message, "content"):
                            user_query = message.content
                            break
    
    # 获取类别（PT 或 SFT）- 默认为 PT
    category = state.get("obtainer_category", "PT").upper()
    if category not in ["PT", "SFT"]:
        logger.warning(f"Invalid category '{category}', defaulting to PT")
        category = "PT"
    
    # 初始化组件
    try:
        model_name = state.get("obtainer_model_path") or state.get("analyze_model_path")
        base_url = state.get("obtainer_base_url") or state.get("analyze_base_url")
        api_key = state.get("obtainer_api_key") or state.get("analyze_api_key")
        temperature = state.get("obtainer_temperature", 0.0)
        
        if not model_name or not base_url or not api_key:
            logger.error("Missing required configuration for post-process node")
            state["exception"] = "Missing model configuration for post-process node"
            return state
        
        # 初始化 prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # 输出目录
        output_dir = state.get("output_dir", "./output")
        download_dir = os.path.join(output_dir, "downloads")
        
        if not os.path.exists(download_dir):
            logger.error(f"Download directory does not exist: {download_dir}")
            state["exception"] = f"Download directory does not exist: {download_dir}"
            return state
        
        # 运行异步工作流
        result = asyncio.run(_postprocess_workflow(
            download_dir=download_dir,
            user_query=user_query,
            category=category,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
        ))
        
        # 更新状态中的结果
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            state["obtainer_postprocess_results"] = {
                "total_records_processed": result.get("total_records_processed", 0),
                "processed_sources_count": result.get("processed_sources_count", 0),
                "output_dir": result.get("output_dir", ""),
            }
            logger.info(
                f"Post-process node completed: {result.get('total_records_processed', 0)} records processed."
            )
            
            # 如果启用了调试模式，发送自定义流事件
            debug_mode = state.get("obtainer_debug", True)
            if debug_mode:
                try:
                    writer = get_stream_writer()
                    if writer:
                        writer(StreamEvent(
                            current=state.get('current', 'postprocess_node'),
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
) -> Dict[str, Any]:
    """异步工作流：后处理下载的数据集"""
    try:
        _ensure_hf_cache_env(download_dir)
        
        # 初始化数据转换器（用于文件发现）
        convertor = DataConvertor(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
        )
        
        # 设置受控的临时目录
        try:
            controlled_tmp = os.getenv("DF_TEMP_DIR") or os.path.join(download_dir, ".tmp")
            os.makedirs(controlled_tmp, exist_ok=True)
            os.environ.setdefault("TMPDIR", controlled_tmp)
        except Exception as e:
            logger.warning(f"Failed to set up controlled temp directory (can be ignored): {e}")
        
        if not os.path.exists(download_dir):
            logger.error(f"Download directory does not exist: {download_dir}")
            return {"exception": f"Download directory does not exist: {download_dir}"}
        
        # 步骤 1: 文件发现（LLM 驱动）
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
        
        # 处理文件发现块，最多 5 个并发任务
        data_file_list: List[str] = []
        seen_paths: Set[str] = set()
        failed_chunks = 0
        
        async def process_chunk(idx: int, chunk_str: str) -> Tuple[int, List[str], Optional[Exception]]:
            """处理单个块"""
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
        
        # 使用信号量限制并发文件发现任务为 5
        semaphore = asyncio.Semaphore(5)
        
        async def process_chunk_with_semaphore(idx: int, chunk_str: str):
            """使用信号量控制处理块"""
            async with semaphore:
                return await process_chunk(idx, chunk_str)
        
        # 为所有块创建任务
        tasks = [
            process_chunk_with_semaphore(idx, chunk_str)
            for idx, chunk_str in enumerate(chunked_file_lists, start=1)
        ]
        
        # 并发执行所有任务（最多同时 5 个）
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
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
        
        # 步骤 2: 处理每个文件 - 读取抽样并调用后处理工具
        output_dir = os.path.join(download_dir, "processed_output")
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"Output directory: {os.path.abspath(output_dir)}")
        
        output_jsonl_prefix = os.path.join(output_dir, f"{category.upper()}")
        logger.info(f"========================================")
        logger.info(f"Output file prefix (absolute path), will split every 10000 records:")
        logger.info(f"   {os.path.abspath(output_jsonl_prefix)}_00001.jsonl ...")
        logger.info(f"========================================")
        
        # 获取后处理工具注册表
        tool_registry = get_tool_registry()
        
        processed_sources_list: List[Tuple[str, int]] = []
        total_records_processed = 0
        
        # 处理每个文件
        for relative_file_path in data_file_list:
            absolute_file_path = os.path.join(download_dir, relative_file_path)
            
            if not os.path.exists(absolute_file_path):
                logger.warning(f"LLM returned non-existent file path '{relative_file_path}', skipping.")
                continue
            
            logger.info(f"--- Processing file: {absolute_file_path} ---")
            
            # 检查是否是压缩文件
            files_to_process = []
            if convertor._is_compressed_file(absolute_file_path):
                logger.info(f"Detected compressed file: {absolute_file_path}")
                extracted_dir = convertor._extract_compressed_file(absolute_file_path)
                
                if not extracted_dir:
                    logger.error(f"Extraction failed, skipping file: {absolute_file_path}")
                    continue
                
                # 遍历解压后的目录，收集所有数据文件
                for root, dirs, files in os.walk(extracted_dir):
                    for f in files:
                        full_path = os.path.join(root, f)
                        # 检查文件扩展名是否支持
                        if tool_registry.is_supported(full_path):
                            files_to_process.append(full_path)
                
                if not files_to_process:
                    logger.warning(f"No supported data files found after extraction: {absolute_file_path}")
                    continue
                
                logger.info(f"Found {len(files_to_process)} supported data files after extraction")
            else:
                # 检查文件是否支持
                if tool_registry.is_supported(absolute_file_path):
                    files_to_process = [absolute_file_path]
                else:
                    logger.warning(f"File format not supported: {absolute_file_path}")
                    continue
            
            # 处理每个文件
            for file_path in files_to_process:
                logger.info(f"--- Processing data file: {file_path} ---")
                
                # 获取对应的后处理工具
                tool = tool_registry.get_tool(file_path)
                if tool is None:
                    logger.warning(f"No tool found for file: {file_path}, skipping.")
                    continue
                
                try:
                    # 步骤 2.1: 读取文件抽样
                    logger.info(f"Reading sample from file: {file_path}")
                    sample_data = tool.read_sample(file_path, sample_size=10)
                    
                    if not sample_data:
                        logger.warning(f"No sample data could be read from: {file_path}")
                        continue
                    
                    logger.info(f"Successfully read {len(sample_data)} sample records from: {file_path}")
                    
                    # 步骤 2.2: 检测文件格式和结构
                    format_info = tool.detect_format(file_path)
                    if format_info:
                        logger.info(f"File format detected: {format_info}")
                    
                    # 步骤 2.3: 调用后处理工具进行处理
                    # 生成输出文件路径
                    file_name = Path(file_path).stem
                    file_ext = Path(file_path).suffix
                    output_file_path = os.path.join(
                        output_dir,
                        f"{category.upper()}_{file_name}{file_ext}"
                    )
                    
                    logger.info(f"Calling postprocess tool for: {file_path}")
                    logger.info(f"Target format: jsonl, Category: {category}, Output: {output_file_path}")
                    
                    # 调用工具进行处理
                    process_result = tool.process(
                        file_path=file_path,
                        target_format="jsonl",
                        output_path=output_file_path,
                        category=category,
                        user_query=user_query,
                        sample_data=sample_data,
                    )
                    
                    if process_result.get("success", False):
                        records_count = process_result.get("records_processed", 0)
                        processed_sources_list.append((file_path, records_count))
                        total_records_processed += records_count
                        logger.info(
                            f"Successfully processed {file_path}: {records_count} records"
                        )
                    else:
                        error_msg = process_result.get("error", "Unknown error")
                        logger.error(f"Failed to process {file_path}: {error_msg}")
                
                except Exception as e:
                    logger.error(f"Error processing file {file_path}: {e}", exc_info=True)
                    continue
        
        # 文件处理循环完成
        logger.info(f"Download directory processing complete. Total extracted {total_records_processed} records.")
        
        # 输出文件位置信息
        if total_records_processed > 0:
            logger.info(f"========================================")
            logger.info(f"Data successfully written to output directory:")
            logger.info(f"Output directory: {os.path.abspath(output_dir)}")
            logger.info(f"Total records: {total_records_processed}")
            logger.info(f"========================================")
        else:
            logger.warning(f"No valid records extracted, output files may be empty or non-existent.")
        
        # 步骤 3: 清理临时目录
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
