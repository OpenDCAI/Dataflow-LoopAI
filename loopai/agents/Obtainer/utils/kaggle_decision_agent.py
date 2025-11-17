"""
Kaggle decision agent
"""
import json
from typing import Dict, List, Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class KaggleDecisionAgent:
    """Kaggle dataset decision agent"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.7,
        prompt_loader: Optional[PromptLoader] = None,
    ):
        """Initialize Kaggle Decision Agent"""
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
        logger.info("\n--- Kaggle Decision Agent ---")

        if not search_results or all(not v for v in search_results.values()):
            logger.info("[Kaggle Decision] No search results, cannot decide")
            return None

        # Use prompt loader if available
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "kaggle_decision_prompt")
                task_prompt = self.prompt_loader("task", "kaggle_decision_prompt")
                human_prompt = task_prompt.format(
                    objective=objective,
                    message=message,
                    max_dataset_size=max_dataset_size if max_dataset_size else "None",
                    search_results=json.dumps(
                        search_results, indent=2, ensure_ascii=False, default=str
                    ),
                )
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(
                    objective, message, search_results, max_dataset_size
                )
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(
                objective, message, search_results, max_dataset_size
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]

        response = await self.llm.ainvoke(messages)
        logger.info(f"Kaggle decision raw response: {response.content}")

        try:
            clean_response = (
                response.content.strip().replace("```json", "").replace("```", "")
            )
            decision = json.loads(clean_response)
            selected_id = decision.get("selected_dataset_id")

            if selected_id:
                logger.info(
                    f"[Kaggle Decision] Selected: {selected_id}. "
                    f"Reason: {decision.get('reasoning', 'N/A')}"
                )
                return selected_id
            else:
                logger.info(
                    f"[Kaggle Decision] No suitable dataset. "
                    f"Reason: {decision.get('reasoning', 'N/A')}"
                )
                return None
        except Exception as e:
            logger.error(f"[Kaggle Decision] Parse error: {e}\nRaw: {response.content}")
            return None

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """You are a Kaggle dataset expert. Your task is to analyze a JSON search results list 
and select the most suitable dataset ID based on user objectives.

Decision criteria:
1. **Relevance**: Dataset title and description must be highly relevant to user objective
2. **Size limit**: If max_dataset_size is provided, must select dataset with size <= limit
3. **Downloadability**: Prefer datasets with high downloads and clear tags
4. **Popularity**: Among similar relevance, choose highest downloads

Output must be a JSON object:
{
    "selected_dataset_id": "owner/dataset-slug" or null,
    "reasoning": "Why you chose this ID, or why filtered due to size limit"
}"""

    def _get_default_task_prompt(
        self,
        objective: str,
        message: str,
        search_results: Dict[str, List[Dict]],
        max_dataset_size: Optional[int],
    ) -> str:
        """Get default task prompt"""
        return f"""User objective: "{objective}"
User message: "{message}"
Max dataset size limit: {max_dataset_size if max_dataset_size else "None"} bytes

Search results:
```json
{json.dumps(search_results, indent=2, ensure_ascii=False, default=str)}
```

Please select the best dataset ID according to the criteria. 
Note: If size limit is provided, ensure selected dataset size <= limit."""


