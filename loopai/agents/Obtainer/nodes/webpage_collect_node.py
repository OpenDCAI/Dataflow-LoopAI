import json
import asyncio
import os
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.agents.Obtainer.utils import (
    WebTools,
    QueryGenerator,
    URLSelector,
    PlaywrightBrowserManager,
    WebPageActionAgent,
    WebPageDataSaver,
)
from loopai.common.prompts import PromptLoader

logger = get_logger()


def webpage_collect_node(state: LoopAIState) -> LoopAIState:
    """
    Webpage collection node that:
    1. Uses Tavily to search for resource websites
    2. Uses Playwright to explore websites and find resource list pages
    3. Uses Jina.ai to perform breadth-first exploration of resource list pages
    4. Saves all collected structured data to database and JSONL
    """
    logger.info("=== WebPage Collect Node: Starting ===")
    
    # Get user query from state
    user_query = ""
    
    # First try to get from automated_query (highest priority)
    if state.get("automated_query"):
        user_query = state.get("automated_query")
    else:
        # Extract user message from messages list
        if state.get("messages") and len(state["messages"]) > 0:
            from langchain_core.messages import HumanMessage
            
            for message in reversed(state["messages"]):
                if isinstance(message, HumanMessage):
                    if hasattr(message, "content"):
                        user_query = message.content
                        break
                elif isinstance(message, dict):
                    msg_type = message.get("type", "")
                    msg_role = message.get("role", "")
                    if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                        user_query = message.get("content", "")
                        if user_query:
                            break
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
            logger.error("Missing required configuration for webpage collect node")
            state["exception"] = "Missing model configuration (model_name, base_url, api_key)"
            return state
        
        # Initialize prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # Get Tavily API key
        tavily_api_key = state.get("obtainer_tavily_api_key", "") or os.getenv("TAVILY_API_KEY", "")
        
        # Output directory
        output_dir = state.get("output_dir", "./output")
        webpage_collect_dir = os.path.join(output_dir, "webpage_collect")
        os.makedirs(webpage_collect_dir, exist_ok=True)
        
        # Run async workflow
        debug_mode = state.get("obtainer_debug", False)
        result = asyncio.run(_webpage_collect_workflow(
            user_query=user_query,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            tavily_api_key=tavily_api_key if tavily_api_key else None,
            output_dir=webpage_collect_dir,
            max_exploration_depth=state.get("obtainer_max_exploration_depth", 5),
            max_jina_urls=state.get("obtainer_max_jina_urls", 50),
            playwright_concurrent_limit=state.get("obtainer_playwright_concurrent_limit", 3),
            jina_concurrent_limit=state.get("obtainer_jina_concurrent_limit", 10),
            proxy=state.get("obtainer_proxy") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("ALL_PROXY") or None,
            debug_mode=debug_mode,
        ))
        
        # Update state with results
        if "exception" in result:
            state["exception"] = result["exception"]
        else:
            state["webpage_collect_summary"] = result.get("summary", "")
            state["webpage_collect_urls_visited"] = result.get("urls_visited", [])
            state["webpage_collect_data_count"] = result.get("data_count", 0)
            state["webpage_collect_jsonl_path"] = result.get("jsonl_path", "")
            state["webpage_collect_db_path"] = result.get("db_path", "")
            logger.info(
                f"WebPage Collect completed: {result.get('data_count', 0)} pages collected, "
                f"{len(result.get('urls_visited', []))} URLs visited"
            )
        
        # Send custom stream event if debug mode is enabled
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current=state.get('current', 'webpage_collect_node'),
                        message="WebPage Collect node completed",
                        data={
                            'user_query': user_query,
                            'data_count': result.get("data_count", 0),
                            'urls_visited_count': len(result.get("urls_visited", [])),
                            'has_exception': "exception" in result,
                        }
                    ).json())
            except Exception as e:
                logger.debug(f"Could not send stream event: {e}")
        
    except Exception as e:
        logger.error(f"WebPage Collect node error: {e}", exc_info=True)
        state["exception"] = f"WebPage Collect error: {str(e)}"
    
    logger.info("=== WebPage Collect Node: Completed ===")
    return state


