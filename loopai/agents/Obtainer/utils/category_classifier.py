import json
from typing import Optional, List, Dict, Any

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
    ) -> Dict[str, str]:
        """
        Classify the task category as SFT or PT based on user query and extract dataset background
        
        Args:
            user_query: The user's query or message
            objective: Optional objective description
            
        Returns:
            Dictionary with "category" ("SFT" or "PT") and "dataset_background" (str)
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
            logger.debug(f"Cleaned response: {clean_response[:200]}...")
            
            # Try to parse as JSON first
            try:
                result = json.loads(clean_response)
                logger.debug(f"Successfully parsed JSON response: {type(result)}")
                
                if isinstance(result, dict):
                    category = result.get("category", "").upper()
                    dataset_background = result.get("dataset_background", "")
                    logger.info(f"[Dataset Background] Extracted from JSON dict - category: {category}, background length: {len(dataset_background)}")
                    if dataset_background:
                        logger.info(f"[Dataset Background] Content: {dataset_background[:150]}...")
                    else:
                        logger.warning("[Dataset Background] Empty dataset_background in JSON dict response")
                elif isinstance(result, str):
                    category = result.upper()
                    dataset_background = ""
                    logger.warning(f"[Dataset Background] Response is string type, cannot extract background. Category: {category}")
                else:
                    category = clean_response.upper()
                    dataset_background = ""
                    logger.warning(f"[Dataset Background] Response is unexpected type ({type(result)}), cannot extract background. Category: {category}")
            except json.JSONDecodeError as e:
                # If not JSON, try to extract category from text
                logger.warning(f"[Dataset Background] Failed to parse JSON response: {e}")
                category = clean_response.upper()
                dataset_background = ""
                logger.warning(f"[Dataset Background] JSON parsing failed, using text extraction. Category: {category}, background will use fallback")
            
            # Validate category
            if category not in ["SFT", "PT"]:
                logger.warning(f"Invalid category '{category}', defaulting to PT")
                category = "PT"
            
            # If dataset_background is empty, try to extract from user_query
            if not dataset_background:
                logger.info("[Dataset Background] Empty dataset_background, using user_query as fallback")
                dataset_background = user_query if user_query else objective
                logger.info(f"[Dataset Background] Fallback background set from user_query/objective, length: {len(dataset_background)}")
                if dataset_background:
                    logger.info(f"[Dataset Background] Fallback content: {dataset_background[:150]}...")
            else:
                logger.info(f"[Dataset Background] Successfully extracted from LLM response, length: {len(dataset_background)}")
            
            logger.info(f"Classified category: {category}")
            if dataset_background:
                logger.info(f"Final dataset background: {dataset_background[:100]}...")
            
            return {
                "category": category,
                "dataset_background": dataset_background
            }
                
        except Exception as e:
            logger.error(f"Error in category classification: {e}")
            logger.info("Defaulting to PT category due to classification error")
            # Fallback: use user_query as dataset_background
            fallback_background = user_query if user_query else objective
            logger.info(f"[Dataset Background] Exception fallback - using user_query/objective as background, length: {len(fallback_background)}")
            if fallback_background:
                logger.info(f"[Dataset Background] Exception fallback content: {fallback_background[:150]}...")
            return {
                "category": "PT",
                "dataset_background": fallback_background
            }

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

Additionally, you need to extract the dataset background description from the user query. The dataset background should describe:
- What type of dataset is needed (e.g., code generation dataset, code evaluation dataset, text classification dataset)
- The domain or topic of the data
- Key characteristics or requirements

Return a JSON object with:
{
    "category": "SFT" or "PT",
    "dataset_background": "A clear description of the dataset background extracted from the user query, describing what type of dataset is needed and its characteristics",
    "reasoning": "Brief explanation of why this category was chosen"
}

The dataset_background field is required and should be a concise but informative description."""

    def _get_default_task_prompt(self, user_query: str, objective: str) -> str:
        """Get default task prompt"""
        query_text = objective if objective else user_query
        return f"""User query: {user_query}

Research objective: {query_text}

Please analyze the user's query and objective to:
1. Determine if they need SFT data (question-answer pairs, instruction-following data) or PT data (raw text corpus, documents, code)
2. Extract the dataset background description from the query, describing what type of dataset is needed and its characteristics

**Few-shot Example:**

Input: 【背景介绍】该数据集为代码生成与评测数据集，包含任务编号（task_id）、模型生成代码（completion）、评测结果（result）、是否通过（passed）等字段。每条样本记录了模型生成的代码片段及其在特定测试用例下的表现，输入为编程任务描述，输出为Python代码实现，旨在评估代码逻辑正确性和功能实现。

Output:
{{
    "category": "SFT",
    "dataset_background": "代码生成与评测数据集，包含任务编号、模型生成代码、评测结果、是否通过等字段。每条样本记录了模型生成的代码片段及其在特定测试用例下的表现，输入为编程任务描述，输出为Python代码实现，旨在评估代码逻辑正确性和功能实现。数据集类型是代码生成或者代码评测数据集。",
    "reasoning": "This is SFT because it requires structured input-output pairs (programming task description -> Python code implementation) for fine-tuning models to generate code."
}}

Return a JSON object with "category", "dataset_background", and "reasoning" fields."""


