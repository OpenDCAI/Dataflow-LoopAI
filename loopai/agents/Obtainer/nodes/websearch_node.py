import json
import asyncio
import os
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
        model_name = state.get("obtainer_model_path") or state.get("analyze_model_path")
        base_url = state.get("obtainer_base_url") or state.get("analyze_base_url")
        api_key = state.get("obtainer_api_key") or state.get("analyze_api_key")
        temperature = state.get("obtainer_temperature", 0.7)
        
        if not model_name or not base_url or not api_key:
            logger.error("Missing required configuration for websearch node")
            state["exception"] = "Missing model configuration (model_name, base_url, api_key)"
            return state
        
        # Initialize prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # Initialize RAG Manager with independent RAG configuration
        rag_persist_dir = state.get("output_dir", "./output") + "/rag_db"
        # Use RAG-specific API config if provided, otherwise fallback to obtainer config
        rag_api_base_url = state.get("obtainer_rag_api_base_url") or base_url
        rag_api_key = state.get("obtainer_rag_api_key") or api_key
        rag_embed_model = state.get("obtainer_rag_embed_model") or None
        rag_collection_name = state.get("obtainer_rag_collection_name", "rag_collection")
        rag_manager = RAGManager(
            api_base_url=rag_api_base_url,
            api_key=rag_api_key,
            embed_model=rag_embed_model,
            persist_directory=rag_persist_dir,
            reset=state.get("obtainer_reset_rag", False),
            collection_name=rag_collection_name,
        )
        
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
            max_download_subtasks=state.get("obtainer_max_download_subtasks"),
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
        tavily_api_key = state.get("obtainer_tavily_api_key", "") or os.getenv("TAVILY_API_KEY", "")
        
        # Run async workflow
        debug_mode = state.get("obtainer_debug", False)
        result = asyncio.run(_websearch_workflow(
            user_query=user_query,
            query_generator=query_generator,
            summary_agent=summary_agent,
            rag_manager=rag_manager,
            url_selector=url_selector,
            search_engine=state.get("obtainer_search_engine", "tavily"),
            max_urls=state.get("obtainer_max_urls", 10),
            max_depth=state.get("obtainer_max_depth", 4),  # Maximum exploration depth
            concurrent_limit=state.get("obtainer_concurrent_limit", 10),  # Concurrent URL processing
            topk_urls=state.get("obtainer_topk_urls", 5),  # Top-k URLs to select from each page
            url_timeout=state.get("obtainer_url_timeout", 60),  # Timeout in seconds for each URL exploration
            tavily_api_key=tavily_api_key if tavily_api_key else None,
            debug_mode=debug_mode,
        ))
        
        # Update state with results
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            state["obtainer_research_summary"] = result.get("research_summary", "")
            state["obtainer_subtasks"] = result.get("subtasks", [])
            state["obtainer_urls_visited"] = result.get("urls_visited", [])
            logger.info(f"WebSearch completed: {len(result.get('subtasks', []))} subtasks generated")
        
        # Send custom stream event if debug mode is enabled
        debug_mode = state.get("obtainer_debug", False)
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current=state.get('current', 'websearch_node'),
                        message="WebSearch node completed",
                        data={
                            'user_query': user_query,
                            'research_summary': result.get("research_summary", "")[:200] if "exception" not in result else None,
                            'subtasks_count': len(result.get("subtasks", [])),
                            'urls_visited_count': len(result.get("urls_visited", [])),
                            'has_exception': "exception" in result,
                            'exception': result.get("exception") if "exception" in result else None
                        }
                    ).json())
            except Exception as e:
                logger.debug(f"Could not send stream event: {e}")
        
    except Exception as e:
        logger.error(f"WebSearch node error: {e}", exc_info=True)
        state["exception"] = f"WebSearch error: {str(e)}"
    
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
) -> Dict[str, Any]:
    """Async workflow for web search"""
    try:
        # Step 1: Generate research queries
        logger.info("Step 1: Generating research queries...")
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current="websearch_workflow",
                        message="Generating research queries",
                        progress=0.0,
                        progress_num=0,
                        total=4
                    ).json())
            except Exception:
                pass
        
        queries = await query_generator.generate_queries(
            objective=user_query,
            message=user_query,
        )
        
        if not queries:
            queries = [user_query]  # Fallback to original query
        
        logger.info(f"Generated {len(queries)} research queries")
        
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current="websearch_workflow",
                        message=f"Generated {len(queries)} research queries",
                        progress=0.25,
                        progress_num=1,
                        total=4,
                        data={'queries': queries}
                    ).json())
            except Exception:
                pass
        
        # Step 2: Search for URLs
        logger.info("Step 2: Searching for URLs...")
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current="websearch_workflow",
                        message="Searching for URLs",
                        progress=0.25,
                        progress_num=1,
                        total=4
                    ).json())
            except Exception:
                pass
        
        all_urls = []
        for query in queries:
            search_results = await WebTools.search_web(query, search_engine, tavily_api_key=tavily_api_key)
            urls = WebTools.extract_urls_from_search_results(search_results)
            all_urls.extend(urls)
            logger.info(f"Query '{query}' found {len(urls)} URLs")
        
        # Remove duplicates and limit
        unique_urls = list(dict.fromkeys(all_urls))[:max_urls]
        logger.info(f"Total unique URLs to visit: {len(unique_urls)}")
        
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current="websearch_workflow",
                        message=f"Found {len(unique_urls)} unique URLs to visit",
                        progress=0.5,
                        progress_num=2,
                        total=4,
                        data={'unique_urls_count': len(unique_urls), 'total_urls_found': len(all_urls)}
                    ).json())
            except Exception:
                pass
        
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
                                new_candidate_urls = [u for u in candidate_urls if u not in visited_urls_set]
                            
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
            
            if debug_mode:
                try:
                    writer = get_stream_writer()
                    if writer:
                        writer(StreamEvent(
                            current="websearch_workflow",
                            message=f"Exploring depth {current_depth}/{max_depth} with {len(current_layer)} URLs",
                            progress=0.5 + (current_depth / max_depth) * 0.25,
                            data={
                                'current_depth': current_depth,
                                'max_depth': max_depth,
                                'urls_at_depth': len(current_layer),
                                'total_visited': len(visited_urls),
                                'queue_size': len(url_queue)
                            }
                        ).json())
                except Exception:
                    pass
            
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
        
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current="websearch_workflow",
                        message=f"Visited and stored {len(visited_urls)}/{len(unique_urls)} URLs",
                        progress=0.75,
                        progress_num=3,
                        total=4,
                        data={'visited_urls_count': len(visited_urls), 'total_urls': len(unique_urls)}
                    ).json())
            except Exception:
                pass
        
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
        
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current="websearch_workflow",
                        message=f"Generated {len(new_subtasks)} download subtasks",
                        progress=1.0,
                        progress_num=4,
                        total=4,
                        data={'subtasks_count': len(new_subtasks), 'research_summary': research_summary[:200]}
                    ).json())
            except Exception:
                pass
        
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

