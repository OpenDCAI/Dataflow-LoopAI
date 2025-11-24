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
        
        # Get Tavily API key from state or environment
        tavily_api_key = state.get("obtainer_tavily_api_key", "") or os.getenv("TAVILY_API_KEY", "")
        
        # Run async workflow
        debug_mode = state.get("obtainer_debug", False)
        result = asyncio.run(_websearch_workflow(
            user_query=user_query,
            query_generator=query_generator,
            summary_agent=summary_agent,
            rag_manager=rag_manager,
            search_engine=state.get("obtainer_search_engine", "tavily"),
            max_urls=state.get("obtainer_max_urls", 10),
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
    search_engine: str = "tavily",
    max_urls: int = 10,
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
        
        # Step 3: Visit URLs with Jina and store in RAG (concurrent, max 5 at a time)
        logger.info("Step 3: Visiting URLs and storing in RAG (concurrent, max 5)...")
        visited_urls = []
        
        # Semaphore to limit concurrent requests to 5
        semaphore = asyncio.Semaphore(5)
        
        async def visit_and_store_url(url: str, index: int, total: int) -> Optional[str]:
            """Visit a URL and store its content in RAG"""
            async with semaphore:
                try:
                    logger.info(f"Visiting URL {index}/{total}: {url}")
                    page_content = await WebTools.read_with_jina_reader(url)
                    
                    if page_content.get("text") and len(page_content["text"].strip()) > 50:
                        await rag_manager.add_webpage_content(
                            url=url,
                            text_content=page_content["text"],
                            metadata={"source": "websearch", "query": user_query}
                        )
                        logger.info(f"Successfully stored content from {url}")
                        return url
                    else:
                        logger.warning(f"URL {url} has insufficient content")
                        return None
                except Exception as e:
                    logger.error(f"Error visiting URL {url}: {e}")
                    return None
        
        # Create tasks for all URLs
        tasks = [
            visit_and_store_url(url, i + 1, len(unique_urls))
            for i, url in enumerate(unique_urls)
        ]
        
        # Execute all tasks concurrently (max 5 at a time due to semaphore)
        results = await asyncio.gather(*tasks)
        
        # Collect successfully visited URLs
        visited_urls = [url for url in results if url is not None]
        
        logger.info(f"Successfully visited and stored {len(visited_urls)} URLs")
        
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
        }
        
    except Exception as e:
        logger.error(f"WebSearch workflow error: {e}", exc_info=True)
        return {
            "exception": f"WebSearch workflow error: {str(e)}",
            "research_summary": "",
            "subtasks": [],
            "urls_visited": [],
        }