class ObtainQueryNormalizer:
    """
    Detect evaluation-recommendation style inputs and rewrite them into
    concrete dataset collection objectives suitable for obtain workflow.
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.2,
        prompt_loader: Optional[PromptLoader] = None,
    ):
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        self.prompt_loader = prompt_loader

    async def normalize(self, user_query: str, objective: str = "") -> dict:
        """
        Identify whether the query is:
        - dataset_request: already a direct dataset collection ask
        - eval_recommendation: based on evaluation results / suggestions, needs rewrite
        Returns dict with intent_type, normalized_query, reason, raw_response.
        """
        if not user_query:
            return {}

        logger.info("\n--- Obtain Query Normalizer ---")

        system_prompt, human_prompt = self._build_prompts(user_query, objective)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]

        try:
            response = await self.llm.ainvoke(messages)
            logger.info(f"Query normalizer raw response: {response.content}")
            clean = response.content.strip().replace("```json", "").replace("```", "").strip()

            result = self._parse_response(clean, user_query, objective)
            return result
        except Exception as e:
            logger.error(f"Error in query normalization: {e}")
            return {}

    def _build_prompts(self, user_query: str, objective: str):
        """Use prompt loader if available, otherwise default prompts."""
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "obtain_query_normalizer_prompt")
                task_prompt = self.prompt_loader("task", "obtain_query_normalizer_prompt")
                human_prompt = task_prompt.format(
                    user_query=user_query,
                    objective=objective if objective else user_query
                )
                return system_prompt, human_prompt
            except Exception as e:
                logger.warning(f"Failed to load obtain_query_normalizer_prompt, using default: {e}")

        # Default prompts
        system_prompt = (
            "You detect whether a request is a direct dataset collection ask, "
            "or an evaluation-based recommendation (e.g., suggests data improvements from eval results). "
            "If it is evaluation-based, rewrite it into a clear dataset collection objective "
            "that the data-obtainer can execute."
        )
        human_prompt = self._default_task_prompt(user_query, objective)
        return system_prompt, human_prompt

    def _default_task_prompt(self, user_query: str, objective: str) -> str:
        query_text = objective if objective else user_query
        return f"""User query: {user_query}

Research objective: {query_text}

Classify intent:
- dataset_request: direct ask to collect datasets for a domain/use (fine-tuning/pretraining).
- eval_recommendation: suggestions derived from model evaluation results about what data to add or improve.

If eval_recommendation, rewrite into a specific dataset collection objective the obtainer can execute.

