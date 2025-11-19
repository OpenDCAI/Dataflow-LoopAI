import os
import logging
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent

from loopai.logger import get_logger
from loopai.agents.Obtainer.nodes import websearch_node, download_node, postprocess_node, deep_explore_node

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


class ObtainerAgent(BaseAgent):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "Obtainer"

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
            Start node for obtainer agent
            Ensure configuration parameters are set in state
            """
            logger.info(f"ObtainerAgent: Starting task")
            
            # Ensure obtainer configuration is set in state
            # Use values from constructor if not already in state
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
            
            # Auto-detect category from user query if not specified
            if "obtainer_category" not in state or not state.get("obtainer_category"):
                # Try to extract category from user query
                user_query = ""
                if state.get("messages") and len(state["messages"]) > 0:
                    last_message = state["messages"][-1]
                    if hasattr(last_message, "content"):
                        user_query = last_message.content
                    elif isinstance(last_message, dict):
                        user_query = last_message.get("content", "")
                
                if not user_query:
                    user_query = state.get("automated_query", "")
                
                # Check for SFT keywords in query
                user_query_lower = user_query.lower()
                if any(keyword in user_query_lower for keyword in ["sft", "supervised fine-tuning", "fine-tuning", "微调", "问答", "qa", "question", "answer"]):
                    state["obtainer_category"] = "SFT"
                    logger.info("Auto-detected category: SFT from user query")
                else:
                    state["obtainer_category"] = "PT"
                    logger.info("Auto-detected category: PT (default)")
            else:
                # Ensure category is uppercase
                state["obtainer_category"] = state["obtainer_category"].upper()
            
            # Ensure output_dir is set
            if not state.get("output_dir"):
                state["output_dir"] = "./output"
            
            # Set default values for obtainer-specific parameters if not in state
            if "obtainer_search_engine" not in state:
                state["obtainer_search_engine"] = "tavily"
            
            if "obtainer_max_urls" not in state:
                state["obtainer_max_urls"] = 10
            
            if "obtainer_max_download_subtasks" not in state:
                state["obtainer_max_download_subtasks"] = None
            
            # RAG configuration (independent from obtainer, set defaults if not in state)
            if "obtainer_reset_rag" not in state:
                state["obtainer_reset_rag"] = False
            
            if "obtainer_rag_embed_model" not in state:
                state["obtainer_rag_embed_model"] = ""
            
            if "obtainer_rag_collection_name" not in state:
                state["obtainer_rag_collection_name"] = "rag_collection"
            
            # RAG API config (if not set, will use obtainer's API config in websearch_node)
            if "obtainer_rag_api_base_url" not in state:
                state["obtainer_rag_api_base_url"] = ""
            
            if "obtainer_rag_api_key" not in state:
                state["obtainer_rag_api_key"] = ""
            
            if "obtainer_kaggle_username" not in state:
                state["obtainer_kaggle_username"] = ""
            
            if "obtainer_kaggle_key" not in state:
                state["obtainer_kaggle_key"] = ""
            
            if "obtainer_tavily_api_key" not in state:
                # Try to read from state first, then environment variable, then from file
                tavily_api_key = state.get("obtainer_tavily_api_key", "")
                if not tavily_api_key:
                    tavily_api_key = os.getenv("TAVILY_API_KEY", "")
                if not tavily_api_key:
                    # Try to read from examples/scripts/tavily_api_key.txt
                    script_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "examples", "scripts")
                    tavily_api_key_file = os.path.join(script_dir, "tavily_api_key.txt")
                    if os.path.exists(tavily_api_key_file):
                        try:
                            with open(tavily_api_key_file, 'r', encoding='utf-8') as f:
                                tavily_api_key = f.read().strip()
                                logger.info(f"Loaded Tavily API key from {tavily_api_key_file}")
                        except Exception as e:
                            logger.debug(f"Failed to read Tavily API key from file: {e}")
                state["obtainer_tavily_api_key"] = tavily_api_key
            
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
                log_dir = os.path.join(output_dir, "obtainer_logs")
                os.makedirs(log_dir, exist_ok=True)
                
                # Check if file handler already exists
                has_file_handler = any(
                    isinstance(h, logging.FileHandler) and "obtainer_debug" in h.baseFilename
                    for h in logger.handlers
                )
                
                if not has_file_handler:
                    from datetime import datetime
                    log_file = os.path.join(log_dir, f"obtainer_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
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
            
            logger.info(f"ObtainerAgent: Configuration set - model: {state.get('obtainer_model_path')}, "
                       f"base_url: {state.get('obtainer_base_url')}, category: {state.get('obtainer_category')}, "
                       f"search_engine: {state.get('obtainer_search_engine')}, max_urls: {state.get('obtainer_max_urls')}, "
                       f"debug: {debug_mode}")
            
            # Send custom stream event if debug mode is enabled
            if debug_mode:
                try:
                    writer = get_stream_writer()
                    if writer:
                        writer(StreamEvent(
                            current=state.get('current', 'obtainer_start_node'),
                            message="ObtainerAgent configuration initialized",
                            data={
                                'model': state.get('obtainer_model_path'),
                                'base_url': state.get('obtainer_base_url'),
                                'category': state.get('obtainer_category'),
                                'search_engine': state.get('obtainer_search_engine'),
                                'max_urls': state.get('obtainer_max_urls'),
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
        End node for obtainer agent
        Set next_to to return to parent graph and summarize results
        """
        logger.info(f"ObtainerAgent: Task completed, returning to parent graph")
        
        # Generate summary of results for LLM
        summary_parts = []
        
        # Check for exceptions
        if state.get("exception"):
            summary_parts.append(f"执行过程中出现错误: {state.get('exception')}")
        else:
            # Summarize research results
            research_summary = state.get("obtainer_research_summary", "")
            if research_summary:
                summary_parts.append(f"研究摘要: {research_summary[:200]}...")
            
            # Summarize subtasks
            subtasks = state.get("obtainer_subtasks", [])
            if subtasks:
                download_tasks = [t for t in subtasks if t.get("type") == "download"]
                completed = [t for t in download_tasks if t.get("status") == "completed_successfully"]
                failed = [t for t in download_tasks if t.get("status") == "failed_to_download"]
                
                summary_parts.append(f"共生成 {len(download_tasks)} 个下载任务")
                if completed:
                    summary_parts.append(f"成功下载 {len(completed)} 个数据集")
                    for task in completed[:3]:  # Show first 3
                        summary_parts.append(f"  - {task.get('objective', 'N/A')}: {task.get('method_used', 'N/A')}")
                if failed:
                    summary_parts.append(f"失败 {len(failed)} 个下载任务")
            
            # Summarize post-process results
            postprocess_results = state.get("obtainer_postprocess_results", {})
            if postprocess_results:
                total_records = postprocess_results.get("total_records_processed", 0)
                if total_records > 0:
                    category = state.get("obtainer_category", "PT")
                    summary_parts.append(f"后处理完成: 共处理 {total_records} 条 {category} 数据记录")
                    output_dir = postprocess_results.get("output_dir", "")
                    if output_dir:
                        summary_parts.append(f"输出目录: {output_dir}")
        
        # Create summary message
        if summary_parts:
            summary_text = "数据获取任务执行完成:\n" + "\n".join(summary_parts)
        else:
            summary_text = "数据获取任务执行完成，但未找到相关数据。"
        
        # Add summary to messages so LLM can see it
        from langchain_core.messages import AIMessage
        if "messages" not in state:
            state["messages"] = []
        
        # Add summary as AI message
        state["messages"].append(AIMessage(content=summary_text))
        logger.info(f"ObtainerAgent: Added summary to messages: {summary_text[:100]}...")
        
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
                        'research_summary': state.get("obtainer_research_summary", ""),
                        'subtasks_count': len(state.get("obtainer_subtasks", [])),
                        'urls_visited_count': len(state.get("obtainer_urls_visited", [])),
                        'download_results': state.get("obtainer_download_results", {}),
                        'postprocess_results': state.get("obtainer_postprocess_results", {})
                    }
                    writer(StreamEvent(
                        current=state.get('current', 'obtainer_end_node'),
                        message="ObtainerAgent task completed",
                        data=summary_data
                    ).json())
            except Exception as e:
                # Stream writer might not be available in all contexts
                logger.debug(f"Could not send stream event: {e}")
        
        # Set next_to to query_node to return to parent graph
        # The parent graph has: builder.add_edge('obtain_node', 'query_node')
        # So when this subgraph finishes, it will automatically go to query_node
        state["next_to"] = "query_node"
        return state

    @staticmethod
    def has_download_tasks(state: LoopAIState) -> str:
        """
        Conditional edge function: check if there are download tasks
        
        Returns:
            "download_node" if there are download tasks, "end_node" otherwise
        """
        subtasks = state.get("obtainer_subtasks", [])
        download_tasks = [task for task in subtasks if task.get("type") == "download"]
        if download_tasks:
            logger.info(f"Found {len(download_tasks)} download tasks, routing to download_node")
            return "download_node"
        else:
            logger.info("No download tasks found, routing to end_node")
            return "end_node"

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
        if successful_downloads:
            logger.info(f"Found {len(successful_downloads)} successful downloads, routing to postprocess_node")
            return "postprocess_node"
        else:
            logger.info("No successful downloads found, routing to end_node")
            return "end_node"

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("start_node", self.get_start_node())
        builder.add_node("websearch_node", websearch_node)
        builder.add_node("deep_explore_node", deep_explore_node)  # 占位节点，未实现，不接入工作流
        builder.add_node("download_node", download_node)
        builder.add_node("postprocess_node", postprocess_node)
        builder.add_node("end_node", self.end_node)
        builder.set_entry_point("start_node")
        builder.add_edge("start_node", "websearch_node")
        builder.add_conditional_edges(
            "websearch_node",
            self.has_download_tasks,
            {
                "download_node": "download_node",
                "end_node": "end_node",
            }
        )
        builder.add_conditional_edges(
            "download_node",
            self.has_successful_downloads,
            {
                "postprocess_node": "postprocess_node",
                "end_node": "end_node",
            }
        )
        builder.add_edge("postprocess_node", "end_node")
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

