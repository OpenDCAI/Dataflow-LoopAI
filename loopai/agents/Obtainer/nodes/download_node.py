import json
import asyncio
import os
import re
from typing import Dict, Any, List, Optional
from pathlib import Path
from urllib.parse import urljoin

try:
    from playwright.async_api import async_playwright, Page, Error as PlaywrightError
except ImportError:
    async_playwright = None
    Page = None
    PlaywrightError = Exception

from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.agents.Obtainer.utils.download_method_decision import DownloadMethodDecisionAgent
from loopai.agents.Obtainer.utils.hf_manager import HuggingFaceManager
from loopai.agents.Obtainer.utils.kaggle_manager import KaggleManager
from loopai.agents.Obtainer.utils.hf_decision_agent import HuggingFaceDecisionAgent
from loopai.agents.Obtainer.utils.kaggle_decision_agent import KaggleDecisionAgent
from loopai.agents.Obtainer.utils.web_tools import WebTools
from loopai.agents.Obtainer.utils.webpage_reader import WebPageReader
from loopai.common.prompts import PromptLoader

logger = get_logger()


def download_node(state: LoopAIState) -> LoopAIState:
    """Download node that executes download subtasks using three methods in priority order"""
    logger.info("=== Download Node: Starting ===")
    
    # Get download subtasks
    subtasks = state.get("obtainer", {}).get("subtasks", [])
    download_tasks = [task for task in subtasks if task.get("type") == "download"]
    
    if not download_tasks:
        logger.info("No download subtasks found, skipping download node")
        return state
    
    logger.info(f"Found {len(download_tasks)} download subtasks to execute")
    
    # Get user query for context
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
    
    # Initialize components
    try:
        model_name = state.get("obtainer", {}).get("model_path") or state.get("analyze_model_path")
        base_url = state.get("obtainer", {}).get("base_url") or state.get("analyze_base_url")
        api_key = state.get("obtainer", {}).get("api_key") or state.get("analyze_api_key")
        temperature = state.get("obtainer", {}).get("temperature", 0.7)
        
        if not model_name or not base_url or not api_key:
            logger.error("Missing required configuration for download node")
            state["exception"] = "Missing model configuration for download node"
            return state
        
        # Initialize prompt loader
        prompt_loader = PromptLoader(state.get("prompt_template_dir"))
        
        # Initialize download method decision agent
        decision_agent = DownloadMethodDecisionAgent(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
        )
        
        # Output directory for downloads
        output_dir = state.get("output_dir", "./output")
        download_dir = os.path.join(output_dir, "downloads")
        os.makedirs(download_dir, exist_ok=True)
        
        # Get search engine for web download
        search_engine = state.get("obtainer", {}).get("search_engine", "tavily")
        max_urls = state.get("obtainer", {}).get("max_urls", 10)
        tavily_api_key = (
            state.get("obtainer", {}).get("tavily_api_key", "")
            or os.getenv("TAVILY_API_KEY", "")
        )
        
        # Get Kaggle credentials from state or environment
        kaggle_username = state.get("obtainer", {}).get("kaggle_username", "") or os.getenv("KAGGLE_USERNAME", "")
        kaggle_key = state.get("obtainer", {}).get("kaggle_key", "") or os.getenv("KAGGLE_KEY", "")
        
        # Run async workflow
        debug_mode = state.get("obtainer_debug", False)
        result = asyncio.run(_download_workflow(
            download_tasks=download_tasks,
            user_query=user_query,
            decision_agent=decision_agent,
            download_dir=download_dir,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            search_engine=search_engine,
            max_urls=max_urls,
            tavily_api_key=tavily_api_key if tavily_api_key else None,
            kaggle_username=kaggle_username if kaggle_username else None,
            kaggle_key=kaggle_key if kaggle_key else None,
            debug_mode=debug_mode,
            event_name=state['current'],
        ))
        
        # Update state with results
        if "exception" in result:
            state["exception"] = result["exception"]
        
        # Update subtasks with status
        completed_tasks = result.get("completed_tasks", [])
        failed_tasks = result.get("failed_tasks", [])
        
        # Update state subtasks
        updated_subtasks = []
        for task in subtasks:
            if task.get("type") == "download":
                # Find matching task in results
                # Normalize search_keywords to string (it might be a list)
                task_search_keywords = task.get("search_keywords", "")
                if isinstance(task_search_keywords, (list, tuple)):
                    task_search_keywords_str = ", ".join(str(kw) for kw in task_search_keywords if kw)
                else:
                    task_search_keywords_str = str(task_search_keywords) if task_search_keywords else ""
                task_id = task.get("objective", "") + "_" + task_search_keywords_str
                found = False
                for completed in completed_tasks:
                    if completed.get("task_id") == task_id:
                        task["status"] = "completed_successfully"
                        task["download_path"] = completed.get("download_path")
                        task["method_used"] = completed.get("method_used")
                        found = True
                        break
                if not found:
                    for failed in failed_tasks:
                        if failed.get("task_id") == task_id:
                            task["status"] = "failed_to_download"
                            task["failure_reason"] = failed.get("failure_reason")
                            found = True
                            break
                if not found:
                    task["status"] = "pending"
            updated_subtasks.append(task)
        
        state.setdefault("obtainer", {})["subtasks"] = updated_subtasks
        state.setdefault("obtainer", {})["download_results"] = {
            "completed": len(completed_tasks),
            "failed": len(failed_tasks),
            "total": len(download_tasks),
        }
        
        logger.info(
            f"Download node completed: {len(completed_tasks)} succeeded, "
            f"{len(failed_tasks)} failed out of {len(download_tasks)} total"
        )
        
        # Send custom stream event if debug mode is enabled
        debug_mode = state.get("obtainer_debug", False)
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        message="Download node completed",
                        progress=len(completed_tasks) / len(download_tasks) if download_tasks else 0,
                        progress_num=len(completed_tasks),
                        total=len(download_tasks),
                        data={
                            'completed_count': len(completed_tasks),
                            'failed_count': len(failed_tasks),
                            'total_count': len(download_tasks),
                            'completed_tasks': [
                                {
                                    'objective': task.get('objective', 'N/A'),
                                    'method_used': task.get('method_used', 'N/A'),
                                    'download_path': task.get('download_path', 'N/A')
                                }
                                for task in completed_tasks[:5]  # Limit to first 5
                            ],
                            'failed_tasks': [
                                {
                                    'objective': task.get('objective', 'N/A'),
                                    'failure_reason': task.get('failure_reason', 'N/A')[:100]
                                }
                                for task in failed_tasks[:5]  # Limit to first 5
                            ]
                        }
                    ).json())
            except Exception as e:
                logger.debug(f"Could not send stream event: {e}")
        
    except Exception as e:
        logger.error(f"Download node error: {e}", exc_info=True)
        state["exception"] = f"Download error: {str(e)}"
    
    logger.info("=== Download Node: Completed ===")
    return state


