"""
Data filter tools for data cleaning subgraph.
These tools perform various cleaning operations on data files.
Currently implemented as Mock functions for testing.
"""
import os
import json
import random
import shutil
import sqlite3
import re
import asyncio
import ast
from typing import Dict, Any, List, Tuple, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from pydantic import BaseModel
from loopai.agents.BaseAgent.base_agent import BaseAgent

logger = get_logger()

# 导入 pyflakes
from pyflakes.api import check
from pyflakes.reporter import Reporter
import io
PYFLAKES_AVAILABLE = True

# 导入 tree-sitter
from tree_sitter import Language, Parser
import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_java
import tree_sitter_cpp
import tree_sitter_c
import tree_sitter_c_sharp
import tree_sitter_go
import tree_sitter_rust
import tree_sitter_ruby
import tree_sitter_swift
import tree_sitter_kotlin
import tree_sitter_scala
import tree_sitter_bash
import tree_sitter_html
import tree_sitter_css
import tree_sitter_json
import tree_sitter_yaml
import tree_sitter_markdown

TREE_SITTER_AVAILABLE = True
_tree_sitter_languages = {}
_tree_sitter_parsers = {}

# 定义所有支持的语言及其对应的 tree-sitter 模块
_language_modules = [
    ('python', tree_sitter_python),
    ('javascript', tree_sitter_javascript),
    ('java', tree_sitter_java),
    ('cpp', tree_sitter_cpp),
    ('c', tree_sitter_c),
    ('csharp', tree_sitter_c_sharp),
    ('go', tree_sitter_go),
    ('rust', tree_sitter_rust),
    ('ruby', tree_sitter_ruby),
    ('swift', tree_sitter_swift),
    ('kotlin', tree_sitter_kotlin),
    ('scala', tree_sitter_scala),
    ('bash', tree_sitter_bash),
    ('shell', tree_sitter_bash),  # shell 和 bash 使用同一个解析器
    ('html', tree_sitter_html),
    ('css', tree_sitter_css),
    ('json', tree_sitter_json),
    ('yaml', tree_sitter_yaml),
    ('markdown', tree_sitter_markdown),
]

# 加载所有语言解析器
for lang_name, module in _language_modules:
    try:
        # 尝试不同的方式获取 language 对象
        # tree-sitter 语言包可能有不同的 API：
        # 1. module.language (属性)
        # 2. module.language() (方法)
        # 3. 其他可能的属性名
        
        lang_obj = None
        
        # 方法1: 检查是否有 language 属性或方法
        if hasattr(module, 'language'):
            lang_attr = getattr(module, 'language')
            if callable(lang_attr):
                lang_obj = lang_attr()
            else:
                lang_obj = lang_attr
        
        # 方法2: 检查是否有 LANGUAGE 属性（某些包可能使用大写）
        if lang_obj is None and hasattr(module, 'LANGUAGE'):
            lang_obj = getattr(module, 'LANGUAGE')
        
        # 方法3: 检查是否有 language_binding 属性
        if lang_obj is None and hasattr(module, 'language_binding'):
            lang_obj = getattr(module, 'language_binding')
        
        if lang_obj is None:
            logger.warning(f"Could not find language object for {lang_name}, skipping")
            continue
        
        _tree_sitter_languages[lang_name] = Language(lang_obj)
        _tree_sitter_parsers[lang_name] = Parser(_tree_sitter_languages[lang_name])
        logger.debug(f"Loaded tree-sitter parser for {lang_name}")
    except Exception as e:
        logger.error(f"Failed to load tree-sitter parser for {lang_name}: {e}")
        continue

logger.info(f"Loaded {len(_tree_sitter_parsers)} tree-sitter language parsers: {', '.join(_tree_sitter_parsers.keys())}")

# 定义支持的语言列表（用于过滤）
SUPPORTED_LANGUAGES = set(_tree_sitter_parsers.keys())
SUPPORTED_LANGUAGES.add('shell')  # shell 和 bash 使用同一个解析器，但标签可能不同

class Text2SQLRepairAgent(BaseAgent):
    """
    专门用于Text2SQL Schema修复的Agent
    """
    @property
    def role_name(self) -> str:
        return "Text2SQLRepair"

    @property
    def system_prompt_type(self) -> str:
        return "system"

    @property
    def system_prompt_name(self) -> str:
        return "text2sql_repair"

    def init_graph(self):
        pass

    def __call__(self):
        pass

    async def repair_schema(self, schema: str, error_msg: str, user_query: str, sql: str) -> str:
        """
        修复Schema
        """
        prompt = (
            f"You are a SQL expert. The following SQLite schema failed to build.\n"
            f"Error message: {error_msg}\n\n"
            f"Context:\n"
            f"User Question: {user_query}\n"
            f"Target SQL: {sql}\n\n"
            f"Broken Schema:\n```sql\n{schema}\n```\n\n"
            f"Please provide a corrected, valid SQLite schema (DDL) that satisfies the query and SQL.\n"
            f"Output ONLY the SQL DDL statements, without any markdown formatting or explanation."
        )
        
        try:
            messages = [
                SystemMessage(content="You are a helpful SQL expert assistant who fixes broken SQLite schemas."),
                HumanMessage(content=prompt)
            ]
            response = await self.llm.ainvoke(messages)
            content = response.content
            # 清理可能的Markdown标记
            content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            return content.strip()
        except Exception as e:
            logger.error(f"Error repairing schema: {e}")
            return schema


