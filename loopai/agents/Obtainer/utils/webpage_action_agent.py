from typing import Dict, Any, Optional, List
import json

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
        # Create Playwright action tools
        playwright_tools = PlaywrightActionTools(browser_manager)
        tools = playwright_tools.tools
        
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
        
        # Prepare initial messages
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Get system prompt
        try:
            system_prompt = self.prompt_loader("system", "webpage_action_agent_prompt")
        except Exception as e:
            logger.warning(f"Failed to load system prompt, using default: {e}")
            system_prompt = "You are a web page exploration agent. Use the available tools to explore web pages and find relevant information."
        
        # Build initial message with page info
        initial_message_parts = [f"User Objective: {user_objective}"]
        
        if current_page_info:
            initial_message_parts.append(f"\nCurrent Page Information:")
            initial_message_parts.append(f"- URL: {current_page_info.get('url', 'N/A')}")
            initial_message_parts.append(f"- Title: {current_page_info.get('title', 'N/A')}")
        
        initial_message_parts.append(
            "\nPlease use the available tools to explore the page and find information related to the user's objective. "
            "You can search, click links, scroll, navigate, etc. "
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
        try:
            result = await self.llm_node.ainvoke(
                {"messages": messages},
                config={"recursion_limit": recursion_limit}
            )
            
            # Extract final message
            final_message = result.get("messages", [])[-1] if result.get("messages") else None
            final_content = ""
            if final_message:
                if hasattr(final_message, "content"):
                    final_content = final_message.content
                elif isinstance(final_message, dict):
                    final_content = final_message.get("content", "")
            
            logger.info(f"[WebPageActionAgent] Exploration completed. Final message length: {len(final_content)}")
            
            return {
                "success": True,
                "final_message": final_content,
                "messages": result.get("messages", []),
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
        
        Args:
            page_content: Page content (HTML or text)
            user_objective: User's objective
        
        Returns:
            Dictionary with is_resource_list flag and reasoning
        """
        logger.info("[WebPageActionAgent] Checking if current page is a resource list page")
        
        try:
            # Get page info if not provided
            if not page_content:
                page_info = await self.browser_manager.get_page_info()
                # Try to get page text content
                try:
                    page = await self.browser_manager.get_page()
                    page_content = await page.evaluate("() => document.body.innerText")
                except Exception:
                    page_content = f"URL: {page_info.get('url', '')}, Title: {page_info.get('title', '')}"
            
            # Limit content length
            if len(page_content) > 8000:
                page_content = page_content[:8000] + "..."
            
            # Use LLM to determine if this is a resource list page
            from langchain_core.messages import SystemMessage, HumanMessage
            
            system_prompt = (
                "You are a web page analyzer. Determine if a web page is a resource list page. "
                "A resource list page typically contains:\n"
                "- Multiple links to resources (datasets, articles, files, etc.)\n"
                "- List or grid layout with multiple items\n"
                "- Pagination or 'load more' functionality\n"
                "- Search/filter functionality for resources\n"
                "\n"
                "Respond with a JSON object containing:\n"
                "- 'is_resource_list': boolean\n"
                "- 'reasoning': string explaining your decision\n"
                "- 'confidence': float between 0 and 1\n"
            )
            
            task_prompt = (
                f"User Objective: {user_objective}\n\n"
                f"Page Content (first 8000 chars):\n{page_content}\n\n"
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

