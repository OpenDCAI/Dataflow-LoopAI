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
from langchain_core.messages import AIMessage
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

    @staticmethod
    def _emit_stream_event(
        state: LoopAIState,
        message: str,
        progress: float = 0.0,
        data: Optional[Dict[str, Any]] = None,
        current: Optional[str] = None,
    ) -> None:
        """Emit a richer StreamEvent payload for frontend observability."""
        writer = get_stream_writer()
        if not writer:
            return

        constructor_state = state.get("constructor", {})
        payload = {
            "agent": "constructor",
            "node": current or state.get("current", "ConstructorAgent.unknown"),
            "task_id": state.get("task_id", ""),
            "next_to": state.get("next_to", ""),
            "category": constructor_state.get("category", ""),
            "model": constructor_state.get("model_path", ""),
            "debug_mode": bool(constructor_state.get("debug", False)),
            "has_exception": bool(state.get("exception")),
        }
        if data:
            payload.update(data)

        writer(StreamEvent(
            current=current or state.get("current", "ConstructorAgent.unknown"),
            message=message,
            progress=progress,
            data=payload,
        ).json())
    
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
            ConstructorAgent._emit_stream_event(
                state,
                message="Constructor: 配置开始",
                progress=0.0,
                data={
                    "phase": "constructor_config",
                    "stage": "start",
                    "has_obtainer_state": bool(state.get("obtainer")),
                },
            )
            
            # Ensure constructor configuration is set in state
            # Use values from constructor if not already in state
            # Prompt 目录写入 state，保证后续节点（如 postprocess_node）能拿到正确的 prompt_loader
            if not state.get("prompt_template_dir") and self.prompt_template_dir:
                state["prompt_template_dir"] = self.prompt_template_dir
            
            # 确保 constructor 字典存在
            if "constructor" not in state:
                state["constructor"] = {}

            # 兼容迁移：若 constructor 关键字段缺失，则从 obtainer 补齐
            obtainer_state = state.get("obtainer", {})
            constructor = state["constructor"]
            fallback_keys = [
                "model_path", "base_url", "api_key", "temperature",
                "user_query", "datasets_background", "category", "subtasks",
                "intermediate_data_path", "confirmed_format", "pending_format",
                "mapping_auto_mode", "confirmation_result", "mapping_user_intent",
                "mapping_selected_format_id", "mapping_custom_description",
                "default_mapping_format", "llm_timeout", "max_retries",
                "max_concurrent_mapping", "max_samples_before_cleaning",
                "cleaning_random_seed", "debug",
                "postprocess_version", "benchmark_source_dir", "benchmark_pool_path",
                "benchmark_pool_size",
            ]
            for key in fallback_keys:
                if key not in constructor and key in obtainer_state:
                    constructor[key] = obtainer_state.get(key)

            # Some fields can exist but be null in constructor state snapshot.
            # Treat null as missing and fallback to obtainer state when possible.
            for key in fallback_keys:
                if constructor.get(key) is None and key in obtainer_state:
                    constructor[key] = obtainer_state.get(key)

            # Empty strings in constructor often mean "not initialized yet".
            # For shared runtime fields (e.g. category) prefer obtainer values.
            for key in fallback_keys:
                if key not in obtainer_state:
                    continue
                cur_val = constructor.get(key)
                if isinstance(cur_val, str) and not cur_val.strip():
                    incoming = obtainer_state.get(key)
                    if incoming is not None and (not isinstance(incoming, str) or incoming.strip()):
                        constructor[key] = incoming

            # model_path: 优先使用 constructor 中的值，其次使用 self.model_name
            if not constructor.get("model_path"):
                if self.model_name:
                    constructor["model_path"] = self.model_name
            
            # base_url: 优先使用 constructor 中的值，其次使用 self.base_url
            if not constructor.get("base_url"):
                if self.base_url:
                    constructor["base_url"] = self.base_url
            
            # api_key: 优先使用 constructor 中的值，其次使用 self.api_key
            if not constructor.get("api_key"):
                if self.api_key:
                    constructor["api_key"] = self.api_key
            
            # temperature: 优先使用 constructor 中的值，其次使用 self.temperature，默认 0.7
            if constructor.get("temperature") is None:
                if hasattr(self, 'temperature') and self.temperature is not None:
                    constructor["temperature"] = self.temperature
                else:
                    constructor["temperature"] = 0.7
            
            # Ensure output_dir is set
            if not state.get("output_dir"):
                state["output_dir"] = "./output"
            
            # Set default values for constructor-specific parameters
            if "llm_timeout" not in constructor:
                constructor["llm_timeout"] = 300.0
            
            if "max_retries" not in constructor:
                constructor["max_retries"] = 3
            
            if "max_concurrent_mapping" not in constructor:
                constructor["max_concurrent_mapping"] = 10
            
            # Mapping configuration
            # default_mapping_format: If set (e.g., "alpaca"), skip user interaction and use this format directly
            # If empty or not set, go through user interaction flow
            if "default_mapping_format" not in constructor:
                constructor["default_mapping_format"] = "alpaca"  # Default to alpaca format

            if "postprocess_version" not in constructor or not str(constructor.get("postprocess_version") or "").strip():
                constructor["postprocess_version"] = "agent_v2"
            if "benchmark_pool_path" not in constructor or not str(constructor.get("benchmark_pool_path") or "").strip():
                constructor["benchmark_pool_path"] = "outputs/benchmark_load/benchmark_pool.jsonl"
            if constructor.get("benchmark_pool_size") is None:
                constructor["benchmark_pool_size"] = 500
            
            # Handle debug mode
            debug_mode = constructor.get("debug", False)
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

            logger.info(f"ConstructorAgent: Configuration set - model: {constructor.get('model_path')}, "
                       f"base_url: {constructor.get('base_url')}, "
                       f"debug: {debug_mode}")
            
            # Manual-trigger support: when webcrawler_dataset_dir is not
            # already set (i.e. no auto-pipeline from WebCrawlerAgent),
            # probe the default output directory for historical data.
            if not constructor.get("webcrawler_dataset_dir"):
                default_wc_dir = os.path.join(
                    state.get("output_dir", "./output"), "webcrawler_dataset"
                )
                if os.path.isdir(default_wc_dir):
                    try:
                        has_jsonl = any(
                            f.endswith(".jsonl")
                            for f in os.listdir(default_wc_dir)
                            if not f.startswith(".")
                        )
                    except OSError:
                        has_jsonl = False
                    if has_jsonl:
                        constructor["webcrawler_dataset_dir"] = default_wc_dir
                        if not constructor.get("intermediate_data_path"):
                            constructor["intermediate_data_path"] = default_wc_dir
                        logger.info(
                            f"start_node: auto-detected webcrawler dataset "
                            f"at {default_wc_dir}"
                        )

            ConstructorAgent._emit_stream_event(
                state,
                message="Constructor: 配置完成",
                progress=1.0,
                data={
                    "phase": "constructor_config",
                    "stage": "completed",
                    "base_url": constructor.get("base_url"),
                    "default_mapping_format": constructor.get("default_mapping_format"),
                    "subtasks_count": len(constructor.get("subtasks") or []),
                },
            )
            
            return state
        
        return start_node

    @staticmethod
    def _canonical_mapping_metrics(mapping_results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        script_mapping_node / llm_mapping_node 写入 mapped_records、output_dir；
        end_node 与前端事件历史上读取 total_mapped_records、final_output_dir。
        此处统一解析，避免映射成功但摘要与 stream data 仍为 0 / 空。
        """
        empty = {
            "total_records": 0,
            "total_mapped_records": 0,
            "failed_records": 0,
            "final_output_dir": "",
        }
        if not mapping_results:
            return empty
        mapped = mapping_results.get("total_mapped_records")
        if mapped is None:
            mapped = mapping_results.get("mapped_records", 0)
        final_dir = mapping_results.get("final_output_dir") or mapping_results.get("output_dir", "")
        return {
            "total_records": mapping_results.get("total_records", 0),
            "total_mapped_records": mapped,
            "failed_records": mapping_results.get("failed_records", 0),
            "final_output_dir": final_dir,
        }

    @staticmethod
    @BaseAgent.set_current
    def end_node(state: LoopAIState):
        """
        End node for constructor agent
        Set next_to to return to parent graph and summarize results
        """
        logger.info(f"ConstructorAgent: Task completed, returning to parent graph")
        state['current'] = "ConstructorAgent.end_node"
        ConstructorAgent._emit_stream_event(
            state,
            message="Constructor: 结束节点开始",
            progress=0.0,
            data={"phase": "constructor_end", "stage": "start"},
        )
        
        # Generate summary of results for LLM
        summary_parts = []
        
        # 获取 constructor 状态
        constructor_state = state.get("constructor", {})
        
        # Check for exceptions
        if state.get("exception"):
            summary_parts.append(f"执行过程中出现错误: {state.get('exception')}")
        else:
            # postprocess v2 的 total_records_processed 是多数据源「导出累计」（清洗前），
            # 清洗后会少很多；若已跑映射，应用映射阶段的条数作为「实际产出」说明，避免误报。
            category = constructor_state.get("category", "PT")
            postprocess_results = constructor_state.get("postprocess_results") or {}
            mapping_results = constructor_state.get("mapping_results") or {}
            mm = (
                ConstructorAgent._canonical_mapping_metrics(mapping_results)
                if mapping_results
                else None
            )

            exported = int(postprocess_results.get("total_records_processed") or 0)
            output_dir_pp = postprocess_results.get("output_dir", "")

            merged_postprocess_mapping_line = False
            if exported > 0:
                if mm is not None and (
                    mm["total_records"] > 0 or mm["total_mapped_records"] > 0
                ):
                    summary_parts.append(
                        f"后处理与映射: v2 导出相关样本累计 {exported} 条（清洗前、多数据源汇总）；"
                        f"清洗后进入映射 {mm['total_records']} 条，成功映射输出 "
                        f"{mm['total_mapped_records']} 条 {category} 记录"
                    )
                    merged_postprocess_mapping_line = True
                else:
                    summary_parts.append(
                        f"后处理完成: 共处理 {exported} 条 {category} 数据记录"
                    )
                if output_dir_pp:
                    summary_parts.append(f"中间格式输出目录: {output_dir_pp}")

            if mm is not None:
                final_output_dir = mm["final_output_dir"]
                if final_output_dir:
                    if not merged_postprocess_mapping_line:
                        summary_parts.append(
                            f"格式映射完成: 共映射 {mm['total_mapped_records']} 条记录"
                        )
                    summary_parts.append(f"最终输出目录: {final_output_dir}")
        
        # Create summary message
        if summary_parts:
            summary_text = "数据构造任务执行完成:\n" + "\n".join(summary_parts)
        else:
            summary_text = "数据构造任务执行完成，但未找到相关数据。"

        summary_text += (
            "\n\n【建议下一步】若需开始模型训练，请在后续轮次中通过主调度 Agent 调用工具 check_motivation，"
            "并将 motivation 设为 train。\n"
            "<cmd>根据用户指令执行: train</cmd>"
        )
        
        # Add summary to messages so LLM can see it
        if "messages" not in state:
            state["messages"] = []
        
        # Keep message type aligned with other agents (e.g. Obtainer),
        # so MessagesState reducer and persistence behave consistently.
        
        state["messages"].append(AIMessage(content=summary_text))
        logger.info(f"ConstructorAgent: Added summary to messages: {summary_text[:100]}...")
        state["automated_query"]="constructor subagent complete"
        postprocess_results = constructor_state.get("postprocess_results", {})
        mapping_results = constructor_state.get("mapping_results", {})
        mm = ConstructorAgent._canonical_mapping_metrics(mapping_results)
        ConstructorAgent._emit_stream_event(
            state,
            message="Constructor: 任务完成",
            progress=1.0,
            data={
                "phase": "constructor_end",
                "stage": "completed",
                "summary_text": summary_text,
                # v2 导出累计（清洗前）；最终条数见 total_mapped_records / records_before_mapping
                "total_records_processed": postprocess_results.get("total_records_processed", 0),
                "records_before_mapping": mm["total_records"],
                "total_mapped_records": mm["total_mapped_records"],
                "final_output_dir": mm["final_output_dir"],
            },
        )
        
        # Clear one-shot webcrawler routing state so that subsequent
        # Constructor runs (e.g. for Obtainer data) are not hijacked
        # into the WebCrawler path again.
        ctor = state.get("constructor", {})
        if ctor.get("webcrawler_dataset_dir"):
            logger.info(
                "ConstructorAgent end_node: clearing webcrawler_dataset_dir "
                "to allow subsequent Obtainer-driven runs"
            )
            ctor.pop("webcrawler_dataset_dir", None)

        # Set next_to to query_node to return to parent graph
        state["next_to"] = "query_node"
        return state

    @staticmethod
    @BaseAgent.set_current
    def postprocess_node_wrapper(state: LoopAIState):
        """
        Wrapper for postprocess_node with StreamEvent.

        Supports two versions controlled by ``constructor.postprocess_version``:
          - ``legacy`` (default): calls the original ``postprocess_node``
          - ``agent_v2``: calls the new Postprocess sub-agent system
        """
        state['current'] = "ConstructorAgent.postprocess_node"
        constructor = state.get("constructor", {})
        version = constructor.get("postprocess_version", "agent_v2")

        # agent_v2: progress/events come from postprocess_agent._emit_postprocess_v2 (no duplicate wrapper start/end).
        if version != "agent_v2":
            ConstructorAgent._emit_stream_event(
                state,
                message=f"Constructor: 后处理开始 (version={version})",
                progress=0.0,
                data={
                    "phase": "postprocess",
                    "stage": "start",
                    "version": version,
                    "category": constructor.get("category", ""),
                },
            )

        try:
            if version == "agent_v2":
                state = ConstructorAgent._run_postprocess_v2(state)
            else:
                state = postprocess_node(state)
        except Exception as e:
            logger.error(f"Constructor postprocess_node error: {e}", exc_info=True)
            state["exception"] = f"Postprocess error: {str(e)}"
            ConstructorAgent._emit_stream_event(
                state,
                message=f"Constructor: 后处理异常: {str(e)[:200]}",
                progress=1.0,
                data={"error": str(e), "phase": "postprocess", "stage": "failed"},
            )
            return state

        if version == "agent_v2":
            if state.get("exception"):
                err = str(state["exception"])
                ConstructorAgent._emit_stream_event(
                    state,
                    message=f"Constructor: 后处理失败: {err[:200]}",
                    progress=1.0,
                    data={"error": err, "phase": "postprocess", "stage": "failed"},
                )
            return state

        res = state.get("constructor", {}).get("postprocess_results", {})
        ConstructorAgent._emit_stream_event(
            state,
            message="Constructor: 后处理完成",
            progress=1.0,
            data={
                "phase": "postprocess",
                "stage": "completed",
                "total_records_processed": res.get("total_records_processed", 0),
                "processed_sources_count": res.get("processed_sources_count", 0),
                "output_dir": res.get("output_dir", ""),
            },
        )
        return state

    @staticmethod
    def _run_postprocess_v2(state: LoopAIState) -> LoopAIState:
        """Delegate to the new Postprocess sub-agent system (agent_v2)."""
        from loopai.agents.Postprocess import run_postprocess_agent_v2

        constructor = state.get("constructor", {})

        download_dir = os.getenv("DOWNLOAD_DIR")
        if not download_dir:
            download_dir = state.get("download_dir")
            if not download_dir:
                output_dir = state.get("output_dir", "./output")
                download_dir = os.path.join(output_dir, "downloads")

        user_query = state.get("automated_query", "")
        if not user_query and state.get("messages"):
            from langchain_core.messages import HumanMessage
            for msg in reversed(state["messages"]):
                if isinstance(msg, HumanMessage) and hasattr(msg, "content"):
                    user_query = msg.content
                    break
                if isinstance(msg, dict):
                    msg_type = str(msg.get("type", "")).lower()
                    msg_role = str(msg.get("role", "")).lower()
                    if msg_type == "human" or msg_role == "user":
                        user_query = str(msg.get("content", "")).strip()
                        if user_query:
                            break

        category = constructor.get("category", "PT").upper()
        if category not in ("PT", "SFT"):
            category = "PT"

        tavily_api_key = (
            state.get("obtainer", {}).get("tavily_api_key", "")
            or os.getenv("TAVILY_API_KEY", "")
        )

        benchmark_dir = (
            (constructor.get("benchmark_source_dir") or "").strip()
            or (state.get("banckmark_jsonl_path") or "").strip()
        )

        result = run_postprocess_agent_v2(
            download_dir=download_dir,
            user_query=user_query,
            category=category,
            model_name=constructor.get("model_path", ""),
            base_url=constructor.get("base_url", ""),
            api_key=constructor.get("api_key", ""),
            temperature=constructor.get("temperature", 0.0),
            datasets_background=constructor.get("datasets_background", ""),
            tavily_api_key=tavily_api_key if tavily_api_key else None,
            store=None,
            thread_id=state.get("task_id", "default"),
            event_name=state.get("current", "ConstructorAgent.postprocess_node"),
            benchmark_dir=benchmark_dir,
            enable_benchmark_reference=True,
        )

        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            if "constructor" not in state:
                state["constructor"] = {}
            state["constructor"]["postprocess_results"] = {
                "total_records_processed": result.get("total_records_processed", 0),
                "processed_sources_count": result.get("processed_sources_count", 0),
                "output_dir": result.get("output_dir", ""),
                "related_jsonl_dir": result.get("related_jsonl_dir", ""),
                "benchmark_source_count": result.get("benchmark_source_count", 0),
                "benchmark_sampled_count": result.get("benchmark_sampled_count", 0),
                "benchmark_samples_file": result.get("benchmark_samples_file", ""),
            }
            state["constructor"]["benchmark_samples_path"] = result.get("benchmark_samples_file", "")
            out_dir = result.get("output_dir", "")
            related_dir = (result.get("related_jsonl_dir") or "").strip()
            intermediate = ""
            if related_dir and os.path.isdir(related_dir):
                try:
                    has_jsonl = any(
                        fn.endswith(".jsonl")
                        and os.path.isfile(os.path.join(related_dir, fn))
                        for fn in os.listdir(related_dir)
                    )
                except OSError:
                    has_jsonl = False
                if has_jsonl:
                    intermediate = related_dir
            if not intermediate and out_dir and os.path.exists(out_dir):
                intermediate = out_dir
            if intermediate:
                state["constructor"]["intermediate_data_path"] = intermediate
                logger.info(f"Postprocess v2: intermediate data at {intermediate}")

        return state

    @staticmethod
    @BaseAgent.set_current
    def data_cleaning_start(state: LoopAIState):
        """
        Start node for data_cleaning subgraph with StreamEvent.
        """
        state['current'] = "ConstructorAgent.data_cleaning"
        ConstructorAgent._emit_stream_event(
            state,
            message="Constructor: 数据清洗开始",
            progress=0.0,
            data={"phase": "data_cleaning", "stage": "start"},
        )

        return state

    @staticmethod
    @BaseAgent.set_current
    def data_cleaning_end(state: LoopAIState):
        """
        End node for data_cleaning subgraph with StreamEvent
        """
        from loopai.logger import get_logger
        get_logger().info("=== data_cleaning_end: ENTER ===")
        cleaning_results = state.get("constructor", {}).get("cleaning_results", {})
        tools_executed = cleaning_results.get("tools_executed", [])
        has_exception = bool(state.get("exception"))
        ConstructorAgent._emit_stream_event(
            state,
            message="Constructor: 数据清洗完成" if not has_exception else "Constructor: 数据清洗完成(有异常)",
            progress=1.0,
            data={
                "phase": "data_cleaning",
                "stage": "completed" if not has_exception else "completed_with_errors",
                "tools_executed": [t.get("tool") if isinstance(t, dict) else str(t) for t in tools_executed],
                "tools_count": len(tools_executed),
            },
            current=state.get('current', 'ConstructorAgent.data_cleaning'),
        )
        return state

    @staticmethod
    @BaseAgent.set_current
    def mapping_subgraph_start(state: LoopAIState):
        """
        Start node for mapping_subgraph with StreamEvent
        """
        state['current'] = "ConstructorAgent.mapping_subgraph"
        constructor_state = state.get("constructor", {})
        ConstructorAgent._emit_stream_event(
            state,
            message="Constructor: 格式映射开始",
            progress=0.0,
            data={
                "phase": "mapping",
                "stage": "start",
                "confirmed_format": (constructor_state.get("confirmed_format") or {}).get("format_id", ""),
                "auto_mode": constructor_state.get("mapping_auto_mode", ""),
            },
        )
        return state

    @staticmethod
    @BaseAgent.set_current
    def mapping_subgraph_end(state: LoopAIState):
        """
        End node for mapping_subgraph with StreamEvent
        """
        mapping_results = state.get("constructor", {}).get("mapping_results", {})
        mm = ConstructorAgent._canonical_mapping_metrics(mapping_results)
        has_exception = bool(state.get("exception"))
        ConstructorAgent._emit_stream_event(
            state,
            message="Constructor: 格式映射完成" if not has_exception else "Constructor: 格式映射完成(有异常)",
            progress=1.0,
            data={
                "phase": "mapping",
                "stage": "completed" if not has_exception else "completed_with_errors",
                "total_records": mm["total_records"],
                "total_mapped_records": mm["total_mapped_records"],
                "failed_records": mm["failed_records"],
                "final_output_dir": mm["final_output_dir"],
            },
            current=state.get('current', 'ConstructorAgent.mapping_subgraph'),
        )
        return state

    @staticmethod
    def has_successful_downloads(state: LoopAIState) -> str:
        """
        Conditional edge function: check if there are successful downloads
        
        Returns:
            "postprocess_node" if there are successful downloads, "end_node" otherwise
        """
        constructor_state = state.get("constructor", {})
        subtasks = constructor_state.get("subtasks") or state.get("obtainer", {}).get("subtasks") or []
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
        
        has_download_files = False
        if os.path.isdir(download_dir):
            try:
                has_download_files = any(
                    os.path.isfile(os.path.join(download_dir, f)) or os.path.isdir(os.path.join(download_dir, f))
                    for f in os.listdir(download_dir)
                    if not f.startswith('.') and f not in ['processed_output', '.tmp', '.cache']
                )
            except OSError as e:
                logger.warning(f"Failed to inspect download directory '{download_dir}': {e}")
        
        if successful_downloads or has_download_files:
            logger.info(f"Found {len(successful_downloads)} successful downloads or files in download directory, routing to postprocess_node")
            ConstructorAgent._emit_stream_event(
                state,
                message="Constructor: 检测到可处理下载结果，进入后处理",
                progress=0.05,
                data={
                    "phase": "routing",
                    "route_to": "postprocess_node",
                    "successful_downloads_count": len(successful_downloads),
                    "has_download_files": bool(has_download_files),
                    "download_dir": download_dir,
                },
                current=state.get("current", "ConstructorAgent.start_node"),
            )
            return "postprocess_node"
        else:
            logger.info("No successful downloads found, routing to end_node")
            ConstructorAgent._emit_stream_event(
                state,
                message="Constructor: 未检测到可处理下载结果，直接结束",
                progress=0.05,
                data={
                    "phase": "routing",
                    "route_to": "end_node",
                    "successful_downloads_count": len(successful_downloads),
                    "has_download_files": bool(has_download_files),
                    "download_dir": download_dir,
                },
                current=state.get("current", "ConstructorAgent.start_node"),
            )
            return "end_node"

    @staticmethod
    def route_after_start(state: LoopAIState) -> str:
        """
        Top-level routing after start_node.

        Adds a **new** WebCrawler entry path while keeping the original
        Obtainer path (has_successful_downloads) completely unchanged.

        Priority:
        1. WebCrawler dataset (constructor.webcrawler_dataset_dir with .jsonl
           files) → "data_cleaning_start" (skip postprocess, data is already
           structured by webcrawler_dataset_node).
        2. Otherwise → delegate to has_successful_downloads which returns
           "postprocess_node" or "end_node" as before.
        """
        constructor_state = state.get("constructor", {})
        webcrawler_dataset_dir = constructor_state.get("webcrawler_dataset_dir", "")

        if webcrawler_dataset_dir and os.path.isdir(webcrawler_dataset_dir):
            try:
                has_jsonl = any(
                    f.endswith(".jsonl")
                    for f in os.listdir(webcrawler_dataset_dir)
                    if not f.startswith(".")
                )
            except OSError:
                has_jsonl = False

            if has_jsonl:
                logger.info(
                    f"route_after_start: WebCrawler dataset detected at "
                    f"'{webcrawler_dataset_dir}', routing to data_cleaning_start"
                )
                ConstructorAgent._emit_stream_event(
                    state,
                    message="Constructor: 检测到 WebCrawler 数据集，直接进入数据清洗",
                    progress=0.05,
                    data={
                        "phase": "routing",
                        "route_to": "data_cleaning_start",
                        "source": "webcrawler",
                        "webcrawler_dataset_dir": webcrawler_dataset_dir,
                    },
                    current=state.get("current", "ConstructorAgent.start_node"),
                )
                return "data_cleaning_start"

        return ConstructorAgent.has_successful_downloads(state)

    @staticmethod
    def should_run_data_cleaning(state: LoopAIState) -> str:
        """
        Conditional: skip data_cleaning subgraph when no intermediate_data_path (nothing to clean).
        Avoids running subgraph when postprocess filtered out all files.
        """
        constructor_state = state.get("constructor", {})
        intermediate_path = constructor_state.get("intermediate_data_path", "")
        if intermediate_path and os.path.exists(intermediate_path):
            return "data_cleaning"
        from loopai.logger import get_logger
        get_logger().info("No intermediate_data_path, skipping data_cleaning subgraph")
        return "data_cleaning_end"

    @staticmethod
    def should_trigger_mapping(state: LoopAIState) -> str:
        """
        Conditional edge function: check if mapping should be triggered
        
        Returns:
            "mapping_subgraph_start" if intermediate data exists and format not confirmed, "end_node" otherwise
        """
        from loopai.logger import get_logger
        get_logger().info("=== should_trigger_mapping: ENTER ===")
        constructor_state = state.get("constructor", {})
        intermediate_path = constructor_state.get("intermediate_data_path", "")
        if intermediate_path and os.path.exists(intermediate_path):
            confirmed_format = constructor_state.get("confirmed_format")
            if not confirmed_format:
                logger.info("Intermediate data found, routing to mapping_subgraph_start for format selection")
                ConstructorAgent._emit_stream_event(
                    state,
                    message="Constructor: 检测到中间数据，进入格式映射",
                    progress=0.75,
                    data={
                        "phase": "routing",
                        "route_to": "mapping_subgraph_start",
                        "intermediate_data_path": intermediate_path,
                    },
                    current=state.get("current", "ConstructorAgent.data_cleaning"),
                )
                return "mapping_subgraph_start"
            else:
                logger.info("Format already confirmed, skipping mapping_subgraph")
                ConstructorAgent._emit_stream_event(
                    state,
                    message="Constructor: 已确认格式，跳过映射流程",
                    progress=0.75,
                    data={
                        "phase": "routing",
                        "route_to": "end_node",
                        "intermediate_data_path": intermediate_path,
                        "confirmed_format": confirmed_format.get("format_id", "") if isinstance(confirmed_format, dict) else str(confirmed_format),
                    },
                    current=state.get("current", "ConstructorAgent.data_cleaning"),
                )
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
        
        # start_node routing:
        #   route_after_start checks for WebCrawler dataset first (new path),
        #   then falls through to the original has_successful_downloads logic.
        builder.add_conditional_edges(
            "start_node",
            self.route_after_start,
            {
                "data_cleaning_start": "data_cleaning_start",
                "postprocess_node": "postprocess_node",
                "end_node": "end_node",
            }
        )
        
        # postprocess_node -> data_cleaning_start -> (conditional) data_cleaning or skip to data_cleaning_end
        builder.add_edge("postprocess_node", "data_cleaning_start")
        builder.add_conditional_edges(
            "data_cleaning_start",
            self.should_run_data_cleaning,
            {
                "data_cleaning": "data_cleaning",
                "data_cleaning_end": "data_cleaning_end",
            }
        )
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