class CodeGenDomainAgent(BaseAgent):
    """
    用于判断数据是否符合代码生成领域，并给代码打上语言标签的Agent
    """
    @property
    def role_name(self) -> str:
        return "CodeGenDomain"

    @property
    def system_prompt_type(self) -> str:
        return "system"

    @property
    def system_prompt_name(self) -> str:
        return "default_prompt"

    def init_graph(self):
        pass

    def __call__(self):
        pass

    def compute_prompt(self):
        return "You are an expert in code generation datasets. Your task is to analyze data and determine if it belongs to code generation domain, and identify the programming language."

    async def analyze_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析记录，判断是否符合代码生成领域，并识别编程语言
        
        Returns:
            {
                "is_codegen": bool,  # 是否符合代码生成领域
                "language": str,      # 编程语言标签 (python, javascript, java, cpp, go, rust, etc.) 或 "unknown"
                "reasoning": str      # 判断理由
            }
        """
        # 提取 assistant 内容
        messages = record.get("messages", [])
        assistant_content = next((m.get("content", "") for m in messages if m.get("role") == "assistant"), "")
        if not assistant_content:
            assistant_content = record.get("assistant", "")
        
        user_content = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        if not user_content:
            user_content = record.get("user", "")
            if not user_content:
                user_content = record.get("instruction", "")
        
        prompt = (
            f"Analyze the following data record and determine:\n"
            f"1. Is this a code generation dataset record? (code generation means the assistant response contains executable code, scripts, or structured data formats like JSON/YAML)\n"
            f"2. If yes, what programming language or format is the code written in?\n\n"
            f"User Query: {user_content}\n\n"
            f"Assistant Response: {assistant_content[:1000]}\n\n"
            f"Return a JSON object with:\n"
            f'{{"is_codegen": true/false, "language": "programming_language", "reasoning": "brief explanation"}}\n'
            f"Supported languages/formats include:\n"
            f"- Programming languages: python, javascript, java, cpp, c, csharp, go, rust, ruby, swift, kotlin, scala, bash, shell\n"
            f"- Markup/Data formats: html, css, json, yaml, markdown\n"
            f"- If language cannot be determined, use 'unknown'\n"
            f"Only return the JSON object, no other text."
        )
        
        try:
            messages_list = [
                SystemMessage(content=self.compute_prompt()),
                HumanMessage(content=prompt)
            ]
            response = await self.llm.ainvoke(messages_list)
            content = response.content.strip()
            
            # 清理可能的 markdown 标记
            content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            content = content.strip()
            
            result = json.loads(content)
            return {
                "is_codegen": result.get("is_codegen", False),
                "language": result.get("language", "unknown").lower(),
                "reasoning": result.get("reasoning", "")
            }
        except Exception as e:
            logger.error(f"Error analyzing record: {e}")
            return {"is_codegen": False, "language": "unknown", "reasoning": f"Error: {str(e)}"}


class CodeGenRepairAgent(BaseAgent):
    """
    专门用于代码生成数据修复的Agent（支持多语言）
    """
    @property
    def role_name(self) -> str:
        return "CodeGenRepair"

    @property
    def system_prompt_type(self) -> str:
        return "system"

    @property
    def system_prompt_name(self) -> str:
        return "default_prompt"

    def init_graph(self):
        pass

    def __call__(self):
        pass

    def compute_prompt(self):
        return "You are a helpful programming expert assistant who fixes broken code in various programming languages."

    async def repair_code(self, code: str, error_msg: str, user_query: str, language: str = "python", syntax_error: bool = True) -> Tuple[str, bool]:
        """
        修复代码，同时判断是否符合代码生成领域
        
        Args:
            code: 需要修复的代码
            error_msg: 错误信息（语法错误或linter错误）
            user_query: 用户查询/需求
            language: 编程语言
            syntax_error: 是否为语法错误（True）还是逻辑错误（False）
        
        Returns:
            (repaired_code, is_codegen) 元组
            is_codegen: 是否符合代码生成领域，如果不符合则返回原代码
        """
        # 首先判断是否符合代码生成领域
        domain_check_prompt = (
            f"Analyze if the following content is a code generation dataset record.\n"
            f"User Query: {user_query}\n\n"
            f"Code Content: {code[:500]}\n\n"
            f"Return JSON: {{\"is_codegen\": true/false, \"reasoning\": \"brief explanation\"}}\n"
            f"Only return JSON, no other text."
        )
        
        try:
            domain_messages = [
                SystemMessage(content="You are an expert in code generation datasets. Determine if data belongs to code generation domain."),
                HumanMessage(content=domain_check_prompt)
            ]
            domain_response = await self.llm.ainvoke(domain_messages)
            domain_content = domain_response.content.strip()
            domain_content = re.sub(r'^```json\s*', '', domain_content, flags=re.MULTILINE)
            domain_content = re.sub(r'^```\s*', '', domain_content, flags=re.MULTILINE)
            domain_content = re.sub(r'```$', '', domain_content, flags=re.MULTILINE)
            domain_result = json.loads(domain_content)
            
            if not domain_result.get("is_codegen", False):
                logger.info(f"Record does not belong to code generation domain, skipping repair")
                return code, False
        except Exception as e:
            logger.warning(f"Error checking domain: {e}, proceeding with repair")
        
        # 修复代码
        lang_map = {
            "python": "Python",
            "javascript": "JavaScript",
            "java": "Java",
            "cpp": "C++",
            "c": "C",
            "csharp": "C#",
            "c#": "C#",
            "go": "Go",
            "rust": "Rust",
            "ruby": "Ruby",
            "swift": "Swift",
            "kotlin": "Kotlin",
            "scala": "Scala",
            "bash": "Bash",
            "shell": "Shell",
            "html": "HTML",
            "css": "CSS",
            "json": "JSON",
            "yaml": "YAML",
            "markdown": "Markdown",
        }
        lang_name = lang_map.get(language.lower(), language.capitalize())
        
        if syntax_error:
            prompt = (
                f"You are a {lang_name} expert. The following {lang_name} code has syntax errors.\n"
                f"Error message: {error_msg}\n\n"
                f"User Requirement: {user_query}\n\n"
                f"Broken Code:\n```{language}\n{code}\n```\n\n"
                f"Please provide a corrected, syntactically valid {lang_name} code that satisfies the requirement.\n"
                f"Output ONLY the {lang_name} code, without any markdown formatting or explanation."
            )
        else:
            prompt = (
                f"You are a {lang_name} expert. The following {lang_name} code has logical errors (e.g., undefined variables).\n"
                f"Error message: {error_msg}\n\n"
                f"User Requirement: {user_query}\n\n"
                f"Broken Code:\n```{language}\n{code}\n```\n\n"
                f"Please provide a corrected, logically valid {lang_name} code that satisfies the requirement.\n"
                f"Output ONLY the {lang_name} code, without any markdown formatting or explanation."
            )
        
        try:
            messages = [
                SystemMessage(content=f"You are a helpful {lang_name} expert assistant who fixes broken {lang_name} code."),
                HumanMessage(content=prompt)
            ]
            response = await self.llm.ainvoke(messages)
            content = response.content
            # 清理可能的Markdown标记
            content = re.sub(rf'^```{re.escape(language)}\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            return content.strip(), True
        except Exception as e:
            logger.error(f"Error repairing code: {e}")
            return code, True


class NormalDomainAgent(BaseAgent):
    """
    用于判断数据是否与 user_query 领域相关的 Agent
    """
    @property
    def role_name(self) -> str:
        return "NormalDomain"

    @property
    def system_prompt_type(self) -> str:
        return "system"

    @property
    def system_prompt_name(self) -> str:
        return "default_prompt"

    def init_graph(self):
        pass

    def __call__(self):
        pass

    def compute_prompt(self):
        return "You are an expert in analyzing dialogue datasets. Your task is to determine if a conversation record is related to a specific domain query."

    async def analyze_record(self, record: Dict[str, Any], user_query: str) -> Dict[str, Any]:
        """
        分析记录，判断是否与 user_query 领域相关
        
        Args:
            record: 数据记录
            user_query: 用户查询/领域描述
        
        Returns:
            {
                "is_related": bool,    # 是否与领域相关
                "reasoning": str        # 判断理由
            }
        """
        # 提取 user 和 assistant 内容
        messages = record.get("messages", [])
        user_content = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        if not user_content:
            user_content = record.get("user", "")
            if not user_content:
                user_content = record.get("instruction", "")
        
        assistant_content = next((m.get("content", "") for m in messages if m.get("role") == "assistant"), "")
        if not assistant_content:
            assistant_content = record.get("assistant", "")
        
        prompt = (
            f"Analyze if the following conversation record is related to the target domain query.\n\n"
            f"Target Domain Query: {user_query}\n\n"
            f"Conversation Record:\n"
            f"User: {user_content[:500]}\n"
            f"Assistant: {assistant_content[:500]}\n\n"
            f"Determine if this conversation is relevant to the target domain query.\n"
            f"Return a JSON object with:\n"
            f'{{"is_related": true/false, "reasoning": "brief explanation"}}\n'
            f"Only return the JSON object, no other text."
        )
        
        try:
            messages_list = [
                SystemMessage(content=self.compute_prompt()),
                HumanMessage(content=prompt)
            ]
            response = await self.llm.ainvoke(messages_list)
            content = response.content.strip()
            
            # 清理可能的 markdown 标记
            content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            content = content.strip()
            
            result = json.loads(content)
            return {
                "is_related": result.get("is_related", False),
                "reasoning": result.get("reasoning", "")
            }
        except Exception as e:
            logger.error(f"Error analyzing record: {e}")
            return {"is_related": False, "reasoning": f"Error: {str(e)}"}


class NormalRepairAgent(BaseAgent):
    """
    专门用于常规对话QA数据完善和验证的Agent
    """
    @property
    def role_name(self) -> str:
        return "NormalRepair"

    @property
    def system_prompt_type(self) -> str:
        return "system"

    @property
    def system_prompt_name(self) -> str:
        return "default_prompt"

    def init_graph(self):
        pass

    def __call__(self):
        pass

    def compute_prompt(self):
        return "You are a helpful assistant expert who improves and validates dialogue responses to ensure they are accurate, relevant, and high-quality."

    async def improve_and_validate(self, user_content: str, assistant_content: str, user_query: str) -> Tuple[str, bool, str]:
        """
        完善回答并验证质量
        
        注意：此方法假设记录已经在第一步中通过了领域相关性检查，因此不再重复验证领域相关性。
        只进行回答完善和质量验证，减少LLM调用次数。
        
        Args:
            user_content: 用户问题
            assistant_content: 助手回答
            user_query: 目标领域查询
        
        Returns:
            (improved_content, is_valid, error_msg) 元组
            is_valid: 回答是否有效（正确且高质量）
            error_msg: 如果无效，错误信息
        """
        # 完善回答（合并完善和验证为一次调用，减少请求次数）
        improve_and_validate_prompt = (
            f"Improve the following assistant's answer and verify its quality.\n\n"
            f"Target Domain Context: {user_query}\n\n"
            f"User Question: {user_content}\n\n"
            f"Original Assistant Answer: {assistant_content}\n\n"
            f"Please:\n"
            f"1. Improve the assistant's answer to make it more accurate, complete, and helpful\n"
            f"2. Verify if the improved answer is correct and relevant\n\n"
            f"Return a JSON object with:\n"
            f'{{"improved_answer": "the improved answer text", "is_valid": true/false, "reasoning": "brief explanation"}}\n'
            f"is_valid should be true only if the improved answer is:\n"
            f"- Accurate and correct\n"
            f"- Complete and helpful\n"
            f"- Relevant to the user's question and target domain\n"
            f"- Well-structured and professional\n\n"
            f"Only return the JSON object, no other text."
        )
        
        try:
            messages = [
                SystemMessage(content="You are an expert assistant who improves dialogue responses and validates their quality."),
                HumanMessage(content=improve_and_validate_prompt)
            ]
            response = await self.llm.ainvoke(messages)
            content = response.content.strip()
            
            # 清理可能的 markdown 标记
            content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            content = content.strip()
            
            result = json.loads(content)
            improved_content = result.get("improved_answer", assistant_content).strip()
            is_valid = result.get("is_valid", False)
            error_msg = "" if is_valid else result.get("reasoning", "Answer validation failed")
            
            return improved_content, is_valid, error_msg
        except Exception as e:
            logger.error(f"Error improving and validating answer: {e}")
            return assistant_content, False, f"Error: {str(e)}"


class BaseCleanResult(BaseModel):
    cleaned_data_path: str = ""
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    success: bool = True  # 标记工具执行是否成功
    error_message: str = ""  # 错误信息


def basic_data_flitter(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    基础工具：逐条检查数据文件是否符合schema规范
    
    根据 obtainer_category (PT/SFT) 检查必要字段：
    - PT模式: 检查 text 字段是否存在且不为null
    - SFT模式: 检查 messages 字段是否存在、是数组、长度>=1，且每个message包含 role 和 content，且至少包含各一条user ， assistant,并且对应content不为空。
    
    Args:
        data_path: 数据文件路径（JSONL格式）
        state: 当前状态（包含 obtainer_category 用于判断PT/SFT模式）
    
    Returns:
        包含清洗结果的字典，格式：
        {
            "cleaned_data_path": str,  # 清洗后的数据路径
            "total_records": int,      # 总记录数
            "valid_records": int,      # 有效记录数
            "invalid_records": int    # 无效记录数
        }
    """
    logger.info(f"basic_data_flitter: Filtering data from {data_path}")
    
    result = BaseCleanResult(
        cleaned_data_path=data_path,
        total_records=0,
        valid_records=0,
        invalid_records=0
    )
    
    def _has_non_empty_content(content: object) -> bool:
        if content is None:
            return False
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return any(isinstance(item, str) and item.strip() for item in content)
        return False

    def _is_valid_pt_record(record: Dict[str, Any]) -> bool:
        if not isinstance(record, dict):
            return False
        if "text" not in record:
            return False
        return _has_non_empty_content(record.get("text"))

    def _is_valid_sft_record(record: Dict[str, Any]) -> bool:
        if not isinstance(record, dict):
            return False
        messages = record.get("messages")
        if not isinstance(messages, list) or not messages:
            return False
        has_user = False
        has_assistant = False
        for msg in messages:
            if not isinstance(msg, dict):
                return False
            # 获取 role，支持大小写不敏感
            role = msg.get("role")
            if not isinstance(role, str) or not role:
                return False
            role_lower = role.lower()
            # 获取 content
            content = msg.get("content")
            if not _has_non_empty_content(content):
                return False
            # 检查 role（支持大小写不敏感）
            if role_lower == "user":
                has_user = True
            elif role_lower == "assistant":
                has_assistant = True
        return has_user and has_assistant

    def _write_valid_jsonl(
        input_path: str,
        output_path: str,
        category: str
    ) -> Dict[str, int]:
        counts = {"total": 0, "valid": 0, "invalid": 0}
        with open(input_path, 'r', encoding='utf-8') as fin, open(output_path, 'w', encoding='utf-8') as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    counts["total"] += 1
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON line in {input_path}: {e}")
                    counts["invalid"] += 1
                    continue

                is_valid = _is_valid_pt_record(record) if category == "PT" else _is_valid_sft_record(record)
                if is_valid:
                    fout.write(json.dumps(record, ensure_ascii=False) + '\n')
                    counts["valid"] += 1
                else:
                    counts["invalid"] += 1
        return counts

    try:
        # 获取数据类别（PT或SFT）- 从 state.obtainer.category 获取
        obtainer_state = state.get("obtainer", {})
        category = obtainer_state.get("category", "PT").upper()
        if category not in ["PT", "SFT"]:
            logger.warning(f"Unknown category '{category}', defaulting to PT")
            category = "PT"

        # 检查文件或目录是否存在
        if not os.path.exists(data_path):
            logger.error(f"Data path does not exist: {data_path}")
            result.success = False
            result.error_message = f"Data path does not exist: {data_path}"
            return result

        if os.path.isfile(data_path):
            if not data_path.endswith(".jsonl"):
                logger.warning(f"Unsupported file format (expect .jsonl): {data_path}")
                result.success = False
                result.error_message = f"Unsupported file format (expect .jsonl): {data_path}"
                return result

            base_dir = os.path.dirname(data_path)
            base_name = os.path.basename(data_path)
            name, ext = os.path.splitext(base_name)
            cleaned_path = os.path.join(base_dir, f"{name}_cleaned{ext}")

            try:
                counts = _write_valid_jsonl(data_path, cleaned_path, category)
            except Exception as e:
                logger.error(f"Error reading file {data_path}: {e}")
                result.success = False
                result.error_message = f"Error reading file {data_path}: {e}"
                return result

            result.total_records = counts["total"]
            result.valid_records = counts["valid"]
            result.invalid_records = counts["invalid"]

            if result.valid_records > 0:
                result.cleaned_data_path = cleaned_path
                logger.info(f"basic_data_flitter: Cleaned data saved to {cleaned_path}")
            else:
                if os.path.exists(cleaned_path):
                    os.remove(cleaned_path)
                logger.warning("No valid records found, keeping original file path")
                result.cleaned_data_path = data_path

        elif os.path.isdir(data_path):
            jsonl_files = [
                f for f in os.listdir(data_path)
                if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
            ]
            if not jsonl_files:
                logger.warning(f"No JSONL files found in directory: {data_path}")
                result.success = False
                result.error_message = f"No JSONL files found in directory: {data_path}"
                return result

            cleaned_dir = f"{data_path}_cleaned"
            os.makedirs(cleaned_dir, exist_ok=True)

            any_valid = False
            for filename in jsonl_files:
                input_path = os.path.join(data_path, filename)
                output_path = os.path.join(cleaned_dir, filename)
                try:
                    counts = _write_valid_jsonl(input_path, output_path, category)
                except Exception as e:
                    logger.warning(f"Error reading file {input_path}: {e}")
                    continue

                result.total_records += counts["total"]
                result.valid_records += counts["valid"]
                result.invalid_records += counts["invalid"]

                if counts["valid"] > 0:
                    any_valid = True
                else:
                    if os.path.exists(output_path):
                        os.remove(output_path)

            if any_valid:
                result.cleaned_data_path = cleaned_dir
                logger.info(f"basic_data_flitter: Cleaned data saved to {cleaned_dir}")
            else:
                shutil.rmtree(cleaned_dir, ignore_errors=True)
                logger.warning("No valid records found, keeping original directory path")
                result.cleaned_data_path = data_path
        else:
            logger.warning(f"Unsupported data path type: {data_path}")
            result.success = False
            result.error_message = f"Unsupported data path type: {data_path}"
            return result

        logger.info(
            f"basic_data_flitter: Filtering completed - "
            f"total: {result.total_records}, "
            f"valid: {result.valid_records}, "
            f"invalid: {result.invalid_records}"
        )

    except Exception as e:
        logger.error(f"basic_data_flitter: Error filtering data from {data_path}: {e}")
        # 发生错误时，保持原路径
        result.cleaned_data_path = data_path
    
    return result


