import os
import logging
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent

from loopai.logger import get_logger
from loopai.agents.Constructor.nodes import postprocess_node
from loopai.agents.Constructor.nodes.filter_node import CleaningSubgraph
from loopai.agents.Constructor.mapping import MappingSubgraph
from loopai.common.prompts import PromptLoader

logger = get_logger()


class ImmediateFlushFileHandler(logging.FileHandler):
    """
    FileHandler that flushes immediately after each log record.
    This ensures logs are written to disk in real-time for debugging.
    """
    def emit(self, record):
        """
        Emit a record and immediately flush to disk.
        """
        super().emit(record)
        self.flush()


class ConstructorAgent(BaseAgent):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "Constructor"

    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"

    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "default_prompt"
    
    def get_start_node(self):
        """
        Get start node function that can access self
        """
        @BaseAgent.set_current
        def start_node(state: LoopAIState):
            """
            Start node for constructor agent
            Ensure configuration parameters are set in state
            """
            logger.info(f"ConstructorAgent: Starting task")
            
            # Ensure constructor configuration is set in state
            # Use values from constructor if not already in state
            # Prompt 目录写入 state，保证后续节点（如 postprocess_node）能拿到正确的 prompt_loader
            if not state.get("prompt_template_dir") and self.prompt_template_dir:
                state["prompt_template_dir"] = self.prompt_template_dir
            
            if not state.get("obtainer_model_path"):
                if self.model_name:
                    state["obtainer_model_path"] = self.model_name
                elif state.get("analyze_model_path"):
                    state["obtainer_model_path"] = state["analyze_model_path"]
            
            if not state.get("obtainer_base_url"):
                if self.base_url:
                    state["obtainer_base_url"] = self.base_url
                elif state.get("analyze_base_url"):
                    state["obtainer_base_url"] = state["analyze_base_url"]
            
            if not state.get("obtainer_api_key"):
                if self.api_key:
                    state["obtainer_api_key"] = self.api_key
                elif state.get("analyze_api_key"):
                    state["obtainer_api_key"] = state["analyze_api_key"]
            
            if "obtainer_temperature" not in state:
                state["obtainer_temperature"] = self.temperature if hasattr(self, 'temperature') else 0.7
            
            # Ensure output_dir is set
            if not state.get("output_dir"):
                state["output_dir"] = "./output"
            
            # Set default values for constructor-specific parameters if not in state
            if "obtainer_llm_timeout" not in state:
                state["obtainer_llm_timeout"] = 120.0
            
            if "obtainer_max_retries" not in state:
                state["obtainer_max_retries"] = 3
            
            if "obtainer_max_concurrent_mapping" not in state:
                state["obtainer_max_concurrent_mapping"] = 10
            
            # Mapping configuration
            # obtainer_default_mapping_format: If set (e.g., "alpaca"), skip user interaction and use this format directly
            # If empty or not set, go through user interaction flow
            if "obtainer_default_mapping_format" not in state:
                state["obtainer_default_mapping_format"] = "alpaca"  # Default to alpaca format
            
            # Handle debug mode
            debug_mode = state.get("obtainer_debug", False)
            if debug_mode:
                # Set logger level to DEBUG
                logger.setLevel(logging.DEBUG)
                # Update existing handlers to DEBUG level
                for handler in logger.handlers:
                    handler.setLevel(logging.DEBUG)
                
                # Add file handler for debug logs if not already added
                output_dir = state.get("output_dir", "./output")
                log_dir = os.path.join(output_dir, "constructor_logs")
                os.makedirs(log_dir, exist_ok=True)
                
                # Check if file handler already exists
                has_file_handler = any(
                    isinstance(h, logging.FileHandler) and "constructor_debug" in h.baseFilename
                    for h in logger.handlers
                )
                
                if not has_file_handler:
                    from datetime import datetime
                    log_file = os.path.join(log_dir, f"constructor_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
                    # Use ImmediateFlushFileHandler for real-time log writing
                    file_handler = ImmediateFlushFileHandler(log_file, encoding='utf-8')
                    file_handler.setLevel(logging.DEBUG)
                    
                    # Use the same formatter as console (but without colors)
                    import logging as std_logging
                    file_formatter = std_logging.Formatter(
                        fmt=(
                            "%(asctime)s.%(msecs)03d"
                            " | %(levelname)-8s"
                            " | %(name)s"
                            ":%(filename)s"
                            ":%(funcName)s"
                            ":%(lineno)d"
                            " - %(message)s"
                        ),
                        datefmt="%Y-%m-%d %H:%M:%S"
                    )
                    file_handler.setFormatter(file_formatter)
                    logger.addHandler(file_handler)
                    logger.info(f"Debug mode enabled: Logs will be saved to {log_file}")
                    # Flush immediately to ensure the message is written
                    file_handler.flush()

            logger.info(f"ConstructorAgent: Configuration set - model: {state.get('obtainer_model_path')}, "
                       f"base_url: {state.get('obtainer_base_url')}, "
                       f"debug: {debug_mode}")
            
            # Send custom stream event if debug mode is enabled
            if debug_mode:
                try:
                    writer = get_stream_writer()
                    if writer:
                        writer(StreamEvent(
                            current=state.get('current', 'constructor_start_node'),
                            message="ConstructorAgent configuration initialized",
                            data={
                                'model': state.get('obtainer_model_path'),
                                'base_url': state.get('obtainer_base_url'),
                                'debug_mode': debug_mode
                            }
                        ).json())
                except Exception as e:
                    # Stream writer might not be available in all contexts
                    logger.debug(f"Could not send stream event: {e}")
            
            return state
        
        return start_node

    @staticmethod
    @BaseAgent.set_current
    def end_node(state: LoopAIState):
        """
        End node for constructor agent
        Set next_to to return to parent graph and summarize results
        """
        logger.info(f"ConstructorAgent: Task completed, returning to parent graph")
        
        # Generate summary of results for LLM
        summary_parts = []
        
        # Check for exceptions
        if state.get("exception"):
            summary_parts.append(f"执行过程中出现错误: {state.get('exception')}")
        else:
            # Summarize post-process results
            postprocess_results = state.get("obtainer_postprocess_results", {})
            if postprocess_results:
                total_records = postprocess_results.get("total_records_processed", 0)
                if total_records > 0:
                    category = state.get("obtainer_category", "PT")
                    summary_parts.append(f"后处理完成: 共处理 {total_records} 条 {category} 数据记录")
                    output_dir = postprocess_results.get("output_dir", "")
                    if output_dir:
                        summary_parts.append(f"中间格式输出目录: {output_dir}")
            
            # Summarize mapping results
            mapping_results = state.get("obtainer_mapping_results", {})
            if mapping_results:
                final_output_dir = mapping_results.get("final_output_dir", "")
                total_mapped_records = mapping_results.get("total_mapped_records", 0)
                if final_output_dir:
                    summary_parts.append(f"格式映射完成: 共映射 {total_mapped_records} 条记录")
                    summary_parts.append(f"最终输出目录: {final_output_dir}")
        
        # Create summary message
        if summary_parts:
            summary_text = "数据构造任务执行完成:\n" + "\n".join(summary_parts)
        else:
            summary_text = "数据构造任务执行完成，但未找到相关数据。"
        
        # Add summary to messages so LLM can see it
        from langchain_core.messages import AIMessage
        if "messages" not in state:
            state["messages"] = []
        
        # Add summary as AI message
        state["messages"].append(AIMessage(content=summary_text))
        logger.info(f"ConstructorAgent: Added summary to messages: {summary_text[:100]}...")
        
        # Send custom stream event if debug mode is enabled
        debug_mode = state.get("obtainer_debug", False)
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    # Prepare summary data
                    summary_data = {
                        'summary_text': summary_text,
                        'has_exception': bool(state.get("exception")),
                        'postprocess_results': state.get("obtainer_postprocess_results", {}),
                        'mapping_results': state.get("obtainer_mapping_results", {})
                    }
                    writer(StreamEvent(
                        current=state.get('current', 'constructor_end_node'),
                        message="ConstructorAgent task completed",
                        data=summary_data
                    ).json())
            except Exception as e:
                # Stream writer might not be available in all contexts
                logger.debug(f"Could not send stream event: {e}")
        
        # Set next_to to query_node to return to parent graph
        state["next_to"] = "query_node"
        return state

    @staticmethod
    def has_successful_downloads(state: LoopAIState) -> str:
        """
        Conditional edge function: check if there are successful downloads
        
        Returns:
            "postprocess_node" if there are successful downloads, "end_node" otherwise
        """
        subtasks = state.get("obtainer_subtasks", [])
        successful_downloads = [
            task for task in subtasks 
            if task.get("type") == "download" and task.get("status") == "completed_successfully"
        ]
        
        # Also check if download directory exists with files
        download_dir = os.getenv("DOWNLOAD_DIR")
        if not download_dir:
            download_dir = state.get("download_dir")
            if not download_dir:
                output_dir = state.get("output_dir", "./output")
                download_dir = os.path.join(output_dir, "downloads")
        
        has_download_files = os.path.exists(download_dir) and any(
            os.path.isfile(os.path.join(download_dir, f)) or os.path.isdir(os.path.join(download_dir, f))
            for f in os.listdir(download_dir)
            if not f.startswith('.') and f not in ['processed_output', '.tmp', '.cache']
        )
        
        if successful_downloads or has_download_files:
            logger.info(f"Found {len(successful_downloads)} successful downloads or files in download directory, routing to postprocess_node")
            return "postprocess_node"
        else:
            logger.info("No successful downloads found, routing to end_node")
            return "end_node"

    @staticmethod
    def should_trigger_mapping(state: LoopAIState) -> str:
        """
        Conditional edge function: check if mapping should be triggered
        
        Returns:
            "mapping_subgraph" if intermediate data exists and format not confirmed, "end_node" otherwise
        """
        intermediate_path = state.get("obtainer_intermediate_data_path", "")
        if intermediate_path and os.path.exists(intermediate_path):
            confirmed_format = state.get("obtainer_confirmed_format")
            if not confirmed_format:
                logger.info("Intermediate data found, routing to mapping_subgraph for format selection")
                return "mapping_subgraph"
            else:
                logger.info("Format already confirmed, skipping mapping_subgraph")
        return "end_node"

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("start_node", self.get_start_node())
        builder.add_node("postprocess_node", postprocess_node)
        
        # 使用 CleaningSubgraph 作为子图（数据清洗）
        cleaning_subgraph = CleaningSubgraph(
            checkpointer=self.checkpointer,
            store=self.store
        )
        builder.add_node("data_cleaning", cleaning_subgraph.build())
        
        # 使用 MappingSubgraph 作为子图
        mapping_subgraph = MappingSubgraph(
            checkpointer=self.checkpointer,
            store=self.store
        )
        builder.add_node("mapping_subgraph", mapping_subgraph.build())
        
        builder.add_node("end_node", self.end_node)
        builder.set_entry_point("start_node")
        
        # start_node -> postprocess_node (检查是否有成功下载)
        builder.add_conditional_edges(
            "start_node",
            self.has_successful_downloads,
            {
                "postprocess_node": "postprocess_node",
                "end_node": "end_node",
            }
        )
        
        # postprocess_node -> data_cleaning (先进行数据清洗)
        builder.add_edge("postprocess_node", "data_cleaning")
        
        # data_cleaning -> mapping_subgraph (检查是否需要映射)
        builder.add_conditional_edges(
            "data_cleaning",
            self.should_trigger_mapping,
            {
                "mapping_subgraph": "mapping_subgraph",
                "end_node": "end_node",
            }
        )
        
        # mapping_subgraph -> end_node
        builder.add_edge("mapping_subgraph", "end_node")
        builder.set_finish_point("end_node")
        
        self.graph = builder.compile(
            checkpointer=self.checkpointer, store=self.store, **kwargs)

    def __call__(self, **kwargs):
        """
        build and return self.graph

        Args:
            kwargs: keyword arguments to pass to init_graph
        """
        self.init_graph(**kwargs)
        return self.graph