Respond in JSON:
{{
  "intent_type": "dataset_request" | "eval_recommendation",
  "normalized_query": "<if eval_recommendation, the rewritten dataset collection objective; otherwise original>",
  "reason": "short reason",
  "confidence": 0-1
}}"""

    def _parse_response(self, content: str, user_query: str, objective: str) -> dict:
        try:
            data = json.loads(content)
            if isinstance(data, str):
                # If plain string, treat as intent_type only
                return {
                    "intent_type": data,
                    "normalized_query": user_query,
                    "reason": "",
                    "raw": content,
                }
            intent = data.get("intent_type") or data.get("intent") or ""
            normalized = data.get("normalized_query") or data.get("rewritten_query") or data.get("query") or ""
            reason = data.get("reason", "")
            confidence = data.get("confidence")
        except Exception:
            # Fallback: heuristic text parsing
            intent = "eval_recommendation" if "eval" in content.lower() else "dataset_request"
            normalized = ""
            reason = content
            confidence = None

        if not normalized:
            normalized = user_query if intent == "dataset_request" else objective or user_query

        return {
            "intent_type": intent,
            "normalized_query": normalized,
            "reason": reason,
            "confidence": confidence,
            "raw": content,
        }


class TaskDecomposer:
    """Task Decomposer for breaking down user input into multiple data collection tasks"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.3,
        prompt_loader: Optional[PromptLoader] = None,
    ):
        """Initialize Task Decomposer"""
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
        self.prompt_loader = prompt_loader

    async def decompose_tasks(self, user_input: str) -> List[Dict[str, Any]]:
        """
        Decompose user input into one or more specific data collection tasks
        
        Args:
            user_input: The user's input query
            
        Returns:
            List of task dictionaries, each with "task_name" field
        """
        logger.info("\n" + "="*60)
        logger.info("--- Task Decomposer ---")
        logger.info(f"原始任务输入: {user_input}")
        logger.info("="*60)
        
        if not user_input:
            logger.warning("Empty user input, returning default single task")
            return [{"task_name": "收集数据集用于大模型微调"}]
        
        # Use prompt loader if available, otherwise use default prompt
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "task_decomposer_prompt")
                task_prompt = self.prompt_loader("task", "task_decomposer_prompt")
                human_prompt = task_prompt.format(user_input=user_input)
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt()
                human_prompt = self._get_default_task_prompt(user_input)
        else:
            system_prompt = self._get_default_system_prompt()
            human_prompt = self._get_default_task_prompt(user_input)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        try:
            response = await self.llm.ainvoke(messages)
            logger.info(f"Task decomposer raw response: {response.content}")

            # Parse response
            clean_response = (
                response.content.strip().replace("```json", "").replace("```", "").strip()
            )
            
            # Try to parse as JSON
            try:
                result = json.loads(clean_response)
                if isinstance(result, list):
                    # Validate each task has task_name
                    tasks = []
                    for task in result:
                        if isinstance(task, dict) and "task_name" in task:
                            tasks.append({"task_name": task["task_name"]})
                        elif isinstance(task, str):
                            tasks.append({"task_name": task})
                    if tasks:
                        logger.info(f"\n任务拆解成功！共拆解为 {len(tasks)} 个子任务：")
                        logger.info("-" * 60)
                        for idx, task in enumerate(tasks, 1):
                            logger.info(f"  子任务 {idx}: {task['task_name']}")
                        logger.info("-" * 60)
                        logger.info(f"原始任务: {user_input}")
                        logger.info(f"拆解结果: {len(tasks)} 个子任务")
                        logger.info("="*60 + "\n")
                        return tasks
                    else:
                        logger.warning("No valid tasks found in response, using default")
                        logger.info(f"\n未找到有效任务，使用原始任务作为默认子任务:")
                        logger.info("-" * 60)
                        logger.info(f"  子任务 1: {user_input}")
                        logger.info("-" * 60)
                        logger.info("="*60 + "\n")
                        return [{"task_name": user_input}]
                elif isinstance(result, dict):
                    # Single task wrapped in dict
                    if "task_name" in result:
                        task_name = result["task_name"]
                        logger.info(f"\n任务拆解结果（单个任务）:")
                        logger.info("-" * 60)
                        logger.info(f"  子任务 1: {task_name}")
                        logger.info("-" * 60)
                        logger.info(f"原始任务: {user_input}")
                        logger.info(f"拆解结果: 1 个子任务（未拆解）")
                        logger.info("="*60 + "\n")
                        return [{"task_name": task_name}]
                    else:
                        logger.warning("Invalid task format, using default")
                        logger.info(f"\n使用原始任务作为默认子任务:")
                        logger.info("-" * 60)
                        logger.info(f"  子任务 1: {user_input}")
                        logger.info("-" * 60)
                        logger.info("="*60 + "\n")
                        return [{"task_name": user_input}]
                else:
                    logger.warning("Unexpected response format, using default")
                    logger.info(f"\n使用原始任务作为默认子任务:")
                    logger.info("-" * 60)
                    logger.info(f"  子任务 1: {user_input}")
                    logger.info("-" * 60)
                    logger.info("="*60 + "\n")
                    return [{"task_name": user_input}]
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON response: {clean_response}")
                # Fallback: treat entire response as single task
                logger.info(f"\nJSON解析失败，使用原始任务作为默认子任务:")
                logger.info("-" * 60)
                logger.info(f"  子任务 1: {user_input}")
                logger.info("-" * 60)
                logger.info("="*60 + "\n")
                return [{"task_name": user_input}]
                
        except Exception as e:
            logger.error(f"Error in task decomposition: {e}")
            logger.info("Using original input as single task due to decomposition error")
            logger.info(f"\n任务拆解出错，使用原始任务作为默认子任务:")
            logger.info("-" * 60)
            logger.info(f"  子任务 1: {user_input}")
            logger.info("-" * 60)
            logger.info("="*60 + "\n")
            return [{"task_name": user_input}]

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt"""
        return """You are a task decomposition expert. Your task is to analyze user input and decompose it into one or more specific data collection tasks.