def domain_text2sql_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    领域工具：针对Text2SQL数据的特定清洗 (并发优化版)
    
    1. 针对message里面的role：system的context（schema）尝试建库
    2. 建库失败的调用Agent尝试根据role：assistant，user的context去理解题目并尝试生成建库的schema
    3. 循环修复直到成功或达到上限（3次）
    4. 执行role：assistant内的sql语句进行真实操作，不报错的数据保留
    
    Args:
        data_path: 数据文件路径
        state: 当前状态
    
    Returns:
        包含清洗结果的字典
    """
    return asyncio.run(_async_domain_text2sql_cleaner(data_path, state))


async def _async_domain_text2sql_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """内部异步实现"""
    logger.info(f"domain_text2sql_cleaner: Cleaning Text2SQL data from {data_path}")
    
    result = BaseCleanResult(
        cleaned_data_path=data_path,
        total_records=0,
        valid_records=0,
        invalid_records=0
    )
    
    # === 配置部分 ===
    BATCH_SIZE = 64  # 设置并发 Batch 大小
    agent_config = state.get("obtainer", {}) or {}
    
    # 初始化修复 Agent
    repair_agent = None
    try:
        repair_agent = Text2SQLRepairAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
    except Exception as e:
        logger.error(f"Failed to initialize Text2SQLRepairAgent: {e}")
        raise e 

    # === 内部辅助函数 ===
    
    def _test_schema_creation(schema_sql: str) -> (bool, str):
        """测试Schema是否能成功建库 (同步CPU密集型，保持同步即可，除非数据量极大才需放线程池)"""
        try:
            conn = sqlite3.connect(':memory:')
            cursor = conn.cursor()
            cursor.executescript(schema_sql)
            conn.close()
            return True, ""
        except Exception as e:
            return False, str(e)

    def _test_sql_execution(schema_sql: str, query_sql: str) -> bool:
        """测试SQL是否能在Schema上执行"""
        try:
            conn = sqlite3.connect(':memory:')
            cursor = conn.cursor()
            cursor.executescript(schema_sql)
            cursor.execute(query_sql)
            conn.close()
            return True
        except Exception as e:
            return False

    async def _repair_single_item(item):
        """
        处理单条数据的修复逻辑：调用Agent -> 验证 -> 更新记录
        """
        record = item["record"]
        current_schema = next((m.get("content", "") for m in record.get("messages", []) if m.get("role") == "system"), "")
        if not current_schema:
            current_schema = record.get("system", "")
        
        try:
            # 异步调用 Agent
            new_schema = await repair_agent.repair_schema(
                schema=current_schema,
                error_msg=item["error"],
                user_query=item["user_content"],
                sql=item["assistant_content"]
            )
            
            # 验证新 Schema
            success, error_msg = _test_schema_creation(new_schema)
            
            if success:
                # 更新逻辑
                system_updated = False
                for msg in record.get("messages", []):
                    if msg.get("role") == "system":
                        msg["content"] = new_schema
                        system_updated = True
                        break
                
                if not system_updated:
                    if "system" in record:
                        record["system"] = new_schema
                    else:
                        if "messages" not in record:
                            record["messages"] = []
                        record["messages"].insert(0, {"role": "system", "content": new_schema})

                item["valid"] = True
                item["record"] = record
                item["error"] = None # 清除错误
                return True # 修复成功
            else:
                item["error"] = error_msg
                return False # 修复失败但API调用成功

        except Exception as e:
            item["error"] = f"Agent Error: {str(e)}"
            return False

    # === 内部函数：处理单个文件 ===
    async def _process_single_file(file_path: str) -> Tuple[List[Dict], int, int, int]:
        """
        处理单个JSONL文件，返回 (有效记录列表, 总数, 有效数, 无效数)
        """
        all_records = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        all_records.append(json.loads(line))
                    except:
                        pass
        
        file_total = len(all_records)
        
        # 第一步：初始 Schema 验证
        processed_records = []
        
        for record in all_records:
            messages = record.get("messages", [])
            system_content = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            if not system_content:
                system_content = record.get("system", "")
                
            user_content = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            assistant_content = next((m.get("content", "") for m in messages if m.get("role") == "assistant"), "")
            
            # 初始验证
            if not system_content:
                success, error_msg = False, "Schema is empty"
            else:
                success, error_msg = _test_schema_creation(system_content)
            
            if success:
                processed_records.append({"record": record, "valid": True})
            else:
                # 记录失败项，准备修复
                processed_records.append({
                    "record": record, 
                    "valid": False, 
                    "error": error_msg,
                    "retry_count": 0,
                    "user_content": user_content,
                    "assistant_content": assistant_content
                })

        # 第二步：并发循环修复
        max_retries = 3
        
        for attempt in range(max_retries):
            to_repair = [
                item for item in processed_records 
                if not item["valid"] and item["retry_count"] < max_retries
            ]
            
            if not to_repair:
                break
                
            logger.info(f"[{os.path.basename(file_path)}] Schema Repair Attempt {attempt + 1}/{max_retries}, items to repair: {len(to_repair)}")
            
            total_items = len(to_repair)
            for i in range(0, total_items, BATCH_SIZE):
                batch = to_repair[i : i + BATCH_SIZE]
                logger.info(f"  > Processing batch {i//BATCH_SIZE + 1}/{(total_items + BATCH_SIZE - 1)//BATCH_SIZE} (size: {len(batch)})")
                
                tasks = []
                for item in batch:
                    item["retry_count"] += 1
                    tasks.append(_repair_single_item(item))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                success_count = sum(1 for r in results if r is True)
                logger.info(f"  > Batch finished. Success: {success_count}/{len(batch)}")

        # 第三步：SQL 执行验证
        final_valid_records = []
        schema_valid_items = [item for item in processed_records if item["valid"]]
        
        for item in schema_valid_items:
            record = item["record"]
            messages = record.get("messages", [])
            system_content = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            if not system_content:
                system_content = record.get("system", "")
            assistant_content = next((m.get("content", "") for m in messages if m.get("role") == "assistant"), "")
            
            if _test_sql_execution(system_content, assistant_content):
                final_valid_records.append(record)

        file_valid = len(final_valid_records)
        file_invalid = file_total - file_valid
        
        return final_valid_records, file_total, file_valid, file_invalid

    # === 数据读取与预处理 ===
    if not os.path.exists(data_path):
        logger.error(f"Data path not found: {data_path}")
        result.success = False
        result.error_message = f"Data path not found: {data_path}"
        return result

    # 处理单个文件
    if os.path.isfile(data_path):
        if not data_path.endswith(".jsonl"):
            logger.warning(f"Unsupported file format (expect .jsonl): {data_path}")
            result.success = False
            result.error_message = f"Unsupported file format (expect .jsonl): {data_path}"
            return result
        
        final_valid_records, file_total, file_valid, file_invalid = await _process_single_file(data_path)
        
        result.total_records = file_total
        result.valid_records = file_valid
        result.invalid_records = file_invalid

        base_dir = os.path.dirname(data_path)
        base_name = os.path.basename(data_path)
        name, ext = os.path.splitext(base_name)
        cleaned_path = os.path.join(base_dir, f"{name}_text2sql_cleaned{ext}")
        
        try:
            with open(cleaned_path, 'w', encoding='utf-8') as f:
                for record in final_valid_records:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
            result.cleaned_data_path = cleaned_path
            logger.info(f"domain_text2sql_cleaner: Cleaned data saved to {cleaned_path}")
        except Exception as e:
            logger.error(f"Error saving cleaned data: {e}")
            result.cleaned_data_path = data_path

    # 处理目录
    elif os.path.isdir(data_path):
        jsonl_files = [
            f for f in os.listdir(data_path)
            if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
        ]
        if not jsonl_files:
            logger.warning(f"No JSONL files found in directory: {data_path}")
            result.success = False
            result.error_message = f"No JSONL files found in directory: {data_path}"
            return result

        cleaned_dir = f"{data_path}_text2sql_cleaned"
        os.makedirs(cleaned_dir, exist_ok=True)

        any_valid = False
        for filename in jsonl_files:
            input_path = os.path.join(data_path, filename)
            output_path = os.path.join(cleaned_dir, filename)
            
            try:
                final_valid_records, file_total, file_valid, file_invalid = await _process_single_file(input_path)
                
                result.total_records += file_total
                result.valid_records += file_valid
                result.invalid_records += file_invalid

                if file_valid > 0:
                    any_valid = True
                    with open(output_path, 'w', encoding='utf-8') as f:
                        for record in final_valid_records:
                            f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    logger.info(f"domain_text2sql_cleaner: Cleaned {filename} -> {output_path}")
            except Exception as e:
                logger.warning(f"Error processing file {input_path}: {e}")
                continue

        if any_valid:
            result.cleaned_data_path = cleaned_dir
            logger.info(f"domain_text2sql_cleaner: Cleaned data saved to {cleaned_dir}")
        else:
            shutil.rmtree(cleaned_dir, ignore_errors=True)
            logger.warning("No valid records found, keeping original directory path")
            result.cleaned_data_path = data_path
    else:
        logger.warning(f"Unsupported data path type: {data_path}")
        result.success = False
        result.error_message = f"Unsupported data path type: {data_path}"
        return result

    logger.info(
        f"domain_text2sql_cleaner: Cleaning completed - "
        f"total: {result.total_records}, "
        f"valid: {result.valid_records}, "
        f"invalid: {result.invalid_records}"
    )
    
    return result

def _test_syntax_with_treesitter(code: str, language: str) -> Tuple[bool, str]:
    """
    使用 tree-sitter 进行语法检查（支持多语言）
    
    Args:
        code: 代码内容
        language: 编程语言标签
    
    Returns:
        (success, error_msg) 元组
    """
    if not code or not code.strip():
        return False, "Code is empty"
    
    language = language.lower()
    
    # 处理语言别名
    language_aliases = {
        "c#": "csharp",
        "csharp": "csharp",
        "shell": "bash",
        "sh": "bash",
        "js": "javascript",
        "py": "python",
    }
    language = language_aliases.get(language, language)
    
    if not TREE_SITTER_AVAILABLE:
        # 如果 tree-sitter 不可用，对于 Python 使用 AST 作为后备
        if language == "python":
            try:
                ast.parse(code)
                return True, ""
            except SyntaxError as e:
                return False, f"SyntaxError: {str(e)}"
            except Exception as e:
                return False, f"ParseError: {str(e)}"
        else:
            logger.warning(f"tree-sitter not available, skipping syntax check for {language}")
            return True, ""  # 其他语言且 tree-sitter 不可用时，跳过检查
    
    if language not in _tree_sitter_parsers:
        # 如果语言不支持，对于 Python 使用 AST 作为后备
        if language == "python":
            try:
                ast.parse(code)
                return True, ""
            except SyntaxError as e:
                return False, f"SyntaxError: {str(e)}"
            except Exception as e:
                return False, f"ParseError: {str(e)}"
        else:
            logger.warning(f"Language {language} not supported by tree-sitter, skipping syntax check")
            return True, ""  # 不支持的语言跳过检查
    
    try:
        parser = _tree_sitter_parsers[language]
        tree = parser.parse(bytes(code, "utf8"))
        
        # 检查是否有语法错误（通过检查是否有错误节点）
        if tree.root_node.has_error:
            return False, f"SyntaxError: Tree-sitter detected syntax errors in {language} code"
        return True, ""
    except Exception as e:
        # tree-sitter 解析失败，对于 Python 使用 AST 作为后备
        if language == "python":
            try:
                ast.parse(code)
                return True, ""
            except SyntaxError as e2:
                return False, f"SyntaxError: {str(e2)}"
            except Exception as e2:
                return False, f"ParseError: {str(e2)}"
        logger.warning(f"Error parsing {language} code with tree-sitter: {e}")
        return False, f"ParseError: {str(e)}"


def domain_code_gen_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    领域工具：针对代码生成数据的特定清洗 (多语言支持版)
    
    新的清洗流程：
    1. 第一步：并发64个batch让Agent判断领域和打语言标签
    2. 第二步：根据标签使用tree-sitter检查语法问题
    3. 第三步：修复循环（Agent也会判断领域，不符合则舍弃）
    4. 第四步：最终验证
    
    Args:
        data_path: 数据文件路径
        state: 当前状态
    
    Returns:
        包含清洗结果的字典
    """
    return asyncio.run(_async_domain_code_gen_cleaner(data_path, state))


