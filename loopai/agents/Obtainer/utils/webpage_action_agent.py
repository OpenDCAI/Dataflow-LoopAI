from typing import Dict, Any, Optional, List
import json
import asyncio

from loopai.agents import BaseAgent
from loopai.common.prompts import PromptLoader
from loopai.logger import get_logger
from loopai.agents.Obtainer.utils.playwright_manager import PlaywrightBrowserManager
from loopai.agents.Obtainer.utils.playwright_tools import PlaywrightActionTools

logger = get_logger()


class WebPageActionAgent(BaseAgent):
    """
    WebPageActionAgent uses LLM with Playwright tools to explore web pages.
    The agent can select appropriate actions based on user objectives and current page state.
    """
    
    @property
    def role_name(self) -> str:
        return "WebPageActionAgent"
    
    @property
    def system_prompt_type(self) -> str:
        return "system"
    
    @property
    def system_prompt_name(self) -> str:
        return "webpage_action_agent_prompt"
    
    def __init__(
        self,
        browser_manager: PlaywrightBrowserManager,
        model_name: str = "gpt-4o",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        prompt_template_dir: Optional[str] = None,
    ):
        """
        Initialize WebPageActionAgent
        
        Args:
            browser_manager: PlaywrightBrowserManager instance
            model_name: LLM model name
            base_url: LLM base URL
            api_key: LLM API key
            temperature: LLM temperature
            prompt_template_dir: Prompt template directory
        """
        # Create Playwright action tools (MCP-based)
        playwright_tools = PlaywrightActionTools(browser_manager)
        # Tools will be initialized asynchronously, start with empty list
        tools = []
        
        super().__init__(
            tools=tools,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_template_dir=prompt_template_dir,
        )
        
        # Override prompt_loader to use obtainer_prompt.json
        self.prompt_loader = PromptLoader(prompt_template_dir)
        self.browser_manager = browser_manager
        self.playwright_tools = playwright_tools
        self._tools_initialized = False
    
    def init_graph(self, **kwargs):
        """
        WebPageActionAgent uses create_react_agent directly via llm_node.
        This is a placeholder to satisfy BaseAgent's abstract method requirement.
        """
        pass
    
    def __call__(self, **kwargs):
        """
        WebPageActionAgent uses llm_node directly for agent execution.
        This is a placeholder to satisfy BaseAgent's abstract method requirement.
        """
        return self.llm_node
    
    async def execute_action(
        self,
        user_objective: str,
        current_page_info: Optional[Dict[str, Any]] = None,
        max_iterations: int = 10,
    ) -> Dict[str, Any]:
        """
        Execute agent actions to explore the page
        
        Args:
            user_objective: User's objective for page exploration
            current_page_info: Current page information (URL, title, etc.)
            max_iterations: Maximum number of agent iterations
        
        Returns:
            Result dictionary with exploration results
        """
        logger.info(f"[WebPageActionAgent] Starting exploration with objective: {user_objective}")
        
        # Initialize tools if needed
        if not self._tools_initialized:
            tools = await self.playwright_tools.get_tools()
            self.tools = tools
            tool_names = [tool.name for tool in tools]
            logger.info(f"[WebPageActionAgent] Initialized {len(tools)} MCP tools: {', '.join(tool_names)}")
            self.create_llm_node()
            self._tools_initialized = True
            logger.info(f"[WebPageActionAgent] LLM node created with {len(tools)} tools")
        
        # IMPORTANT: playwright-mcp manages its own browser instance
        # If we have a URL from current_page_info, navigate to it using browser_navigate
        browser_navigate_tool = None
        browser_snapshot_tool = None
        
        for tool in self.tools:
            if tool.name == "browser_navigate":
                browser_navigate_tool = tool
            elif tool.name == "browser_snapshot":
                browser_snapshot_tool = tool
        
        # Navigate to URL if provided (playwright-mcp's browser instance)
        if current_page_info and current_page_info.get('url') and browser_navigate_tool:
            target_url = current_page_info.get('url')
            logger.info(f"[WebPageActionAgent] Navigating playwright-mcp browser to: {target_url}")
            try:
                nav_result = await asyncio.wait_for(
                    browser_navigate_tool.ainvoke({"url": target_url}),
                    timeout=60.0
                )
                logger.info(f"[WebPageActionAgent] Navigation completed")
                # Wait a bit for page to load
                await asyncio.sleep(2)
            except asyncio.TimeoutError:
                logger.warning(f"[WebPageActionAgent] Navigation to {target_url} timed out")
            except Exception as e:
                logger.warning(f"[WebPageActionAgent] Navigation failed: {e}")
        
        # Automatically get browser_snapshot at the start of each action
        # This gives the agent visibility into the current page state
        page_snapshot = None
        try:
            # Find and call browser_snapshot tool automatically
            
            if browser_snapshot_tool:
                logger.info("[WebPageActionAgent] Automatically calling browser_snapshot to get current page state")
                logger.debug(f"[WebPageActionAgent] browser_snapshot tool type: {type(browser_snapshot_tool)}")
                logger.debug(f"[WebPageActionAgent] browser_snapshot tool name: {browser_snapshot_tool.name}")
                try:
                    import time
                    start_time = time.time()
                    result = await asyncio.wait_for(
                        browser_snapshot_tool.ainvoke({}),
                        timeout=30.0
                    )
                    elapsed = time.time() - start_time
                    logger.info(f"[WebPageActionAgent] browser_snapshot completed in {elapsed:.2f}s")
                    # Parse result
                    if isinstance(result, str):
                        try:
                            result_dict = json.loads(result)
                            if result_dict.get("success") and "result" in result_dict:
                                page_snapshot = result_dict["result"]
                            else:
                                page_snapshot = result
                        except json.JSONDecodeError:
                            page_snapshot = result
                    else:
                        page_snapshot = str(result)
                    logger.info(f"[WebPageActionAgent] Got page snapshot ({len(page_snapshot)} chars)")
                except asyncio.TimeoutError:
                    logger.warning("[WebPageActionAgent] browser_snapshot timed out, using fallback")
                    page_snapshot = None
                except Exception as e:
                    logger.warning(f"[WebPageActionAgent] Failed to get browser_snapshot: {e}, using fallback")
                    page_snapshot = None
            else:
                logger.warning("[WebPageActionAgent] browser_snapshot tool not found")
        except Exception as e:
            logger.warning(f"[WebPageActionAgent] Error getting browser_snapshot: {e}")
        
        # Prepare initial messages
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Get system prompt
        try:
            system_prompt = self.prompt_loader("system", "webpage_action_agent_prompt")
        except Exception as e:
            logger.warning(f"Failed to load system prompt, using default: {e}")
            system_prompt = "You are a web page exploration agent. Use the available tools to explore web pages and find relevant information."
        
        # Build initial message with page info and snapshot
        initial_message_parts = [f"User Objective: {user_objective}"]
        
        if current_page_info:
            initial_message_parts.append(f"\nCurrent Page Information:")
            initial_message_parts.append(f"- URL: {current_page_info.get('url', 'N/A')}")
            initial_message_parts.append(f"- Title: {current_page_info.get('title', 'N/A')}")
        
        # Include page snapshot if available
        if page_snapshot:
            # Limit snapshot length to avoid token limits
            snapshot_preview = page_snapshot[:8000] if len(page_snapshot) > 8000 else page_snapshot
            initial_message_parts.append(f"\nCurrent Page Snapshot (accessibility tree):")
            initial_message_parts.append(f"```\n{snapshot_preview}\n```")
            if len(page_snapshot) > 8000:
                initial_message_parts.append(f"\n(Note: Snapshot truncated, showing first 8000 chars of {len(page_snapshot)} total)")
        
        initial_message_parts.append(
            "\nBased on the page snapshot above, use the available tools (browser_click, browser_navigate, browser_fill_form, browser_press_key) to explore the page and find information related to the user's objective. "
            "The snapshot shows you the current page structure - use it to identify clickable elements, links, buttons, and navigation options. "
            "When you find a resource list page (a page with multiple resource links), indicate that you've found it."
        )
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="\n".join(initial_message_parts)),
        ]
        
        # Execute agent with max iterations
        # Ensure recursion_limit is at least max_iterations + some buffer
        # LangGraph default is 5, so we need to set it explicitly
        recursion_limit = max(max_iterations + 2, 30)  # At least 10, or max_iterations + 2
        
        # Calculate timeout: max_iterations * 30 seconds per iteration + buffer
        timeout_seconds = max(max_iterations * 30, 300)  # At least 5 minutes
        
        try:
            logger.info(f"[WebPageActionAgent] Starting agent execution (max_iterations={max_iterations}, timeout={timeout_seconds}s)")
            
            # Add timeout protection to prevent hanging
            result = await asyncio.wait_for(
                self.llm_node.ainvoke(
                    {"messages": messages},
                    config={"recursion_limit": recursion_limit}
                ),
                timeout=timeout_seconds
            )
            
            # Extract final message
            final_message = result.get("messages", [])[-1] if result.get("messages") else None
            final_content = ""
            if final_message:
                if hasattr(final_message, "content"):
                    final_content = final_message.content
                elif isinstance(final_message, dict):
                    final_content = final_message.get("content", "")
            
            # Log tool usage for debugging
            messages_list = result.get("messages", [])
            tool_calls_count = 0
            tool_names_used = []
            
            for msg in messages_list:
                # Check for tool calls in AIMessage
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    tool_calls_count += len(msg.tool_calls)
                    for tool_call in msg.tool_calls:
                        tool_name = tool_call.get('name', 'unknown') if isinstance(tool_call, dict) else getattr(tool_call, 'name', 'unknown')
                        tool_names_used.append(tool_name)
                        logger.info(f"[WebPageActionAgent] Tool called: {tool_name}")
                # Check for ToolMessage (tool results)
                if hasattr(msg, 'name') and msg.name == 'tool':
                    tool_name = getattr(msg, 'tool', 'unknown')
                    logger.debug(f"[WebPageActionAgent] Tool result received from: {tool_name}")
            
            logger.info(f"[WebPageActionAgent] Exploration completed. Final message length: {len(final_content)}")
            logger.info(f"[WebPageActionAgent] Tool usage: {tool_calls_count} tool calls made. Tools used: {', '.join(set(tool_names_used)) if tool_names_used else 'None'}")
            
            if tool_calls_count == 0:
                logger.warning("[WebPageActionAgent] WARNING: No tools were called during exploration! Agent may not be using tools correctly.")
            
            return {
                "success": True,
                "final_message": final_content,
                "messages": messages_list,
                "tool_calls_count": tool_calls_count,
                "tools_used": list(set(tool_names_used)),
                "page_info": await self.browser_manager.get_page_info(),
            }
            
        except asyncio.TimeoutError:
            logger.error(f"[WebPageActionAgent] Exploration timed out after {timeout_seconds}s")
            return {
                "success": False,
                "error": f"Exploration timed out after {timeout_seconds} seconds",
                "timeout": True,
                "page_info": await self.browser_manager.get_page_info(),
            }
        except Exception as e:
            logger.error(f"[WebPageActionAgent] Error during exploration: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "page_info": await self.browser_manager.get_page_info(),
            }
    
    async def check_if_resource_list_page(
        self,
        page_content: Optional[str] = None,
        user_objective: str = "",
    ) -> Dict[str, Any]:
        """
        Check if current page is a resource list page using LLM
        
        Uses browser_snapshot (accessibility snapshot) from MCP tools for better semantic understanding.
        
        Args:
            page_content: Page content (browser_snapshot format or text)
            user_objective: User's objective
        
        Returns:
            Dictionary with is_resource_list flag and reasoning
        """
        logger.info("[WebPageActionAgent] Checking if current page is a resource list page")
        
        try:
            # Get page info if not provided
            if not page_content:
                page_info = await self.browser_manager.get_page_info()
                # Try to use browser_snapshot from MCP tools first
                try:
                    # Initialize tools if needed
                    if not self._tools_initialized:
                        tools = await self.playwright_tools.get_tools()
                        self.tools = tools
                        self.create_llm_node()
                        self._tools_initialized = True
                    
                    # Find browser_snapshot tool
                    browser_snapshot_tool = None
                    for tool in self.tools:
                        if tool.name == "browser_snapshot":
                            browser_snapshot_tool = tool
                            break
                    
                    if browser_snapshot_tool:
                        # Use browser_snapshot tool with timeout
                        logger.info("[WebPageActionAgent] Using browser_snapshot MCP tool")
                        try:
                            result = await asyncio.wait_for(
                                browser_snapshot_tool.ainvoke({}),
                                timeout=30.0  # 30 second timeout for snapshot
                            )
                            # Parse result
                            if isinstance(result, str):
                                try:
                                    result_dict = json.loads(result)
                                    if result_dict.get("success") and "result" in result_dict:
                                        page_content = result_dict["result"]
                                    else:
                                        raise ValueError("browser_snapshot returned unsuccessful result")
                                except json.JSONDecodeError:
                                    page_content = result
                            else:
                                page_content = str(result)
                        except asyncio.TimeoutError:
                            logger.warning("[WebPageActionAgent] browser_snapshot timed out, using fallback")
                            raise  # Will be caught by outer try-except
                    else:
                        # Fallback to aria_snapshot
                        logger.warning("[WebPageActionAgent] browser_snapshot tool not found, using aria_snapshot")
                        page_content = await self.browser_manager.get_aria_snapshot()
                        if not page_content:
                            # Fallback to innerText
                            page = await self.browser_manager.get_page()
                            page_content = await page.evaluate("() => document.body.innerText")
                except Exception as e:
                    logger.warning(f"[WebPageActionAgent] Failed to get browser_snapshot: {e}, using fallback")
                    try:
                        # Fallback to aria_snapshot
                        page_content = await self.browser_manager.get_aria_snapshot()
                        if not page_content:
                            # Fallback to innerText
                            page = await self.browser_manager.get_page()
                            page_content = await page.evaluate("() => document.body.innerText")
                    except Exception:
                        page_info = await self.browser_manager.get_page_info()
                        page_content = f"URL: {page_info.get('url', '')}, Title: {page_info.get('title', '')}"
            
            # Limit content length (browser_snapshot is compact, so we can use more)
            max_length = 15000  # browser_snapshot is very compact, allow more content
            if len(page_content) > max_length:
                page_content = page_content[:max_length] + "..."
            
            # Use LLM to determine if this is a resource list page
            from langchain_core.messages import SystemMessage, HumanMessage
            
            system_prompt = (
                "You are a web page analyzer. Determine if a web page is a resource list page. "
                "You will receive the page content in browser_snapshot format (accessibility snapshot), "
                "which represents the semantic structure of the page, ignoring decorative elements.\n\n"
                "A resource list page typically contains:\n"
                "- Multiple links to resources (datasets, articles, files, etc.)\n"
                "- List or grid layout with multiple items\n"
                "- Pagination or 'load more' functionality\n"
                "- Search/filter functionality for resources\n\n"
                "The browser_snapshot format shows:\n"
                "- Role-based semantic structure (e.g., 'list', 'listitem', 'link', 'button')\n"
                "- Text content and labels\n"
                "- Hierarchical structure in a compact format\n\n"
                "Respond with a JSON object containing:\n"
                "- 'is_resource_list': boolean\n"
                "- 'reasoning': string explaining your decision\n"
                "- 'confidence': float between 0 and 1\n"
            )
            
            task_prompt = (
                f"User Objective: {user_objective}\n\n"
                f"Page Content (browser_snapshot format, first {len(page_content)} chars):\n"
                f"```\n{page_content}\n```\n\n"
                "Is this a resource list page? Respond with JSON only."
            )
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=task_prompt),
            ]
            
            response = await self.llm.ainvoke(messages)
            response_content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse JSON response
            clean_response = response_content.strip().replace("```json", "").replace("```", "")
            result = json.loads(clean_response)
            
            logger.info(
                f"[WebPageActionAgent] Resource list check: {result.get('is_resource_list', False)} "
                f"(confidence: {result.get('confidence', 0)})"
            )
            
            return {
                "is_resource_list": bool(result.get("is_resource_list", False)),
                "reasoning": result.get("reasoning", ""),
                "confidence": float(result.get("confidence", 0)),
            }
            
        except json.JSONDecodeError as e:
            logger.error(f"[WebPageActionAgent] Failed to parse LLM response: {e}")
            logger.debug(f"[WebPageActionAgent] Raw response: {response_content}")
            return {
                "is_resource_list": False,
                "reasoning": "Failed to parse LLM response",
                "confidence": 0.0,
            }
        except Exception as e:
            logger.error(f"[WebPageActionAgent] Error checking resource list page: {e}", exc_info=True)
            return {
                "is_resource_list": False,
                "reasoning": f"Error: {str(e)}",
                "confidence": 0.0,
            }

