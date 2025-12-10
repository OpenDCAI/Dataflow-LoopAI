import json
from typing import List, Optional, Dict, Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class URLSelector:
    """URL Selector for selecting top-k most relevant URLs from webpage content"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.3,
        prompt_loader: Optional[PromptLoader] = None,
    ):
        """Initialize URL Selector"""
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        self.prompt_loader = prompt_loader

    async def select_top_urls(
        self,
        research_objective: str,
        url_list: List[str],
        webpage_content: str,
        topk: int = 5,
    ) -> List[str]:
        """Select top-k most relevant URLs based on research objective
        
        Args:
            research_objective: Current research objective/subtask goal
            url_list: List of URLs found in the webpage
            webpage_content: Webpage content text (will be truncated to 8000 chars)
            topk: Number of top URLs to return
            
        Returns:
            List of top-k most relevant URLs
        """
        logger.info(f"\n--- URL Selector: Selecting top {topk} URLs from {len(url_list)} candidates ---")
        
        # Truncate webpage content to 8000 characters
        truncated_content = webpage_content[:8000]
        if len(webpage_content) > 8000:
            logger.info(f"Webpage content truncated from {len(webpage_content)} to 8000 characters")
        
        # Format URL list for prompt
        url_list_str = "\n".join([f"{i+1}. {url}" for i, url in enumerate(url_list)])
        
        # Use prompt loader if available, otherwise use default prompt
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "url_selector_prompt")
                task_prompt = self.prompt_loader("task", "url_selector_prompt")
                human_prompt = task_prompt.format(
                    research_objective=research_objective,
                    url_list=url_list_str,
                    webpage_content=truncated_content,
                    topk=topk
                )
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(
                    research_objective, url_list_str, truncated_content, topk
                )
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(
                research_objective, url_list_str, truncated_content, topk
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        try:
            response = await self.llm.ainvoke(messages)
            logger.info(f"URL selector raw response: {response.content}")

            clean_response = (
                response.content.strip().replace("```json", "").replace("```", "")
            )
            result = json.loads(clean_response)
            
            # Handle different response formats
            if isinstance(result, dict) and "urls" in result:
                selected_urls = result["urls"]
            elif isinstance(result, list):
                selected_urls = result
            else:
                logger.warning("URL selector response format error, returning empty list")
                return []
            
            # Validate URLs are in the original list
            valid_urls = [url for url in selected_urls if url in url_list]
            
            # Limit to topk
            selected_urls = valid_urls[:topk]
            
            logger.info(f"Selected {len(selected_urls)} URLs: {selected_urls}")
            return selected_urls
            
        except Exception as e:
            logger.error(f"Error in URL selection: {e}\nRaw response: {response.content if 'response' in locals() else 'N/A'}")
            # Fallback: return first topk URLs if LLM fails
            logger.info(f"Falling back to first {topk} URLs")
            return url_list[:topk]

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """你是一个URL选择专家。你的任务是根据研究目标，从网页内容中的URL列表中选择最有可能包含相关信息的URL。
你需要分析网页正文内容和URL列表，选择与研究目标最相关的URL。
返回格式为JSON，包含一个urls数组，例如: {"urls": ["url1", "url2", "url3"]}"""

    def _get_default_task_prompt(self, research_objective: str, url_list: str, webpage_content: str, topk: int) -> str:
        """Get default task prompt"""
        return f"""研究目标: {research_objective}

网页正文内容（前8000字符）:
{webpage_content}

URL列表:
{url_list}

请从上述URL列表中选择最多{topk}个最有可能包含与研究目标相关信息的URL。
考虑URL的域名、路径以及网页正文中对这些链接的描述。
返回JSON格式，包含一个urls数组，例如: {{"urls": ["url1", "url2", "url3"]}}"""