async def _async_domain_code_gen_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """内部异步实现"""
    logger.info(f"domain_code_gen_cleaner: Cleaning code generation data from {data_path}")
    
    result = BaseCleanResult(
        cleaned_data_path=data_path,
        total_records=0,
        valid_records=0,
        invalid_records=0
    )
    
    # === 配置部分 ===
    BATCH_SIZE = 64  # 设置并发 Batch 大小
    agent_config = state.get("obtainer", {}) or {}
    
    # 初始化 Agent
    domain_agent = None
    repair_agent = None
    try:
        domain_agent = CodeGenDomainAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
        repair_agent = CodeGenRepairAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
    except Exception as e:
        logger.error(f"Failed to initialize Agents: {e}")
        raise e

    # === 内部辅助函数 ===
    
    def _extract_code_from_record(record: Dict[str, Any]) -> Tuple[str, str]:
        """
        从记录中提取代码和用户查询
        
        Returns:
            (code, user_query) 元组
        """
        messages = record.get("messages", [])
        # 提取 assistant 的代码
        assistant_content = next((m.get("content", "") for m in messages if m.get("role") == "assistant"), "")
        if not assistant_content:
            assistant_content = record.get("assistant", "")
        
        # 提取 user 的查询
        user_content = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        if not user_content:
            user_content = record.get("user", "")
            if not user_content:
                user_content = record.get("instruction", "")
        
        # 清理代码中的 markdown 标记（支持多语言）
        code = assistant_content
        if code:
            # 匹配各种语言的代码块标记
            code = re.sub(r'^```\w*\s*', '', code, flags=re.MULTILINE)
            code = re.sub(r'^```\s*', '', code, flags=re.MULTILINE)
            code = re.sub(r'```$', '', code, flags=re.MULTILINE)
            code = code.strip()
        
        return code, user_content
    
    # === 第一步：并发判断领域和打标签 ===
    async def _analyze_single_record(record):
        """分析单条记录：判断领域和打语言标签"""
        try:
            analysis = await domain_agent.analyze_record(record)
            return {
                "record": record,
                "is_codegen": analysis.get("is_codegen", False),
                "language": analysis.get("language", "unknown"),
                "reasoning": analysis.get("reasoning", "")
            }
        except Exception as e:
            logger.error(f"Error analyzing record: {e}")
            return {
                "record": record,
                "is_codegen": False,
                "language": "unknown",
                "reasoning": f"Error: {str(e)}"
            }

    # === 内部函数：处理单个文件 ===
    async def _process_single_file(file_path: str) -> Tuple[List[Dict], int, int, int]:
        """
        处理单个JSONL文件，返回 (有效记录列表, 总数, 有效数, 无效数)
        """
        all_records = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        all_records.append(json.loads(line))
                    except:
                        pass
        
        file_total = len(all_records)
        logger.info(f"[{os.path.basename(file_path)}] Total records to process: {file_total}")
        
        # 第一步：并发64个batch判断领域和打标签
        logger.info(f"[{os.path.basename(file_path)}] Step 1: Analyzing domain and labeling language...")
        analyzed_records = []
        
        for i in range(0, file_total, BATCH_SIZE):
            batch = all_records[i : i + BATCH_SIZE]
            logger.info(f"  > Processing analysis batch {i//BATCH_SIZE + 1}/{(file_total + BATCH_SIZE - 1)//BATCH_SIZE} (size: {len(batch)})")
            
            tasks = [_analyze_single_record(record) for record in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Error in analysis: {r}")
                    continue
                analyzed_records.append(r)
        
        # 过滤出符合代码生成领域且语言在支持列表中的记录
        codegen_records = [
            item for item in analyzed_records 
            if item["is_codegen"] and item.get("language", "unknown").lower() in SUPPORTED_LANGUAGES
        ]
        filtered_count = len([item for item in analyzed_records if item["is_codegen"] and item.get("language", "unknown").lower() not in SUPPORTED_LANGUAGES])
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} records with unsupported languages")
        logger.info(f"Code generation records with supported languages: {len(codegen_records)}/{file_total}")
        
        # 第二步：使用tree-sitter检查语法
        logger.info(f"[{os.path.basename(file_path)}] Step 2: Checking syntax with tree-sitter...")
        processed_records = []
        
        for item in codegen_records:
            record = item["record"]
            code, user_query = _extract_code_from_record(record)
            language = item["language"]
            
            if not code:
                processed_records.append({
                    "record": record,
                    "valid": False,
                    "error": "No code found",
                    "retry_count": 0,
                    "user_query": user_query,
                    "language": language,
                    "is_syntax_error": True
                })
            else:
                success, error_msg = _test_syntax_with_treesitter(code, language)
                
                if success:
                    processed_records.append({
                        "record": record,
                        "valid": True,
                        "language": language
                    })
                else:
                    processed_records.append({
                        "record": record,
                        "valid": False,
                        "error": error_msg,
                        "retry_count": 0,
                        "user_query": user_query,
                        "language": language,
                        "is_syntax_error": True
                    })

        # 第三步：修复循环
        logger.info(f"[{os.path.basename(file_path)}] Step 3: Repairing syntax errors...")
        max_retries = 3
        
        async def _repair_single_item(item):
            """处理单条数据的修复逻辑"""
            record = item["record"]
            current_code, user_query = _extract_code_from_record(record)
            language = item.get("language", "python")
            
            if not current_code:
                item["valid"] = False
                item["error"] = "No code found in record"
                return False
            
            try:
                new_code, is_codegen = await repair_agent.repair_code(
                    code=current_code,
                    error_msg=item["error"],
                    user_query=user_query,
                    language=language,
                    syntax_error=item.get("is_syntax_error", True)
                )
                
                if not is_codegen:
                    item["valid"] = False
                    item["error"] = "Not code generation domain"
                    return False
                
                success, error_msg = _test_syntax_with_treesitter(new_code, language)
                
                if success:
                    code_updated = False
                    for msg in record.get("messages", []):
                        if msg.get("role") == "assistant":
                            msg["content"] = new_code
                            code_updated = True
                            break
                    
                    if not code_updated:
                        if "assistant" in record:
                            record["assistant"] = new_code
                        else:
                            if "messages" not in record:
                                record["messages"] = []
                            assistant_found = False
                            for msg in record["messages"]:
                                if msg.get("role") == "assistant":
                                    msg["content"] = new_code
                                    assistant_found = True
                                    break
                            if not assistant_found:
                                record["messages"].append({"role": "assistant", "content": new_code})

                    item["valid"] = True
                    item["record"] = record
                    item["error"] = None
                    return True
                else:
                    item["error"] = error_msg
                    return False

            except Exception as e:
                item["error"] = f"Agent Error: {str(e)}"
                return False
        
        for attempt in range(max_retries):
            to_repair = [
                item for item in processed_records
                if not item["valid"] and item.get("retry_count", 0) < max_retries
            ]
            
            if not to_repair:
                break
                
            logger.info(f"Code Repair Attempt {attempt + 1}/{max_retries}, items to repair: {len(to_repair)}")
            
            total_items = len(to_repair)
            for i in range(0, total_items, BATCH_SIZE):
                batch = to_repair[i : i + BATCH_SIZE]
                logger.info(f"  > Processing batch {i//BATCH_SIZE + 1}/{(total_items + BATCH_SIZE - 1)//BATCH_SIZE} (size: {len(batch)})")
                
                tasks = []
                for item in batch:
                    item["retry_count"] = item.get("retry_count", 0) + 1
                    tasks.append(_repair_single_item(item))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                success_count = sum(1 for r in results if r is True)
                logger.info(f"  > Batch finished. Success: {success_count}/{len(batch)}")

        # 第四步：最终验证
        logger.info(f"[{os.path.basename(file_path)}] Step 4: Final validation...")
        final_valid_records = []
        code_valid_items = [item for item in processed_records if item["valid"]]
        
        for item in code_valid_items:
            record = item["record"]
            code, _ = _extract_code_from_record(record)
            language = item.get("language", "python")
            
            success, _ = _test_syntax_with_treesitter(code, language)
            
            if success:
                final_valid_records.append(record)

        file_valid = len(final_valid_records)
        file_invalid = file_total - file_valid
        
        return final_valid_records, file_total, file_valid, file_invalid

    # === 数据读取与预处理 ===
    if not os.path.exists(data_path):
        logger.error(f"Data path not found: {data_path}")
        result.success = False
        result.error_message = f"Data path not found: {data_path}"
        return result

    # 处理单个文件
    if os.path.isfile(data_path):
        if not data_path.endswith(".jsonl"):
            logger.warning(f"Unsupported file format (expect .jsonl): {data_path}")
            result.success = False
            result.error_message = f"Unsupported file format (expect .jsonl): {data_path}"
            return result
        
        final_valid_records, file_total, file_valid, file_invalid = await _process_single_file(data_path)
        
        result.total_records = file_total
        result.valid_records = file_valid
        result.invalid_records = file_invalid

        base_dir = os.path.dirname(data_path)
        base_name = os.path.basename(data_path)
        name, ext = os.path.splitext(base_name)
        cleaned_path = os.path.join(base_dir, f"{name}_codegen_cleaned{ext}")
        
        try:
            with open(cleaned_path, 'w', encoding='utf-8') as f:
                for record in final_valid_records:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
            result.cleaned_data_path = cleaned_path
            logger.info(f"domain_code_gen_cleaner: Cleaned data saved to {cleaned_path}")
        except Exception as e:
            logger.error(f"Error saving cleaned data: {e}")
            result.cleaned_data_path = data_path

    # 处理目录
    elif os.path.isdir(data_path):
        jsonl_files = [
            f for f in os.listdir(data_path)
            if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
        ]
        if not jsonl_files:
            logger.warning(f"No JSONL files found in directory: {data_path}")
            result.success = False
            result.error_message = f"No JSONL files found in directory: {data_path}"
            return result

        cleaned_dir = f"{data_path}_codegen_cleaned"
        os.makedirs(cleaned_dir, exist_ok=True)

        any_valid = False
        for filename in jsonl_files:
            input_path = os.path.join(data_path, filename)
            output_path = os.path.join(cleaned_dir, filename)
            
            try:
                final_valid_records, file_total, file_valid, file_invalid = await _process_single_file(input_path)
                
                result.total_records += file_total
                result.valid_records += file_valid
                result.invalid_records += file_invalid

                if file_valid > 0:
                    any_valid = True
                    with open(output_path, 'w', encoding='utf-8') as f:
                        for record in final_valid_records:
                            f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    logger.info(f"domain_code_gen_cleaner: Cleaned {filename} -> {output_path}")
            except Exception as e:
                logger.warning(f"Error processing file {input_path}: {e}")
                continue

        if any_valid:
            result.cleaned_data_path = cleaned_dir
            logger.info(f"domain_code_gen_cleaner: Cleaned data saved to {cleaned_dir}")
        else:
            shutil.rmtree(cleaned_dir, ignore_errors=True)
            logger.warning("No valid records found, keeping original directory path")
            result.cleaned_data_path = data_path
    else:
        logger.warning(f"Unsupported data path type: {data_path}")
        result.success = False
        result.error_message = f"Unsupported data path type: {data_path}"
        return result

    logger.info(
        f"domain_code_gen_cleaner: Cleaning completed - "
        f"total: {result.total_records}, "
        f"valid: {result.valid_records}, "
        f"invalid: {result.invalid_records}"
    )
    
    return result


