import json
from typing import Dict, List, Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class SummaryAgent:
    """Summary Agent for generating download subtasks"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.7,
        prompt_loader: Optional[PromptLoader] = None,
        max_download_subtasks: Optional[int] = None,
    ):
        """Initialize Summary Agent"""
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        self.prompt_loader = prompt_loader
        self.max_download_subtasks = max_download_subtasks

    async def generate_subtasks(
        self,
        objective: str,
        context: str,
        existing_subtasks: Optional[List[Dict[str, Any]]] = None,
        message: str = "",
    ) -> Dict[str, Any]:
        """Generate download subtasks from research context"""
        logger.info("\n--- Summary Agent: Generating download subtasks ---")

        existing_subtasks_str = (
            json.dumps(existing_subtasks, indent=2, ensure_ascii=False)
            if existing_subtasks
            else "[]"
        )

        # Use prompt loader if available
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "summary_agent_prompt")
                task_prompt = self.prompt_loader("task", "summary_agent_prompt")
                human_prompt = task_prompt.format(
                    objective=objective,
                    message=message,
                    existing_subtasks=existing_subtasks_str,
                    context=context,
                )
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(
                    objective, message, existing_subtasks_str, context
                )
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(
                objective, message, existing_subtasks_str, context
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]

        response = await self.llm.ainvoke(messages)
        logger.info(f"Summary agent raw response: {response.content}")

        try:
            clean_response = (
                response.content.strip()
                .replace("```json", "")
                .replace("```", "")
            )
            summary_plan = json.loads(clean_response)

            new_tasks = summary_plan.get("new_sub_tasks", [])
            summary_text = summary_plan.get("summary", "")

            # Apply download limit if specified
            if self.max_download_subtasks is not None:
                download_tasks = [t for t in new_tasks if t.get("type") == "download"]
                if len(download_tasks) > self.max_download_subtasks:
                    logger.info(
                        f"[Summary] Applying download limit: {len(download_tasks)} -> {self.max_download_subtasks}"
                    )
                    # Keep first max_download_subtasks download tasks
                    kept_downloads = 0
                    filtered_tasks = []
                    for task in new_tasks:
                        if task.get("type") == "download":
                            if kept_downloads >= self.max_download_subtasks:
                                continue
                            kept_downloads += 1
                        filtered_tasks.append(task)
                    new_tasks = filtered_tasks

            logger.info(f"[Summary] Generated {len(new_tasks)} new subtasks")
            if summary_text:
                logger.info(f"[Summary] Summary: {summary_text[:200]}...")

            return {
                "new_sub_tasks": new_tasks,
                "summary": summary_text,
            }
        except Exception as e:
            logger.info(f"Error parsing summary agent response: {e}\nRaw response: {response.content}")
            return {
                "new_sub_tasks": [],
                "summary": "Failed to generate summary",
            }

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """You are a research summary and task planning expert. Based on the research context, 
generate download subtasks that will help achieve the research objective. Return a JSON object with:
- "new_sub_tasks": List of subtask objects, each with "type" (should be "download"), "objective", and "search_keywords"
- "summary": A brief summary of the research findings

Avoid generating duplicate subtasks that already exist in existing_subtasks."""

    def _get_default_task_prompt(
        self, objective: str, message: str, existing_subtasks_str: str, context: str
    ) -> str:
        """Get default task prompt"""
        return f"""Research objective: {objective}

User message: {message}

Existing subtasks:
{existing_subtasks_str}

Research context:
{context[:18000]}

Based on the research context, generate download subtasks that will help achieve the research objective.
Return a JSON object with "new_sub_tasks" (list) and "summary" (string)."""


