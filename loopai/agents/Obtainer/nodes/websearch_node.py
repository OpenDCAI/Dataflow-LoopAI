import json
import asyncio
import os
import random
import time
from urllib.parse import urlparse

from typing import Dict, Any, List, Optional

from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.agents.Obtainer.utils import (
    RAGManager,
    WebTools,
    QueryGenerator,
    SummaryAgent,
    URLSelector,
)
from loopai.common.prompts import PromptLoader

logger = get_logger()


def _emit_webresearch_progress(
    event_name: str,
    message: str,
    progress: Optional[float] = None,
    progress_num: Optional[int] = None,
    total: Optional[int] = None,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """发送 WebResearch 进度事件，供前端进度条展示（始终发送，不依赖 debug_mode）"""
    try:
        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=event_name,
                message=message,
                progress=progress,
                progress_num=progress_num,
                total=total,
                data=data,
            ).json())
    except Exception as e:
        logger.debug(f"Could not send webresearch progress event: {e}")


# 需要跳过的域名列表（这些网站可能会触发 CAPTCHA 验证或有反爬虫保护）
BLOCKED_DOMAINS = [
    "stackoverflow.com",
]


def _is_blocked_url(url: str) -> bool:
    """检查 URL 是否属于被阻止的域名"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        for blocked in BLOCKED_DOMAINS:
            if blocked in domain:
                return True
        return False
    except Exception:
        return False


def websearch_node(state: LoopAIState) -> LoopAIState:
    """Web search node that searches web, stores content in RAG, and generates download subtasks"""
    logger.info("=== WebSearch Node: Starting ===")
    
    # Get user query from state
    user_query = ""
    
    # First try to get from automated_query (highest priority)
    if state.get("automated_query"):
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
            logger.error("Missing required configuration for websearch node")
            state["exception"] = "Missing model configuration (model_name, base_url, api_key)"
            return state
        
        # Initialize prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # Initialize RAG Manager with independent RAG configuration
        rag_persist_dir = state.get("output_dir", "./output") + "/rag_db"
        # Use RAG-specific API config if provided, otherwise fallback to obtainer config
        rag_api_base_url = state.get("obtainer", {}).get("rag_api_base_url") or base_url
        rag_api_key = state.get("obtainer", {}).get("rag_api_key") or api_key
        rag_embed_model = state.get("obtainer", {}).get("rag_embed_model") or None
        rag_collection_name = state.get("obtainer", {}).get("rag_collection_name", "rag_collection")
        rag_manager = RAGManager(
            api_base_url=rag_api_base_url,
            api_key=rag_api_key,
            embed_model=rag_embed_model,
            persist_directory=rag_persist_dir,
            reset=state.get("obtainer", {}).get("reset_rag", False),
            collection_name=rag_collection_name,
        )
        
        # Store RAG manager in state for cleanup (if needed)
        # We'll close it at the end of this function
        
        # Initialize Query Generator
        query_generator = QueryGenerator(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
        )
        
        # Initialize Summary Agent
        summary_agent = SummaryAgent(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            max_download_subtasks=state.get("obtainer", {}).get("max_download_subtasks"),
        )
        
        # Initialize URL Selector for intelligent URL selection
        url_selector = URLSelector(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=0.3,  # Lower temperature for more consistent URL selection
            prompt_loader=prompt_loader,
        )
        
        # Get Tavily API key from state or environment
        tavily_api_key = state.get("obtainer", {}).get("tavily_api_key", "") or os.getenv("TAVILY_API_KEY", "")
        
        # Run async workflow
        debug_mode = state.get("obtainer_debug", False)
        result = asyncio.run(_websearch_workflow(
            user_query=user_query,
            query_generator=query_generator,
            summary_agent=summary_agent,
            rag_manager=rag_manager,
            url_selector=url_selector,
            search_engine=state.get("obtainer", {}).get("search_engine", "tavily"),
            max_urls=state.get("obtainer", {}).get("max_urls", 10),
            max_depth=state.get("obtainer", {}).get("max_depth", 4),  # Maximum exploration depth
            concurrent_limit=state.get("obtainer", {}).get("concurrent_limit", 10),  # Concurrent URL processing
            topk_urls=state.get("obtainer", {}).get("topk_urls", 5),  # Top-k URLs to select from each page
            url_timeout=state.get("obtainer", {}).get("url_timeout", 60),  # Timeout in seconds for each URL exploration
            tavily_api_key=tavily_api_key if tavily_api_key else None,
            debug_mode=debug_mode,
            event_name=state['current'],
        ))
        
        # Update state with results
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            state.setdefault("obtainer", {})["research_summary"] = result.get("research_summary", "")
            state.setdefault("obtainer", {})["subtasks"] = result.get("subtasks", [])
            state.setdefault("obtainer", {})["urls_visited"] = result.get("urls_visited", [])
            logger.info(f"WebSearch completed: {len(result.get('subtasks', []))} subtasks generated")
        
        # 发送 WebResearch 节点完成事件（与内部进度条一致）
        try:
            writer = get_stream_writer()
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    message="WebResearch: 节点完成",
                    progress=1.0,
                    data={
                        "user_query": user_query,
                        "research_summary": result.get("research_summary", "")[:200] if "exception" not in result else None,
                        "subtasks_count": len(result.get("subtasks", [])),
                        "urls_visited_count": len(result.get("urls_visited", [])),
                        "has_exception": "exception" in result,
                        "exception": result.get("exception") if "exception" in result else None,
                    },
                ).json())
        except Exception as e:
            logger.debug(f"Could not send stream event: {e}")
        
    except Exception as e:
        logger.error(f"WebSearch node error: {e}", exc_info=True)
        state["exception"] = f"WebSearch error: {str(e)}"
    finally:
        # Always close RAG manager to release database connections
        try:
            if 'rag_manager' in locals():
                logger.info("[RAG] Closing RAG Manager to release database connections...")
                rag_manager.close()
                logger.info("[RAG] RAG Manager closed")
        except Exception as e:
            logger.warning(f"[RAG] Error closing RAG Manager: {e}")
    
    logger.info("=== WebSearch Node: Completed ===")
    return state


async def _websearch_workflow(
    user_query: str,
    query_generator: QueryGenerator,
    summary_agent: SummaryAgent,
    rag_manager: RAGManager,
    url_selector: URLSelector,
    search_engine: str = "tavily",
    max_urls: int = 10,
    max_depth: int = 4,
    concurrent_limit: int = 10,
    topk_urls: int = 5,
    url_timeout: int = 60,
    tavily_api_key: str = None,
    debug_mode: bool = False,
    event_name: str = "websearch_workflow"
) -> Dict[str, Any]:
    """Async workflow for web search"""
    try:
        # Ensure integer parameters are properly typed
        max_urls = int(max_urls) if max_urls else 10
        max_depth = int(max_depth) if max_depth else 4
        concurrent_limit = int(concurrent_limit) if concurrent_limit else 10
        topk_urls = int(topk_urls) if topk_urls else 5
        url_timeout = int(url_timeout) if url_timeout else 60
        
        # Step 1: Generate research queries
        logger.info("Step 1: Generating research queries...")
        _emit_webresearch_progress(
            event_name,
            "WebResearch: 生成检索查询中",
            progress=0.0,
            progress_num=0,
            total=4,
        )

        queries = await query_generator.generate_queries(
            objective=user_query,
            message=user_query,
        )
        
        if not queries:
            queries = [user_query]  # Fallback to original query
        
        logger.info(f"Generated {len(queries)} research queries")
        _emit_webresearch_progress(
            event_name,
            f"WebResearch: 已生成 {len(queries)} 个检索查询",
            progress=0.25,
            progress_num=1,
            total=4,
            data={"queries": queries} if debug_mode else None,
        )

        # Step 2: Search for URLs
        logger.info("Step 2: Searching for URLs...")
        _emit_webresearch_progress(
            event_name,
            "WebResearch: 搜索 URL 中",
            progress=0.25,
            progress_num=1,
            total=4,
        )

        all_urls = []
        for query in queries:
            search_results = await WebTools.search_web(query, search_engine, tavily_api_key=tavily_api_key)
            urls = WebTools.extract_urls_from_search_results(search_results)
            # 过滤掉被阻止的域名
            urls = [u for u in urls if not _is_blocked_url(u)]
            all_urls.extend(urls)
            logger.info(f"Query '{query}' found {len(urls)} URLs (after filtering blocked domains)")
        
        # Remove duplicates and limit
        unique_urls = list(dict.fromkeys(all_urls))[:max_urls]
        logger.info(f"Total unique URLs to visit: {len(unique_urls)}")
        _emit_webresearch_progress(
            event_name,
            f"WebResearch: 已找到 {len(unique_urls)} 个待访问 URL",
            progress=0.5,
            progress_num=2,
            total=4,
            data={"unique_urls_count": len(unique_urls), "total_urls_found": len(all_urls)},
        )

        # Step 3: Explore web forest with depth-limited BFS (max depth 4, concurrent 10)
        logger.info(f"Step 3: Exploring web forest (max_depth={max_depth}, concurrent={concurrent_limit}, topk={topk_urls}, timeout={url_timeout}s)...")
        visited_urls = []
        visited_urls_set = set()  # Track visited URLs to avoid duplicates
        rag_tasks = []  # async tasks for RAG writes to avoid blocking exploration
        rag_write_semaphore = asyncio.Semaphore(2)  # limit concurrent RAG writes
        crawled_pages = []  # Store crawled page content for return
        
        # URL queue: each item is (url, depth)
        url_queue = [(url, 0) for url in unique_urls]  # Initialize with depth 0
        
        # Semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(concurrent_limit)
        queue_lock = asyncio.Lock()  # Lock for queue operations
        
        async def explore_url(url: str, depth: int) -> Optional[Dict[str, Any]]:
            """Explore a URL: read content, store in RAG, and return candidate URLs for next layer"""
            async with semaphore:
                try:
                    # 检查 URL 是否属于被阻止的域名
                    if _is_blocked_url(url):
                        logger.info(f"[Depth {depth}/{max_depth}] Skipping blocked domain: {url}")
                        return {
                            "url": url,
                            "depth": depth,
                            "candidate_urls": [],
                            "success": False,
                            "reason": "blocked_domain"
                        }
                    
                    # 每个网页实际爬取前增加 2-4 秒的随机延迟
                    delay = random.uniform(2.0, 4.0)
                    logger.info(f"[Depth {depth}/{max_depth}] Sleeping {delay:.2f}s before crawling URL: {url}")
                    time.sleep(delay)

                    logger.info(f"[Depth {depth}/{max_depth}] Exploring URL: {url}")
                    
                    # Read webpage content
                    page_content = await asyncio.wait_for(
                        WebTools.read_with_jina_reader(url),
                        timeout=url_timeout
                    )
                    
                    webpage_text = page_content.get("text", "")
                    candidate_urls = page_content.get("urls", [])
                    
                    if webpage_text and len(webpage_text.strip()) > 50:
                        # Store content in RAG
                        async def _store_to_rag(u: str, txt: str, d: int):
                            async with rag_write_semaphore:
                                await rag_manager.add_webpage_content(
                                    url=u,
                                    text_content=txt,
                                    metadata={
                                        "source": "websearch",
                                        "query": user_query,
                                        "depth": d
                                    }
                                )
                        rag_tasks.append(asyncio.create_task(_store_to_rag(url, webpage_text, depth)))
                        
                        # Track visited URL
                        async with queue_lock:
                            if url not in visited_urls_set:
                                visited_urls_set.add(url)
                                visited_urls.append(url)
                                # Store page content for return (even if RAG is disabled)
                                crawled_pages.append({
                                    "source_url": url,
                                    "text_content": webpage_text,
                                    "extraction_method": "jina_reader",
                                    "structured_content": {
                                        "title": page_content.get("title", ""),
                                        "url": url
                                    }
                                })
                        
                        logger.info(f"[Depth {depth}] Successfully stored content from {url} ({len(candidate_urls)} links found)")
                        
                        # If not at max depth, use LLM to select topk URLs from candidate links
                        selected_urls = []
                        if depth < max_depth - 1 and candidate_urls:
                            # Filter out already visited URLs (with lock protection)
                            async with queue_lock:
                                # 过滤已访问的 URL 和被阻止的域名
                                new_candidate_urls = [u for u in candidate_urls if u not in visited_urls_set and not _is_blocked_url(u)]
                            
                            if new_candidate_urls:
                                try:
                                    # Truncate webpage text to 8000 characters for LLM
                                    truncated_text = webpage_text[:8000]
                                    
                                    # Use LLM to select topk most relevant URLs
                                    selected_urls = await url_selector.select_top_urls(
                                        research_objective=user_query,
                                        url_list=new_candidate_urls,
                                        webpage_content=truncated_text,
                                        topk=topk_urls,
                                    )
                                    logger.info(f"[Depth {depth}] LLM selected {len(selected_urls)} URLs from {len(new_candidate_urls)} candidates")
                                except Exception as e:
                                    logger.warning(f"[Depth {depth}] URL selection failed: {e}, falling back to first {topk_urls} URLs")
                                    selected_urls = new_candidate_urls[:topk_urls]
                        
                        return {
                            "url": url,
                            "depth": depth,
                            "candidate_urls": selected_urls,
                            "success": True
                        }
                    else:
                        logger.warning(f"[Depth {depth}] URL {url} has insufficient content")
                        return {
                            "url": url,
                            "depth": depth,
                            "candidate_urls": [],
                            "success": False
                        }
                except Exception as e:
                    logger.error(f"[Depth {depth}] Error exploring URL {url}: {e}")
                    return {
                        "url": url,
                        "depth": depth,
                        "candidate_urls": [],
                        "success": False
                    }
        
        # Process URLs layer by layer
        current_depth = 0
        while url_queue and current_depth < max_depth:
            # Get all URLs at current depth
            current_layer = [(url, depth) for url, depth in url_queue if depth == current_depth]
            url_queue = [(url, depth) for url, depth in url_queue if depth != current_depth]
            
            if not current_layer:
                current_depth += 1
                continue
            
            logger.info(f"[Forest Exploration] Processing depth {current_depth}: {len(current_layer)} URLs")
            _emit_webresearch_progress(
                event_name,
                f"WebResearch: 探索深度 {current_depth}/{max_depth}，本层 {len(current_layer)} 个 URL",
                progress=0.5 + (current_depth / max_depth) * 0.25,
                data={
                    "current_depth": current_depth,
                    "max_depth": max_depth,
                    "urls_at_depth": len(current_layer),
                    "total_visited": len(visited_urls),
                    "queue_size": len(url_queue),
                },
            )

            # Process URLs at current depth in batches to avoid queuing time counting toward timeout
            results = []
            for i in range(0, len(current_layer), concurrent_limit):
                batch = current_layer[i:i + concurrent_limit]
                tasks = [explore_url(url, depth) for url, depth in batch]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend(batch_results)
            
            # Handle exceptions that might have been returned
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    url, depth = current_layer[i]
                    logger.error(f"[Depth {depth}] Exception in task for {url}: {result}")
                    processed_results.append({
                        "url": url,
                        "depth": depth,
                        "candidate_urls": [],
                        "success": False,
                        "error": str(result)
                    })
                elif result is None:
                    url, depth = current_layer[i]
                    logger.warning(f"[Depth {depth}] Task returned None for {url}")
                    processed_results.append({
                        "url": url,
                        "depth": depth,
                        "candidate_urls": [],
                        "success": False,
                        "error": "None result"
                    })
                else:
                    processed_results.append(result)
            
            results = processed_results
            
            # Collect candidate URLs for next layer
            # Process all results within a single lock to improve efficiency
            async with queue_lock:
                for result in results:
                    if result and result.get("success") and result.get("candidate_urls"):
                        next_depth = current_depth + 1
                        for candidate_url in result["candidate_urls"]:
                            # Check if URL already in queue or visited
                            if candidate_url not in visited_urls_set:
                                url_already_in_queue = any(url == candidate_url for url, _ in url_queue)
                                if not url_already_in_queue:
                                    url_queue.append((candidate_url, next_depth))
            
            # Move to next depth
            current_depth += 1
        
        logger.info(f"Forest exploration completed: visited {len(visited_urls)} URLs across {current_depth} layers")
        
        # Wait for all pending RAG writes before persisting and summarizing
        if rag_tasks:
            logger.info(f"[RAG] Waiting for {len(rag_tasks)} pending RAG write tasks...")
            rag_results = await asyncio.gather(*rag_tasks, return_exceptions=True)
            for res in rag_results:
                if isinstance(res, Exception):
                    logger.warning(f"[RAG] Write task error: {res}")

        # Force persist RAG data before moving to next step
        try:
            logger.info("[RAG] Force persisting all data after exploration...")
            await rag_manager.force_persist()
            logger.info("[RAG] Force persist completed")
        except Exception as e:
            logger.warning(f"[RAG] Force persist failed: {e}")

        _emit_webresearch_progress(
            event_name,
            f"WebResearch: 已访问并存储 {len(visited_urls)}/{len(unique_urls)} 个 URL",
            progress=0.75,
            progress_num=3,
            total=4,
            data={"visited_urls_count": len(visited_urls), "total_urls": len(unique_urls)},
        )

        # Step 4: Generate download subtasks using Summary Agent
        logger.info("Step 4: Generating download subtasks...")
        
        # Get context from RAG
        context = await rag_manager.get_context_for_single_query(
            query=user_query,
            max_chars=18000
        )
        
        if not context:
            # Fallback: use all visited URLs info
            context = f"Visited {len(visited_urls)} URLs related to: {user_query}"
        
        # Generate subtasks
        subtask_result = await summary_agent.generate_subtasks(
            objective=user_query,
            context=context,
            existing_subtasks=[],
            message=user_query,
        )
        
        research_summary = subtask_result.get("summary", "")
        new_subtasks = subtask_result.get("new_sub_tasks", [])
        
        logger.info(f"Generated {len(new_subtasks)} download subtasks")
        _emit_webresearch_progress(
            event_name,
            f"WebResearch: 已生成 {len(new_subtasks)} 个下载子任务",
            progress=1.0,
            progress_num=4,
            total=4,
            data={"subtasks_count": len(new_subtasks), "research_summary": (research_summary[:200] if research_summary else "")},
        )

        return {
            "research_summary": research_summary,
            "subtasks": new_subtasks,
            "urls_visited": visited_urls,
            "crawled_pages": crawled_pages,  # Return crawled page content
        }
        
    except Exception as e:
        logger.error(f"WebSearch workflow error: {e}", exc_info=True)
        return {
            "exception": f"WebSearch workflow error: {str(e)}",
            "research_summary": "",
            "subtasks": [],
            "urls_visited": [],
            "crawled_pages": [],
        }

