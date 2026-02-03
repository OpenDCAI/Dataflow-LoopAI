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
            state['current'] = "ConstructorAgent.start_node"
            writer = get_stream_writer()
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message="Constructor: 配置开始",
                    progress=0.0,
                    data={"phase": "constructor_config"},
                ).json())
            
            # Ensure constructor configuration is set in state
            # Use values from constructor if not already in state
            # Prompt 目录写入 state，保证后续节点（如 postprocess_node）能拿到正确的 prompt_loader
            if not state.get("prompt_template_dir") and self.prompt_template_dir:
                state["prompt_template_dir"] = self.prompt_template_dir
            
            # 确保 obtainer 字典存在
            if "obtainer" not in state:
                state["obtainer"] = {}
            
            # 使用嵌套结构 state["obtainer"]["xxx"]，符合 states.py 中 ObtainerState 的定义
            obtainer = state["obtainer"]
            
            # model_path: 优先使用 obtainer 中的值，其次使用 self.model_name
            if not obtainer.get("model_path"):
                if self.model_name:
                    obtainer["model_path"] = self.model_name
            
            # base_url: 优先使用 obtainer 中的值，其次使用 self.base_url
            if not obtainer.get("base_url"):
                if self.base_url:
                    obtainer["base_url"] = self.base_url
            
            # api_key: 优先使用 obtainer 中的值，其次使用 self.api_key
            if not obtainer.get("api_key"):
                if self.api_key:
                    obtainer["api_key"] = self.api_key
            
            # temperature: 优先使用 obtainer 中的值，其次使用 self.temperature，默认 0.7
            if obtainer.get("temperature") is None:
                if hasattr(self, 'temperature') and self.temperature is not None:
                    obtainer["temperature"] = self.temperature
                else:
                    obtainer["temperature"] = 0.7
            
            # Ensure output_dir is set
            if not state.get("output_dir"):
                state["output_dir"] = "./output"
            
            # 确保 obtainer 字典存在
            if "obtainer" not in state:
                state["obtainer"] = {}
            
            # Set default values for constructor-specific parameters if not in state (使用嵌套结构)
            obtainer_state = state.get("obtainer", {})
            if "llm_timeout" not in obtainer_state:
                state["obtainer"]["llm_timeout"] = 120.0
            
            if "max_retries" not in obtainer_state:
                state["obtainer"]["max_retries"] = 3
            
            if "max_concurrent_mapping" not in obtainer_state:
                state["obtainer"]["max_concurrent_mapping"] = 10
            
            # Mapping configuration
            # default_mapping_format: If set (e.g., "alpaca"), skip user interaction and use this format directly
            # If empty or not set, go through user interaction flow
            if "default_mapping_format" not in obtainer_state:
                state["obtainer"]["default_mapping_format"] = "alpaca"  # Default to alpaca format
            
            # Handle debug mode
            debug_mode = obtainer_state.get("debug", False)
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

            logger.info(f"ConstructorAgent: Configuration set - model: {obtainer.get('model_path')}, "
                       f"base_url: {obtainer.get('base_url')}, "
                       f"debug: {debug_mode}")
            
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message="Constructor: 配置完成",
                    progress=1.0,
                    data={
                        "phase": "constructor_config",
                        "model": obtainer.get("model_path"),
                        "base_url": obtainer.get("base_url"),
                        "debug_mode": debug_mode,
                    },
                ).json())
            
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
        state['current'] = "ConstructorAgent.end_node"
        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message="Constructor: 结束节点开始",
                progress=0.0,
                data={"phase": "constructor_end"},
            ).json())
        
        # Generate summary of results for LLM
        summary_parts = []
        
        # 获取 obtainer 状态
        obtainer_state = state.get("obtainer", {})
        
        # Check for exceptions
        if state.get("exception"):
            summary_parts.append(f"执行过程中出现错误: {state.get('exception')}")
        else:
            # Summarize post-process results
            postprocess_results = obtainer_state.get("postprocess_results", {})
            if postprocess_results:
                total_records = postprocess_results.get("total_records_processed", 0)
                if total_records > 0:
                    category = obtainer_state.get("category", "PT")
                    summary_parts.append(f"后处理完成: 共处理 {total_records} 条 {category} 数据记录")
                    output_dir = postprocess_results.get("output_dir", "")
                    if output_dir:
                        summary_parts.append(f"中间格式输出目录: {output_dir}")
            
            # Summarize mapping results
            mapping_results = obtainer_state.get("mapping_results", {})
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
        
        if writer:
            postprocess_results = obtainer_state.get("postprocess_results", {})
            mapping_results = obtainer_state.get("mapping_results", {})
            writer(StreamEvent(
                current=state['current'],
                message="Constructor: 任务完成",
                progress=1.0,
                data={
                    "phase": "constructor_end",
                    "summary_text": summary_text,
                    "has_exception": bool(state.get("exception")),
                    "total_records_processed": postprocess_results.get("total_records_processed", 0),
                    "total_mapped_records": mapping_results.get("total_mapped_records", 0),
                    "final_output_dir": mapping_results.get("final_output_dir", ""),
                },
            ).json())
        
        # Set next_to to query_node to return to parent graph
        state["next_to"] = "query_node"
        return state

    @staticmethod
    @BaseAgent.set_current
    def postprocess_node_wrapper(state: LoopAIState):
        """
        Wrapper for postprocess_node with StreamEvent
        """
        writer = get_stream_writer()
        state['current'] = "ConstructorAgent.postprocess_node"
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message="Constructor: 后处理开始",
                progress=0.0,
                data={"phase": "postprocess"},
            ).json())
        state = postprocess_node(state)
        if writer:
            res = state.get("obtainer", {}).get("postprocess_results", {})
            writer(StreamEvent(
                current=state['current'],
                message="Constructor: 后处理完成",
                progress=1.0,
                data={
                    "phase": "postprocess",
                    "total_records_processed": res.get("total_records_processed", 0),
                    "processed_sources_count": res.get("processed_sources_count", 0),
                    "output_dir": res.get("output_dir", ""),
                },
            ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def data_cleaning_start(state: LoopAIState):
        """
        Start node for data_cleaning subgraph with StreamEvent
        """
        writer = get_stream_writer()
        state['current'] = "ConstructorAgent.data_cleaning"
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message="Constructor: 数据清洗开始",
                progress=0.0,
                data={"phase": "data_cleaning"},
            ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def data_cleaning_end(state: LoopAIState):
        """
        End node for data_cleaning subgraph with StreamEvent
        """
        writer = get_stream_writer()
        if writer:
            cleaning_results = state.get("obtainer", {}).get("cleaning_results", {})
            tools_executed = cleaning_results.get("tools_executed", [])
            writer(StreamEvent(
                current=state.get('current', 'ConstructorAgent.data_cleaning'),
                message="Constructor: 数据清洗完成",
                progress=1.0,
                data={
                    "phase": "data_cleaning",
                    "tools_executed": [t.get("tool") if isinstance(t, dict) else str(t) for t in tools_executed],
                    "tools_count": len(tools_executed),
                },
            ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def mapping_subgraph_start(state: LoopAIState):
        """
        Start node for mapping_subgraph with StreamEvent
        """
        writer = get_stream_writer()
        state['current'] = "ConstructorAgent.mapping_subgraph"
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message="Constructor: 格式映射开始",
                progress=0.0,
                data={"phase": "mapping"},
            ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def mapping_subgraph_end(state: LoopAIState):
        """
        End node for mapping_subgraph with StreamEvent
        """
        writer = get_stream_writer()
        if writer:
            mapping_results = state.get("obtainer", {}).get("mapping_results", {})
            writer(StreamEvent(
                current=state.get('current', 'ConstructorAgent.mapping_subgraph'),
                message="Constructor: 格式映射完成",
                progress=1.0,
                data={
                    "phase": "mapping",
                    "total_mapped_records": mapping_results.get("total_mapped_records", 0),
                    "final_output_dir": mapping_results.get("final_output_dir", ""),
                },
            ).json())
        return state

    @staticmethod
    def has_successful_downloads(state: LoopAIState) -> str:
        """
        Conditional edge function: check if there are successful downloads
        
        Returns:
            "postprocess_node" if there are successful downloads, "end_node" otherwise
        """
        obtainer_state = state.get("obtainer", {})
        subtasks = obtainer_state.get("subtasks", [])
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
            "mapping_subgraph_start" if intermediate data exists and format not confirmed, "end_node" otherwise
        """
        # 使用嵌套结构访问 obtainer 状态，符合 states.py 中 ObtainerState 的定义
        obtainer_state = state.get("obtainer", {})
        intermediate_path = obtainer_state.get("intermediate_data_path", "")
        if intermediate_path and os.path.exists(intermediate_path):
            confirmed_format = obtainer_state.get("confirmed_format")
            if not confirmed_format:
                logger.info("Intermediate data found, routing to mapping_subgraph_start for format selection")
                return "mapping_subgraph_start"
            else:
                logger.info("Format already confirmed, skipping mapping_subgraph")
        return "end_node"

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("start_node", self.get_start_node())
        builder.add_node("postprocess_node", self.postprocess_node_wrapper)
        
        # 使用 CleaningSubgraph 作为子图（数据清洗）
        cleaning_subgraph = CleaningSubgraph(
            checkpointer=self.checkpointer,
            store=self.store
        )
        # 添加数据清洗子图的开始和结束包装节点
        builder.add_node("data_cleaning_start", self.data_cleaning_start)
        builder.add_node("data_cleaning", cleaning_subgraph.build())
        builder.add_node("data_cleaning_end", self.data_cleaning_end)
        
        # 使用 MappingSubgraph 作为子图
        mapping_subgraph = MappingSubgraph(
            checkpointer=self.checkpointer,
            store=self.store
        )
        # 添加映射子图的开始和结束包装节点
        builder.add_node("mapping_subgraph_start", self.mapping_subgraph_start)
        builder.add_node("mapping_subgraph", mapping_subgraph.build())
        builder.add_node("mapping_subgraph_end", self.mapping_subgraph_end)
        
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
        
        # postprocess_node -> data_cleaning_start -> data_cleaning -> data_cleaning_end (先进行数据清洗)
        builder.add_edge("postprocess_node", "data_cleaning_start")
        builder.add_edge("data_cleaning_start", "data_cleaning")
        builder.add_edge("data_cleaning", "data_cleaning_end")
        
        # data_cleaning_end -> mapping_subgraph (检查是否需要映射)
        builder.add_conditional_edges(
            "data_cleaning_end",
            self.should_trigger_mapping,
            {
                "mapping_subgraph_start": "mapping_subgraph_start",
                "end_node": "end_node",
            }
        )
        
        # mapping_subgraph_start -> mapping_subgraph -> mapping_subgraph_end -> end_node
        builder.add_edge("mapping_subgraph_start", "mapping_subgraph")
        builder.add_edge("mapping_subgraph", "mapping_subgraph_end")
        builder.add_edge("mapping_subgraph_end", "end_node")
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