**Task Decomposition Rules:**
1. If the user input is a single, specific task, return a list with one task.
2. If the user input contains multiple related but distinct tasks, decompose it into separate tasks.
3. Each task should be specific and actionable for data collection.
4. Task names should be clear and descriptive.

**Output Format:**
You must return a JSON array, where each element is a dictionary with a "task_name" field."""

    def _get_default_task_prompt(self, user_input: str) -> str:
        """Get default task prompt"""
        return f"""User input: {user_input}

Please analyze the user input and decompose it into one or more specific data collection tasks. Unless the user explicitly requests to collect data for pre-training, all subtasks should end with "for large model fine-tuning".

**Few-shot Example:**

Input: 【背景介绍】该数据集为代码生成与评测数据集，包含任务编号（task_id）、模型生成代码（completion）、评测结果（result）、是否通过（passed）等字段。每条样本记录了模型生成的代码片段及其在特定测试用例下的表现，输入为编程任务描述，输出为Python代码实现，旨在评估代码逻辑正确性和功能实现。常见标签 Top：python:1679, error:861, function:859, runtime:834, unknown:834, 逻辑错误:586, 语法错误:433, Python:350。整体来看，建议优先修复最常见错误并优化边界测试。模型生成的改进建议：1. 优化语法处理逻辑。2. 增强递归处理能力。3. 改进值和类型检查机制。

Output:
[
  {{
    "task_name": "收集代码语法处理纠错数据集用于大模型微调",
    "dataset_background": "代码生成与评测数据集，包含任务编号、模型生成代码、评测结果、是否通过等字段。每条样本记录了模型生成的代码片段及其在特定测试用例下的表现，输入为编程任务描述，输出为Python代码实现，旨在评估代码逻辑正确性和功能实现。数据集类型是代码生成或者代码评测数据集。"
  }},
  {{
    "task_name": "收集递归题目的代码生成数据集用于大模型微调",
    "dataset_background": "代码生成与评测数据集，包含任务编号、模型生成代码、评测结果、是否通过等字段。每条样本记录了模型生成的代码片段及其在特定测试用例下的表现，输入为编程任务描述，输出为Python代码实现，旨在评估代码逻辑正确性和功能实现。数据集类型是代码生成或者代码评测数据集。"
  }},
  {{
    "task_name": "收集代码的值和类型检查纠错数据集用于大模型微调",
    "dataset_background": "代码生成与评测数据集，包含任务编号、模型生成代码、评测结果、是否通过等字段。每条样本记录了模型生成的代码片段及其在特定测试用例下的表现，输入为编程任务描述，输出为Python代码实现，旨在评估代码逻辑正确性和功能实现。数据集类型是代码生成或者代码评测数据集。"
  }}
]

Return a JSON array of tasks, each with a "task_name" field. For example:
[
  {{
    "task_name": "收集text2sql数据集用于大模型微调"
  }}
]"""

