import json
import asyncio
from typing import Dict, Any, List, Optional

from loopai.agents import BaseAgent
from loopai.common.prompts import PromptLoader
from loopai.logger import get_logger

logger = get_logger()


class WebPageReader(BaseAgent):
    """
    WebPageReader uses LLM to analyze web page content and extract download links.
    Similar to the old implementation, it feeds the webpage text and discovered URLs
    to the model, which returns a JSON with download links.
    """
    
    @property
    def role_name(self) -> str:
        return "WebPageReader"
    
    @property
    def system_prompt_type(self) -> str:
        return "system"
    
    @property
    def system_prompt_name(self) -> str:
        return "webpage_reader_prompt"
    
    def __init__(
        self,
        model_name: str = "gpt-4o",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.7,
    ):
        super().__init__(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_template_dir=None,  # Will use default
        )
        # Override prompt_loader to use obtainer_prompt.json
        self.prompt_loader = PromptLoader()
    
    def init_graph(self, **kwargs):
        """
        WebPageReader is not a full agent, so we don't need to initialize a graph.
        This is just a placeholder to satisfy BaseAgent's abstract method requirement.
        """
        pass
    
    def __call__(self, **kwargs):
        """
        WebPageReader is not a full agent, so we don't need to return a graph.
        This is just a placeholder to satisfy BaseAgent's abstract method requirement.
        """
        return None
    
    async def analyze_page(
        self,
        url: str,
        text_content: str,
        discovered_urls: List[str],
        objective: str,
    ) -> Dict[str, Any]:
        """Analyze a web page using LLM to extract download links"""
        logger.info(f"[WebPageReader] Analyzing page: {url}")
        
        # Prepare prompts
        system_prompt = self.prompt_loader.get_prompt(
            "obtainer_prompt",
            "webpage_reader_prompt",
            prompt_type="system"
        ) or "You are a web analysis agent that extracts download links from web pages."
        
        # Limit text content to avoid token limits
        compact_text = text_content[:16000]
        
        # Limit discovered URLs
        urls_block = "\n".join(discovered_urls[:100])
        
        task_prompt = self.prompt_loader.get_prompt(
            "obtainer_prompt",
            "webpage_reader_prompt",
            prompt_type="task",
            objective=objective,
            urls_block=urls_block,
            text_content=compact_text,
        )
        
        # Create messages
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_prompt),
        ]
        
        # Invoke LLM
        try:
            response = await self.llm.ainvoke(messages)
            response_content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse JSON response
            clean_response = response_content.strip().replace("```json", "").replace("```", "")
            action_plan = json.loads(clean_response)
            
            # Ensure discovered_urls is included
            action_plan["discovered_urls"] = discovered_urls[:100]
            
            # Ensure is_relevant is boolean
            is_relevant = bool(action_plan.get("is_relevant", True))
            action_plan["is_relevant"] = is_relevant
            
            logger.info(
                f"[WebPageReader] Analysis complete. Action: {action_plan.get('action')}, "
                f"Description: {action_plan.get('description', 'N/A')}"
            )
            
            if action_plan.get("action") == "download":
                urls_count = len(action_plan.get("urls", []))
                logger.info(f"[WebPageReader] Found {urls_count} download links")
            
            return action_plan
            
        except json.JSONDecodeError as e:
            logger.error(f"[WebPageReader] Failed to parse LLM response: {e}")
            logger.debug(f"[WebPageReader] Raw response: {response_content}")
            return {
                "action": "dead_end",
                "description": "Failed to parse LLM response",
                "is_relevant": False,
                "discovered_urls": discovered_urls[:100],
            }
        except Exception as e:
            logger.error(f"[WebPageReader] Error analyzing page: {e}", exc_info=True)
            return {
                "action": "dead_end",
                "description": f"Error: {str(e)}",
                "is_relevant": False,
                "discovered_urls": discovered_urls[:100],
            }