def domain_normal_data_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    领域工具：针对常规对话QA数据的特定清洗
    
    清洗流程：
    1. 第一步：并发判断每条记录是否与 user_query 领域相关
    2. 第二步：对相关的记录调用 Agent 完善回答并验证质量
    3. 第三步：输出清洗后的数据
    
    Args:
        data_path: 数据文件路径
        state: 当前状态（包含 obtainer.user_query 用于领域判断）
    
    Returns:
        包含清洗结果的字典，格式：
        {
            "cleaned_data_path": str,
            "total_records": int,
            "valid_records": int,
            "invalid_records": int
        }
    """
    return asyncio.run(_async_domain_normal_data_cleaner(data_path, state))


async def _async_domain_normal_data_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """内部异步实现"""
    logger.info(f"domain_normal_data_cleaner: Cleaning normal QA data from {data_path}")
    
    result = BaseCleanResult(
        cleaned_data_path=data_path,
        total_records=0,
        valid_records=0,
        invalid_records=0
    )
    
    # === 配置部分 ===
    BATCH_SIZE = 64  # 设置并发 Batch 大小
    agent_config = state.get("obtainer", {}) or {}
    user_query = agent_config.get("user_query", "")
    
    if not user_query:
        logger.warning("user_query is empty, cannot perform domain filtering")
        return result
    
    # 初始化 Agent
    domain_agent = None
    repair_agent = None
    try:
        domain_agent = NormalDomainAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
        repair_agent = NormalRepairAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
    except Exception as e:
        logger.error(f"Failed to initialize Agents: {e}")
        raise e
    
    # === 内部辅助函数 ===
    
    def _extract_content_from_record(record: Dict[str, Any]) -> Tuple[str, str]:
        """
        从记录中提取用户问题和助手回答
        
        Returns:
            (user_content, assistant_content) 元组
        """
        messages = record.get("messages", [])
        # 提取 user 的内容
        user_content = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        if not user_content:
            user_content = record.get("user", "")
            if not user_content:
                user_content = record.get("instruction", "")
        
        # 提取 assistant 的内容
        assistant_content = next((m.get("content", "") for m in messages if m.get("role") == "assistant"), "")
        if not assistant_content:
            assistant_content = record.get("assistant", "")
        
        return user_content, assistant_content
    
    # === 内部函数：处理单个文件 ===
    async def _process_single_file(file_path: str) -> Tuple[List[Dict], int, int, int]:
        """
        处理单个JSONL文件，返回 (有效记录列表, 总数, 有效数, 无效数)
        """
        all_records = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        all_records.append(json.loads(line))
                    except:
                        pass
        
        file_total = len(all_records)
        logger.info(f"[{os.path.basename(file_path)}] Total records to process: {file_total}")
        logger.info(f"[{os.path.basename(file_path)}] Target domain query: {user_query}")
        
        # 第一步：并发判断领域相关性
        logger.info(f"[{os.path.basename(file_path)}] Step 1: Analyzing domain relevance...")
        analyzed_records = []
        
        for i in range(0, file_total, BATCH_SIZE):
            batch = all_records[i : i + BATCH_SIZE]
            logger.info(f"  > Processing analysis batch {i//BATCH_SIZE + 1}/{(file_total + BATCH_SIZE - 1)//BATCH_SIZE} (size: {len(batch)})")
            
            async def _analyze_single_record(record):
                """分析单条记录：判断是否与领域相关"""
                try:
                    analysis = await domain_agent.analyze_record(record, user_query)
                    return {
                        "record": record,
                        "is_related": analysis.get("is_related", False),
                        "reasoning": analysis.get("reasoning", "")
                    }
                except Exception as e:
                    logger.error(f"Error analyzing record: {e}")
                    return {
                        "record": record,
                        "is_related": False,
                        "reasoning": f"Error: {str(e)}"
                    }
            
            tasks = [_analyze_single_record(record) for record in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Error in analysis: {r}")
                    continue
                analyzed_records.append(r)
        
        # 过滤出与领域相关的记录
        related_records = [
            item for item in analyzed_records 
            if item["is_related"]
        ]
        filtered_count = len(analyzed_records) - len(related_records)
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} records not related to the domain")
        logger.info(f"Domain-related records: {len(related_records)}/{file_total}")
        
        # 第二步：完善回答并验证
        logger.info(f"[{os.path.basename(file_path)}] Step 2: Improving answers and validating quality...")
        processed_records = []
        
        total_related = len(related_records)
        for i in range(0, total_related, BATCH_SIZE):
            batch = related_records[i : i + BATCH_SIZE]
            logger.info(f"  > Processing improvement batch {i//BATCH_SIZE + 1}/{(total_related + BATCH_SIZE - 1)//BATCH_SIZE} (size: {len(batch)})")
            
            async def _improve_single_item(item):
                """处理单条数据的完善逻辑"""
                record = item["record"]
                user_content, assistant_content = _extract_content_from_record(record)
                
                if not user_content or not assistant_content:
                    item["valid"] = False
                    item["error"] = "Missing user or assistant content"
                    return False
                
                try:
                    improved_content, is_valid, error_msg = await repair_agent.improve_and_validate(
                        user_content=user_content,
                        assistant_content=assistant_content,
                        user_query=user_query
                    )
                    
                    if not is_valid:
                        item["valid"] = False
                        item["error"] = error_msg
                        return False
                    
                    content_updated = False
                    for msg in record.get("messages", []):
                        if msg.get("role") == "assistant":
                            msg["content"] = improved_content
                            content_updated = True
                            break
                    
                    if not content_updated:
                        if "assistant" in record:
                            record["assistant"] = improved_content
                        else:
                            if "messages" not in record:
                                record["messages"] = []
                            assistant_found = False
                            for msg in record["messages"]:
                                if msg.get("role") == "assistant":
                                    msg["content"] = improved_content
                                    assistant_found = True
                                    break
                            if not assistant_found:
                                record["messages"].append({"role": "assistant", "content": improved_content})
                    
                    item["valid"] = True
                    item["record"] = record
                    item["error"] = None
                    return True
                    
                except Exception as e:
                    item["error"] = f"Agent Error: {str(e)}"
                    item["valid"] = False
                    return False
            
            tasks = [_improve_single_item(item) for item in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for item, r in zip(batch, results):
                if isinstance(r, Exception):
                    logger.error(f"Error in improvement: {r}")
                    item["valid"] = False
                    item["error"] = f"Exception: {str(r)}"
                processed_records.append(item)
        
        # 第三步：收集有效记录
        logger.info(f"[{os.path.basename(file_path)}] Step 3: Collecting valid records...")
        final_valid_records = []
        
        for item in processed_records:
            if item.get("valid", False):
                final_valid_records.append(item["record"])
        
        file_valid = len(final_valid_records)
        file_invalid = file_total - file_valid
        
        return final_valid_records, file_total, file_valid, file_invalid

    # === 数据读取与预处理 ===
    if not os.path.exists(data_path):
        logger.error(f"Data path not found: {data_path}")
        result.success = False
        result.error_message = f"Data path not found: {data_path}"
        return result

    # 处理单个文件
    if os.path.isfile(data_path):
        if not data_path.endswith(".jsonl"):
            logger.warning(f"Unsupported file format (expect .jsonl): {data_path}")
            result.success = False
            result.error_message = f"Unsupported file format (expect .jsonl): {data_path}"
            return result
        
        final_valid_records, file_total, file_valid, file_invalid = await _process_single_file(data_path)
        
        result.total_records = file_total
        result.valid_records = file_valid
        result.invalid_records = file_invalid

        base_dir = os.path.dirname(data_path)
        base_name = os.path.basename(data_path)
        name, ext = os.path.splitext(base_name)
        cleaned_path = os.path.join(base_dir, f"{name}_normal_cleaned{ext}")
        
        try:
            with open(cleaned_path, 'w', encoding='utf-8') as f:
                for record in final_valid_records:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
            result.cleaned_data_path = cleaned_path
            logger.info(f"domain_normal_data_cleaner: Cleaned data saved to {cleaned_path}")
        except Exception as e:
            logger.error(f"Error saving cleaned data: {e}")
            result.cleaned_data_path = data_path

    # 处理目录
    elif os.path.isdir(data_path):
        jsonl_files = [
            f for f in os.listdir(data_path)
            if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
        ]
        if not jsonl_files:
            logger.warning(f"No JSONL files found in directory: {data_path}")
            result.success = False
            result.error_message = f"No JSONL files found in directory: {data_path}"
            return result

        cleaned_dir = f"{data_path}_normal_cleaned"
        os.makedirs(cleaned_dir, exist_ok=True)

        any_valid = False
        for filename in jsonl_files:
            input_path = os.path.join(data_path, filename)
            output_path = os.path.join(cleaned_dir, filename)
            
            try:
                final_valid_records, file_total, file_valid, file_invalid = await _process_single_file(input_path)
                
                result.total_records += file_total
                result.valid_records += file_valid
                result.invalid_records += file_invalid

                if file_valid > 0:
                    any_valid = True
                    with open(output_path, 'w', encoding='utf-8') as f:
                        for record in final_valid_records:
                            f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    logger.info(f"domain_normal_data_cleaner: Cleaned {filename} -> {output_path}")
            except Exception as e:
                logger.warning(f"Error processing file {input_path}: {e}")
                continue

        if any_valid:
            result.cleaned_data_path = cleaned_dir
            logger.info(f"domain_normal_data_cleaner: Cleaned data saved to {cleaned_dir}")
        else:
            shutil.rmtree(cleaned_dir, ignore_errors=True)
            logger.warning("No valid records found, keeping original directory path")
            result.cleaned_data_path = data_path
    else:
        logger.warning(f"Unsupported data path type: {data_path}")
        result.success = False
        result.error_message = f"Unsupported data path type: {data_path}"
        return result
    
    logger.info(
        f"domain_normal_data_cleaner: Cleaning completed - "
        f"total: {result.total_records}, "
        f"valid: {result.valid_records}, "
        f"invalid: {result.invalid_records}"
    )
    
    return result


def benchmark_data_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    Benchmark 数据清洗工具：从清洗后的数据集中移除与 benchmark 数据集相似/重复的记录
    
    清洗流程：
    1. 读取 benchmark JSONL 文件，构建去重索引
    2. 遍历数据集，移除与 benchmark 数据相似的记录
    3. 输出清洗后的数据
    
    相似度判断规则：
    - 对于 SFT 数据：比较 user 和 assistant 内容的相似度
    - 对于 PT 数据：比较 text 内容的相似度
    - 使用多种策略：精确匹配、归一化匹配、关键内容匹配
    
    Args:
        data_path: 数据文件路径
        state: 当前状态（包含 banckmark_jsonl_path 用于指定 benchmark 数据路径）
    
    Returns:
        包含清洗结果的字典
    """
    logger.info(f"benchmark_data_cleaner: Cleaning benchmark data from {data_path}")
    
    result = BaseCleanResult(
        cleaned_data_path=data_path,
        total_records=0,
        valid_records=0,
        invalid_records=0
    )
    
    # 获取 benchmark 文件路径
    benchmark_path = state.get("banckmark_jsonl_path", "")
    if not benchmark_path:
        logger.info("No benchmark_jsonl_path specified, skipping benchmark cleaning")
        return result
    
    if not os.path.exists(benchmark_path):
        logger.warning(f"Benchmark file not found: {benchmark_path}, skipping benchmark cleaning")
        return result
    
    # 获取数据类别（PT或SFT）- 从 state.obtainer.category 获取
    obtainer_state = state.get("obtainer", {})
    category = obtainer_state.get("category", "PT").upper()
    if category not in ["PT", "SFT"]:
        logger.warning(f"Unknown category '{category}', defaulting to SFT for benchmark cleaning")
        category = "SFT"
    
    logger.info(f"benchmark_data_cleaner: Category = {category}, Benchmark path = {benchmark_path}")
    
    # === 内部辅助函数 ===
    
    def _normalize_text(text: str) -> str:
        """归一化文本：移除多余空白、转小写"""
        if not text:
            return ""
        # 移除多余空白字符
        normalized = ' '.join(text.split())
        # 转小写
        normalized = normalized.lower()
        return normalized
    
    def _extract_key_content(text: str, max_len: int = 500) -> str:
        """提取关键内容用于快速比较"""
        if not text:
            return ""
        normalized = _normalize_text(text)
        # 取前 max_len 个字符作为关键内容
        return normalized[:max_len]
    
    def _extract_record_signature_sft(record: Dict[str, Any]) -> Tuple[str, str, str]:
        """
        提取 SFT 记录的签名用于比较
        
        Returns:
            (user_content, assistant_content, combined_key) 元组
        """
        messages = record.get("messages", [])
        user_content = ""
        assistant_content = ""
        
        for msg in messages:
            role = msg.get("role", "").lower()
            content = msg.get("content", "")
            if role == "user" and not user_content:
                user_content = content
            elif role == "assistant" and not assistant_content:
                assistant_content = content
        
        # 如果 messages 中没有，尝试从顶层字段获取
        if not user_content:
            user_content = record.get("user", "") or record.get("instruction", "") or record.get("input", "")
        if not assistant_content:
            assistant_content = record.get("assistant", "") or record.get("output", "") or record.get("response", "")
        
        # 创建组合键用于快速查找
        user_key = _extract_key_content(user_content)
        assistant_key = _extract_key_content(assistant_content)
        combined_key = f"{user_key}|||{assistant_key}"
        
        return user_content, assistant_content, combined_key
    
    def _extract_record_signature_pt(record: Dict[str, Any]) -> Tuple[str, str]:
        """
        提取 PT 记录的签名用于比较
        
        Returns:
            (text_content, text_key) 元组
        """
        text_content = record.get("text", "")
        text_key = _extract_key_content(text_content)
        return text_content, text_key
    
    def _is_similar_sft(record_sig: Tuple[str, str, str], benchmark_sigs: set, benchmark_full: Dict[str, Tuple[str, str]]) -> bool:
        """
        判断 SFT 记录是否与 benchmark 数据相似
        
        使用多层次匹配：
        1. 快速键匹配
        2. 用户内容精确匹配
        3. 用户内容高相似度匹配
        """
        user_content, assistant_content, combined_key = record_sig
        
        # 1. 快速键匹配
        if combined_key in benchmark_sigs:
            return True
        
        # 2. 用户内容精确匹配（归一化后）
        user_normalized = _normalize_text(user_content)
        for bm_key, (bm_user, bm_assistant) in benchmark_full.items():
            bm_user_normalized = _normalize_text(bm_user)
            # 用户问题完全相同
            if user_normalized == bm_user_normalized:
                return True
            # 用户问题是子串关系且长度相近
            if len(user_normalized) > 50 and len(bm_user_normalized) > 50:
                if user_normalized in bm_user_normalized or bm_user_normalized in user_normalized:
                    # 长度相近才认为是相似
                    len_ratio = len(user_normalized) / len(bm_user_normalized) if len(bm_user_normalized) > 0 else 0
                    if 0.8 <= len_ratio <= 1.25:
                        return True
        
        return False
    
    def _is_similar_pt(record_sig: Tuple[str, str], benchmark_sigs: set, benchmark_full: Dict[str, str]) -> bool:
        """
        判断 PT 记录是否与 benchmark 数据相似
        """
        text_content, text_key = record_sig
        
        # 1. 快速键匹配
        if text_key in benchmark_sigs:
            return True
        
        # 2. 精确匹配（归一化后）
        text_normalized = _normalize_text(text_content)
        for bm_key, bm_text in benchmark_full.items():
            bm_normalized = _normalize_text(bm_text)
            if text_normalized == bm_normalized:
                return True
            # 子串关系且长度相近
            if len(text_normalized) > 100 and len(bm_normalized) > 100:
                if text_normalized in bm_normalized or bm_normalized in text_normalized:
                    len_ratio = len(text_normalized) / len(bm_normalized) if len(bm_normalized) > 0 else 0
                    if 0.8 <= len_ratio <= 1.25:
                        return True
        
        return False
    
    # === 加载 Benchmark 数据 ===
    logger.info(f"Loading benchmark data from {benchmark_path}...")
    benchmark_sigs = set()  # 快速查找键集合
    benchmark_full = {}  # 完整内容字典
    benchmark_count = 0
    
    try:
        # 支持单个文件或目录
        benchmark_files = []
        if os.path.isfile(benchmark_path):
            benchmark_files = [benchmark_path]
        elif os.path.isdir(benchmark_path):
            benchmark_files = [
                os.path.join(benchmark_path, f) 
                for f in os.listdir(benchmark_path) 
                if f.endswith(".jsonl") and os.path.isfile(os.path.join(benchmark_path, f))
            ]
        
        for bm_file in benchmark_files:
            with open(bm_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        benchmark_count += 1
                        
                        if category == "SFT":
                            user_content, assistant_content, combined_key = _extract_record_signature_sft(record)
                            benchmark_sigs.add(combined_key)
                            benchmark_full[combined_key] = (user_content, assistant_content)
                        else:  # PT
                            text_content, text_key = _extract_record_signature_pt(record)
                            benchmark_sigs.add(text_key)
                            benchmark_full[text_key] = text_content
                            
                    except json.JSONDecodeError:
                        continue
        
        logger.info(f"Loaded {benchmark_count} benchmark records, {len(benchmark_sigs)} unique signatures")
        
    except Exception as e:
        logger.error(f"Error loading benchmark data: {e}")
        result.success = False
        result.error_message = f"Error loading benchmark data: {e}"
        return result
    
    if benchmark_count == 0:
        logger.warning("No benchmark records loaded, skipping benchmark cleaning")
        return result
    
    # === 处理数据文件 ===
    
    def _process_single_file(input_path: str, output_path: str) -> Dict[str, int]:
        """处理单个文件，返回统计信息"""
        counts = {"total": 0, "valid": 0, "invalid": 0, "benchmark_removed": 0}
        
        with open(input_path, 'r', encoding='utf-8') as fin, open(output_path, 'w', encoding='utf-8') as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    counts["total"] += 1
                except json.JSONDecodeError:
                    counts["invalid"] += 1
                    continue
                
                # 提取签名并检查相似度
                is_benchmark = False
                if category == "SFT":
                    record_sig = _extract_record_signature_sft(record)
                    is_benchmark = _is_similar_sft(record_sig, benchmark_sigs, benchmark_full)
                else:  # PT
                    record_sig = _extract_record_signature_pt(record)
                    is_benchmark = _is_similar_pt(record_sig, benchmark_sigs, benchmark_full)
                
                if is_benchmark:
                    counts["benchmark_removed"] += 1
                    counts["invalid"] += 1
                else:
                    fout.write(json.dumps(record, ensure_ascii=False) + '\n')
                    counts["valid"] += 1
        
        return counts
    
    try:
        if not os.path.exists(data_path):
            logger.error(f"Data path does not exist: {data_path}")
            result.success = False
            result.error_message = f"Data path does not exist: {data_path}"
            return result
        
        if os.path.isfile(data_path):
            if not data_path.endswith(".jsonl"):
                logger.warning(f"Unsupported file format (expect .jsonl): {data_path}")
                result.success = False
                result.error_message = f"Unsupported file format (expect .jsonl): {data_path}"
                return result
            
            base_dir = os.path.dirname(data_path)
            base_name = os.path.basename(data_path)
            name, ext = os.path.splitext(base_name)
            cleaned_path = os.path.join(base_dir, f"{name}_benchmark_cleaned{ext}")
            
            counts = _process_single_file(data_path, cleaned_path)
            
            result.total_records = counts["total"]
            result.valid_records = counts["valid"]
            result.invalid_records = counts["invalid"]
            
            if result.valid_records > 0:
                result.cleaned_data_path = cleaned_path
                logger.info(f"benchmark_data_cleaner: Cleaned data saved to {cleaned_path}")
                logger.info(f"benchmark_data_cleaner: Removed {counts['benchmark_removed']} benchmark-similar records")
            else:
                if os.path.exists(cleaned_path):
                    os.remove(cleaned_path)
                logger.warning("No valid records after benchmark cleaning, keeping original file path")
                result.cleaned_data_path = data_path
        
        elif os.path.isdir(data_path):
            jsonl_files = [
                f for f in os.listdir(data_path)
                if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
            ]
            if not jsonl_files:
                logger.warning(f"No JSONL files found in directory: {data_path}")
                result.success = False
                result.error_message = f"No JSONL files found in directory: {data_path}"
                return result
            
            cleaned_dir = f"{data_path}_benchmark_cleaned"
            os.makedirs(cleaned_dir, exist_ok=True)
            
            total_benchmark_removed = 0
            any_valid = False
            for filename in jsonl_files:
                input_path = os.path.join(data_path, filename)
                output_path = os.path.join(cleaned_dir, filename)
                
                try:
                    counts = _process_single_file(input_path, output_path)
                    
                    result.total_records += counts["total"]
                    result.valid_records += counts["valid"]
                    result.invalid_records += counts["invalid"]
                    total_benchmark_removed += counts["benchmark_removed"]
                    
                    if counts["valid"] > 0:
                        any_valid = True
                    else:
                        if os.path.exists(output_path):
                            os.remove(output_path)
                            
                except Exception as e:
                    logger.warning(f"Error processing file {input_path}: {e}")
                    continue
            
            if any_valid:
                result.cleaned_data_path = cleaned_dir
                logger.info(f"benchmark_data_cleaner: Cleaned data saved to {cleaned_dir}")
                logger.info(f"benchmark_data_cleaner: Total removed {total_benchmark_removed} benchmark-similar records")
            else:
                shutil.rmtree(cleaned_dir, ignore_errors=True)
                logger.warning("No valid records after benchmark cleaning, keeping original directory path")
                result.cleaned_data_path = data_path
        else:
            logger.warning(f"Unsupported data path type: {data_path}")
            result.success = False
            result.error_message = f"Unsupported data path type: {data_path}"
            return result
        
        logger.info(
            f"benchmark_data_cleaner: Cleaning completed - "
            f"total: {result.total_records}, "
            f"valid: {result.valid_records}, "
            f"invalid: {result.invalid_records}"
        )
        
    except Exception as e:
        logger.error(f"benchmark_data_cleaner: Error cleaning data from {data_path}: {e}")
        result.cleaned_data_path = data_path
        result.success = False
        result.error_message = str(e)
    
    return result