async def _webpage_collect_workflow(
    user_query: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: PromptLoader,
    tavily_api_key: Optional[str],
    output_dir: str,
    max_exploration_depth: int = 5,
    max_jina_urls: int = 50,
    playwright_concurrent_limit: int = 3,
    jina_concurrent_limit: int = 10,
    proxy: Optional[str] = None,
    debug_mode: bool = False,
) -> Dict[str, Any]:
    """
    Main workflow for webpage collection
    
    Steps:
    1. Generate search queries using LLM
    2. Search for resource websites using Tavily
    3. Use Playwright to explore websites and find resource list pages
    4. Use Jina.ai to perform breadth-first exploration
    5. Save all collected data
    """
    browser_manager = None
    data_saver = None
    
    try:
        # Initialize data saver
        data_saver = WebPageDataSaver(
            output_dir=output_dir,
            jsonl_filename="webpage_data.jsonl",
            db_filename="webpage_data.db",
        )
        
        # Step 1: Generate search queries
        logger.info("Step 1: Generating search queries...")
        query_generator = QueryGenerator(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
        )
        
        queries = await query_generator.generate_queries(
            objective=user_query,
            message=user_query,
        )
        
        if not queries:
            queries = [user_query]
        
        logger.info(f"Generated {len(queries)} search queries")
        
        # Step 2: Search for resource websites using Tavily
        logger.info("Step 2: Searching for resource websites...")
        all_urls = []
        for query in queries:
            search_results = await WebTools.search_web(query, "tavily", tavily_api_key=tavily_api_key)
            urls = WebTools.extract_urls_from_search_results(search_results)
            all_urls.extend(urls)
            logger.info(f"Query '{query}' found {len(urls)} URLs")
        
        # Remove duplicates
        unique_urls = list(dict.fromkeys(all_urls))[:10]  # Limit to 10 URLs
        logger.info(f"Total unique URLs to explore: {len(unique_urls)}")
        
        # Step 3: Playwright exploration phase
        logger.info("Step 3: Starting Playwright exploration...")
        # Get proxy from parameter or environment (parameter takes precedence)
        if not proxy:
            proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("ALL_PROXY") or None
        browser_manager = PlaywrightBrowserManager(
            headless=True, 
            timeout=60000,  # 60 seconds timeout
            proxy=proxy,
        )
        await browser_manager.start()
        
        action_agent = WebPageActionAgent(
            browser_manager=browser_manager,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_template_dir=prompt_loader.prompt_template_dir if prompt_loader else None,
        )
        
        url_selector = URLSelector(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=0.3,
            prompt_loader=prompt_loader,
        )
        
        visited_urls = []
        resource_list_urls = []
        collected_data = []
        
        # Concurrent limit for Playwright exploration (use separate pages for each URL)
        playwright_semaphore = asyncio.Semaphore(playwright_concurrent_limit)
        visited_urls_lock = asyncio.Lock()
        
        async def explore_url_with_playwright(url: str) -> Dict[str, Any]:
            """Explore a single URL with Playwright"""
            async with playwright_semaphore:
                try:
                    logger.info(f"Exploring URL: {url}")
                    
                    # Create a new page for this URL to avoid conflicts
                    page = await browser_manager.get_page()
                    
                    # Navigate to URL with retry and fallback
                    navigation_success = False
                    nav_error_msg = None
                    
                    # Retry navigation up to 2 times
                    for retry_attempt in range(2):
                        try:
                            # Try networkidle first
                            try:
                                await page.goto(url, wait_until="networkidle", timeout=60000)
                                navigation_success = True
                                break
                            except Exception as nav_error:
                                nav_error_msg = str(nav_error)
                                # Check if it's ERR_ABORTED (non-retryable)
                                if "ERR_ABORTED" in str(nav_error) or "net::ERR" in str(nav_error):
                                    logger.warning(f"Navigation aborted for {url}: {nav_error_msg}")
                                    # Try with less strict wait condition
                                    try:
                                        await page.goto(url, wait_until="load", timeout=30000)
                                        navigation_success = True
                                        break
                                    except Exception:
                                        # If still fails, try domcontentloaded
                                        try:
                                            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                                            navigation_success = True
                                            break
                                        except Exception:
                                            # If all fail, break retry loop
                                            if retry_attempt < 1:
                                                logger.info(f"Retrying navigation for {url} (attempt {retry_attempt + 1}/2)")
                                                await asyncio.sleep(2)  # Wait before retry
                                                continue
                                            else:
                                                raise
                                else:
                                    # Other errors, try with less strict conditions
                                    logger.warning(f"networkidle timeout for {url}, trying with 'load': {nav_error_msg}")
                                    try:
                                        await page.goto(url, wait_until="load", timeout=60000)
                                        navigation_success = True
                                        break
                                    except Exception as load_error:
                                        logger.warning(f"load timeout for {url}, trying with 'domcontentloaded': {load_error}")
                                        try:
                                            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                                            navigation_success = True
                                            break
                                        except Exception as dom_error:
                                            if retry_attempt < 1:
                                                logger.info(f"Retrying navigation for {url} (attempt {retry_attempt + 1}/2)")
                                                await asyncio.sleep(2)
                                                continue
                                            else:
                                                raise
                        except Exception as e:
                            if retry_attempt < 1:
                                logger.info(f"Navigation failed for {url}, retrying (attempt {retry_attempt + 1}/2): {e}")
                                await asyncio.sleep(2)
                                continue
                            else:
                                raise
                    
                    if not navigation_success:
                        raise Exception(f"Failed to navigate to {url} after retries: {nav_error_msg}")
                    
                    await asyncio.sleep(2)  # Wait for page to stabilize
                    
                    # Track visited URL
                    async with visited_urls_lock:
                        if url not in visited_urls:
                            visited_urls.append(url)
                    
                    # Check if this is already a resource list page
                    page_info = await browser_manager.get_page_info()
                    page_text = await page.evaluate("() => document.body.innerText")
                    
                    is_resource_list = await action_agent.check_if_resource_list_page(
                        page_content=page_text[:8000],
                        user_objective=user_query,
                    )
                    
                    result = {
                        "url": url,
                        "is_resource_list": is_resource_list.get("is_resource_list", False),
                        "final_url": url,
                        "exploration_result": None,
                    }
                    
                    if is_resource_list.get("is_resource_list", False):
                        logger.info(f"Found resource list page: {url}")
                        return result
                    else:
                        # Use agent to explore the page
                        logger.info(f"Using agent to explore: {url}")
                        exploration_result = await action_agent.execute_action(
                            user_objective=user_query,
                            current_page_info=page_info,
                            max_iterations=max_exploration_depth,
                        )
                        result["exploration_result"] = exploration_result
                        
                        # Check again if we found a resource list page
                        final_page_info = await browser_manager.get_page_info()
                        if final_page_info.get("url") != url:
                            # Agent navigated to a new page
                            final_page_text = await page.evaluate("() => document.body.innerText")
                            is_resource_list = await action_agent.check_if_resource_list_page(
                                page_content=final_page_text[:8000],
                                user_objective=user_query,
                            )
                            if is_resource_list.get("is_resource_list", False):
                                logger.info(f"Agent found resource list page: {final_page_info.get('url')}")
                                result["is_resource_list"] = True
                                result["final_url"] = final_page_info.get("url")
                        
                        return result
                        
                except Exception as e:
                    error_msg = str(e)
                    # Check if it's a navigation error (ERR_ABORTED, timeout, etc.)
                    if "ERR_ABORTED" in error_msg or "net::ERR" in error_msg:
                        logger.warning(f"Navigation aborted for {url}: {error_msg}. This may be due to network issues, proxy problems, or server restrictions.")
                    elif "timeout" in error_msg.lower():
                        logger.warning(f"Navigation timeout for {url}: {error_msg}")
                    else:
                        logger.error(f"Error exploring URL {url}: {error_msg}")
                    
                    return {
                        "url": url,
                        "is_resource_list": False,
                        "final_url": url,
                        "error": error_msg,
                        "error_type": "navigation_failed",
                    }
        
        # Process URLs concurrently
        logger.info(f"Processing {len(unique_urls)} URLs with {playwright_concurrent_limit} concurrent workers...")
        playwright_tasks = [explore_url_with_playwright(url) for url in unique_urls]
        playwright_results = await asyncio.gather(*playwright_tasks, return_exceptions=True)
        
        # Process results
        for result in playwright_results:
            if isinstance(result, Exception):
                logger.error(f"Exception in playwright exploration: {result}")
                continue
            
            if result.get("is_resource_list", False):
                final_url = result.get("final_url", result.get("url"))
                async with visited_urls_lock:
                    if final_url not in resource_list_urls:
                        resource_list_urls.append(final_url)
        
        # Clean up pages after Playwright exploration
        try:
            await browser_manager.close_page()
        except Exception:
            pass
        
        logger.info(f"Found {len(resource_list_urls)} resource list pages")
        
        # Step 4: Jina breadth-first exploration
        logger.info("Step 4: Starting Jina breadth-first exploration...")
        
        # Concurrent limit for Jina exploration
        jina_semaphore = asyncio.Semaphore(jina_concurrent_limit)
        
        async def explore_resource_list_page(resource_url: str) -> List[Dict[str, Any]]:
            """Explore a resource list page and its extracted URLs"""
            async with jina_semaphore:
                try:
                    logger.info(f"Exploring resource list page with Jina: {resource_url}")
                    
                    # Use Jina to parse the resource list page
                    page_content = await WebTools.read_with_jina_reader(resource_url)
                    structured_content = page_content.get("structured_content", {})
                    markdown_content = page_content.get("text", "")
                    extracted_urls = page_content.get("urls", [])
                    
                    # Save the resource list page itself
                    await data_saver.save_webpage_data(
                        url=resource_url,
                        title=structured_content.get("title", ""),
                        content=markdown_content,
                        structured_data=structured_content,
                        source="jina_resource_list",
                        metadata={"extracted_urls_count": len(extracted_urls)},
                    )
                    
                    page_data = {
                        "url": resource_url,
                        "title": structured_content.get("title", ""),
                        "content": markdown_content,
                        "structured_data": structured_content,
                    }
                    
                    all_page_data = [page_data]
                    
                    # Filter URLs using LLM
                    filtered_urls = []
                    if extracted_urls:
                        logger.info(f"Found {len(extracted_urls)} URLs, filtering with LLM...")
                        filtered_urls = await url_selector.select_top_urls(
                            research_objective=user_query,
                            url_list=extracted_urls,
                            webpage_content=markdown_content[:8000],
                            topk=min(max_jina_urls, len(extracted_urls)),
                        )
                        logger.info(f"Filtered to {len(filtered_urls)} relevant URLs")
                    
                    # Explore filtered URLs concurrently
                    async def explore_extracted_url(extracted_url: str) -> Optional[Dict[str, Any]]:
                        """Explore a single extracted URL"""
                        try:
                            # Convert relative URLs to absolute
                            full_url = urljoin(resource_url, extracted_url)
                            
                            # Check if already visited
                            async with visited_urls_lock:
                                if full_url in visited_urls:
                                    return None
                                visited_urls.append(full_url)
                            
                            logger.info(f"Exploring with Jina: {full_url}")
                            
                            # Parse with Jina
                            item_content = await WebTools.read_with_jina_reader(full_url)
                            item_structured = item_content.get("structured_content", {})
                            item_markdown = item_content.get("text", "")
                            
                            # Save collected data
                            await data_saver.save_webpage_data(
                                url=full_url,
                                title=item_structured.get("title", ""),
                                content=item_markdown,
                                structured_data=item_structured,
                                source="jina_exploration",
                                metadata={"parent_url": resource_url},
                            )
                            
                            return {
                                "url": full_url,
                                "title": item_structured.get("title", ""),
                                "content": item_markdown,
                                "structured_data": item_structured,
                            }
                            
                        except Exception as e:
                            logger.error(f"Error exploring URL {extracted_url}: {e}")
                            return None
                    
                    # Process extracted URLs concurrently
                    if filtered_urls:
                        extracted_tasks = [explore_extracted_url(url) for url in filtered_urls[:max_jina_urls]]
                        extracted_results = await asyncio.gather(*extracted_tasks, return_exceptions=True)
                        
                        for result in extracted_results:
                            if isinstance(result, Exception):
                                logger.error(f"Exception in extracted URL exploration: {result}")
                                continue
                            if result:
                                all_page_data.append(result)
                    
                    return all_page_data
                    
                except Exception as e:
                    logger.error(f"Error exploring resource list page {resource_url}: {e}")
                    return []
        
        # Process resource list pages concurrently
        logger.info(f"Processing {len(resource_list_urls)} resource list pages with {jina_concurrent_limit} concurrent workers...")
        jina_tasks = [explore_resource_list_page(url) for url in resource_list_urls]
        jina_results = await asyncio.gather(*jina_tasks, return_exceptions=True)
        
        # Collect all data
        for result in jina_results:
            if isinstance(result, Exception):
                logger.error(f"Exception in Jina exploration: {result}")
                continue
            if result:
                collected_data.extend(result)
        
        # Get final counts
        counts = await data_saver.get_saved_count()
        
        summary = (
            f"Collected {len(collected_data)} web pages. "
            f"Explored {len(visited_urls)} URLs, found {len(resource_list_urls)} resource list pages. "
            f"Data saved to: {data_saver.get_jsonl_path()}"
        )
        
        return {
            "summary": summary,
            "urls_visited": visited_urls,
            "resource_list_urls": resource_list_urls,
            "data_count": len(collected_data),
            "jsonl_path": data_saver.get_jsonl_path(),
            "db_path": data_saver.get_db_path(),
        }
        
    except Exception as e:
        logger.error(f"WebPage Collect workflow error: {e}", exc_info=True)
        return {
            "exception": f"WebPage Collect workflow error: {str(e)}",
            "summary": "",
            "urls_visited": [],
            "resource_list_urls": [],
            "data_count": 0,
            "jsonl_path": "",
            "db_path": "",
        }
    finally:
        # Cleanup
        if browser_manager:
            try:
                # Close MCP connection if action_agent was created
                if 'action_agent' in locals() and hasattr(action_agent, 'playwright_tools'):
                    try:
                        await action_agent.playwright_tools.close()
                    except Exception as e:
                        logger.warning(f"Error closing MCP connection: {e}")
                await browser_manager.close()
            except Exception as e:
                logger.warning(f"Error closing browser manager: {e}")