async def _download_workflow(
    download_tasks: List[Dict[str, Any]],
    user_query: str,
    decision_agent: DownloadMethodDecisionAgent,
    download_dir: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.7,
    prompt_loader: Optional[PromptLoader] = None,
    search_engine: str = "tavily",
    max_urls: int = 10,
    tavily_api_key: Optional[str] = None,
    kaggle_username: Optional[str] = None,
    kaggle_key: Optional[str] = None,
    debug_mode: bool = False,
    event_name: str = "download_workflow"
) -> Dict[str, Any]:
    """Async workflow for executing download tasks"""
    completed_tasks = []
    failed_tasks = []
    
    for task_idx, task in enumerate(download_tasks, 1):
        task_objective = task.get("objective", "")
        search_keywords = task.get("search_keywords", task_objective)
        
        # Normalize search_keywords to string (it might be a list)
        if isinstance(search_keywords, (list, tuple)):
            search_keywords_str = ", ".join(str(kw) for kw in search_keywords if kw)
        else:
            search_keywords_str = str(search_keywords) if search_keywords else ""
        
        task_id = task_objective + "_" + search_keywords_str
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing download task {task_idx}/{len(download_tasks)}")
        logger.info(f"Objective: {task_objective}")
        logger.info(f"Search Keywords: {search_keywords}")
        logger.info(f"{'='*60}")
        
        # Send progress event if debug mode is enabled
        if debug_mode:
            try:
                writer = get_stream_writer()
                if writer:
                    writer(StreamEvent(
                        current=event_name,
                        message=f"Processing download task {task_idx}/{len(download_tasks)}: {task_objective}",
                        progress=task_idx / len(download_tasks) if download_tasks else 0,
                        progress_num=task_idx,
                        total=len(download_tasks),
                        data={'task_objective': task_objective, 'search_keywords': search_keywords_str}
                    ).json())
            except Exception:
                pass
        
        try:
            # Step 1: Decide download method order
            logger.info("Step 1: Deciding download method order...")
            decision = await decision_agent.decide_method_order(
                user_original_request=user_query,
                current_task_objective=task_objective,
                search_keywords=search_keywords,
            )
            
            method_order = decision.get("method_order", ["huggingface", "kaggle", "web"])
            hf_keywords = decision.get("keywords_for_hf", [])
            
            logger.info(f"Method order decided: {method_order}")
            logger.info(f"HF keywords: {hf_keywords}")
            
            # Step 2: Try each method in order
            download_success = False
            method_used = None
            download_path = None
            failure_reasons = []
            
            for method in method_order:
                logger.info(f"\nTrying download method: {method}")
                try:
                    if method == "huggingface":
                        result = await _try_huggingface_download(
                            task_objective=task_objective,
                            search_keywords=search_keywords,
                            hf_keywords=hf_keywords,
                            download_dir=download_dir,
                            model_name=model_name,
                            base_url=base_url,
                            api_key=api_key,
                            prompt_loader=prompt_loader,
                        )
                    elif method == "kaggle":
                        result = await _try_kaggle_download(
                            task_objective=task_objective,
                            search_keywords=search_keywords,
                            download_dir=download_dir,
                            model_name=model_name,
                            base_url=base_url,
                            api_key=api_key,
                            prompt_loader=prompt_loader,
                            kaggle_username=kaggle_username,
                            kaggle_key=kaggle_key,
                        )
                    elif method == "web":
                        result = await _try_web_download(
                            task_objective=task_objective,
                            search_keywords=search_keywords,
                            download_dir=download_dir,
                            search_engine=search_engine,
                            max_urls=max_urls,
                            model_name=model_name,
                            base_url=base_url,
                            api_key=api_key,
                            temperature=temperature,
                            tavily_api_key=tavily_api_key,
                        )
                    else:
                        logger.warning(f"Unknown download method: {method}, skipping")
                        continue
                    
                    if result.get("success"):
                        download_success = True
                        method_used = method
                        download_path = result.get("download_path")
                        logger.info(f"✓ Download succeeded using {method}")
                        
                        # Send success event if debug mode is enabled
                        if debug_mode:
                            try:
                                writer = get_stream_writer()
                                if writer:
                                    writer(StreamEvent(
                                        current=event_name,
                                        message=f"Download succeeded: {task_objective} via {method}",
                                        progress=task_idx / len(download_tasks) if download_tasks else 0,
                                        progress_num=task_idx,
                                        total=len(download_tasks),
                                        data={
                                            'task_objective': task_objective,
                                            'method_used': method,
                                            'download_path': download_path
                                        }
                                    ).json())
                            except Exception:
                                pass
                        break
                    else:
                        reason = result.get("reason", "Unknown error")
                        failure_reasons.append(f"{method}: {reason}")
                        logger.info(f"✗ Download failed using {method}: {reason}")
                        
                except Exception as e:
                    failure_reasons.append(f"{method}: {str(e)}")
                    logger.error(f"Error trying {method}: {e}", exc_info=True)
            
            # Step 3: Record result
            if download_success:
                completed_tasks.append({
                    "task_id": task_id,
                    "objective": task_objective,
                    "search_keywords": search_keywords,
                    "method_used": method_used,
                    "download_path": download_path,
                })
            else:
                failed_tasks.append({
                    "task_id": task_id,
                    "objective": task_objective,
                    "search_keywords": search_keywords,
                    "failure_reason": "; ".join(failure_reasons),
                })
                
        except Exception as e:
            logger.error(f"Error processing download task: {e}", exc_info=True)
            failed_tasks.append({
                "task_id": task_id,
                "objective": task_objective,
                "search_keywords": search_keywords,
                "failure_reason": f"Task processing error: {str(e)}",
            })
    
    return {
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
    }


