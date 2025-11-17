"""
Query Generator for generating search queries from user requirements
"""
import json
from typing import List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class QueryGenerator:
    """Query Generator for generating diverse search queries"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.7,
        prompt_loader: Optional[PromptLoader] = None,
    ):
        """
        Initialize Query Generator
        
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

    async def generate_queries(
        self, objective: str, message: str = ""
    ) -> List[str]:
        """
        Generate search queries from user requirements
        
        Args:
            objective: Research objective
            message: User message
            
        Returns:
            List of search queries
        """
        logger.info("\n--- Query Generator ---")
        
        # Use prompt loader if available, otherwise use default prompt
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "query_generator_prompt")
                task_prompt = self.prompt_loader("task", "query_generator_prompt")
                human_prompt = task_prompt.format(objective=objective, message=message)
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(objective, message)
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(objective, message)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        response = await self.llm.ainvoke(messages)
        logger.info(f"Query generator raw response: {response.content}")

        try:
            clean_response = (
                response.content.strip().replace("```json", "").replace("```", "")
            )
            queries = json.loads(clean_response)
            if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
                logger.info(f"Generated {len(queries)} search queries: {queries}")
                return queries
            logger.info("Query generation format error, returning empty list")
            return []
        except Exception as e:
            logger.info(f"Error parsing query generation response: {e}\nRaw response: {response.content}")
            return []

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """You are a query generation expert. Your task is to generate diverse search queries 
based on user requirements. Generate 3-5 search queries that cover different aspects of the research objective.
Return only a JSON array of query strings, for example: ["query1", "query2", "query3"]."""

    def _get_default_task_prompt(self, objective: str, message: str) -> str:
        """Get default task prompt"""
        return f"""Research objective: {objective}

User message: {message}

Please generate 3-5 diverse search queries that will help gather comprehensive information 
about the research objective. Return only a JSON array of query strings."""


