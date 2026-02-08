import os
import logging
import asyncio
import shutil
import time
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent

from loopai.logger import get_logger
from loopai.agents.Obtainer.nodes import websearch_node, download_node, deep_explore_node
from loopai.agents.Obtainer.utils import CategoryClassifier, ObtainQueryNormalizer, TaskDecomposer, RAGManager
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
        def start_node(state: LoopAIState):
            """
            Start node for obtainer agent
            Ensure configuration parameters are set in state
            """
            logger.info(f"ObtainerAgent: Starting task")
            state['current'] = "ObtainerAgent.start_node"
            writer = get_stream_writer()
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message="ObtainerAgent Config Start",
                    progress=0
                ).json())
            
            # Ensure obtainer configuration is set in state
            # Use values from constructor if not already in state
            # Prompt 目录写入 state，保证后续节点（如 postprocess_node）能拿到正确的 prompt_loader
            if not state.get("prompt_template_dir") and self.prompt_template_dir:
                print(self.prompt_template_dir)
                state["prompt_template_dir"] = self.prompt_template_dir
            
            # Initialize obtainer dict if not exists
            if "obtainer" not in state:
                state["obtainer"] = {}
            
            if not state.get("obtainer", {}).get("model_path"):
                if self.model_name:
                    state.setdefault("obtainer", {})["model_path"] = self.model_name
                elif state.get("analyze_model_path"):
                    state.setdefault("obtainer", {})["model_path"] = state["analyze_model_path"]
            
            if not state.get("obtainer", {}).get("base_url"):
                if self.base_url:
                    state.setdefault("obtainer", {})["base_url"] = self.base_url
                elif state.get("analyze_base_url"):
                    state.setdefault("obtainer", {})["base_url"] = state["analyze_base_url"]
            
            if not state.get("obtainer", {}).get("api_key"):
                if self.api_key:
                    state.setdefault("obtainer", {})["api_key"] = self.api_key
                elif state.get("analyze_api_key"):
                    state.setdefault("obtainer", {})["api_key"] = state["analyze_api_key"]
            
            if "temperature" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["temperature"] = self.temperature if hasattr(self, 'temperature') else 0.7
            
            # Prepare prompt loader for downstream nodes
            prompt_loader = None
            if state.get("prompt_template_dir"):
                try:
                    prompt_loader = PromptLoader(state.get("prompt_template_dir"))
                except Exception as e:
                    logger.debug(f"PromptLoader init failed, will use defaults: {e}")
            
            # Extract dataset background from user query (category will be determined per subtask)
            # Try to extract dataset background from user query
            user_query = ""
            objective = ""
            
            if state.get("messages") and len(state["messages"]) > 0:
                last_message = state["messages"][-4]
                if hasattr(last_message, "content"):
                    user_query = last_message.content
                elif isinstance(last_message, dict):
                    user_query = last_message.get("content", "")
            
            if not user_query:
                user_query = state.get("automated_query", "")
            
            # Write user_query to state if available
            if user_query:
                state.setdefault("obtainer", {})["user_query"] = user_query
            
            # Get objective if available
            objective = state.get("automated_query", user_query)
            
            # LLM normalize: detect eval-based recommendations and rewrite to dataset request
            logger.info(f"[Obtainer] Using user_query for dataset background extraction: {user_query[:200]}...")
            if user_query:
                try:
                    model_name = state.get("obtainer", {}).get("model_path") or state.get("analyze_model_path")
                    base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
                    api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
                    temperature = state.get("obtainer", {}).get("temperature", 0.2)
                    if model_name and base_url and api_key:
                        normalizer = ObtainQueryNormalizer(
                            model_name=model_name,
                            base_url=base_url,
                            api_key=api_key,
                            temperature=temperature,
                            prompt_loader=prompt_loader,
                        )
                        norm_result = asyncio.run(
                            normalizer.normalize(
                                user_query=user_query,
                                objective=objective
                            )
                        ) or {}
                        intent_type = norm_result.get("intent_type")
                        if intent_type:
                            state.setdefault("obtainer", {})["intent_type"] = intent_type
                        normalized_query = norm_result.get("normalized_query")
                        reason = norm_result.get("reason", "")
                        if normalized_query:
                            state.setdefault("obtainer", {})["normalized_query"] = normalized_query
                            state.setdefault("obtainer", {})["normalized_reason"] = reason
                            # Only override when a rewrite is provided
                            if normalized_query != user_query:
                                state["automated_query"] = normalized_query
                                user_query = normalized_query
                                state.setdefault("obtainer", {})["user_query"] = user_query
                                objective = normalized_query
                                logger.info(
                                    f"Obtain query normalized from eval-style to dataset request: "
                                    f"{normalized_query[:120]}..."
                                )
                    else:
                        logger.debug("Skip normalization: missing model config")
                except Exception as e:
                    logger.warning(f"Query normalization failed, continue with original query: {e}")
                
                # Extract dataset background using LLM (but not category - category will be determined per subtask)
                if user_query:
                    try:
                        # Get model configuration
                        model_name = state.get("obtainer", {}).get("model_path") or state.get("analyze_model_path")
                        base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
                        api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
                        temperature = state.get("obtainer", {}).get("temperature", 0.3)  # Lower temperature for classification
                        logger.info(f"Dataset background extraction - model_name: {model_name}, base_url: {base_url}, api_key: {'***' if api_key else ''}, temperature: {temperature}, user_query: {user_query[:100] if user_query else ''}, objective: {objective[:100] if objective else ''}")
                        if model_name and base_url and api_key:
                            # Initialize category classifier (used for background extraction)
                            category_classifier = CategoryClassifier(
                                model_name=model_name,
                                base_url=base_url,
                                api_key=api_key,
                                temperature=temperature,
                                prompt_loader=prompt_loader,
                            )
                            
                            # Extract dataset background using LLM (category will be determined per subtask)
                            logger.info("Extracting dataset background from user query...")
                            classification_result = asyncio.run(
                                category_classifier.classify_category(
                                    user_query=user_query,
                                    objective=objective
                                )
                            )
                            # Extract dataset_background but do not set obtainer_category
                            if isinstance(classification_result, dict):
                                dataset_background = classification_result.get("dataset_background", "")
                                if dataset_background:
                                    state.setdefault("obtainer", {})["datasets_background"] = dataset_background
                                    logger.info(f"Extracted dataset background: {dataset_background[:100]}...")
                                else:
                                    # Use user_query as fallback dataset_background
                                    state.setdefault("obtainer", {})["datasets_background"] = user_query if user_query else objective
                                    logger.info("No dataset background extracted, using user_query as fallback")
                            else:
                                # Backward compatibility: if it returns a string, use user_query as fallback
                                state.setdefault("obtainer", {})["datasets_background"] = user_query if user_query else objective
                                logger.info("Classification result is string format, using user_query as fallback for dataset background")
                        else:
                            # Fallback: use user_query as dataset_background
                            logger.warning("Model configuration missing, using user_query as dataset background")
                            state.setdefault("obtainer", {})["datasets_background"] = user_query if user_query else objective
                    except Exception as e:
                        logger.error(f"Error in dataset background extraction: {e}, using user_query as fallback")
                        # Fallback: use user_query as dataset_background
                        state.setdefault("obtainer", {})["datasets_background"] = user_query if user_query else objective
                else:
                    # No user query available, use empty string
                    state.setdefault("obtainer", {})["datasets_background"] = ""
                    logger.info("No user query found, dataset background not set")
            
            # If user explicitly specified category, ensure it is uppercase (for backward compatibility)
            if state.get("obtainer", {}).get("category"):
                state.setdefault("obtainer", {})["category"] = state.get("obtainer", {}).get("category", "").upper()
            
            # Ensure output_dir is set
            if not state.get("output_dir"):
                state["output_dir"] = "./output"
            
            # Set default values for obtainer-specific parameters if not in state
            if "search_engine" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["search_engine"] = "tavily"
            
            if "max_urls" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["max_urls"] = 10
            
            # Web exploration forest parameters (for websearch_node)
            if "max_depth" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["max_depth"] = 4  # Maximum exploration depth
            
            if "concurrent_limit" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["concurrent_limit"] = 3 
            
            if "topk_urls" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["topk_urls"] = 5  # Top-k URLs to select from each page
            
            if "url_timeout" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["url_timeout"] = 60  # Timeout in seconds for each URL exploration (default: 60s)
            
            if "max_download_subtasks" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["max_download_subtasks"] = 5  # 每个子任务最多5个下载子任务
            
            # 设置递归上限为50，支持更复杂的图结构
            # recursion_limit 会在调用图时通过 config 参数传递
            if "recursion_limit" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["recursion_limit"] = 50
            
            # RAG configuration (independent from obtainer, set defaults if not in state)
            if "reset_rag" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["reset_rag"] = False
            
            if "rag_embed_model" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["rag_embed_model"] = ""
            
            if "rag_collection_name" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["rag_collection_name"] = "rag_collection"
            
            # RAG API config (if not set, will use obtainer's API config in websearch_node)
            if "rag_api_base_url" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["rag_api_base_url"] = ""
            
            if "rag_api_key" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["rag_api_key"] = ""
            
            if "kaggle_username" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["kaggle_username"] = ""
            
            if "kaggle_key" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["kaggle_key"] = ""
            
            if "tavily_api_key" not in state.get("obtainer", {}):
                # Try to read from state first, then environment variable, then from file
                tavily_api_key = state.get("obtainer", {}).get("tavily_api_key", "")
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
                state.setdefault("obtainer", {})["tavily_api_key"] = tavily_api_key
            
            # Mapping configuration
            # default_mapping_format: If set (e.g., "alpaca"), skip user interaction and use this format directly
            # If empty or not set, go through user interaction flow
            if "default_mapping_format" not in state.get("obtainer", {}):
                state.setdefault("obtainer", {})["default_mapping_format"] = "alpaca"  # Empty means user interaction mode
            
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

            logger.info(f"ObtainerAgent: Configuration set - model: {state.get('obtainer', {}).get('model_path')}, "
                       f"base_url: {state.get('obtainer', {}).get('base_url')}, category: {state.get('obtainer', {}).get('category')}, "
                       f"search_engine: {state.get('obtainer', {}).get('search_engine')}, max_urls: {state.get('obtainer', {}).get('max_urls')}, "
                       f"debug: {debug_mode}")
            
            # Send configuration stream event (always, not debug_mode only)
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message="ObtainerAgent 配置初始化完成",
                    progress=1,
                    data={
                        'model': state.get('obtainer', {}).get('model_path'),
                        'base_url': state.get('obtainer', {}).get('base_url'),
                        'category': state.get('obtainer', {}).get('category'),
                        'search_engine': state.get('obtainer', {}).get('search_engine'),
                        'max_urls': state.get('obtainer', {}).get('max_urls'),
                        'debug_mode': debug_mode
                    }
                ).json())

            return state
        
        return start_node

    def get_task_decomposer_node(self):
        """
        Get task decomposer node function that can access self
        """
        @BaseAgent.set_current
        def task_decomposer_node(state: LoopAIState):
            """
            Task decomposer node: decompose user input into task list
            """
            logger.info("ObtainerAgent: Task Decomposer Node - Starting task decomposition")
            state['current'] = "ObtainerAgent.task_decomposer_node"
            writer = get_stream_writer()
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message="Task Decomposer Start",
                    progress=0
                ).json())

            
            # Check if task_list already exists (skip if already decomposed)
            if state.get("obtainer", {}).get("task_list") and len(state.get("obtainer", {}).get("task_list", [])) > 0:
                logger.info(f"Task list already exists with {len(state.get('obtainer', {}).get('task_list', []))} tasks, skipping decomposition")
                return state
            
            # Get user input - find the last HumanMessage, not just the last message
            # Priority: 1. Original HumanMessage from messages, 2. automated_query
            user_input = ""
            
            # Extract user message from messages list
            # Look for the last HumanMessage in the messages list (this is the original user input)
            if state.get("messages") and len(state["messages"]) > 0:
                from langchain_core.messages import HumanMessage
                
                # Search backwards for the last HumanMessage
                for message in reversed(state["messages"]):
                    # Check if it's a HumanMessage
                    if isinstance(message, HumanMessage):
                        if hasattr(message, "content"):
                            content = message.content
                            # Skip if it looks like a system message (contains <cmd> tags)
                            if content and not content.strip().startswith("<cmd>"):
                                user_input = content
                                break
                    # Also check dict format
                    elif isinstance(message, dict):
                        # Check if it's a human message by type or role
                        msg_type = message.get("type", "")
                        msg_role = message.get("role", "")
                        if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                            content = message.get("content", "")
                            # Skip if it looks like a system message
                            if content and not content.strip().startswith("<cmd>"):
                                user_input = content
                                break
                    # Fallback: check if message has content and looks like user input
                    elif hasattr(message, "type"):
                        if message.type == "human":
                            if hasattr(message, "content"):
                                content = message.content
                                # Skip if it looks like a system message
                                if content and not content.strip().startswith("<cmd>"):
                                    user_input = content
                                    break
            
            # Fallback to automated_query if no valid HumanMessage found
            if not user_input:
                user_input = state.get("automated_query", "")
            
            if not user_input:
                logger.warning("No user input found, using default task")
                state.setdefault("obtainer", {})["task_list"] = [{"task_name": "收集数据集用于大模型微调"}]
                state.setdefault("obtainer", {})["current_task_index"] = 1  # 第一个任务已分配执行，索引指向下一个
                state["automated_query"] = state.get("obtainer", {}).get("task_list", [])[0]["task_name"]
                return state
            
            # Prepare prompt loader
            prompt_loader = None
            if state.get("prompt_template_dir"):
                try:
                    prompt_loader = PromptLoader(state.get("prompt_template_dir"))
                except Exception as e:
                    logger.debug(f"PromptLoader init failed, will use defaults: {e}")
            
            # Decompose tasks using LLM
            try:
                model_name = state.get("obtainer", {}).get("model_path") or state.get("analyze_model_path")
                base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
                api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
                temperature = state.get("obtainer", {}).get("temperature", 0.3)
                
                if model_name and base_url and api_key:
                    task_decomposer = TaskDecomposer(
                        model_name=model_name,
                        base_url=base_url,
                        api_key=api_key,
                        temperature=temperature,
                        prompt_loader=prompt_loader,
                    )
                    
                    logger.info(f"Decomposing user input into tasks: {user_input[:100]}...")
                    task_list = asyncio.run(task_decomposer.decompose_tasks(user_input))
                    
                    # 限制任务拆解为最多5个子任务，防止超过递归上限
                    max_decomposed_tasks = 5
                    if len(task_list) > max_decomposed_tasks:
                        logger.warning(
                            f"Task decomposition returned {len(task_list)} tasks, "
                            f"limiting to {max_decomposed_tasks} to prevent recursion limit issues"
                        )
                        task_list = task_list[:max_decomposed_tasks]
                    
                    state.setdefault("obtainer", {})["task_list"] = task_list
                    state.setdefault("obtainer", {})["current_task_index"] = 1  # 第一个任务已分配执行，索引指向下一个
                    
                    # Set first task as automated_query
                    if task_list and len(task_list) > 0:
                        first_task = task_list[0].get("task_name", user_input)
                        state["automated_query"] = first_task
                        logger.info(f"Decomposed into {len(task_list)} tasks. Starting with task 1/{len(task_list)}: {first_task[:100]}...")
                        if writer:
                            writer(StreamEvent(
                                current=state['current'],
                                message=f"Decomposed into {len(task_list)} tasks. Starting with task 1/{len(task_list)}: {first_task[:100]}...",
                            ).json())

                        # Clear RAG collection for the first task (keep downloads folder)
                        output_dir = state.get("output_dir", "./output")
                        download_dir = os.path.join(output_dir, "downloads")
                        rag_db_dir = os.path.join(output_dir, "rag_db")
                        
                        # Ensure downloads directory exists (but don't clear it)
                        os.makedirs(download_dir, exist_ok=True)
                        os.makedirs(rag_db_dir, exist_ok=True)
                        
                        # Clear RAG collection instead of deleting the database folder
                        try:
                            # Get RAG configuration (use RAG-specific config if available, otherwise fallback to obtainer config)
                            base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
                            api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
                            rag_api_base_url = state.get("obtainer", {}).get("rag_api_base_url") or base_url
                            rag_api_key = state.get("obtainer", {}).get("rag_api_key") or api_key
                            rag_collection_name = state.get("obtainer", {}).get("rag_collection_name", "rag_collection")
                            
                            if rag_api_base_url and rag_api_key:
                                logger.info(f"Clearing RAG collection for first task: {rag_collection_name}")
                                # Create a temporary RAGManager instance to clear the collection
                                rag_manager = RAGManager(
                                    api_base_url=rag_api_base_url,
                                    api_key=rag_api_key,
                                    persist_directory=rag_db_dir,
                                    collection_name=rag_collection_name,
                                )
                                rag_manager.clear_collection()
                                rag_manager.close()
                                logger.info("RAG collection cleared successfully for first task")
                            else:
                                logger.warning("RAG API configuration missing, skipping collection clear for first task")
                        except Exception as e:
                            logger.warning(f"Failed to clear RAG collection for first task: {e}, continuing anyway")
                        
                        # Determine category for the first subtask
                        logger.info(f"Determining category for first subtask: {first_task[:100]}...")
                        try:
                            category_classifier = CategoryClassifier(
                                model_name=model_name,
                                base_url=base_url,
                                api_key=api_key,
                                temperature=temperature,
                                prompt_loader=prompt_loader,
                            )
                            
                            classification_result = asyncio.run(
                                category_classifier.classify_category(
                                    user_query=first_task,
                                    objective=first_task
                                )
                            )
                            
                            if isinstance(classification_result, dict):
                                category = classification_result.get("category", "PT")
                                # dataset_background = classification_result.get("dataset_background", "")
                                state.setdefault("obtainer", {})["category"] = category
                                # if dataset_background:
                                    # state.setdefault("obtainer", {})["datasets_background"] = dataset_background
                                    # logger.info(f"First subtask category: {category}, dataset background: {dataset_background[:100]}...")
                                # else:
                                    # state.setdefault("obtainer", {})["datasets_background"] = first_task
                                    # logger.info(f"First subtask category: {category}, using task_name as dataset background")
                            else:
                                category = classification_result if isinstance(classification_result, str) else "PT"
                                state.setdefault("obtainer", {})["category"] = category
                                state.setdefault("obtainer", {})["datasets_background"] = first_task
                                logger.info(f"First subtask category: {category} (string format), using task_name as dataset background")
                        except Exception as e:
                            logger.error(f"Error in first subtask category classification: {e}, using keyword detection")
                            first_task_lower = first_task.lower()
                            if any(keyword in first_task_lower for keyword in ["sft", "supervised fine-tuning", "fine-tuning", "微调", "问答", "qa", "question", "answer"]):
                                state.setdefault("obtainer", {})["category"] = "SFT"
                            else:
                                state.setdefault("obtainer", {})["category"] = "PT"
                            state.setdefault("obtainer", {})["datasets_background"] = first_task
                            logger.info(f"First subtask category (fallback): {state.get('obtainer', {}).get('category')}")
                    else:
                        logger.warning("Task decomposition returned empty list, using original input")
                        state.setdefault("obtainer", {})["task_list"] = [{"task_name": user_input}]
                        state.setdefault("obtainer", {})["current_task_index"] = 1  # 第一个任务已分配执行，索引指向下一个
                        state["automated_query"] = user_input
                        # Determine category for the fallback task
                        if model_name and base_url and api_key:
                            try:
                                category_classifier = CategoryClassifier(
                                    model_name=model_name,
                                    base_url=base_url,
                                    api_key=api_key,
                                    temperature=temperature,
                                    prompt_loader=prompt_loader,
                                )
                                classification_result = asyncio.run(
                                    category_classifier.classify_category(
                                        user_query=user_input,
                                        objective=user_input
                                    )
                                )
                                if isinstance(classification_result, dict):
                                    category = classification_result.get("category", "PT")
                                    # dataset_background = classification_result.get("dataset_background", "")
                                    state.setdefault("obtainer", {})["category"] = category
                                    # state.setdefault("obtainer", {})["datasets_background"] = dataset_background if dataset_background else user_input
                                else:
                                    category = classification_result if isinstance(classification_result, str) else "PT"
                                    state.setdefault("obtainer", {})["category"] = category
                                    # state.setdefault("obtainer", {})["datasets_background"] = user_input
                            except Exception as e:
                                logger.error(f"Error in category classification: {e}")
                                user_input_lower = user_input.lower()
                                if any(keyword in user_input_lower for keyword in ["sft", "supervised fine-tuning", "fine-tuning", "微调", "问答", "qa", "question", "answer"]):
                                    state.setdefault("obtainer", {})["category"] = "SFT"
                                else:
                                    state.setdefault("obtainer", {})["category"] = "PT"
                                state.setdefault("obtainer", {})["datasets_background"] = user_input
                else:
                    logger.warning("Model configuration missing, using original input as single task")
                    state.setdefault("obtainer", {})["task_list"] = [{"task_name": user_input}]
                    state.setdefault("obtainer", {})["current_task_index"] = 1  # 第一个任务已分配执行，索引指向下一个
                    state["automated_query"] = user_input
                    # Use keyword-based detection as fallback
                    user_input_lower = user_input.lower()
                    if any(keyword in user_input_lower for keyword in ["sft", "supervised fine-tuning", "fine-tuning", "微调", "问答", "qa", "question", "answer"]):
                        state.setdefault("obtainer", {})["category"] = "SFT"
                    else:
                        state.setdefault("obtainer", {})["category"] = "PT"
                    state.setdefault("obtainer", {})["datasets_background"] = user_input
            except Exception as e:
                logger.error(f"Error in task decomposition: {e}, using original input as single task")
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        message=f"ObtainerAgent 任务分解异常: {str(e)[:200]}，将使用原始输入作为单一任务",
                        data={"error": str(e), "phase": "task_decomposer"},
                    ).json())
                state.setdefault("obtainer", {})["task_list"] = [{"task_name": user_input}]
                state.setdefault("obtainer", {})["current_task_index"] = 1  # 第一个任务已分配执行，索引指向下一个
                state["automated_query"] = user_input
                # Use keyword-based detection as fallback
                user_input_lower = user_input.lower()
                if any(keyword in user_input_lower for keyword in ["sft", "supervised fine-tuning", "fine-tuning", "微调", "问答", "qa", "question", "answer"]):
                    state.setdefault("obtainer", {})["category"] = "SFT"
                else:
                    state.setdefault("obtainer", {})["category"] = "PT"
                state.setdefault("obtainer", {})["datasets_background"] = user_input
            
            return state
        
        return task_decomposer_node

    @staticmethod
    def should_continue_tasks(state: LoopAIState) -> str:
        """
        Conditional edge function: check if there are more tasks to execute
        Routes to next_task_node if more tasks, or end_node if all tasks completed
        """
        task_list = state.get("obtainer", {}).get("task_list", [])
        current_index = state.get("obtainer", {}).get("current_task_index", 0)
        
        if current_index < len(task_list):
            logger.info(f"Task router: More tasks to execute ({current_index + 1}/{len(task_list)})")
            return "next_task_node"
        else:
            logger.info(f"Task router: All tasks completed ({len(task_list)}/{len(task_list)})")
            return "end_node"

    @staticmethod
    @BaseAgent.set_current
    def check_next_task_node(state: LoopAIState):
        """
        Check next task node: clear category information before routing to next task
        to prevent category conflicts between subtasks
        """
        task_list = state.get("obtainer", {}).get("task_list", [])
        current_index = state.get("obtainer", {}).get("current_task_index", 0)
        
        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message=f"ObtainerAgent 检查任务进度 ({current_index}/{len(task_list)})",
                progress=0,
                data={"phase": "check_next_task", "current_index": current_index, "total_tasks": len(task_list)},
            ).json())
        
        # If there are more tasks, clear category information to prevent conflicts
        # The next task will determine its own category in next_task_node
        if current_index < len(task_list):
            logger.info(f"Clearing category information before next task to prevent conflicts")
            # Clear category but keep dataset_background as it might be useful context
            # Actually, let's clear both to ensure each subtask gets fresh classification
            if "obtainer" in state and "category" in state["obtainer"]:
                del state["obtainer"]["category"]
            # Note: We keep obtainer_datasets_background as it's the global background
            # Each subtask will get its own dataset_background in next_task_node
        
        if writer:
            has_more = current_index < len(task_list)
            writer(StreamEvent(
                current=state['current'],
                message=f"ObtainerAgent 任务检查完成 - {'继续下一任务' if has_more else '所有任务已完成'}",
                progress=1,
                data={"phase": "check_next_task", "has_more_tasks": has_more, "current_index": current_index, "total_tasks": len(task_list)},
            ).json())
        
        return state

    @staticmethod
    @BaseAgent.set_current
    def next_task_node(state: LoopAIState):
        """
        Next task node: prepare next task for execution
        """
        task_list = state.get("obtainer", {}).get("task_list", [])
        current_index = state.get("obtainer", {}).get("current_task_index", 0)
        writer = get_stream_writer()
        
        if current_index < len(task_list):
            next_task = task_list[current_index]
            task_name = next_task.get("task_name", "")
            
            logger.info(f"Next task node: Preparing task {current_index + 1}/{len(task_list)}: {task_name[:100]}...")

            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message=f"Next task node: Preparing task {current_index + 1}/{len(task_list)}: {task_name[:100]}...",
                    progress=(current_index + 1)/(len(task_list) + 1),
                ).json())

            
            # Clear RAG collection to prevent data duplication between subtasks (keep downloads folder)
            output_dir = state.get("output_dir", "./output")
            download_dir = os.path.join(output_dir, "downloads")
            rag_db_dir = os.path.join(output_dir, "rag_db")
            
            # Ensure downloads directory exists (but don't clear it)
            os.makedirs(download_dir, exist_ok=True)
            os.makedirs(rag_db_dir, exist_ok=True)
            
            # Clear RAG collection instead of deleting the database folder
            try:
                # Get RAG configuration (use RAG-specific config if available, otherwise fallback to obtainer config)
                base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
                api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
                rag_api_base_url = state.get("obtainer", {}).get("rag_api_base_url") or base_url
                rag_api_key = state.get("obtainer", {}).get("rag_api_key") or api_key
                rag_collection_name = state.get("obtainer", {}).get("rag_collection_name", "rag_collection")
                
                if rag_api_base_url and rag_api_key:
                    logger.info(f"Clearing RAG collection for next task: {rag_collection_name}")
                    # Create a temporary RAGManager instance to clear the collection
                    rag_manager = RAGManager(
                        api_base_url=rag_api_base_url,
                        api_key=rag_api_key,
                        persist_directory=rag_db_dir,
                        collection_name=rag_collection_name,
                    )
                    rag_manager.clear_collection()
                    rag_manager.close()
                    logger.info("RAG collection cleared successfully for next task")
                else:
                    logger.warning("RAG API configuration missing, skipping collection clear for next task")
            except Exception as e:
                logger.warning(f"Failed to clear RAG collection for next task: {e}, continuing anyway")
            
            # Set automated_query for next task
            state["automated_query"] = task_name
            
            # Reset task-specific state for new task
            # Keep accumulated results but reset per-task state
            state.setdefault("obtainer", {})["research_summary"] = ""
            state.setdefault("obtainer", {})["subtasks"] = []
            state.setdefault("obtainer", {})["urls_visited"] = []
            state.setdefault("obtainer", {})["download_results"] = {}
            
            # Determine category for this subtask using LLM
            logger.info(f"Determining category for subtask: {task_name[:100]}...")
            try:
                # Get model configuration
                model_name = state.get("obtainer", {}).get("model_path") or state.get("analyze_model_path")
                base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
                api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
                temperature = state.get("obtainer", {}).get("temperature", 0.3)
                
                if model_name and base_url and api_key:
                    # Prepare prompt loader
                    prompt_loader = None
                    if state.get("prompt_template_dir"):
                        try:
                            from loopai.common.prompts import PromptLoader
                            prompt_loader = PromptLoader(state.get("prompt_template_dir"))
                        except Exception as e:
                            logger.debug(f"PromptLoader init failed, will use defaults: {e}")
                    
                    # Initialize category classifier
                    category_classifier = CategoryClassifier(
                        model_name=model_name,
                        base_url=base_url,
                        api_key=api_key,
                        temperature=temperature,
                        prompt_loader=prompt_loader,
                    )
                    
                    # Classify category for this subtask
                    logger.info(f"Analyzing subtask to determine category (SFT/PT): {task_name[:100]}...")
                    classification_result = asyncio.run(
                        category_classifier.classify_category(
                            user_query=task_name,
                            objective=task_name
                        )
                    )
                    
                    # Handle both old string format and new dict format for backward compatibility
                    if isinstance(classification_result, dict):
                        category = classification_result.get("category", "PT")
                        # dataset_background = classification_result.get("dataset_background", "")
                        state.setdefault("obtainer", {})["category"] = category
                        # if dataset_background:
                            # state.setdefault("obtainer", {})["datasets_background"] = dataset_background
                            # logger.info(f"Subtask category: {category}, dataset background: {dataset_background[:100]}...")
                        # else:
                            # Use task_name as fallback dataset_background
                            # state.setdefault("obtainer", {})["datasets_background"] = task_name
                            # logger.info(f"Subtask category: {category}, using task_name as dataset background")
                    else:
                        # Backward compatibility: if it returns a string
                        category = classification_result if isinstance(classification_result, str) else "PT"
                        state.setdefault("obtainer", {})["category"] = category
                        state.setdefault("obtainer", {})["datasets_background"] = task_name
                        logger.info(f"Subtask category: {category} (string format), using task_name as dataset background")
                else:
                    # Fallback to keyword-based detection if model config is missing
                    logger.warning("Model configuration missing, using keyword-based category detection for subtask")
                    task_name_lower = task_name.lower()
                    if any(keyword in task_name_lower for keyword in ["sft", "supervised fine-tuning", "fine-tuning", "微调", "问答", "qa", "question", "answer"]):
                        state.setdefault("obtainer", {})["category"] = "SFT"
                        logger.info("Keyword-based detection for subtask: SFT")
                    else:
                        state.setdefault("obtainer", {})["category"] = "PT"
                        logger.info("Keyword-based detection for subtask: PT (default)")
                    state.setdefault("obtainer", {})["datasets_background"] = task_name
            except Exception as e:
                logger.error(f"Error in subtask category classification: {e}, falling back to keyword detection")
                # Fallback to keyword-based detection on error
                task_name_lower = task_name.lower()
                if any(keyword in task_name_lower for keyword in ["sft", "supervised fine-tuning", "fine-tuning", "微调", "问答", "qa", "question", "answer"]):
                    state.setdefault("obtainer", {})["category"] = "SFT"
                    logger.info("Fallback keyword detection for subtask: SFT")
                else:
                    state.setdefault("obtainer", {})["category"] = "PT"
                    logger.info("Fallback keyword detection for subtask: PT (default)")
                state.setdefault("obtainer", {})["datasets_background"] = task_name
            
            # Increment task index
            state.setdefault("obtainer", {})["current_task_index"] = current_index + 1
            
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message="Task Decomposer Complete",
                    progress=1
                ).json())
            return state
        else:
            logger.warning("Next task node: No more tasks, should not reach here")
            return state

    @staticmethod
    @BaseAgent.set_current
    def end_node(state: LoopAIState):
        """
        End node for obtainer agent
        Set next_to to return to parent graph and summarize results
        """
        logger.info(f"ObtainerAgent: All tasks completed, returning to parent graph")
        
        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message="ObtainerAgent 开始生成任务摘要",
                progress=0,
                data={"phase": "end_node"},
            ).json())
        
        # Generate summary of results for LLM
        summary_parts = []
        
        # Check task list completion
        task_list = state.get("obtainer", {}).get("task_list", [])
        if task_list:
            summary_parts.append(f"共执行 {len(task_list)} 个数据收集任务:")
            for i, task in enumerate(task_list, 1):
                task_name = task.get("task_name", f"任务 {i}")
                summary_parts.append(f"  {i}. {task_name}")
        
        # Check for exceptions
        if state.get("exception"):
            summary_parts.append(f"执行过程中出现错误: {state.get('exception')}")
        else:
            # Summarize research results
            research_summary = state.get("obtainer", {}).get("research_summary", "")
            if research_summary:
                summary_parts.append(f"研究摘要: {research_summary[:200]}...")
            
            # Summarize subtasks
            subtasks = state.get("obtainer", {}).get("subtasks", [])
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
        
        # Send completion stream event (always, not debug_mode only)
        if writer:
            summary_data = {
                'summary_text': summary_text,
                'has_exception': bool(state.get("exception")),
                'research_summary': state.get("obtainer", {}).get("research_summary", ""),
                'subtasks_count': len(state.get("obtainer", {}).get("subtasks", [])),
                'urls_visited_count': len(state.get("obtainer", {}).get("urls_visited", [])),
                'download_results': state.get("obtainer", {}).get("download_results", {})
            }
            writer(StreamEvent(
                current=state['current'],
                message="ObtainerAgent 任务完成",
                progress=1,
                data=summary_data
            ).json())
        
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
            "download_node" if there are download tasks, "check_next_task_node" otherwise
        """
        subtasks = state.get("obtainer", {}).get("subtasks", [])
        download_tasks = [task for task in subtasks if task.get("type") == "download"]
        if download_tasks:
            logger.info(f"Found {len(download_tasks)} download tasks, routing to download_node")
            return "download_node"
        else:
            logger.info("No download tasks found, routing to check_next_task_node to continue with next subtask")
            return "check_next_task_node"

    @staticmethod
    @BaseAgent.set_current
    def websearch_node(state: LoopAIState):
        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message="ObtainerAgent WebResearch 开始",
                progress=0.0,
                data={"phase": "webresearch", "message": "WebResearch 流程启动，将显示内部进度"},
            ).json())
        try:
            state = websearch_node(state)
        except Exception as e:
            logger.error(f"ObtainerAgent websearch_node error: {e}", exc_info=True)
            state["exception"] = f"WebResearch error: {str(e)}"
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message=f"ObtainerAgent WebResearch 异常: {str(e)[:200]}",
                    data={"error": str(e), "phase": "webresearch"},
                ).json())
        if writer:
            subtasks = state.get("obtainer", {}).get("subtasks", [])
            writer(StreamEvent(
                current=state['current'],
                message="ObtainerAgent WebResearch 完成",
                progress=1.0,
                data={
                    "phase": "webresearch",
                    "subtasks_count": len(subtasks),
                    "urls_visited_count": len(state.get("obtainer", {}).get("urls_visited", [])),
                    "has_exception": bool(state.get("exception")),
                },
            ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def deep_explore_node(state: LoopAIState):
        return deep_explore_node(state)

    @staticmethod
    @BaseAgent.set_current
    def download_node(state: LoopAIState):
        writer = get_stream_writer()
        subtasks = state.get("obtainer", {}).get("subtasks", [])
        download_tasks = [t for t in subtasks if t.get("type") == "download"]
        if writer:
            writer(StreamEvent(
                current=state['current'],
                message="ObtainerAgent Download Start",
                progress=0,
                data={"phase": "download", "download_tasks_count": len(download_tasks)},
            ).json())
        try:
            state = download_node(state)
        except Exception as e:
            logger.error(f"ObtainerAgent download_node error: {e}", exc_info=True)
            state["exception"] = f"Download error: {str(e)}"
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message=f"ObtainerAgent Download 异常: {str(e)[:200]}",
                    data={"error": str(e), "phase": "download"},
                ).json())
        if writer:
            completed = [t for t in subtasks if t.get("status") == "completed_successfully"]
            failed = [t for t in subtasks if t.get("status") == "failed_to_download"]
            writer(StreamEvent(
                current=state['current'],
                message="ObtainerAgent Download Complete",
                progress=1,
                data={
                    "phase": "download",
                    "download_tasks_count": len(download_tasks),
                    "completed_count": len(completed),
                    "failed_count": len(failed),
                    "has_exception": bool(state.get("exception")),
                },
            ).json())
        return state


    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("start_node", self.get_start_node())
        builder.add_node("task_decomposer_node", self.get_task_decomposer_node())
        builder.add_node("websearch_node", self.websearch_node)
        builder.add_node("deep_explore_node", self.deep_explore_node)  # 占位节点，未实现，不接入工作流
        builder.add_node("download_node", self.download_node)
        builder.add_node("check_next_task_node", self.check_next_task_node)
        builder.add_node("next_task_node", self.next_task_node)
        builder.add_node("end_node", self.end_node)
        builder.set_entry_point("start_node")
        builder.add_edge("start_node", "task_decomposer_node")
        builder.add_edge("task_decomposer_node", "websearch_node")
        builder.add_conditional_edges(
            "websearch_node",
            self.has_download_tasks,
            {
                "download_node": "download_node",
                "check_next_task_node": "check_next_task_node",
            }
        )
        # download_node 完成后检查是否有下一个任务
        builder.add_edge("download_node", "check_next_task_node")
        # check_next_task_node 根据是否有更多任务路由
        builder.add_conditional_edges(
            "check_next_task_node",
            self.should_continue_tasks,
            {
                "next_task_node": "next_task_node",
                "end_node": "end_node",
            }
        )
        # next_task_node 完成后回到 websearch_node 执行下一个任务
        builder.add_edge("next_task_node", "websearch_node")
        builder.set_finish_point("end_node")
        
        self.graph = builder.compile(
            checkpointer=self.checkpointer, 
            store=self.store, 
            **kwargs)

    def __call__(self, **kwargs):
        """
        build and return self.graph

        Args:
            kwargs: keyword arguments to pass to init_graph
        """
        self.init_graph(**kwargs)
        return self.graph

