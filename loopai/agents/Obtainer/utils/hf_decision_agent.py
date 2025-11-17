"""
HuggingFace decision agent
"""
import json
from typing import Dict, List, Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class HuggingFaceDecisionAgent:
    """HuggingFace dataset decision agent"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.7,
        prompt_loader: Optional[PromptLoader] = None,
    ):
        """Initialize HuggingFace Decision Agent"""
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        self.prompt_loader = prompt_loader

    async def execute(
        self,
        search_results: Dict[str, List[Dict]],
        objective: str,
        message: str = "",
        max_dataset_size: Optional[int] = None,
    ) -> Optional[str]:
        """Execute decision to select best dataset"""
        logger.info("\n--- HuggingFace Decision Agent ---")

        if not search_results or all(not v for v in search_results.values()):
            logger.info("[HuggingFace Decision] No search results, cannot decide")
            return None

        # Use prompt loader if available
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "huggingface_decision_prompt")
                task_prompt = self.prompt_loader("task", "huggingface_decision_prompt")
                human_prompt = task_prompt.format(
                    objective=objective,
                    message=message,
                    search_results=json.dumps(search_results, indent=2, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(
                    objective, message, search_results
                )
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(
                objective, message, search_results
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]

        response = await self.llm.ainvoke(messages)
        logger.info(f"HuggingFace decision raw response: {response.content}")

        try:
            clean_response = (
                response.content.strip().replace("```json", "").replace("```", "")
            )
            decision = json.loads(clean_response)
            selected_id = decision.get("selected_dataset_id")

            if selected_id:
                logger.info(
                    f"[HuggingFace Decision] Selected: {selected_id}. "
                    f"Reason: {decision.get('reasoning', 'N/A')}"
                )
                return selected_id
            else:
                logger.info(
                    f"[HuggingFace Decision] No suitable dataset. "
                    f"Reason: {decision.get('reasoning', 'N/A')}"
                )
                return None
        except Exception as e:
            logger.error(f"[HuggingFace Decision] Parse error: {e}\nRaw: {response.content}")
            return None

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """You are a HuggingFace dataset expert. Your task is to analyze a JSON search results list 
and select the most suitable dataset ID based on user objectives.

Decision criteria:
1. **Relevance**: Dataset title and description must be highly relevant to user objective
2. **Downloadability**: Prefer datasets with high downloads and clear tags
3. **Popularity**: Among similar relevance, choose highest downloads

Output must be a JSON object:
{
    "selected_dataset_id": "best/dataset-id" or null,
    "reasoning": "Why you chose this ID"
}"""

    def _get_default_task_prompt(
        self, objective: str, message: str, search_results: Dict[str, List[Dict]]
    ) -> str:
        """Get default task prompt"""
        return f"""User objective: "{objective}"
User message: "{message}"

Search results:
```json
{json.dumps(search_results, indent=2, ensure_ascii=False)}
```

Please select the best dataset ID according to the criteria."""


