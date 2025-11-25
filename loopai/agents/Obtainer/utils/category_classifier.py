import json
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class CategoryClassifier:
    """Category Classifier for determining SFT or PT task type from user query"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.3,  # Lower temperature for more consistent classification
        prompt_loader: Optional[PromptLoader] = None,
    ):
        """Initialize Category Classifier"""
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        self.prompt_loader = prompt_loader

    async def classify_category(
        self, user_query: str, objective: str = ""
    ) -> str:
        """
        Classify the task category as SFT or PT based on user query
        
        Args:
            user_query: The user's query or message
            objective: Optional objective description
            
        Returns:
            "SFT" or "PT"
        """
        logger.info("\n--- Category Classifier ---")
        
        # Use prompt loader if available, otherwise use default prompt
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "category_classifier_prompt")
                task_prompt = self.prompt_loader("task", "category_classifier_prompt")
                human_prompt = task_prompt.format(
                    user_query=user_query,
                    objective=objective if objective else user_query
                )
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(user_query, objective)
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(user_query, objective)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        try:
            response = await self.llm.ainvoke(messages)
            logger.info(f"Category classifier raw response: {response.content}")

            # Parse response
            clean_response = (
                response.content.strip().replace("```json", "").replace("```", "").strip()
            )
            
            # Try to parse as JSON first
            try:
                result = json.loads(clean_response)
                if isinstance(result, dict):
                    category = result.get("category", "").upper()
                elif isinstance(result, str):
                    category = result.upper()
                else:
                    category = clean_response.upper()
            except json.JSONDecodeError:
                # If not JSON, try to extract category from text
                category = clean_response.upper()
            
            # Validate and return
            if category in ["SFT", "PT"]:
                logger.info(f"Classified category: {category}")
                return category
            else:
                logger.warning(f"Invalid category '{category}', defaulting to PT")
                return "PT"
                
        except Exception as e:
            logger.error(f"Error in category classification: {e}")
            logger.info("Defaulting to PT category due to classification error")
            return "PT"

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """You are a task category classification expert. Your task is to analyze user queries and determine whether they are requesting data for:

1. **SFT (Supervised Fine-Tuning)**: Tasks that require question-answer pairs, instruction-following data, conversational data, or any structured input-output pairs for fine-tuning language models to follow instructions.

2. **PT (Pre-training)**: Tasks that require raw text data, documents, code, or any continuous text corpus for pre-training language models from scratch or continuing pre-training.

Key indicators for SFT:
- Mentions of "question", "answer", "QA", "instruction", "conversation", "dialogue", "chat", "fine-tuning", "SFT", "微调", "问答"
- Requests for structured data with input-output pairs
- Tasks involving teaching models to follow instructions

Key indicators for PT:
- Mentions of "pre-training", "PT", "corpus", "text data", "documents", "code dataset"
- Requests for raw, unstructured text data
- Tasks involving building foundational language understanding

Return a JSON object with:
{
    "category": "SFT" or "PT",
    "reasoning": "Brief explanation of why this category was chosen"
}

Or simply return "SFT" or "PT" as a string."""

    def _get_default_task_prompt(self, user_query: str, objective: str) -> str:
        """Get default task prompt"""
        query_text = objective if objective else user_query
        return f"""User query: {user_query}

Research objective: {query_text}

Please analyze the user's query and objective to determine if they need:
- SFT data (question-answer pairs, instruction-following data)
- PT data (raw text corpus, documents, code)

Return a JSON object with "category" and "reasoning", or simply "SFT" or "PT"."""