async def _try_huggingface_download(
    task_objective: str,
    search_keywords: str,
    hf_keywords: List[str],
    download_dir: str,
    model_name: str,
    base_url: str,
    api_key: str,
    prompt_loader: Optional[PromptLoader] = None,
) -> Dict[str, Any]:
    """Try downloading from HuggingFace"""
    logger.info("[HuggingFace] Attempting download...")
    
    try:
        # Initialize managers
        hf_manager = HuggingFaceManager(disable_cache=False)
        hf_decision_agent = HuggingFaceDecisionAgent(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            prompt_loader=prompt_loader,
        )
        
        # Prepare keywords
        if not hf_keywords:
            if isinstance(search_keywords, (list, tuple)):
                hf_keywords = [kw for kw in search_keywords if isinstance(kw, str) and kw.strip()] or [task_objective]
            else:
                hf_keywords = [search_keywords] if search_keywords else [task_objective]
        
        # Search datasets
        logger.info(f"[HuggingFace] Searching with keywords: {hf_keywords}")
        search_results = await hf_manager.search_datasets(hf_keywords, max_results=5)
        
        if not search_results or all(not v for v in search_results.values()):
            return {
                "success": False,
                "reason": "No search results found",
            }
        
        # Decision agent selects best dataset
        selected_id = await hf_decision_agent.execute(
            search_results=search_results,
            objective=task_objective,
            message="",
        )
        
        if not selected_id:
            return {
                "success": False,
                "reason": "No suitable dataset selected",
            }
        
        # Download dataset
        hf_save_dir = os.path.join(download_dir, "hf_datasets")
        os.makedirs(hf_save_dir, exist_ok=True)
        
        save_path = await hf_manager.download_dataset(selected_id, hf_save_dir)
        
        if save_path:
            logger.info(f"[HuggingFace] Download successful: {save_path}")
            return {
                "success": True,
                "download_path": save_path,
                "dataset_id": selected_id,
            }
        else:
            return {
                "success": False,
                "reason": "Download failed",
            }
            
    except Exception as e:
        logger.error(f"[HuggingFace] Download error: {e}", exc_info=True)
        return {
            "success": False,
            "reason": f"Error: {str(e)}",
        }


