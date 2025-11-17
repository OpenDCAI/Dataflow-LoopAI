"""
Download method decision agent - decides the order of three download methods
"""
import json
from typing import Dict, Any, List

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class DownloadMethodDecisionAgent:
    """Download method decision agent - decides the priority order of three download methods"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.7,
        prompt_loader: PromptLoader = None,
    ):
        """
        Initialize Download Method Decision Agent
        
        Args:
            model_name: LLM model name
            base_url: LLM base URL
            api_key: LLM API key
            temperature: Temperature for LLM
            prompt_loader: Prompt loader instance
        """
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        self.prompt_loader = prompt_loader

    async def decide_method_order(
        self,
        user_original_request: str,
        current_task_objective: str,
        search_keywords: str,
    ) -> Dict[str, Any]:
        """
        Decide the priority order of three download methods
        
        Args:
            user_original_request: User's original request
            current_task_objective: Current task objective
            search_keywords: Search keywords
            
        Returns:
            Dictionary with method_order (list of method names in priority order),
            keywords_for_hf, and reasoning
        """
        logger.info("\n--- Download Method Decision Agent ---")
        
        # Use prompt loader if available
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "download_method_decision_prompt")
                task_prompt = self.prompt_loader("task", "download_method_decision_prompt")
                human_prompt = task_prompt.format(
                    user_original_request=user_original_request,
                    current_task_objective=current_task_objective,
                    keywords=search_keywords
                )
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(
                    user_original_request, current_task_objective, search_keywords
                )
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(
                user_original_request, current_task_objective, search_keywords
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]

        response = await self.llm.ainvoke(messages)
        logger.info(f"Download method decision raw response: {response.content}")

        try:
            clean_response = (
                response.content.strip()
                .replace("```json", "")
                .replace("```", "")
            )
            decision = json.loads(clean_response)
            
            # Validate method_order
            method_order = decision.get("method_order", [])
            valid_methods = ["huggingface", "kaggle", "web"]
            
            # Filter invalid methods and ensure all three are present
            filtered_order = [m for m in method_order if m in valid_methods]
            for method in valid_methods:
                if method not in filtered_order:
                    filtered_order.append(method)
            
            decision["method_order"] = filtered_order[:3]  # Ensure only 3 methods
            
            logger.info(
                f"Download method order decided: {decision['method_order']} - "
                f"{decision.get('reasoning', 'N/A')}"
            )
            return decision
        except Exception as e:
            logger.error(f"Error parsing download method decision: {e}\nRaw response: {response.content}")
            # Fallback: default order
            fallback_result = {
                "method_order": ["huggingface", "kaggle", "web"],
                "reasoning": "Parsing failed, using default order",
                "keywords_for_hf": [search_keywords] if isinstance(search_keywords, str) else search_keywords,
            }
            logger.info(f"Using fallback decision: {fallback_result}")
            return fallback_result

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """You are an intelligent download strategy decision maker. Your task is to decide the priority order 
of three download methods based on the user's requirements and task objective.

The three available methods are:
1. "huggingface" - Download datasets from HuggingFace Hub
2. "kaggle" - Download datasets from Kaggle
3. "web" - Download files directly from web pages using Playwright

You should analyze the task and decide which method is most likely to succeed first, second, and third.

Return a JSON object with:
- "method_order": A list of three method names in priority order, e.g. ["huggingface", "kaggle", "web"]
- "keywords_for_hf": A list of keywords for HuggingFace search (avoid generic terms like "datasets", "machine learning")
- "reasoning": Brief explanation of why this order was chosen"""

    def _get_default_task_prompt(
        self, user_original_request: str, current_task_objective: str, keywords: str
    ) -> str:
        """Get default task prompt"""
        return f"""User's original request: {user_original_request}
Current task objective: {current_task_objective}
Search keywords: {keywords}

Please analyze the task and decide the priority order of the three download methods.
Consider:
1. What is the user's overall goal (original request)
2. What is the specific objective of the current subtask
3. Which method is most likely to find and download the required data

Return a JSON object with method_order, keywords_for_hf, and reasoning."""