async def _try_kaggle_download(
    task_objective: str,
    search_keywords: str,
    download_dir: str,
    model_name: str,
    base_url: str,
    api_key: str,
    prompt_loader: Optional[PromptLoader] = None,
    kaggle_username: Optional[str] = None,
    kaggle_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Try downloading from Kaggle"""
    logger.info("[Kaggle] Attempting download...")
    
    if async_playwright is None:
        return {
            "success": False,
            "reason": "Playwright not installed",
        }
    
    try:
        # Initialize managers with Kaggle credentials
        kaggle_manager = KaggleManager(
            disable_cache=False,
            kaggle_username=kaggle_username,
            kaggle_key=kaggle_key,
        )
        kaggle_decision_agent = KaggleDecisionAgent(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            prompt_loader=prompt_loader,
        )
        
        # Prepare keywords
        if isinstance(search_keywords, (list, tuple)):
            keywords = [kw for kw in search_keywords if isinstance(kw, str) and kw.strip()] or [task_objective]
        else:
            keywords = [search_keywords] if search_keywords else [task_objective]
        
        # Search datasets
        logger.info(f"[Kaggle] Searching with keywords: {keywords}")
        search_results = await kaggle_manager.search_datasets(keywords, max_results=5)
        
        if not search_results or all(not v for v in search_results.values()):
            return {
                "success": False,
                "reason": "No search results found",
            }
        
        # Decision agent selects best dataset
        selected_id = await kaggle_decision_agent.execute(
            search_results=search_results,
            objective=task_objective,
            message="",
        )
        
        if not selected_id:
            return {
                "success": False,
                "reason": "No suitable dataset selected",
            }
        
        # Download dataset using Playwright
        kaggle_save_dir = os.path.join(download_dir, "kaggle_datasets")
        os.makedirs(kaggle_save_dir, exist_ok=True)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            try:
                save_path = await kaggle_manager.try_download(page, selected_id, kaggle_save_dir)
                
                if save_path:
                    logger.info(f"[Kaggle] Download successful: {save_path}")
                    return {
                        "success": True,
                        "download_path": save_path,
                        "dataset_id": selected_id,
                    }
                else:
                    return {
                        "success": False,
                        "reason": "Download failed",
                    }
            finally:
                await browser.close()
                
    except Exception as e:
        logger.error(f"[Kaggle] Download error: {e}", exc_info=True)
        return {
            "success": False,
            "reason": f"Error: {str(e)}",
        }


async def _try_web_download(
    task_objective: str,
    search_keywords: str,
    download_dir: str,
    search_engine: str = "tavily",
    max_urls: int = 10,
    max_cycles: int = 3,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.7,
    tavily_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Try downloading from web using Playwright and LLM-based link extraction"""
    logger.info("[Web] Attempting download...")
    
    if async_playwright is None:
        return {
            "success": False,
            "reason": "Playwright not installed",
        }
    
    if not model_name or not base_url or not api_key:
        return {
            "success": False,
            "reason": "Missing LLM configuration for web download",
        }
    
    try:
        # Initialize WebPageReader
        webpage_reader = WebPageReader(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        
        # Step 1: Search for URLs
        query_kw = (
            ", ".join(search_keywords)
            if isinstance(search_keywords, (list, tuple))
            else search_keywords
        )
        logger.info(f"[Web] Searching with query: {query_kw}")
        search_results = await WebTools.search_web(
            query_kw,
            search_engine,
            tavily_api_key=tavily_api_key or "",
        )
        
        # Extract URLs from search results
        urls = WebTools.extract_urls_from_search_results(search_results)
        if not urls:
            return {
                "success": False,
                "reason": "No URLs found in search results",
            }
        
        # Limit URLs
        urls = urls[:max_urls]
        logger.info(f"[Web] Found {len(urls)} URLs to check")
        
        # Step 2: Visit URLs and use LLM to extract download links
        web_save_dir = os.path.join(download_dir, "web_downloads")
        os.makedirs(web_save_dir, exist_ok=True)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            try:
                visited_urls = set()
                download_success = False
                download_path = None
                
                for cycle in range(max_cycles):
                    if download_success:
                        break
                    
                    current_urls = [u for u in urls if u not in visited_urls]
                    if not current_urls:
                        break
                    
                    for url in current_urls[:5]:  # Process up to 5 URLs per cycle
                        if download_success:
                            break
                        
                        visited_urls.add(url)
                        logger.info(f"[Web] Analyzing URL: {url}")
                        
                        try:
                            # Read page with Jina
                            page_content = await WebTools.read_with_jina_reader(url)
                            text_content = page_content.get("text", "")
                            discovered_urls = page_content.get("urls", [])
                            
                            # Use LLM to analyze page and extract download links
                            action_plan = await webpage_reader.analyze_page(
                                url=url,
                                text_content=text_content,
                                discovered_urls=discovered_urls,
                                objective=task_objective,
                            )
                            
                            # Check if LLM found download links
                            if action_plan.get("action") == "download":
                                download_urls = action_plan.get("urls", [])
                                logger.info(f"[Web] LLM found {len(download_urls)} download links")
                                
                                # Verify and download each link
                                for download_url in download_urls:
                                    if download_success:
                                        break
                                    
                                    # Convert relative URLs to absolute
                                    full_url = urljoin(url, download_url)
                                    
                                    # Check if it's a valid download link
                                    logger.info(f"[Web] Verifying download link: {full_url}")
                                    check_result = await _check_if_download_link(full_url)
                                    
                                    if not check_result.get("is_download"):
                                        logger.info(f"[Web] Link verification failed: {check_result.get('reason', 'Unknown')}")
                                        continue
                                    
                                    # Try downloading
                                    try:
                                        logger.info(f"[Web] Attempting to download: {full_url}")
                                        saved_path = await _download_file_with_playwright(
                                            page, full_url, web_save_dir
                                        )
                                        if saved_path:
                                            download_success = True
                                            download_path = saved_path
                                            logger.info(f"[Web] Download successful: {saved_path}")
                                            break
                                    except Exception as e:
                                        logger.info(f"[Web] Download attempt failed: {e}")
                                        continue
                            
                            elif action_plan.get("action") == "navigate":
                                # LLM suggests navigating to another URL
                                navigate_url = action_plan.get("url")
                                if navigate_url:
                                    full_navigate_url = urljoin(url, navigate_url)
                                    if full_navigate_url not in visited_urls and full_navigate_url not in urls:
                                        urls.append(full_navigate_url)
                                        logger.info(f"[Web] LLM suggests navigating to: {full_navigate_url}")
                            
                            if download_success:
                                break
                                
                        except Exception as e:
                            logger.info(f"[Web] Error processing URL {url}: {e}")
                            continue
                
                await browser.close()
                
                if download_success:
                    return {
                        "success": True,
                        "download_path": download_path,
                    }
                else:
                    return {
                        "success": False,
                        "reason": "No download links found or all download attempts failed",
                    }
                    
            except Exception as e:
                await browser.close()
                raise e
                
    except Exception as e:
        logger.error(f"[Web] Download error: {e}", exc_info=True)
        return {
            "success": False,
            "reason": f"Error: {str(e)}",
        }


async def _check_if_download_link(url: str) -> Dict[str, Any]:
    """Check if URL is a download link"""
    import httpx
    
    result = {
        "is_download": False,
        "reason": "",
        "content_type": "",
        "filename": "",
    }
    
    url_lower = url.lower()
    # Prioritize data file extensions for dataset downloads
    data_file_extensions = [
        ".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz",
        ".csv", ".xlsx", ".xls", ".json", ".jsonl", ".xml", ".tsv",
        ".parquet", ".arrow", ".h5", ".hdf5", ".pkl", ".pickle",
        ".txt", ".md",  # Text files might be datasets
    ]
    
    # First check: file extension (fast, no network request)
    for ext in data_file_extensions:
        if url_lower.endswith(ext) or f"{ext}?" in url_lower or f"{ext}#" in url_lower:
            result["is_download"] = True
            result["reason"] = f"URL contains data file extension: {ext}"
            result["filename"] = url.split("/")[-1].split("?")[0].split("#")[0]
            return result
    
    # Skip HEAD request for common non-download URLs to save time
    skip_patterns = [
        "/issues", "/pull", "/wiki", "/discussions", "/projects",
        "/settings", "/actions", "/security", "/pulse", "/graphs",
        "/commits", "/blob", "/tree", "/releases/tag",
    ]
    for pattern in skip_patterns:
        if pattern in url_lower:
            result["reason"] = f"URL appears to be a GitHub page, not a download link"
            return result
    
    try:
        # Use shorter timeout to avoid hanging
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.head(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    )
                },
            )
    except asyncio.TimeoutError:
        result["reason"] = "HEAD request timeout"
        return result
    except Exception as exc:
        result["reason"] = f"HEAD request failed: {exc}"
        return result
    
    content_disposition = response.headers.get("Content-Disposition", "")
    if content_disposition and "attachment" in content_disposition.lower():
        result["is_download"] = True
        result["reason"] = "Content-Disposition contains attachment"
        if "filename=" in content_disposition:
            filename_match = re.search(
                r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', content_disposition
            )
            if filename_match:
                result["filename"] = filename_match.group(1).strip('\'"')
        return result
    
    content_type = response.headers.get("Content-Type", "").lower()
    result["content_type"] = content_type
    
    downloadable_types = [
        "application/octet-stream",
        "application/zip",
        "application/x-zip-compressed",
        "application/x-rar-compressed",
        "application/pdf",
        "text/csv",
        "application/json",
        "application/xml",
        "image/",
        "video/",
        "audio/",
    ]
    
    for dtype in downloadable_types:
        if dtype in content_type:
            result["is_download"] = True
            result["reason"] = f"Content-Type is downloadable: {content_type}"
            return result
    
    if "text/html" in content_type:
        result["is_download"] = False
        result["reason"] = "Content-Type is HTML page, not file download"
        return result
    
    result["reason"] = "Cannot determine if download link"
    return result


async def _download_file_with_playwright(
    page: "Page", url: str, save_dir: str
) -> Optional[str]:
    """Download file using Playwright"""
    import shutil
    
    logger.info(f"[Playwright] Preparing to download from {url}")
    os.makedirs(save_dir, exist_ok=True)
    
    download_page = await page.context.new_page()
    try:
        async with download_page.expect_download(timeout=12000) as download_info:
            try:
                await download_page.goto(url, timeout=60000)
            except PlaywrightError as exc:
                if "Download is starting" in str(exc) or "navigation" in str(exc):
                    logger.info("Download triggered via navigation/redirect")
                else:
                    raise exc
        download = await download_info.value
        try:
            await download_page.close()
        except Exception as close_exc:
            logger.info(f"Error closing download page (ignorable): {close_exc}")
        
        suggested_filename = download.suggested_filename
        save_path = os.path.join(save_dir, suggested_filename)
        logger.info(f"File '{suggested_filename}' is being saved...")
        temp_file_path = await download.path()
        if not temp_file_path:
            logger.info("[Playwright] Download failed, could not get temp file path")
            await download.delete()
            return None
        shutil.move(temp_file_path, save_path)
        logger.info(f"[Playwright] Download completed: {save_path}")
        return save_path
    except Exception as exc:
        logger.info(f"[Playwright] Unexpected error during download ({url}): {exc}")
        try:
            await download_page.close()
        except Exception:
            pass
        return None

