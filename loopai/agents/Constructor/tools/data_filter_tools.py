"""
Data filter tools for data cleaning subgraph.
These tools perform various cleaning operations on data files.
Currently implemented as Mock functions for testing.
"""
import os
import sys
import json
import random
import shutil
import sqlite3
import re
import asyncio
import ast
import hashlib
import time
import copy
import tempfile
import importlib.util
import types
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from pydantic import BaseModel, Field
from loopai.agents.BaseAgent.base_agent import BaseAgent
from loopai.agents.Constructor.utils.openai_compat_chat import (
    OpenAIChatParams,
    chat_completion_async,
)

logger = get_logger()

DEFAULT_LLM_TIMEOUT_SECONDS = 300.0

# 改写 prompt 中单条「待处理记录」JSON 文本最大字符数（与 UTF-8 多字节无关，按 Python str 长度）
REWRITE_RECORD_JSON_MAX_CHARS_DEFAULT = 50000


def rewrite_record_prompt_json_max_chars(constructor: Optional[Dict[str, Any]] = None) -> int:
    """constructor.sharegpt_rewrite_max_raw_chars > 环境变量 CONSTRUCTOR_SHAREGPT_REWRITE_MAX_RAW_CHARS > 默认 50000。"""
    c = constructor or {}
    raw = c.get("sharegpt_rewrite_max_raw_chars")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    try:
        return max(
            1,
            int(
                os.getenv(
                    "CONSTRUCTOR_SHAREGPT_REWRITE_MAX_RAW_CHARS",
                    str(REWRITE_RECORD_JSON_MAX_CHARS_DEFAULT),
                )
            ),
        )
    except ValueError:
        return REWRITE_RECORD_JSON_MAX_CHARS_DEFAULT


def truncate_json_for_llm_prompt(obj: Any, max_chars: int) -> str:
    """将对象序列化为 JSON 字符串；超过 max_chars 时尾部截断并附说明（供 LLM 读，不要求合法 JSON）。"""
    raw = json.dumps(obj, ensure_ascii=False)
    n = len(raw)
    if n <= max_chars:
        return raw
    note = f"\n...[record JSON truncated: original_chars={n}, max={max_chars}]"
    budget = max_chars - len(note)
    if budget < 64:
        note = "\n...[truncated]"
        budget = max_chars - len(note)
    return raw[: max(0, budget)] + note


async def _await_llm_with_timeout(
    llm,
    messages,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    op_name: str = "llm_call"
):
    """
    对 LLM 调用增加统一超时保护，避免单个请求长期挂起拖死整批任务。
    """
    timeout = float(timeout_seconds) if timeout_seconds else DEFAULT_LLM_TIMEOUT_SECONDS
    if timeout <= 0:
        timeout = DEFAULT_LLM_TIMEOUT_SECONDS
    try:
        # Route ChatOpenAI calls through OpenAI-compatible HTTP client to avoid
        # LangGraph messages-stream propagation into Starter stream_message.
        if isinstance(llm, ChatOpenAI):
            base_url = (
                getattr(llm, "openai_api_base", None)
                or getattr(llm, "base_url", None)
                or ""
            )
            model_name = (
                getattr(llm, "model_name", None)
                or getattr(llm, "model", None)
                or ""
            )
            api_key_raw = getattr(llm, "openai_api_key", None) or getattr(llm, "api_key", None)
            if hasattr(api_key_raw, "get_secret_value"):
                api_key = api_key_raw.get_secret_value()
            else:
                api_key = str(api_key_raw or "")
            temperature = getattr(llm, "temperature", 0.0)
            top_p = getattr(llm, "top_p", 0.95)
            max_tokens = (
                getattr(llm, "max_tokens", None)
                or getattr(llm, "max_completion_tokens", None)
                or 4096
            )
            params = OpenAIChatParams(
                model=str(model_name),
                base_url=str(base_url),
                api_key=str(api_key),
                temperature=float(temperature if temperature is not None else 0.0),
                top_p=float(top_p if top_p is not None else 0.95),
                max_completion_tokens=int(max_tokens),
            )
            return await chat_completion_async(params, messages, timeout_seconds=timeout)
        return await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout)
    except asyncio.TimeoutError as e:
        raise TimeoutError(f"{op_name} timed out after {timeout:.1f}s") from e

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

    async def prebuild_schema(
        self,
        schema: str,
        user_query: str,
        assistant_sql: str,
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    ) -> str:
        """
        Schema预构建：在进入建库验证之前，使用LLM对schema进行初步修改。
        确保schema不包含最终答案SQL，保留或补充外键，评估与user需求的匹配度。
        """
        few_shot = (
            "Few-shot examples for schema pre-building:\n\n"
            "--- Example 1 (answer leakage removal) ---\n"
            "User: How many singers are from France?\n"
            "Schema:\n"
            "CREATE TABLE singer(Singer_ID INT PRIMARY KEY, Name TEXT, Country TEXT);\n"
            "SELECT count(*) FROM singer WHERE Country = 'France';\n"
            "Problem: Schema contains the answer SQL query.\n"
            "Fixed Schema:\n"
            "CREATE TABLE singer(Singer_ID INT PRIMARY KEY, Name TEXT, Country TEXT);\n"
            "Rule: Remove any SELECT/INSERT/UPDATE/DELETE statements from schema. Keep only DDL.\n\n"
            "--- Example 2 (hardcoded answer in schema) ---\n"
            "User: What is the total revenue for the last 3 years?\n"
            "Schema:\n"
            "CREATE TABLE revenue(id INT, year INT, amount REAL);\n"
            "CREATE VIEW last3 AS SELECT * FROM revenue WHERE year BETWEEN 2021 AND 2023;\n"
            "Problem: Schema hardcodes the answer logic (year range).\n"
            "Fixed Schema:\n"
            "CREATE TABLE revenue(id INT, year INT, amount REAL);\n"
            "Rule: Remove views/triggers that embed answer logic. Keep base tables only.\n\n"
            "--- Example 3 (foreign key needed) ---\n"
            "User: List all albums with their artist names.\n"
            "Schema:\n"
            "CREATE TABLE artist(artist_id INT PRIMARY KEY, name TEXT);\n"
            "CREATE TABLE album(album_id INT PRIMARY KEY, title TEXT, artist_id INT);\n"
            "Problem: album.artist_id lacks FOREIGN KEY reference to artist.\n"
            "Fixed Schema:\n"
            "CREATE TABLE artist(artist_id INT PRIMARY KEY, name TEXT);\n"
            "CREATE TABLE album(album_id INT PRIMARY KEY, title TEXT, artist_id INT, "
            "FOREIGN KEY (artist_id) REFERENCES artist(artist_id));\n"
            "Rule: When tables are related and query requires JOIN, ensure FK constraints exist.\n\n"
            "--- Example 4 (schema matches user, no change needed) ---\n"
            "User: How many employees work in each department?\n"
            "Schema:\n"
            "CREATE TABLE department(dept_id INT PRIMARY KEY, dept_name TEXT);\n"
            "CREATE TABLE employee(emp_id INT PRIMARY KEY, name TEXT, dept_id INT, "
            "FOREIGN KEY (dept_id) REFERENCES department(dept_id));\n"
            "Analysis: Schema is clean and aligned with user query.\n"
            "Fixed Schema: (unchanged)\n"
            "Rule: If schema is already correct, return it unchanged.\n\n"
            "--- Example 5 (schema missing fields for user query) ---\n"
            "User: What is the average temperature per year in each region?\n"
            "Schema:\n"
            "CREATE TABLE measurement(id INT PRIMARY KEY, region TEXT, temperature REAL);\n"
            "Problem: User asks 'per year' but schema has no year/date column.\n"
            "Fixed Schema:\n"
            "CREATE TABLE measurement(id INT PRIMARY KEY, region TEXT, temperature REAL, year INT);\n"
            "Rule: Add missing columns that are clearly implied by the user's query.\n"
        )

        prompt = (
            "You are a SQLite schema expert performing pre-build cleanup.\n\n"
            "Your task:\n"
            "1. REMOVE any answer SQL (SELECT/INSERT/UPDATE/DELETE) or hardcoded answer logic from the schema.\n"
            "2. PRESERVE all existing FOREIGN KEY constraints.\n"
            "3. ADD FOREIGN KEY constraints if tables are clearly related and the user's query requires JOINs.\n"
            "4. CHECK if the schema covers all columns/tables needed by the user's query. "
            "If a column is clearly implied (e.g. user asks 'per year' but no year column), add it.\n"
            "5. Output ONLY valid SQLite DDL (CREATE TABLE/INDEX/PRAGMA). No DML, no views embedding answers.\n\n"
            f"{few_shot}\n"
            f"Now process:\n"
            f"User Question: {user_query}\n"
            f"Reference SQL: {assistant_sql}\n\n"
            f"Current Schema:\n```sql\n{schema}\n```\n\n"
            "Output ONLY the cleaned SQLite DDL schema. No markdown, no explanation."
        )

        try:
            messages = [
                SystemMessage(content="You are a SQLite schema expert. Output only valid DDL statements."),
                HumanMessage(content=prompt)
            ]
            response = await _await_llm_with_timeout(
                self.llm, messages, timeout_seconds, "text2sql_prebuild_schema"
            )
            content = response.content
            content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            return content.strip()
        except Exception as e:
            logger.error(f"Error in schema prebuild: {e}")
            return schema

    async def repair_schema(
        self,
        schema: str,
        error_msg: str,
        user_query: str,
        sql: str,
        alignment_hint: str = "",
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    ) -> str:
        """
        修复Schema。当 alignment_hint 非空时，表示 schema 需要补充缺失字段以满足查询意图。
        """
        few_shot = (
            "Few-shot rules:\n"
            "Example A (bad schema - contains answer SQL):\n"
            "Schema: CREATE TABLE t(a INT); SELECT * FROM t;\n"
            "Why bad: system schema includes answer SQL leakage.\n"
            "Example A (good):\n"
            "CREATE TABLE t(a INT);\n"
            "Rule: Output DDL/PRAGMA only, never include target SQL query.\n\n"
            "Example B (bad schema - hardcoded answer logic):\n"
            "CREATE TABLE orders(id INT, created_at TEXT);\n"
            "User asks last 5 years, schema appends WHERE year BETWEEN 2018 AND 2023.\n"
            "Why bad: hardcoded answer logic in schema.\n"
            "Example B (good):\n"
            "CREATE TABLE orders(id INT, created_at TEXT);\n"
            "Rule: keep schema pure and leave reasoning to SQL generation.\n\n"
            "Example C (schema does not match user needs):\n"
            "User: What is the average temperature per year?\n"
            "Schema: CREATE TABLE Ocean(id INT, region VARCHAR(20), temperature DECIMAL(5,2));\n"
            "Problem: User asks 'per year' but schema has no year/date column.\n"
            "Hint: Add a year INT column to Ocean table for temporal grouping.\n"
            "Fixed: CREATE TABLE Ocean(id INT, region VARCHAR(20), temperature DECIMAL(5,2), year INT);\n"
            "Rule: When alignment hint says fields are missing, ADD them to the schema.\n\n"
            "Example D (foreign key preservation):\n"
            "User: List albums with artist names.\n"
            "Schema: CREATE TABLE artist(id INT PK, name TEXT); CREATE TABLE album(id INT, title TEXT, artist_id INT);\n"
            "Fixed: CREATE TABLE artist(id INT PK, name TEXT); "
            "CREATE TABLE album(id INT, title TEXT, artist_id INT, FOREIGN KEY(artist_id) REFERENCES artist(id));\n"
            "Rule: Preserve existing FKs and add missing FKs when JOINs are needed.\n\n"
            "Example E (redundant ID column - BAD):\n"
            "Schema: CREATE TABLE artist(id INT PRIMARY KEY, name VARCHAR(50));\n"
            "Need: artists_valuation table needs FK to artist.\n"
            "Bad fix: CREATE TABLE artist(id INT PRIMARY KEY, name VARCHAR(50), artist_id INT);\n"
            "Why bad: artist already has 'id' as PK. Adding 'artist_id' is redundant.\n"
            "Good fix: CREATE TABLE artists_valuation(id INT, artist_id INT REFERENCES artist(id), ...);\n"
            "Rule: NEVER add redundant *_id columns to entity tables that already have a PK. "
            "Use FK references from the dependent table to the existing PK instead.\n"
        )
        alignment_block = ""
        if alignment_hint:
            alignment_block = (
                f"\n*** Schema Alignment Issue ***\n"
                f"{alignment_hint}\n"
                f"The schema is syntactically valid but MISSING columns/tables needed by the user's query.\n"
                f"You MUST add the missing columns/tables described above while keeping existing columns intact.\n"
            )
        prompt = (
            f"You are a SQL expert. The following SQLite schema needs repair.\n"
            f"Error message: {error_msg}\n\n"
            f"Context:\n"
            f"User Question: {user_query}\n"
            f"Target SQL: {sql}\n\n"
            f"{alignment_block}"
            f"{few_shot}\n"
            f"Current Schema:\n```sql\n{schema}\n```\n\n"
            f"Please provide a corrected, valid SQLite schema (DDL) that satisfies the query and SQL.\n"
            f"Output ONLY the SQL DDL statements, without any markdown formatting or explanation."
        )
        
        try:
            messages = [
                SystemMessage(content="You are a helpful SQL expert assistant who fixes broken SQLite schemas."),
                HumanMessage(content=prompt)
            ]
            response = await _await_llm_with_timeout(
                self.llm, messages, timeout_seconds, "text2sql_repair_schema"
            )
            content = response.content
            # 清理可能的Markdown标记
            content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            return content.strip()
        except Exception as e:
            logger.error(f"Error repairing schema: {e}")
            return schema


class Text2SQLExecutionRepairAgent(BaseAgent):
    """
    专门用于Text2SQL SQL语句执行修复的Agent。
    当SQL语句在真实建库后执行失败时，根据schema、用户问题和错误信息迭代修复SQL。
    """
    @property
    def role_name(self) -> str:
        return "Text2SQLExecutionRepair"

    @property
    def system_prompt_type(self) -> str:
        return "system"

    @property
    def system_prompt_name(self) -> str:
        return "text2sql_execution_repair"

    def init_graph(self):
        pass

    def __call__(self):
        pass

    async def repair_sql(
        self,
        schema: str,
        sql: str,
        error_msg: str,
        user_query: str,
        expected_operation: str = "",
        evidence_text: str = "",
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    ) -> str:
        """
        修复执行失败的SQL语句。

        Args:
            schema: 已成功建库的DDL schema
            sql: 执行失败的SQL语句
            error_msg: SQLite返回的错误信息
            user_query: 用户原始自然语言问题

        Returns:
            修复后的SQL语句
        """
        few_shot = (
            "Few-shot rules (BIRD benchmark, SQLite dialect):\n"
            "1) Output MUST be a SELECT query (or WITH...SELECT CTE). "
            "NEVER output INSERT/UPDATE/DELETE/CREATE/ALTER/DROP.\n"
            "2) Never invent constants not grounded in question/evidence.\n"
            "3) For time expressions like last N years, follow Evidence time anchor.\n"
            "4) If evidence defines a formula (e.g. 'metric = A * B'), the SQL MUST use that exact formula.\n"
            "5) If a column was added for a purpose (e.g. passenger_count for multiplication), the SQL MUST use it.\n"
            "6) All non-aggregate columns in SELECT MUST appear in GROUP BY (ANSI strict mode).\n"
            "7) Use only SQLite-compatible functions and syntax.\n\n"
            "--- Bad example 1 ---\n"
            "SQL: DELETE FROM orders WHERE id = 1002\n"
            "Why bad: Not a SELECT query; also 1002 never appears in input.\n"
            "--- Bad example 2 ---\n"
            "SQL: UPDATE stats SET cnt = (SELECT count(*) FROM t)\n"
            "Why bad: Modifies the database. BIRD only allows read-only queries.\n"
            "--- Bad example 3 ---\n"
            "Evidence says total_revenue = fare * passenger_count, SQL uses SUM(fare) only.\n"
            "Why bad: Ignores the formula from evidence.\n"
            "--- Bad example 4 ---\n"
            "SELECT name, SUM(amount) FROM t GROUP BY id (name not in GROUP BY).\n"
            "Why bad: Violates ANSI strict grouping.\n\n"
            "--- Good example 1 ---\n"
            "SELECT SUM(fare * passenger_count) AS total_revenue FROM rides\n"
            "Why good: Uses evidence formula, pure SELECT, valid SQLite.\n"
            "--- Good example 2 ---\n"
            "SELECT name, SUM(amount) FROM t GROUP BY id, name\n"
            "Why good: All non-aggregate columns in GROUP BY.\n"
            "--- Good example 3 ---\n"
            "WITH ranked AS (SELECT *, ROW_NUMBER() OVER(PARTITION BY dept ORDER BY salary DESC) rn FROM emp) "
            "SELECT * FROM ranked WHERE rn = 1\n"
            "Why good: CTE-based query, pure read, valid SQLite.\n"
        )
        operation_guard = f"Expected SQL operation: {expected_operation}\n" if expected_operation else ""
        evidence_block = f"Evidence:\n{evidence_text}\n\n" if evidence_text else ""
        prompt = (
            f"You are a SQL expert. The following SQL query failed validation on a valid SQLite database.\n\n"
            f"Database Schema:\n```sql\n{schema}\n```\n\n"
            f"User Question: {user_query}\n\n"
            f"{evidence_block}"
            f"{operation_guard}"
            f"Failed SQL:\n```sql\n{sql}\n```\n\n"
            f"Error/Violation: {error_msg}\n\n"
            f"{few_shot}\n"
            f"Please provide a corrected SQL query that:\n"
            f"1. Is valid SQLite syntax\n"
            f"2. Correctly references tables and columns defined in the schema\n"
            f"3. Fully answers the user's question using ALL relevant columns and evidence formulas\n"
            f"4. Strictly matches the expected operation type when provided\n"
            f"5. Includes ALL non-aggregate SELECT columns in the GROUP BY clause\n"
            f"6. Implements any formula or computation specified in the evidence\n"
            f"Output ONLY the SQL query, without any markdown formatting or explanation."
        )

        try:
            messages = [
                SystemMessage(content="You are a helpful SQL expert assistant who fixes broken SQL queries based on database schema and error messages."),
                HumanMessage(content=prompt)
            ]
            response = await _await_llm_with_timeout(
                self.llm, messages, timeout_seconds, "text2sql_execution_repair"
            )
            content = response.content
            content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            return content.strip()
        except Exception as e:
            logger.error(f"Error repairing SQL: {e}")
            return sql


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
            response = await _await_llm_with_timeout(
                self.llm, messages_list, DEFAULT_LLM_TIMEOUT_SECONDS, "codegen_analyze_record"
            )
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
            domain_response = await _await_llm_with_timeout(
                self.llm, domain_messages, DEFAULT_LLM_TIMEOUT_SECONDS, "codegen_domain_check"
            )
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
            response = await _await_llm_with_timeout(
                self.llm, messages, DEFAULT_LLM_TIMEOUT_SECONDS, "codegen_repair_code"
            )
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
        return (
            "You are an expert in analyzing dialogue datasets. Your task is to determine if a conversation "
            "record is related to a specific domain query. Be conservative when filtering: keep records unless "
            "they are clearly unrelated. If relevance is weak, indirect, partial, ambiguous, or uncertain, mark "
            "the record as related."
        )

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
            f"Filtering policy (important):\n"
            f"- Keep if there is any plausible, weak, indirect, partial, or contextual relevance.\n"
            f"- Keep if uncertain or information is insufficient.\n"
            f"- Only mark as unrelated when it is clearly and definitively outside the domain.\n"
            f"Return a JSON object with:\n"
            f'{{"is_related": true/false, "reasoning": "brief explanation"}}\n'
            f"Only return the JSON object, no other text."
        )
        
        try:
            messages_list = [
                SystemMessage(content=self.compute_prompt()),
                HumanMessage(content=prompt)
            ]
            response = await _await_llm_with_timeout(
                self.llm, messages_list, DEFAULT_LLM_TIMEOUT_SECONDS, "normal_domain_analyze_record"
            )
            content = response.content.strip()
            
            # 清理可能的 markdown 标记
            content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            content = content.strip()
            
            result = json.loads(content)
            return {
                "is_related": result.get("is_related", True),
                "reasoning": result.get("reasoning", "")
            }
        except Exception as e:
            logger.error(f"Error analyzing record: {e}")
            # Fail-open to avoid accidental data loss during domain filtering.
            return {"is_related": True, "reasoning": f"Error (kept by default): {str(e)}"}


class NormalDomainQueryRewriteAgent(BaseAgent):
    """
    用于将用户原始 query 改写为更适合领域相关性召回的检索描述。
    """

    @property
    def role_name(self) -> str:
        return "NormalDomainQueryRewrite"

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
        return (
            "You are an expert in domain query rewriting for dataset filtering. "
            "Rewrite the query to maximize recall while preserving original intent. "
            "Do not narrow the domain. Expand with semantically related terms and coverage hints. "
            "Output valid JSON only."
        )

    async def rewrite_query(self, user_query: str) -> Dict[str, str]:
        """
        对 user_query 进行语义扩写，生成更利于召回的领域描述。
        """
        prompt = (
            "Rewrite the following domain query for high-recall dataset filtering.\n\n"
            f"Original Query: {user_query}\n\n"
            "Requirements:\n"
            "1) Preserve the original intent and task goal.\n"
            "2) Expand domain scope with close, semantically related subdomains and terminology.\n"
            "3) Keep it concise and practical for relevance matching.\n"
            "4) Avoid introducing unrelated domains.\n"
            "Return JSON only with keys:\n"
            '{"rewritten_query":"...", "domain_focus":"...", "keep_policy_note":"..."}'
        )
        try:
            messages_list = [
                SystemMessage(content=self.compute_prompt()),
                HumanMessage(content=prompt),
            ]
            response = await _await_llm_with_timeout(
                self.llm, messages_list, DEFAULT_LLM_TIMEOUT_SECONDS, "normal_domain_query_rewrite"
            )
            content = response.content.strip()
            content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            content = content.strip()
            result = json.loads(content)
            rewritten_query = str(result.get("rewritten_query", "")).strip()
            return {
                "rewritten_query": rewritten_query or user_query,
                "domain_focus": str(result.get("domain_focus", "")).strip(),
                "keep_policy_note": str(result.get("keep_policy_note", "")).strip(),
            }
        except Exception as e:
            logger.error(f"Error rewriting domain query: {e}")
            return {
                "rewritten_query": user_query,
                "domain_focus": "",
                "keep_policy_note": f"fallback_to_original_due_to_error: {str(e)}",
            }


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
            response = await _await_llm_with_timeout(
                self.llm, messages, DEFAULT_LLM_TIMEOUT_SECONDS, "normal_improve_and_validate"
            )
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
    diagnostics: Dict[str, Any] = Field(default_factory=dict)  # 结构化诊断信息


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

    def _is_valid_messages_sft_record(record: Dict[str, Any]) -> bool:
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
            # system 角色允许 content 为空，其他角色不允许
            if role_lower != "system" and not _has_non_empty_content(content):
                return False
            # 检查 role（支持大小写不敏感）
            if role_lower == "user":
                has_user = True
            elif role_lower == "assistant":
                has_assistant = True
        return has_user and has_assistant

    def _is_valid_sharegpt_sft_record(record: Dict[str, Any]) -> bool:
        """ShareGPT / LlamaFactory 对话格式：conversations[].from / value"""
        if not isinstance(record, dict):
            return False
        conversations = record.get("conversations")
        if not isinstance(conversations, list) or not conversations:
            return False
        has_human = False
        has_gpt = False
        for turn in conversations:
            if not isinstance(turn, dict):
                return False
            from_ = turn.get("from")
            if not isinstance(from_, str) or not from_.strip():
                return False
            value = turn.get("value")
            if not _has_non_empty_content(value):
                return False
            fl = from_.lower()
            if fl == "human":
                has_human = True
            elif fl == "gpt":
                has_gpt = True
        return has_human and has_gpt

    def _is_valid_sft_record(record: Dict[str, Any]) -> bool:
        return _is_valid_messages_sft_record(record) or _is_valid_sharegpt_sft_record(record)

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
        constructor_state = state.get("constructor", {})
        category = constructor_state.get("category", "PT").upper()
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


def _extract_message_content(record: Dict[str, Any], role: str) -> str:
    messages = record.get("messages", [])
    if isinstance(messages, list):
        for msg in messages:
            if str(msg.get("role", "")).lower() == role.lower():
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(str(x) for x in content)
                return str(content)
    # 兼容顶层字段
    if role.lower() == "system":
        return str(record.get("system", ""))
    if role.lower() == "assistant":
        return str(record.get("assistant", ""))
    if role.lower() == "user":
        return str(record.get("user", "") or record.get("instruction", ""))
    return ""


def _upsert_message_content(record: Dict[str, Any], role: str, content: str) -> None:
    messages = record.get("messages", [])
    if not isinstance(messages, list):
        messages = []
        record["messages"] = messages
    for msg in messages:
        if str(msg.get("role", "")).lower() == role.lower():
            msg["content"] = content
            return
    messages.append({"role": role.lower(), "content": content})

    if role.lower() == "system":
        record["system"] = content
    if role.lower() == "assistant":
        record["assistant"] = content


def _strip_sql_markdown(sql_text: str) -> str:
    content = sql_text or ""
    content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
    content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
    content = re.sub(r'```$', '', content, flags=re.MULTILINE)
    return content.strip()


def _split_sql_statements(sql_text: str) -> List[str]:
    # 简单分句：按分号拆分并去除空语句，足够覆盖当前清洗场景
    return [stmt.strip() for stmt in (sql_text or "").split(";") if stmt.strip()]


def _sql_op_type(sql_text: str) -> str:
    sql = (sql_text or "").strip()
    if not sql:
        return "UNKNOWN"
    first = re.match(r"^\s*([A-Za-z]+)", sql)
    if not first:
        return "UNKNOWN"
    op = first.group(1).upper()
    if op == "WITH":
        return "SELECT"
    return op


def _sanitize_schema_and_detect_leakage(schema_sql: str, assistant_sql: str) -> Tuple[str, bool, str]:
    """
    清理 system schema 中混入的答案 SQL。
    策略：仅检测 assistant SQL 在 system 中的直接泄露（高重合/包含关系），
    不再把 SELECT/INSERT 等操作类型作为泄露规则——因为 DDL 注释和示例中常含 SELECT。
    """
    statements = _split_sql_statements(_strip_sql_markdown(schema_sql))
    kept: List[str] = []
    leakage_reasons: List[str] = []
    asst_norm = re.sub(r"\s+", " ", _strip_sql_markdown(assistant_sql)).strip().lower()

    for stmt in statements:
        norm_stmt = re.sub(r"\s+", " ", stmt).strip().lower()
        leaked = False
        if asst_norm and norm_stmt and len(norm_stmt) > 10:
            if norm_stmt == asst_norm or norm_stmt in asst_norm or asst_norm in norm_stmt:
                leaked = True
                leakage_reasons.append("assistant_sql_leakage")
        if not leaked:
            kept.append(stmt)

    sanitized = ";\n".join(kept).strip()
    if sanitized and not sanitized.endswith(";"):
        sanitized += ";"
    leakage_detected = len(leakage_reasons) > 0
    reason = ",".join(sorted(set(leakage_reasons))) if leakage_reasons else ""
    return sanitized, leakage_detected, reason


def _extract_schema_tables_and_columns(schema_sql: str) -> Tuple[List[str], List[str]]:
    table_pattern = re.compile(r'CREATE\s+TABLE\s+"?([A-Za-z_][\w]*)"?', re.IGNORECASE)
    # 兼容 "col" TYPE 或 col TYPE
    col_pattern = re.compile(r'^\s*"?(?P<col>[A-Za-z_][\w\s]*)"?\s+[A-Za-z]+', re.IGNORECASE)
    tables = table_pattern.findall(schema_sql or "")
    cols: List[str] = []
    for line in (schema_sql or "").splitlines():
        line = line.strip().rstrip(",")
        if not line or line.upper().startswith(("CREATE TABLE", "PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT", ");")):
            continue
        m = col_pattern.match(line)
        if m:
            col = m.group("col").strip().replace(" ", "_")
            cols.append(col)
    return sorted(set(tables))[:20], sorted(set(cols))[:40]


def _extract_year_anchor(user_text: str) -> Optional[str]:
    m = re.search(r'last\s+(\d+)\s+years?', user_text, flags=re.IGNORECASE)
    if m:
        n = m.group(1)
        # 默认给出显式时间锚点，避免模型盲目硬编码
        return f"Current year is 2023, so last {n} years means [2023-{n}+1, 2023]."
    return None


def _build_evidence_prefix(user_text: str, schema_sql: str, sql_error: str = "") -> str:
    tables, columns = _extract_schema_tables_and_columns(schema_sql)
    evidence_parts = []
    year_anchor = _extract_year_anchor(user_text or "")
    if year_anchor:
        evidence_parts.append(year_anchor)
    if tables:
        evidence_parts.append(f"Allowed tables: {', '.join(tables)}.")
    if columns:
        evidence_parts.append(f"Allowed columns (partial): {', '.join(columns[:20])}.")
    if sql_error:
        evidence_parts.append(f"Execution feedback: {sql_error}")
    if not evidence_parts:
        return ""
    return "Evidence: " + " ".join(evidence_parts)


def _classify_user_intent(user_text: str) -> str:
    text = (user_text or "").lower()
    if any(w in text for w in ["delete", "remove", "drop"]):
        return "DELETE"
    if any(w in text for w in ["update", "set "]):
        return "UPDATE"
    if any(w in text for w in ["insert", "add ", "create record"]):
        return "INSERT"
    if any(w in text for w in ["create table", "build table", "new table"]):
        return "DDL"
    return "SELECT"


def _is_intent_sql_aligned(user_intent: str, sql_op: str) -> bool:
    sql_op = (sql_op or "").upper()
    intent = (user_intent or "SELECT").upper()
    if intent == "DDL":
        return sql_op in {"CREATE", "ALTER", "DROP"}
    if intent == "SELECT":
        return sql_op in {"SELECT", "WITH"}
    return intent == sql_op


def _extract_numeric_literals(text: str) -> List[str]:
    return re.findall(r"\b\d{1,4}\b", text or "")


def _has_suspicious_hardcode(sql: str, user_text: str, evidence_text: str) -> bool:
    sql_nums = set(_extract_numeric_literals(sql))
    if not sql_nums:
        return False
    allowed = set(_extract_numeric_literals(user_text)) | set(_extract_numeric_literals(evidence_text))
    # 0/1 等布尔样式常量不做拦截
    allowed |= {"0", "1"}
    return any(num not in allowed for num in sql_nums)


def _extract_alignment_constraints(record: Dict[str, Any], evidence_prefix: str) -> Dict[str, Any]:
    """
    从 record.diagnostics 和 evidence_prefix 提取应被 SQL 落实的诊断约束。
    返回 {"hints": [...], "evidence_statements": [...], "has_constraints": bool}
    """
    diag = record.get("diagnostics", {})
    hints = []
    evidence_statements = []

    regen_hint = diag.get("alignment_regen_hint", "")
    if regen_hint:
        hints.append(regen_hint)

    missing = diag.get("alignment_missing_fields", [])
    for f in missing:
        hints.append(f)

    generated_ev = diag.get("generated_evidence", "")
    if generated_ev:
        for sentence in re.split(r'[.\n]', generated_ev):
            s = sentence.strip()
            if s:
                evidence_statements.append(s)

    if evidence_prefix:
        for sentence in re.split(r'[.\n]', evidence_prefix):
            s = sentence.strip()
            if s and s.lower().startswith("evidence"):
                evidence_statements.append(s)

    has = bool(hints or evidence_statements)
    return {"hints": hints, "evidence_statements": evidence_statements, "has_constraints": has}


async def _is_sql_semantically_synchronized(
    sql: str,
    constraints: Dict[str, Any],
    schema_sql: str,
    user_query: str,
    llm,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
) -> Tuple[bool, str]:
    """
    用 LLM 审核最终 SQL 是否落实了诊断约束。
    约束来自 alignment 阶段：缺失字段 hint、evidence 公式等。
    返回 (is_synchronized, failure_reason)。
    """
    if not constraints.get("has_constraints", False):
        return True, ""

    hints_text = "\n".join(f"- {h}" for h in constraints.get("hints", []))
    evidence_text = "\n".join(f"- {e}" for e in constraints.get("evidence_statements", []))
    prompt = (
        "You are a strict SQL label quality auditor.\n"
        "A data-cleaning pipeline diagnosed that the schema needed changes and/or evidence was generated "
        "to guide the SQL. Your job is to verify whether the FINAL SQL actually implements those constraints.\n\n"
        "Constraints from diagnosis:\n"
        f"Alignment hints:\n{hints_text or '(none)'}\n\n"
        f"Evidence / formula definitions:\n{evidence_text or '(none)'}\n\n"
        f"User question: {user_query}\n\n"
        f"Database schema:\n```sql\n{schema_sql}\n```\n\n"
        f"Final SQL:\n```sql\n{sql}\n```\n\n"
        "Check:\n"
        "1. If hints say a column was added for a purpose (e.g. 'year for temporal grouping'), "
        "the SQL MUST use that column (e.g. GROUP BY year).\n"
        "2. If evidence defines a formula (e.g. 'metric = A + B'), "
        "the SQL MUST use that formula, not a simplified version.\n"
        "3. If a column was added to support multiplication (e.g. 'fare * passenger_count'), "
        "the SQL MUST include that multiplication.\n\n"
        'Output ONLY a JSON object: {"synchronized": true/false, "reason": "..."}\n'
        "If synchronized, reason should be empty string. No markdown, no explanation."
    )
    try:
        messages = [
            SystemMessage(content="You are a strict SQL label quality auditor. Output valid JSON only."),
            HumanMessage(content=prompt)
        ]
        response = await _await_llm_with_timeout(
            llm, messages, timeout_seconds, "sql_semantic_sync_check"
        )
        content = response.content.strip()
        content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'```$', '', content, flags=re.MULTILINE)
        result = json.loads(content.strip())
        synced = result.get("synchronized", True)
        reason = result.get("reason", "")
        return bool(synced), str(reason)
    except Exception as e:
        logger.warning(f"Semantic sync check failed, defaulting to synced: {e}")
        return True, ""


def _detect_redundant_id_injection(old_schema: str, new_schema: str) -> Tuple[bool, str]:
    """
    检测 schema repair 是否向已有实体表注入了冗余的 *_id 列。
    例如 artist 表已有 id 主键，却新增了 artist_id 列。
    返回 (has_pollution, reason)
    """
    old_tables: Dict[str, set] = {}
    new_tables: Dict[str, set] = {}

    table_re = re.compile(r'CREATE\s+TABLE\s+"?(\w+)"?\s*\(', re.IGNORECASE)
    col_re = re.compile(r'^\s*"?(\w+)"?\s+\w+', re.IGNORECASE)
    pk_re = re.compile(r'PRIMARY\s+KEY', re.IGNORECASE)
    fk_re = re.compile(r'FOREIGN\s+KEY|REFERENCES|CONSTRAINT', re.IGNORECASE)

    def _parse_tables(ddl: str) -> Dict[str, set]:
        tables = {}
        current_table = None
        for line in ddl.splitlines():
            tm = table_re.search(line)
            if tm:
                current_table = tm.group(1).lower()
                tables[current_table] = set()
                remainder = line[tm.end():]
                for cm in col_re.finditer(remainder):
                    c = cm.group(1).lower()
                    if not pk_re.match(c) and not fk_re.match(c):
                        tables[current_table].add(c)
                continue
            if current_table and line.strip() and not line.strip().startswith(')'):
                if pk_re.search(line) or fk_re.search(line):
                    continue
                cm = col_re.match(line)
                if cm:
                    c = cm.group(1).lower()
                    tables[current_table].add(c)
        return tables

    old_tables = _parse_tables(old_schema or "")
    new_tables = _parse_tables(new_schema or "")

    pollution_reasons = []
    for tname, new_cols in new_tables.items():
        old_cols = old_tables.get(tname, set())
        added = new_cols - old_cols
        if not added:
            continue
        has_id_pk = "id" in old_cols
        for col in added:
            if col.endswith("_id") and has_id_pk:
                base = col[:-3]
                if base == tname or base in tname or tname in base:
                    pollution_reasons.append(
                        f"Redundant column '{col}' added to table '{tname}' which already has 'id' as PK. "
                        f"Use FK reference to {tname}.id instead."
                    )
    if pollution_reasons:
        return True, "; ".join(pollution_reasons)
    return False, ""


def _violates_strict_grouping(sql: str) -> Tuple[bool, str]:
    """
    检测聚合查询中 SELECT 里的非聚合列是否全部出现在 GROUP BY 中。
    返回 (violates, violation_detail)
    """
    sql_upper = (sql or "").upper()
    if "GROUP BY" not in sql_upper:
        return False, ""

    agg_funcs = {"COUNT", "SUM", "AVG", "MIN", "MAX", "GROUP_CONCAT", "TOTAL"}
    agg_pattern = re.compile(
        r'\b(?:' + '|'.join(agg_funcs) + r')\s*\(', re.IGNORECASE
    )

    select_m = re.search(r'\bSELECT\b(.*?)\bFROM\b', sql, re.IGNORECASE | re.DOTALL)
    if not select_m:
        return False, ""
    select_clause = select_m.group(1)

    group_m = re.search(r'\bGROUP\s+BY\b(.+?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|;|$)', sql, re.IGNORECASE | re.DOTALL)
    if not group_m:
        return False, ""
    group_clause = group_m.group(1)

    def _strip_aliases(clause: str) -> List[str]:
        """Extract column references from a comma-separated clause, stripping AS aliases."""
        parts = []
        depth = 0
        current = []
        for ch in clause:
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())
        return parts

    select_items = _strip_aliases(select_clause)
    group_cols_raw = _strip_aliases(group_clause)
    group_cols = set()
    for g in group_cols_raw:
        cleaned = re.sub(r'\s+AS\s+\w+', '', g, flags=re.IGNORECASE).strip().lower()
        group_cols.add(cleaned)

    missing = []
    for item in select_items:
        if agg_pattern.search(item):
            continue
        if item.strip() == '*':
            continue
        cleaned = re.sub(r'\s+AS\s+\w+', '', item, flags=re.IGNORECASE).strip().lower()
        if not cleaned:
            continue
        if cleaned not in group_cols:
            aliases_check = cleaned.split('.')[-1]
            if aliases_check not in group_cols:
                missing.append(cleaned)

    if missing:
        return True, f"Non-aggregate columns not in GROUP BY: {', '.join(missing)}"
    return False, ""


async def _llm_validate_text2sql_semantics(
    user_query: str,
    schema_sql: str,
    assistant_sql: str,
    llm,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    """
    统一 LLM 语义审核器，替代高误报规则判定。
    审核：泄露、JOIN 合法性（基于 REFERENCES）、方言兼容性、是否纯查询。
    返回 JSON:
      {
        "is_query_sql": bool,       # 仅 SELECT/WITH
        "has_leakage": bool,        # system 是否泄露 answer
        "join_alignment_ok": bool,  # JOIN 关系是否合法
        "dialect_issues": [...],    # SQLite 方言兼容性警告
        "issues": [...],            # 结构化问题列表
        "fix_suggestion": ""        # 可选修复建议
      }
    """
    prompt = (
        "You are a strict Text-to-SQL data quality auditor for the BIRD benchmark.\n"
        "Given a user question, a database schema (DDL), and an assistant-generated SQL, "
        "audit the data sample for the following issues.\n\n"
        "## Checks\n"
        "1. **is_query_sql**: Is the assistant SQL purely a query (SELECT or WITH...SELECT)? "
        "UPDATE/INSERT/DELETE/DDL statements are NOT acceptable for BIRD.\n"
        "2. **has_leakage**: Does the schema (system field) contain the assistant's answer SQL "
        "or substantial fragments of it? DDL comments or example data do NOT count as leakage.\n"
        "3. **join_alignment_ok**: Are all JOINs in the SQL valid given the schema's "
        "FOREIGN KEY ... REFERENCES declarations and actual column existence? "
        "IMPORTANT: Joins like `artist.id = artists_valuation.artist_id` are VALID when "
        "`artists_valuation(artist_id REFERENCES artist(id))` exists in the DDL. "
        "Columns do NOT need to have the same name to form a valid join.\n"
        "4. **dialect_issues**: List any non-SQLite type names or functions that might cause "
        "problems (e.g. TIMESTAMP, AUTO_INCREMENT). Note: VARCHAR, DECIMAL are tolerated by "
        "SQLite's type affinity and should only be flagged as low-severity warnings, not errors.\n\n"
        "## Few-shot examples\n\n"
        "--- Example 1 (valid join, no issue) ---\n"
        "Schema: CREATE TABLE artist(id INT PRIMARY KEY, name VARCHAR(50));\n"
        "        CREATE TABLE artists_valuation(id INT, artist_id INT REFERENCES artist(id), value REAL);\n"
        "SQL: SELECT a.name, v.value FROM artist a JOIN artists_valuation v ON a.id = v.artist_id\n"
        'Output: {"is_query_sql": true, "has_leakage": false, "join_alignment_ok": true, '
        '"dialect_issues": ["VARCHAR(50) is non-standard SQLite but tolerated via type affinity"], '
        '"issues": [], "fix_suggestion": ""}\n\n'
        "--- Example 2 (DML, not a query) ---\n"
        "SQL: UPDATE users SET status = 'active' WHERE id = 1\n"
        'Output: {"is_query_sql": false, "has_leakage": false, "join_alignment_ok": true, '
        '"dialect_issues": [], "issues": ["Assistant SQL is UPDATE, not a query"], '
        '"fix_suggestion": ""}\n\n'
        "--- Example 3 (answer leakage) ---\n"
        "Schema: CREATE TABLE t(a INT); SELECT count(*) FROM t;\n"
        "SQL: SELECT count(*) FROM t\n"
        'Output: {"is_query_sql": true, "has_leakage": true, "join_alignment_ok": true, '
        '"dialect_issues": [], "issues": ["Schema contains the answer SQL"], '
        '"fix_suggestion": "Remove SELECT count(*) FROM t from schema"}\n\n'
        "Now audit the following:\n\n"
        f"User Question: {user_query}\n\n"
        f"Schema (system field):\n```sql\n{schema_sql}\n```\n\n"
        f"Assistant SQL:\n```sql\n{assistant_sql}\n```\n\n"
        "Output ONLY a single JSON object with the keys above. No markdown, no explanation."
    )
    default = {
        "is_query_sql": True, "has_leakage": False, "join_alignment_ok": True,
        "dialect_issues": [], "issues": [], "fix_suggestion": ""
    }
    try:
        messages = [
            SystemMessage(content="You are a Text-to-SQL data quality auditor for BIRD benchmark. Output valid JSON only."),
            HumanMessage(content=prompt)
        ]
        response = await _await_llm_with_timeout(
            llm, messages, timeout_seconds, "text2sql_semantic_audit"
        )
        content = response.content.strip()
        content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'```$', '', content, flags=re.MULTILINE)
        result = json.loads(content.strip())
        for key in default:
            if key not in result:
                result[key] = default[key]
        return result
    except Exception as e:
        logger.warning(f"LLM semantic validation failed, defaulting to pass: {e}")
        return default


async def _check_schema_query_alignment(
    user_query: str,
    schema_sql: str,
    assistant_sql: str,
    llm,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    """
    用 LLM 判断 schema 是否能完整覆盖 user query 的语义意图。
    返回:
      {
        "aligned": bool,
        "missing_fields": [...],   # 需要 schema 补列
        "evidence_gaps": [...],    # 需要补充 Evidence 定义
        "regen_hint": str          # 传给 schema repair 的修复建议
      }
    """
    prompt = (
        "You are a strict Text-to-SQL data quality auditor.\n"
        "Given a user question, a database schema (DDL), and an expected SQL answer, "
        "determine whether the schema contains ALL columns/tables needed to FULLY satisfy the user's query intent.\n\n"
        "Check for TWO types of problems:\n"
        "1. **missing_fields**: The user's question implies columns or tables that do NOT exist in the schema. "
        "For example, if the user asks 'per year' but the schema has no year/date column, that is a missing field.\n"
        "2. **evidence_gaps**: The user's question uses domain-specific terms or requires formulas/definitions "
        "that are NOT self-evident from the schema alone. "
        "For example, 'sustainability metric' requires a formula definition like "
        "'sustainability_metric = carbon_footprint + water_usage + waste_generation'.\n\n"
        "Few-shot examples:\n\n"
        "--- Example 1 (missing field) ---\n"
        "User: What is the average sea surface temperature in the Pacific Ocean per year?\n"
        "Schema: CREATE TABLE Ocean(id INT, region VARCHAR(20), temperature DECIMAL(5,2));\n"
        "SQL: SELECT AVG(temperature) FROM Ocean WHERE region LIKE '%Pacific%'\n"
        "Analysis: User asks 'per year' requiring GROUP BY on a temporal column, but schema has no year/date column.\n"
        'Output: {"aligned": false, "missing_fields": ["year or date column in Ocean table for temporal grouping"], '
        '"evidence_gaps": [], '
        '"regen_hint": "Add a year INT or record_date DATE column to the Ocean table to support per-year aggregation"}\n\n'
        "--- Example 2 (evidence gap) ---\n"
        "User: Which regions have the highest and lowest sustainability metrics for products?\n"
        "Schema: CREATE TABLE products(id INT, region TEXT, carbon_footprint REAL, water_usage REAL, waste_generation REAL);\n"
        "SQL: SELECT region, MAX(carbon_footprint + water_usage + waste_generation) ...\n"
        "Analysis: 'sustainability metric' is a domain-specific composite concept; the formula must be provided as evidence.\n"
        'Output: {"aligned": false, "missing_fields": [], '
        '"evidence_gaps": ["Definition of sustainability_metric: e.g. carbon_footprint + water_usage + waste_generation"], '
        '"regen_hint": ""}\n\n'
        "--- Example 3 (aligned) ---\n"
        "User: How many singers do we have?\n"
        "Schema: CREATE TABLE singer(Singer_ID INT, Name TEXT, Country TEXT, Age INT);\n"
        "SQL: SELECT count(*) FROM singer\n"
        "Analysis: Schema has the singer table; count(*) needs no extra columns.\n"
        'Output: {"aligned": true, "missing_fields": [], "evidence_gaps": [], "regen_hint": ""}\n\n'
        "--- Example 4 (schema contains answer SQL - misaligned) ---\n"
        "User: What is the total number of orders?\n"
        "Schema: CREATE TABLE orders(id INT, product TEXT, amount REAL); SELECT count(*) FROM orders;\n"
        "SQL: SELECT count(*) FROM orders\n"
        "Analysis: Schema embeds the answer SQL 'SELECT count(*) FROM orders'. "
        "The DDL itself is aligned but it leaks the answer.\n"
        'Output: {"aligned": false, "missing_fields": [], "evidence_gaps": [], '
        '"regen_hint": "Remove embedded SELECT count(*) FROM orders from schema DDL"}\n\n'
        "Now analyze the following:\n\n"
        f"User Question: {user_query}\n\n"
        f"Schema:\n```sql\n{schema_sql}\n```\n\n"
        f"Expected SQL: {assistant_sql}\n\n"
        "Output ONLY a single JSON object with keys: aligned, missing_fields, evidence_gaps, regen_hint.\n"
        "No markdown, no explanation."
    )
    default_result = {"aligned": True, "missing_fields": [], "evidence_gaps": [], "regen_hint": ""}
    try:
        messages = [
            SystemMessage(content="You are a Text-to-SQL schema completeness auditor. Output valid JSON only."),
            HumanMessage(content=prompt)
        ]
        response = await _await_llm_with_timeout(
            llm, messages, timeout_seconds, "schema_query_alignment_check"
        )
        content = response.content.strip()
        content = re.sub(r'^```json\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'```$', '', content, flags=re.MULTILINE)
        result = json.loads(content.strip())
        for key in ("aligned", "missing_fields", "evidence_gaps", "regen_hint"):
            if key not in result:
                result[key] = default_result[key]
        return result
    except Exception as e:
        logger.warning(f"Schema-query alignment check failed, defaulting to aligned: {e}")
        return default_result


async def _generate_evidence_for_gaps(
    user_query: str,
    schema_sql: str,
    evidence_gaps: List[str],
    llm,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
) -> str:
    """
    对 evidence_gaps 中列出的模糊概念，用 LLM 生成明确的 Evidence 文本
    （公式定义、术语解释等），注入到 user prompt。
    """
    gaps_text = "\n".join(f"- {g}" for g in evidence_gaps)
    prompt = (
        "You are a domain knowledge assistant for Text-to-SQL tasks.\n"
        "The user's question references concepts that are ambiguous without explicit definitions.\n"
        "Based on the database schema and the listed knowledge gaps, "
        "generate concise evidence statements that a SQL model would need to write correct SQL.\n\n"
        "Rules:\n"
        "- Each evidence statement should be a single clear sentence or formula.\n"
        "- Use column names from the schema when defining formulas.\n"
        "- Do NOT generate SQL. Only generate plain-text definitions.\n\n"
        "Few-shot example:\n"
        "Gaps: Definition of sustainability_metric\n"
        "Schema columns: carbon_footprint, water_usage, waste_generation\n"
        "Evidence: sustainability_metric is defined as (carbon_footprint + water_usage + waste_generation).\n\n"
        f"User Question: {user_query}\n\n"
        f"Schema:\n```sql\n{schema_sql}\n```\n\n"
        f"Knowledge gaps to fill:\n{gaps_text}\n\n"
        "Output ONLY the evidence text (one or more sentences). No markdown, no JSON."
    )
    try:
        messages = [
            SystemMessage(content="You are a domain knowledge assistant. Output plain-text evidence definitions only."),
            HumanMessage(content=prompt)
        ]
        response = await _await_llm_with_timeout(
            llm, messages, timeout_seconds, "generate_evidence_for_gaps"
        )
        return response.content.strip()
    except Exception as e:
        logger.warning(f"Evidence generation failed: {e}")
        return ""


async def _regenerate_compliant_sql(
    schema_sql: str,
    user_query: str,
    original_sql: str,
    issue_description: str,
    llm,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
) -> str:
    """
    当 assistant SQL 不合规（非 SELECT/WITH、含 mutation 操作等）时，
    使用 LLM 重新生成一条符合 BIRD 基准的 SQLite 查询语句。
    """
    few_shot = (
        "Few-shot examples for SQL regeneration (BIRD benchmark, SQLite):\n\n"
        "--- Example 1 (non-query to query) ---\n"
        "User: How many students are enrolled?\n"
        "Original SQL: INSERT INTO stats(cnt) SELECT count(*) FROM student\n"
        "Issue: SQL is INSERT, not a pure query. BIRD requires SELECT-only.\n"
        "Regenerated: SELECT count(*) FROM student\n\n"
        "--- Example 2 (mutation to query) ---\n"
        "User: Show active users\n"
        "Original SQL: UPDATE users SET status='active'; SELECT * FROM users WHERE status='active'\n"
        "Issue: SQL contains UPDATE which modifies the database.\n"
        "Regenerated: SELECT * FROM users WHERE status = 'active'\n\n"
        "--- Example 3 (DDL to query) ---\n"
        "User: Create a summary of sales by region\n"
        "Original SQL: CREATE VIEW summary AS SELECT region, SUM(amount) FROM sales GROUP BY region\n"
        "Issue: SQL is CREATE VIEW, not a query.\n"
        "Regenerated: SELECT region, SUM(amount) FROM sales GROUP BY region\n\n"
        "--- Example 4 (DELETE to query) ---\n"
        "User: Which orders were placed before 2020?\n"
        "Original SQL: DELETE FROM orders WHERE order_date < '2020-01-01'\n"
        "Issue: SQL is DELETE, not a query.\n"
        "Regenerated: SELECT * FROM orders WHERE order_date < '2020-01-01'\n\n"
        "--- Example 5 (leakage-tainted SQL) ---\n"
        "User: What is the average salary per department?\n"
        "Original SQL: SELECT dept, AVG(salary) FROM emp GROUP BY dept\n"
        "Issue: LLM audit flagged schema leakage; regenerate to ensure SQL is self-contained.\n"
        "Regenerated: SELECT dept, AVG(salary) FROM emp GROUP BY dept\n"
    )

    prompt = (
        "You are a SQL expert for the BIRD benchmark (SQLite).\n"
        "The original SQL is non-compliant and needs to be regenerated.\n\n"
        "STRICT RULES:\n"
        "1. Output MUST start with SELECT (or WITH...SELECT for CTEs)\n"
        "2. Output MUST NOT contain INSERT/UPDATE/DELETE/CREATE/ALTER/DROP\n"
        "3. Output MUST be valid SQLite syntax\n"
        "4. Output MUST correctly answer the user's question using the given schema\n"
        "5. All non-aggregate columns in SELECT must appear in GROUP BY\n\n"
        f"{few_shot}\n"
        f"Database Schema:\n```sql\n{schema_sql}\n```\n\n"
        f"User Question: {user_query}\n\n"
        f"Original SQL: {original_sql}\n"
        f"Issue: {issue_description}\n\n"
        "Output ONLY the corrected SELECT query. No markdown, no explanation."
    )

    try:
        messages = [
            SystemMessage(content="You are a SQL expert for BIRD benchmark. Output only valid SQLite SELECT queries."),
            HumanMessage(content=prompt)
        ]
        response = await _await_llm_with_timeout(
            llm, messages, timeout_seconds, "regenerate_compliant_sql"
        )
        content = response.content
        content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'```$', '', content, flags=re.MULTILINE)
        return content.strip()
    except Exception as e:
        logger.warning(f"SQL regeneration failed: {e}")
        return original_sql


# =====================================================================
# Text2SQL Checkpoint / Resume Infrastructure
# =====================================================================

_TEXT2SQL_STAGES = [
    "load_data",
    "schema_prebuild",
    "schema_verify",
    "schema_repair",
    "alignment_check",
    "alignment_regen",
    "sql_audit",
    "sql_regen",
    "sql_exec_verify",
    "sql_repair",
    "semantic_sync",
    "finalize",
]


def _ckpt_paths(file_path: str) -> Dict[str, str]:
    """Derive checkpoint / partial / state-snapshot paths from input file."""
    base, ext = os.path.splitext(file_path)
    return {
        "progress": f"{base}_text2sql_progress.json",
        "partial": f"{base}_text2sql_cleaned.partial.jsonl",
        "state": f"{base}_text2sql_state.snapshot.json",
    }


def _file_signature(file_path: str) -> Dict[str, Any]:
    """Lightweight file identity: path + size + mtime + head-hash."""
    stat = os.stat(file_path)
    head_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        head_hash.update(f.read(8192))
    return {
        "path": os.path.abspath(file_path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "head_md5": head_hash.hexdigest(),
    }


def _safe_serialize(obj: Any) -> Any:
    """Best-effort JSON-safe conversion for state snapshots."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError, OverflowError):
        return str(obj)


def _atomic_json_write(path: str, data: Any) -> None:
    """Write JSON atomically via tmp + rename + fsync."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _save_text2sql_checkpoint(
    file_path: str,
    stage: str,
    batch_index: int,
    file_diag: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Overwrite the progress checkpoint for *file_path*."""
    paths = _ckpt_paths(file_path)
    payload: Dict[str, Any] = {
        "version": 1,
        "file_signature": _file_signature(file_path),
        "stage": stage,
        "batch_index": batch_index,
        "file_diag": copy.deepcopy(file_diag),
        "ts": time.time(),
    }
    if extra:
        payload["extra"] = _safe_serialize(extra)
    _atomic_json_write(paths["progress"], payload)
    logger.debug(
        f"[checkpoint] saved stage={stage} batch={batch_index} -> {paths['progress']}"
    )


def _save_partial_records(file_path: str, records: List[Dict]) -> None:
    """Overwrite the partial-cleaned JSONL for *file_path*."""
    paths = _ckpt_paths(file_path)
    tmp = paths["partial"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, paths["partial"])
    logger.debug(f"[checkpoint] saved {len(records)} partial records -> {paths['partial']}")


def _save_state_snapshot(file_path: str, state: Any) -> None:
    """Persist a JSON-safe snapshot of the LoopAI state dict."""
    paths = _ckpt_paths(file_path)
    payload = {
        "version": 1,
        "ts": time.time(),
        "state": _safe_serialize(dict(state) if hasattr(state, "items") else state),
    }
    _atomic_json_write(paths["state"], payload)
    logger.debug(f"[checkpoint] state snapshot -> {paths['state']}")


def _load_checkpoint(file_path: str) -> Optional[Dict[str, Any]]:
    """Load checkpoint if it exists and matches the current input file."""
    paths = _ckpt_paths(file_path)
    prog_path = paths["progress"]
    if not os.path.exists(prog_path):
        return None
    try:
        with open(prog_path, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
    except Exception as e:
        logger.warning(f"[checkpoint] failed to read {prog_path}: {e}")
        return None

    saved_sig = ckpt.get("file_signature", {})
    try:
        cur_sig = _file_signature(file_path)
    except Exception:
        return None

    if (
        saved_sig.get("path") != cur_sig["path"]
        or saved_sig.get("size") != cur_sig["size"]
        or saved_sig.get("head_md5") != cur_sig["head_md5"]
    ):
        backup = prog_path + f".bak.{int(time.time())}"
        logger.warning(
            f"[checkpoint] file signature mismatch – backing up old checkpoint to {backup}"
        )
        os.replace(prog_path, backup)
        return None

    return ckpt


def _load_partial_records(file_path: str) -> Optional[List[Dict]]:
    """Load previously saved partial-cleaned records."""
    paths = _ckpt_paths(file_path)
    partial_path = paths["partial"]
    if not os.path.exists(partial_path):
        return None
    records: List[Dict] = []
    try:
        with open(partial_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        return records
    except Exception as e:
        logger.warning(f"[checkpoint] failed to read partial file {partial_path}: {e}")
        return None


def _stage_gte(a: str, b: str) -> bool:
    """Return True if stage *a* >= stage *b* in the pipeline ordering."""
    try:
        return _TEXT2SQL_STAGES.index(a) >= _TEXT2SQL_STAGES.index(b)
    except ValueError:
        return False


def _cleanup_checkpoint_files(file_path: str) -> None:
    """Remove checkpoint / partial files after successful completion."""
    for p in _ckpt_paths(file_path).values():
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

# =====================================================================
# End checkpoint infrastructure
# =====================================================================


def domain_text2sql_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    领域工具：针对Text2SQL数据的特定清洗 (并发优化版)

    1. 针对message里面的role：system的context（schema）先用LLM预构建清理，再尝试建库
    2. 建库失败的调用Agent尝试根据role：assistant，user的context去理解题目并尝试生成建库的schema
    3. 循环修复直到成功或达到上限（3次）
    4. 对assistant SQL进行合规预处理（非查询类SQL通过LLM重生为SELECT）
    5. 执行role：assistant内的sql语句进行真实操作，不报错的数据保留

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
    BATCH_SIZE = 502  # 设置并发 Batch 大小
    agent_config = state.get("constructor", {}) or {}
    llm_timeout = float(agent_config.get("llm_timeout", DEFAULT_LLM_TIMEOUT_SECONDS) or DEFAULT_LLM_TIMEOUT_SECONDS)
    if llm_timeout <= 0:
        llm_timeout = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_audit_batch_size = max(1, int(agent_config.get("llm_audit_batch_size", BATCH_SIZE) or BATCH_SIZE))
    sync_check_batch_size = max(1, int(agent_config.get("sync_check_batch_size", BATCH_SIZE) or BATCH_SIZE))
    
    # 初始化修复 Agent
    repair_agent = None
    sql_repair_agent = None
    try:
        repair_agent = Text2SQLRepairAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
        sql_repair_agent = Text2SQLExecutionRepairAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
    except Exception as e:
        logger.error(f"Failed to initialize Text2SQL Agents: {e}")
        raise e

    aggregate_diag = {
        "schema_prebuild_count": 0,
        "leakage_fixed_count": 0,
        "evidence_injected_count": 0,
        "intent_mismatch_fixed_count": 0,
        "hardcode_flagged_count": 0,
        "schema_alignment_regen_count": 0,
        "evidence_gap_injected_count": 0,
        "schema_alignment_final_drop_count": 0,
        "semantic_desync_flagged_count": 0,
        "semantic_desync_final_drop_count": 0,
        "schema_pollution_blocked_count": 0,
        "strict_grouping_fixed_count": 0,
        "llm_semantic_checked_count": 0,
        "llm_leakage_flagged_count": 0,
        "llm_join_misalignment_count": 0,
        "non_query_sql_dropped_count": 0,
        "sql_preprocess_regen_count": 0,
        "sql_preprocess_regen_success_count": 0,
        "sqlite_dialect_warning_count": 0,
        "final_drop_reasons": {}
    }

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

    def _test_sql_execution(schema_sql: str, query_sql: str) -> Tuple[bool, str]:
        """测试SQL是否能在Schema上执行，返回 (成功与否, 错误信息)"""
        try:
            conn = sqlite3.connect(':memory:')
            cursor = conn.cursor()
            cursor.executescript(schema_sql)
            cursor.execute(query_sql)
            conn.close()
            return True, ""
        except Exception as e:
            return False, str(e)

    async def _repair_single_item(item, file_diag):
        """
        处理单条数据的修复逻辑：调用Agent -> 验证 -> 更新记录
        支持 alignment_hint 用于语义对齐修复场景。
        包含冗余 *_id 列注入检测（Schema 污染防护）。
        """
        record = item["record"]
        current_schema = next((m.get("content", "") for m in record.get("messages", []) if m.get("role") == "system"), "")
        if not current_schema:
            current_schema = record.get("system", "")
        
        try:
            new_schema = await repair_agent.repair_schema(
                schema=current_schema,
                error_msg=item["error"],
                user_query=item["user_content"],
                sql=item["assistant_content"],
                alignment_hint=item.get("alignment_hint", ""),
                timeout_seconds=llm_timeout
            )
            
            success, error_msg = _test_schema_creation(new_schema)
            
            if success:
                pollution, pollution_reason = _detect_redundant_id_injection(current_schema, new_schema)
                if pollution:
                    item["error"] = f"schema_pollution_redundant_id: {pollution_reason}"
                    file_diag["schema_pollution_blocked_count"] += 1
                    record.setdefault("diagnostics", {})
                    record["diagnostics"]["schema_pollution_blocked"] = pollution_reason
                    return False

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
                item["error"] = None
                return True
            else:
                item["error"] = error_msg
                return False

        except Exception as e:
            item["error"] = f"Agent Error: {str(e)}"
            return False

    # === 内部函数：处理单个文件 ===
    async def _process_single_file(file_path: str) -> Tuple[List[Dict], int, int, int, Dict[str, Any]]:
        """
        处理单个JSONL文件，返回 (有效记录列表, 总数, 有效数, 无效数, diagnostics)。
        支持 checkpoint/resume：每个 batch 完成后落盘，中断后自动续跑。
        """
        basename = os.path.basename(file_path)
        _DIAG_DEFAULTS = {
            "schema_prebuild_count": 0,
            "leakage_fixed_count": 0,
            "evidence_injected_count": 0,
            "intent_mismatch_fixed_count": 0,
            "hardcode_flagged_count": 0,
            "schema_alignment_regen_count": 0,
            "evidence_gap_injected_count": 0,
            "schema_alignment_final_drop_count": 0,
            "semantic_desync_flagged_count": 0,
            "semantic_desync_final_drop_count": 0,
            "schema_pollution_blocked_count": 0,
            "strict_grouping_fixed_count": 0,
            "llm_semantic_checked_count": 0,
            "llm_leakage_flagged_count": 0,
            "llm_join_misalignment_count": 0,
            "non_query_sql_dropped_count": 0,
            "sql_preprocess_regen_count": 0,
            "sql_preprocess_regen_success_count": 0,
            "sqlite_dialect_warning_count": 0,
            "final_drop_reasons": {}
        }
        file_diag = dict(_DIAG_DEFAULTS)

        alignment_llm = repair_agent.llm

        # ------------------------------------------------------------------
        # Checkpoint / Resume setup
        # ------------------------------------------------------------------
        ckpt = _load_checkpoint(file_path)
        completed_stages: set = set()
        all_records: Optional[List[Dict]] = None
        resumed = False

        if ckpt:
            completed_stages = set(ckpt.get("extra", {}).get("completed_stages", []))
            saved_diag = ckpt.get("file_diag")
            if saved_diag and isinstance(saved_diag, dict):
                merged = dict(_DIAG_DEFAULTS)
                merged.update(saved_diag)
                file_diag = merged
            partial = _load_partial_records(file_path)
            if partial is not None:
                all_records = partial
                resumed = True
                logger.info(
                    f"[{basename}] RESUMED from checkpoint: "
                    f"completed_stages={sorted(completed_stages)}, records={len(all_records)}"
                )

        if all_records is None:
            all_records = []
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            all_records.append(json.loads(line))
                        except Exception:
                            pass

        file_total = len(all_records)

        def _ckpt_save(stage: str, batch_idx: int = 0) -> None:
            """Persist checkpoint + partial records after every batch."""
            _save_text2sql_checkpoint(
                file_path, stage, batch_idx, file_diag,
                extra={"completed_stages": sorted(completed_stages)},
            )
            _save_partial_records(file_path, all_records)

        # ==================================================================
        # Stage: load_data — rule preprocessing (leakage stripping)
        # ==================================================================
        if "load_data" not in completed_stages:
            for record in all_records:
                system_content = _extract_message_content(record, "system")
                assistant_content = _strip_sql_markdown(
                    _extract_message_content(record, "assistant")
                )
                sanitized_schema, leakage_detected, leakage_reason = (
                    _sanitize_schema_and_detect_leakage(
                        schema_sql=system_content,
                        assistant_sql=assistant_content,
                    )
                )
                if leakage_detected:
                    file_diag["leakage_fixed_count"] += 1
                    _upsert_message_content(record, "system", sanitized_schema)
                    record.setdefault("diagnostics", {})
                    record["diagnostics"]["leakage_detected"] = True
                    record["diagnostics"]["leakage_reason"] = leakage_reason

            completed_stages.add("load_data")
            _ckpt_save("load_data")
            logger.info(f"[{basename}] Stage load_data complete ({file_total} records)")
        else:
            logger.info(f"[{basename}] Skipping load_data (resumed)")

        # Build prebuild_items from (possibly resumed) records
        prebuild_items = []
        for record in all_records:
            prebuild_items.append({
                "record": record,
                "system_content": _extract_message_content(record, "system"),
                "user_content": _extract_message_content(record, "user"),
                "assistant_content": _strip_sql_markdown(
                    _extract_message_content(record, "assistant")
                ),
            })

        # ==================================================================
        # Stage: schema_prebuild — LLM Schema prebuild (batched)
        # ==================================================================
        if "schema_prebuild" not in completed_stages:
            logger.info(
                f"[{basename}] LLM Schema Prebuild: {len(prebuild_items)} items"
            )

            async def _prebuild_single(item):
                try:
                    return await repair_agent.prebuild_schema(
                        schema=item["system_content"],
                        user_query=item["user_content"],
                        assistant_sql=item["assistant_content"],
                    )
                except Exception as e:
                    logger.warning(f"Schema prebuild failed, keeping original: {e}")
                    return item["system_content"]

            total_pb = len(prebuild_items)
            for i in range(0, total_pb, BATCH_SIZE):
                batch = prebuild_items[i : i + BATCH_SIZE]
                batch_num = i // BATCH_SIZE + 1
                total_batches = (total_pb + BATCH_SIZE - 1) // BATCH_SIZE
                logger.info(
                    f"  > Prebuild batch {batch_num}/{total_batches} "
                    f"(size: {len(batch)})"
                )
                prebuild_tasks = [_prebuild_single(it) for it in batch]
                prebuild_results = await asyncio.gather(
                    *prebuild_tasks, return_exceptions=True
                )
                for it, pb_result in zip(batch, prebuild_results):
                    if isinstance(pb_result, Exception):
                        logger.warning(
                            f"Schema prebuild exception, keeping original: {pb_result}"
                        )
                    else:
                        it["system_content"] = pb_result
                        _upsert_message_content(it["record"], "system", pb_result)
                        file_diag["schema_prebuild_count"] += 1
                _ckpt_save("schema_prebuild", batch_num)

            completed_stages.add("schema_prebuild")
            _ckpt_save("schema_prebuild")
            logger.info(f"[{basename}] Stage schema_prebuild complete")
        else:
            logger.info(f"[{basename}] Skipping schema_prebuild (resumed)")
            for item in prebuild_items:
                item["system_content"] = _extract_message_content(
                    item["record"], "system"
                )

        # ==================================================================
        # Stage: schema_verify — fast sqlite tests + evidence construction
        # Always rebuild (deterministic & fast)
        # ==================================================================
        processed_records = []
        for item in prebuild_items:
            record = item["record"]
            system_content = item["system_content"]
            user_content = item["user_content"]
            assistant_content = item["assistant_content"]

            evidence_prefix = _build_evidence_prefix(user_content, system_content)
            if evidence_prefix and "schema_verify" not in completed_stages:
                file_diag["evidence_injected_count"] += 1
            user_query_with_evidence = (
                f"{evidence_prefix}\nQuestion: {user_content}".strip()
                if evidence_prefix else user_content
            )
            expected_operation = _classify_user_intent(user_content)

            if not system_content.strip():
                success, error_msg = False, "Schema is empty"
            else:
                success, error_msg = _test_schema_creation(system_content)

            if success:
                processed_records.append({
                    "record": record,
                    "valid": True,
                    "user_content": user_query_with_evidence,
                    "assistant_content": assistant_content,
                    "expected_operation": expected_operation,
                    "evidence_prefix": evidence_prefix,
                })
            else:
                processed_records.append({
                    "record": record,
                    "valid": False,
                    "error": error_msg,
                    "retry_count": 0,
                    "user_content": user_query_with_evidence,
                    "assistant_content": assistant_content,
                    "expected_operation": expected_operation,
                    "evidence_prefix": evidence_prefix,
                })

        if "schema_verify" not in completed_stages:
            completed_stages.add("schema_verify")
            _ckpt_save("schema_verify")
            logger.info(f"[{basename}] Stage schema_verify complete")

        # ==================================================================
        # Stage: schema_repair — batched LLM repair (3 retries)
        # ==================================================================
        if "schema_repair" not in completed_stages:
            max_retries = 3
            for attempt in range(max_retries):
                to_repair = [
                    item for item in processed_records
                    if not item["valid"]
                    and item.get("retry_count", 0) < max_retries
                ]
                if not to_repair:
                    break

                logger.info(
                    f"[{basename}] Schema Repair Attempt {attempt + 1}/{max_retries}, "
                    f"items to repair: {len(to_repair)}"
                )

                total_items = len(to_repair)
                for i in range(0, total_items, BATCH_SIZE):
                    batch = to_repair[i : i + BATCH_SIZE]
                    batch_num = i // BATCH_SIZE + 1
                    total_batches = (total_items + BATCH_SIZE - 1) // BATCH_SIZE
                    logger.info(
                        f"  > Processing batch {batch_num}/{total_batches} "
                        f"(size: {len(batch)})"
                    )

                    tasks = []
                    for item in batch:
                        item["retry_count"] = item.get("retry_count", 0) + 1
                        tasks.append(_repair_single_item(item, file_diag))

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    success_count = sum(1 for r in results if r is True)
                    logger.info(
                        f"  > Batch finished. Success: {success_count}/{len(batch)}"
                    )
                    _ckpt_save("schema_repair", attempt * 1000 + batch_num)

            completed_stages.add("schema_repair")
            _ckpt_save("schema_repair")
            logger.info(f"[{basename}] Stage schema_repair complete")
        else:
            logger.info(f"[{basename}] Skipping schema_repair (resumed)")
            for item in processed_records:
                if not item["valid"]:
                    sc = _extract_message_content(item["record"], "system")
                    if sc.strip():
                        ok, err = _test_schema_creation(sc)
                        if ok:
                            item["valid"] = True
                            item.pop("error", None)

        # ==================================================================
        # Stage: alignment — Schema-Query alignment check + regen (batched)
        # ==================================================================
        if "alignment" not in completed_stages:
            ALIGNMENT_MAX_ROUNDS = 2

            for alignment_round in range(ALIGNMENT_MAX_ROUNDS):
                items_to_check = [
                    item for item in processed_records if item["valid"]
                ]
                if not items_to_check:
                    break

                logger.info(
                    f"[{basename}] Schema-Query Alignment Check round "
                    f"{alignment_round + 1}/{ALIGNMENT_MAX_ROUNDS}, "
                    f"items: {len(items_to_check)}"
                )

                ALIGNMENT_BATCH_SIZE = 128
                alignment_results = []
                total_align = len(items_to_check)
                for ai in range(0, total_align, ALIGNMENT_BATCH_SIZE):
                    batch = items_to_check[ai : ai + ALIGNMENT_BATCH_SIZE]
                    ab_num = ai // ALIGNMENT_BATCH_SIZE + 1
                    ab_total = (
                        (total_align + ALIGNMENT_BATCH_SIZE - 1) // ALIGNMENT_BATCH_SIZE
                    )
                    logger.info(
                        f"  > Alignment batch {ab_num}/{ab_total} "
                        f"(size: {len(batch)})"
                    )
                    batch_tasks = []
                    for item in batch:
                        record = item["record"]
                        schema_now = _extract_message_content(record, "system")
                        user_now = _extract_message_content(record, "user")
                        asst_now = _strip_sql_markdown(
                            _extract_message_content(record, "assistant")
                        )
                        batch_tasks.append(
                            _check_schema_query_alignment(
                                user_now,
                                schema_now,
                                asst_now,
                                alignment_llm,
                                timeout_seconds=llm_timeout,
                            )
                        )
                    batch_results = await asyncio.gather(
                        *batch_tasks, return_exceptions=True
                    )
                    alignment_results.extend(batch_results)
                    _ckpt_save(
                        "alignment_check",
                        alignment_round * 1000 + ab_num,
                    )

                items_needing_regen = []
                for item, aresult in zip(items_to_check, alignment_results):
                    if isinstance(aresult, Exception):
                        logger.warning(
                            f"Alignment check exception, skipping: {aresult}"
                        )
                        continue
                    if aresult.get("aligned", True):
                        continue

                    record = item["record"]
                    missing = aresult.get("missing_fields", [])
                    gaps = aresult.get("evidence_gaps", [])
                    regen_hint = aresult.get("regen_hint", "")

                    record.setdefault("diagnostics", {})
                    record["diagnostics"]["schema_user_mismatch"] = True
                    mismatch_reasons = []
                    if missing:
                        mismatch_reasons.append(f"missing_fields: {missing}")
                    if gaps:
                        mismatch_reasons.append(f"evidence_gaps: {gaps}")
                    if regen_hint:
                        mismatch_reasons.append(f"regen_hint: {regen_hint}")
                    record["diagnostics"]["schema_user_mismatch_reason"] = (
                        "; ".join(mismatch_reasons)
                    )

                    if missing and regen_hint:
                        item["valid"] = False
                        item["error"] = f"Schema incomplete: {regen_hint}"
                        item["alignment_hint"] = regen_hint
                        item["retry_count"] = 0
                        item.setdefault("alignment_round", 0)
                        item["alignment_round"] += 1
                        items_needing_regen.append(item)
                        file_diag["schema_alignment_regen_count"] += 1
                        record["diagnostics"]["alignment_missing_fields"] = missing
                        record["diagnostics"]["alignment_regen_hint"] = regen_hint
                    elif missing:
                        item["valid"] = False
                        item["error"] = (
                            f"Schema-user mismatch: missing {missing}"
                        )
                        item["alignment_hint"] = (
                            f"Add missing fields: {', '.join(missing)}"
                        )
                        item["retry_count"] = 0
                        item.setdefault("alignment_round", 0)
                        item["alignment_round"] += 1
                        items_needing_regen.append(item)
                        file_diag["schema_alignment_regen_count"] += 1
                        record["diagnostics"]["alignment_missing_fields"] = missing

                    if gaps:
                        user_now = _extract_message_content(record, "user")
                        schema_now = _extract_message_content(record, "system")
                        extra_evidence = await _generate_evidence_for_gaps(
                            user_now,
                            schema_now,
                            gaps,
                            alignment_llm,
                            timeout_seconds=llm_timeout,
                        )
                        if extra_evidence:
                            old_evidence = item.get("evidence_prefix", "")
                            item["evidence_prefix"] = (
                                old_evidence + " " + extra_evidence
                            ).strip()
                            new_user = (
                                f"{item['evidence_prefix']}\n"
                                f"Question: {user_now}"
                            ).strip()
                            item["user_content"] = new_user
                            file_diag["evidence_gap_injected_count"] += 1
                            record.setdefault("diagnostics", {})
                            record["diagnostics"]["evidence_gaps_filled"] = gaps
                            record["diagnostics"][
                                "generated_evidence"
                            ] = extra_evidence

                if not items_needing_regen:
                    logger.info(
                        f"[{basename}] All items aligned after round "
                        f"{alignment_round + 1}"
                    )
                    break

                logger.info(
                    f"[{basename}] {len(items_needing_regen)} items need "
                    f"schema regen (round {alignment_round + 1})"
                )

                max_retries = 3
                for regen_attempt in range(max_retries):
                    to_regen = [
                        it
                        for it in items_needing_regen
                        if not it["valid"]
                        and it.get("retry_count", 0) < max_retries
                    ]
                    if not to_regen:
                        break
                    logger.info(
                        f"  > Alignment-driven Schema Repair attempt "
                        f"{regen_attempt + 1}/{max_retries}, "
                        f"items: {len(to_regen)}"
                    )
                    total_regen = len(to_regen)
                    for i in range(0, total_regen, BATCH_SIZE):
                        batch = to_regen[i : i + BATCH_SIZE]
                        regen_tasks = []
                        for it in batch:
                            it["retry_count"] = it.get("retry_count", 0) + 1
                            regen_tasks.append(_repair_single_item(it, file_diag))
                        regen_results = await asyncio.gather(
                            *regen_tasks, return_exceptions=True
                        )
                        s_count = sum(1 for r in regen_results if r is True)
                        logger.info(
                            f"  > Regen batch done. Success: "
                            f"{s_count}/{len(batch)}"
                        )
                        _ckpt_save(
                            "alignment_regen",
                            alignment_round * 10000
                            + regen_attempt * 1000
                            + i // BATCH_SIZE
                            + 1,
                        )

            for item in processed_records:
                ar = item.get("alignment_round", 0)
                if ar > 0 and not item["valid"]:
                    file_diag["schema_alignment_final_drop_count"] += 1
                    reason = (
                        item.get("error", "alignment_regen_exhausted")
                        or "alignment_regen_exhausted"
                    )
                    reason_key = f"alignment_drop:{reason[:60]}"
                    file_diag["final_drop_reasons"][reason_key] = (
                        file_diag["final_drop_reasons"].get(reason_key, 0) + 1
                    )

            completed_stages.add("alignment")
            _ckpt_save("alignment")
            logger.info(f"[{basename}] Stage alignment complete")
        else:
            logger.info(f"[{basename}] Skipping alignment (resumed)")

        # ==================================================================
        # Stage: sql_audit — SQL compliance + LLM semantic audit (batched)
        # ==================================================================
        schema_valid_items = [
            item for item in processed_records if item["valid"]
        ]

        if "sql_audit" not in completed_stages:
            SQL_REGEN_MAX_RETRIES = 3
            items_needing_sql_regen = []
            compliant_items = []
            for item in schema_valid_items:
                record = item["record"]
                assistant_content = _strip_sql_markdown(
                    _extract_message_content(record, "assistant")
                )
                sql_op = _sql_op_type(assistant_content)
                if sql_op not in ("SELECT", "UNKNOWN"):
                    file_diag["sql_preprocess_regen_count"] += 1
                    record.setdefault("diagnostics", {})
                    record["diagnostics"]["original_sql_op"] = sql_op
                    items_needing_sql_regen.append({
                        "item": item,
                        "issue": (
                            f"SQL is {sql_op}, not a query. "
                            f"BIRD requires SELECT-only."
                        ),
                        "regen_retry": 0,
                    })
                else:
                    compliant_items.append(item)

            llm_audit_results = []
            total_audit = len(compliant_items)
            for i in range(0, total_audit, llm_audit_batch_size):
                batch = compliant_items[i : i + llm_audit_batch_size]
                ab_num = i // llm_audit_batch_size + 1
                ab_total = (
                    (total_audit + llm_audit_batch_size - 1) // llm_audit_batch_size
                )
                logger.info(
                    f"  > SQL semantic audit batch {ab_num}/{ab_total} "
                    f"(size: {len(batch)})"
                )
                batch_tasks = []
                for item in batch:
                    record = item["record"]
                    system_content = _extract_message_content(record, "system")
                    assistant_content = _strip_sql_markdown(
                        _extract_message_content(record, "assistant")
                    )
                    user_content = _extract_message_content(record, "user")
                    batch_tasks.append(
                        _llm_validate_text2sql_semantics(
                            user_content,
                            system_content,
                            assistant_content,
                            alignment_llm,
                            timeout_seconds=llm_timeout,
                        )
                    )
                batch_results = await asyncio.gather(
                    *batch_tasks, return_exceptions=True
                )
                llm_audit_results.extend(batch_results)
                _ckpt_save("sql_audit", ab_num)
            file_diag["llm_semantic_checked_count"] += len(compliant_items)

            query_only_items = []
            for item, audit in zip(compliant_items, llm_audit_results):
                record = item["record"]
                record.setdefault("diagnostics", {})

                if isinstance(audit, Exception):
                    logger.warning(f"LLM audit exception, skipping: {audit}")
                    record["diagnostics"]["llm_audit_error"] = str(audit)
                    query_only_items.append(item)
                    continue

                needs_regen = False
                regen_issues = []

                if not audit.get("is_query_sql", True):
                    file_diag["non_query_sql_dropped_count"] += 1
                    record["diagnostics"]["llm_non_query_flag"] = True
                    needs_regen = True
                    regen_issues.append(
                        "LLM audit: SQL is not a pure query (SELECT/WITH)"
                    )

                if audit.get("has_leakage", False):
                    file_diag["llm_leakage_flagged_count"] += 1
                    record["diagnostics"]["llm_leakage_flagged"] = True
                    record["diagnostics"]["llm_leakage_issues"] = audit.get(
                        "issues", []
                    )
                    needs_regen = True
                    regen_issues.append(
                        "LLM audit: schema leakage detected, SQL needs regeneration"
                    )

                if not audit.get("join_alignment_ok", True):
                    file_diag["llm_join_misalignment_count"] += 1
                    record["diagnostics"]["llm_join_misalignment"] = True
                    record["diagnostics"]["llm_join_issues"] = audit.get(
                        "issues", []
                    )
                    needs_regen = True
                    regen_issues.append(
                        "LLM audit: JOIN misalignment with schema FK"
                    )

                dialect_issues = audit.get("dialect_issues", [])
                if dialect_issues:
                    file_diag["sqlite_dialect_warning_count"] += 1
                    record["diagnostics"]["sqlite_dialect_warnings"] = (
                        dialect_issues
                    )

                if needs_regen:
                    file_diag["sql_preprocess_regen_count"] += 1
                    items_needing_sql_regen.append({
                        "item": item,
                        "issue": "; ".join(regen_issues),
                        "regen_retry": 0,
                    })
                else:
                    query_only_items.append(item)

            # SQL 重生循环
            if items_needing_sql_regen:
                logger.info(
                    f"[{basename}] SQL Preprocess: "
                    f"{len(items_needing_sql_regen)} items need SQL regeneration"
                )

            async def _do_sql_regen(regen_info):
                it = regen_info["item"]
                rec = it["record"]
                schema_sql = _extract_message_content(rec, "system")
                user_q = _extract_message_content(rec, "user")
                old_sql = _strip_sql_markdown(
                    _extract_message_content(rec, "assistant")
                )
                new_sql = await _regenerate_compliant_sql(
                    schema_sql=schema_sql,
                    user_query=user_q,
                    original_sql=old_sql,
                    issue_description=regen_info["issue"],
                    llm=alignment_llm,
                    timeout_seconds=llm_timeout,
                )
                new_sql = _strip_sql_markdown(new_sql)
                new_op = _sql_op_type(new_sql)
                if new_op in ("SELECT", "UNKNOWN"):
                    _upsert_message_content(rec, "assistant", new_sql)
                    return True
                else:
                    regen_info["issue"] = (
                        f"Regenerated SQL is still {new_op}, not SELECT. "
                        f"Previous issue: {regen_info['issue']}"
                    )
                    return False

            for regen_attempt in range(SQL_REGEN_MAX_RETRIES):
                to_regen = [
                    r
                    for r in items_needing_sql_regen
                    if r["regen_retry"] < SQL_REGEN_MAX_RETRIES
                    and r["item"].get("valid", True)
                ]
                if not to_regen:
                    break

                logger.info(
                    f"  > SQL Regen attempt {regen_attempt + 1}/"
                    f"{SQL_REGEN_MAX_RETRIES}, items: {len(to_regen)}"
                )

                total_regen = len(to_regen)
                for i in range(0, total_regen, BATCH_SIZE):
                    batch = to_regen[i : i + BATCH_SIZE]
                    regen_tasks = []
                    for ri in batch:
                        ri["regen_retry"] += 1
                        regen_tasks.append(_do_sql_regen(ri))
                    regen_results = await asyncio.gather(
                        *regen_tasks, return_exceptions=True
                    )
                    s_count = sum(1 for r in regen_results if r is True)
                    logger.info(
                        f"  > SQL Regen batch done. "
                        f"Success: {s_count}/{len(batch)}"
                    )
                    _ckpt_save(
                        "sql_regen",
                        regen_attempt * 1000 + i // BATCH_SIZE + 1,
                    )

            for regen_info in items_needing_sql_regen:
                it = regen_info["item"]
                rec = it["record"]
                final_sql = _strip_sql_markdown(
                    _extract_message_content(rec, "assistant")
                )
                final_op = _sql_op_type(final_sql)
                if final_op in ("SELECT", "UNKNOWN"):
                    file_diag["sql_preprocess_regen_success_count"] += 1
                    query_only_items.append(it)
                else:
                    file_diag["non_query_sql_dropped_count"] += 1
                    rk = f"non_query_sql_regen_exhausted:{final_op}"
                    file_diag["final_drop_reasons"][rk] = (
                        file_diag["final_drop_reasons"].get(rk, 0) + 1
                    )
                    rec.setdefault("diagnostics", {})
                    rec["diagnostics"]["non_query_regen_exhausted"] = True
                    it["valid"] = False

            if items_needing_sql_regen:
                regen_success = file_diag["sql_preprocess_regen_success_count"]
                regen_total = file_diag["sql_preprocess_regen_count"]
                logger.info(
                    f"[{basename}] SQL Preprocess regen summary: "
                    f"{regen_success}/{regen_total} regenerated successfully"
                )

            completed_stages.add("sql_audit")
            _ckpt_save("sql_audit")
            logger.info(f"[{basename}] Stage sql_audit complete")
        else:
            logger.info(f"[{basename}] Skipping sql_audit (resumed)")
            query_only_items = list(schema_valid_items)

        # ==================================================================
        # Stage: sql_exec_verify — fast SQL execution tests + grouping
        # Always rebuild (deterministic sqlite operations)
        # ==================================================================
        sql_items = []
        valid_query_items = [
            item for item in query_only_items if item.get("valid", True)
        ]

        for item in valid_query_items:
            record = item["record"]
            system_content = _extract_message_content(record, "system")
            assistant_content = _strip_sql_markdown(
                _extract_message_content(record, "assistant")
            )
            user_content = _extract_message_content(record, "user")
            expected_operation = item.get(
                "expected_operation", _classify_user_intent(user_content)
            )
            evidence_prefix = item.get("evidence_prefix", "")

            constraints = _extract_alignment_constraints(record, evidence_prefix)

            sql_ok = True
            sql_err = ""

            exec_ok, exec_err = _test_sql_execution(
                system_content, assistant_content
            )
            if not exec_ok:
                sql_ok = False
                sql_err = exec_err

            if sql_ok:
                grouping_bad, grouping_detail = _violates_strict_grouping(
                    assistant_content
                )
                if grouping_bad:
                    sql_ok = False
                    sql_err = f"strict_grouping_violation: {grouping_detail}"
                    if "sql_exec_verify" not in completed_stages:
                        file_diag["strict_grouping_fixed_count"] += 1

            sql_items.append({
                "record": record,
                "sql_valid": sql_ok,
                "sql_error": sql_err,
                "sql_retry_count": 0,
                "schema": system_content,
                "current_sql": assistant_content,
                "user_content": user_content,
                "user_query_with_evidence": item.get(
                    "user_content", user_content
                ),
                "expected_operation": expected_operation,
                "evidence_prefix": evidence_prefix,
                "alignment_constraints": constraints,
            })

        if "sql_exec_verify" not in completed_stages:
            completed_stages.add("sql_exec_verify")
            _ckpt_save("sql_exec_verify")
            logger.info(f"[{basename}] Stage sql_exec_verify complete")

        # ==================================================================
        # Stage: sql_repair — batched LLM SQL repair (3 retries)
        # ==================================================================
        if "sql_repair" not in completed_stages:
            sql_to_repair = [it for it in sql_items if not it["sql_valid"]]
            if sql_to_repair:
                logger.info(
                    f"[{basename}] SQL execution: "
                    f"{len(sql_items) - len(sql_to_repair)} passed, "
                    f"{len(sql_to_repair)} failed -> entering repair loop"
                )

            async def _repair_single_sql(item):
                try:
                    new_sql = await sql_repair_agent.repair_sql(
                        schema=item["schema"],
                        sql=item["current_sql"],
                        error_msg=item["sql_error"],
                        user_query=item["user_query_with_evidence"],
                        expected_operation=item.get("expected_operation", ""),
                        evidence_text=item.get("evidence_prefix", ""),
                        timeout_seconds=llm_timeout,
                    )
                    sql_ok, sql_err = _test_sql_execution(
                        item["schema"], new_sql
                    )

                    if sql_ok:
                        grouping_bad, grouping_detail = (
                            _violates_strict_grouping(new_sql)
                        )
                        if grouping_bad:
                            sql_ok = False
                            sql_err = (
                                "strict_grouping_violation_after_repair: "
                                + grouping_detail
                            )

                    if sql_ok:
                        _upsert_message_content(
                            item["record"], "assistant", new_sql
                        )
                        item["current_sql"] = new_sql
                        item["sql_valid"] = True
                        item["sql_error"] = ""
                        return True
                    else:
                        item["current_sql"] = new_sql
                        item["sql_error"] = sql_err
                        return False
                except Exception as e:
                    item["sql_error"] = f"Agent Error: {str(e)}"
                    return False

            sql_max_retries = 3
            for sql_attempt in range(sql_max_retries):
                to_fix = [
                    it
                    for it in sql_items
                    if not it["sql_valid"]
                    and it["sql_retry_count"] < sql_max_retries
                ]
                if not to_fix:
                    break

                logger.info(
                    f"[{basename}] SQL Repair Attempt "
                    f"{sql_attempt + 1}/{sql_max_retries}, "
                    f"items to repair: {len(to_fix)}"
                )

                total_fix = len(to_fix)
                for i in range(0, total_fix, BATCH_SIZE):
                    batch = to_fix[i : i + BATCH_SIZE]
                    batch_num = i // BATCH_SIZE + 1
                    total_batches = (
                        (total_fix + BATCH_SIZE - 1) // BATCH_SIZE
                    )
                    logger.info(
                        f"  > Processing SQL repair batch "
                        f"{batch_num}/{total_batches} (size: {len(batch)})"
                    )

                    tasks = []
                    for it in batch:
                        it["sql_retry_count"] += 1
                        tasks.append(_repair_single_sql(it))

                    results = await asyncio.gather(
                        *tasks, return_exceptions=True
                    )
                    success_count = sum(1 for r in results if r is True)
                    logger.info(
                        f"  > SQL repair batch finished. "
                        f"Success: {success_count}/{len(batch)}"
                    )
                    _ckpt_save(
                        "sql_repair",
                        sql_attempt * 1000 + batch_num,
                    )

            completed_stages.add("sql_repair")
            _ckpt_save("sql_repair")
            logger.info(f"[{basename}] Stage sql_repair complete")
        else:
            logger.info(f"[{basename}] Skipping sql_repair (resumed)")
            for it in sql_items:
                if not it["sql_valid"]:
                    ok, err = _test_sql_execution(
                        it["schema"], it["current_sql"]
                    )
                    if ok:
                        it["sql_valid"] = True
                        it["sql_error"] = ""

        # ==================================================================
        # Stage: semantic_sync — final LLM semantic sync check (batched)
        # ==================================================================
        if "semantic_sync" not in completed_stages:
            items_needing_sync_check = [
                it
                for it in sql_items
                if it["sql_valid"]
                and it.get("alignment_constraints", {}).get(
                    "has_constraints", False
                )
            ]
            if items_needing_sync_check:
                logger.info(
                    f"[{basename}] Semantic sync check on "
                    f"{len(items_needing_sync_check)} items with "
                    f"alignment constraints"
                )
                sync_results = []
                total_sync = len(items_needing_sync_check)
                for i in range(0, total_sync, sync_check_batch_size):
                    batch = items_needing_sync_check[
                        i : i + sync_check_batch_size
                    ]
                    sb_num = i // sync_check_batch_size + 1
                    sb_total = (
                        (total_sync + sync_check_batch_size - 1)
                        // sync_check_batch_size
                    )
                    logger.info(
                        f"  > Semantic sync batch {sb_num}/{sb_total} "
                        f"(size: {len(batch)})"
                    )
                    sync_tasks = []
                    for it in batch:
                        sync_tasks.append(
                            _is_sql_semantically_synchronized(
                                sql=it["current_sql"],
                                constraints=it["alignment_constraints"],
                                schema_sql=it["schema"],
                                user_query=it["user_content"],
                                llm=alignment_llm,
                                timeout_seconds=llm_timeout,
                            )
                        )
                    batch_results = await asyncio.gather(
                        *sync_tasks, return_exceptions=True
                    )
                    sync_results.extend(batch_results)
                    _ckpt_save("semantic_sync", sb_num)

                desync_count = 0
                for it, sr in zip(items_needing_sync_check, sync_results):
                    if isinstance(sr, Exception):
                        logger.warning(
                            f"Semantic sync check exception, keeping item: {sr}"
                        )
                        continue
                    synced, reason = sr
                    if not synced:
                        it["sql_valid"] = False
                        it["sql_error"] = (
                            f"semantic_desync_after_repair: {reason}"
                        )
                        file_diag["semantic_desync_flagged_count"] += 1
                        desync_count += 1
                        it["record"].setdefault("diagnostics", {})
                        it["record"]["diagnostics"][
                            "semantic_desync_reason"
                        ] = reason

                if desync_count > 0:
                    logger.info(
                        f"[{basename}] Semantic desync hard-drop: "
                        f"{desync_count} items failed sync check"
                    )
                    file_diag[
                        "semantic_desync_final_drop_count"
                    ] += desync_count

            completed_stages.add("semantic_sync")
            _ckpt_save("semantic_sync")
            logger.info(f"[{basename}] Stage semantic_sync complete")
        else:
            logger.info(f"[{basename}] Skipping semantic_sync (resumed)")

        # ==================================================================
        # Finalize — collect valid records + clean up checkpoint
        # ==================================================================
        final_valid_records = [
            it["record"] for it in sql_items if it["sql_valid"]
        ]
        for it in sql_items:
            if not it["sql_valid"]:
                reason = (
                    it.get("sql_error", "unknown_sql_failure")
                    or "unknown_sql_failure"
                )
                reason_key = reason.split(":")[0][:80]
                file_diag["final_drop_reasons"][reason_key] = (
                    file_diag["final_drop_reasons"].get(reason_key, 0) + 1
                )

        file_valid = len(final_valid_records)
        file_invalid = file_total - file_valid

        logger.info(
            f"[{basename}] All stages complete: "
            f"total={file_total}, valid={file_valid}, invalid={file_invalid}"
        )

        return final_valid_records, file_total, file_valid, file_invalid, file_diag

    # === Helper: aggregate file diagnostics into the run-level summary ===
    _DIAG_KEYS = (
        "schema_prebuild_count", "leakage_fixed_count", "evidence_injected_count",
        "intent_mismatch_fixed_count", "hardcode_flagged_count",
        "schema_alignment_regen_count", "evidence_gap_injected_count",
        "schema_alignment_final_drop_count", "semantic_desync_flagged_count",
        "semantic_desync_final_drop_count", "schema_pollution_blocked_count",
        "strict_grouping_fixed_count", "llm_semantic_checked_count",
        "llm_leakage_flagged_count", "llm_join_misalignment_count",
        "non_query_sql_dropped_count", "sql_preprocess_regen_count",
        "sql_preprocess_regen_success_count", "sqlite_dialect_warning_count",
    )

    def _merge_diag(file_diag: Dict[str, Any]) -> None:
        for key in _DIAG_KEYS:
            aggregate_diag[key] += file_diag.get(key, 0)
        for k, v in file_diag.get("final_drop_reasons", {}).items():
            aggregate_diag["final_drop_reasons"][k] = (
                aggregate_diag["final_drop_reasons"].get(k, 0) + v
            )

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

        _save_state_snapshot(data_path, state)

        final_valid_records, file_total, file_valid, file_invalid, file_diag = (
            await _process_single_file(data_path)
        )

        result.total_records = file_total
        result.valid_records = file_valid
        result.invalid_records = file_invalid
        _merge_diag(file_diag)

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

        logger.info(
            f"domain_text2sql_cleaner: Checkpoint files kept at "
            f"{_ckpt_paths(data_path)['progress']} for audit/re-run"
        )

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
                _save_state_snapshot(input_path, state)

                final_valid_records, file_total, file_valid, file_invalid, file_diag = (
                    await _process_single_file(input_path)
                )

                result.total_records += file_total
                result.valid_records += file_valid
                result.invalid_records += file_invalid
                _merge_diag(file_diag)

                if file_valid > 0:
                    any_valid = True
                    with open(output_path, 'w', encoding='utf-8') as f:
                        for record in final_valid_records:
                            f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    logger.info(
                        f"domain_text2sql_cleaner: Cleaned {filename} -> {output_path}"
                    )

                logger.info(
                    f"domain_text2sql_cleaner: Checkpoint files kept at "
                    f"{_ckpt_paths(input_path)['progress']} for audit/re-run"
                )
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
    result.diagnostics = aggregate_diag
    logger.info(
        "domain_text2sql_cleaner diagnostics - "
        f"schema_prebuild={aggregate_diag['schema_prebuild_count']}, "
        f"leakage_fixed={aggregate_diag['leakage_fixed_count']}, "
        f"evidence_injected={aggregate_diag['evidence_injected_count']}, "
        f"schema_alignment_regen={aggregate_diag['schema_alignment_regen_count']}, "
        f"schema_alignment_final_drop={aggregate_diag['schema_alignment_final_drop_count']}, "
        f"semantic_desync_flagged={aggregate_diag['semantic_desync_flagged_count']}, "
        f"semantic_desync_final_drop={aggregate_diag['semantic_desync_final_drop_count']}, "
        f"schema_pollution_blocked={aggregate_diag['schema_pollution_blocked_count']}, "
        f"strict_grouping_fixed={aggregate_diag['strict_grouping_fixed_count']}, "
        f"llm_semantic_checked={aggregate_diag['llm_semantic_checked_count']}, "
        f"llm_leakage_flagged={aggregate_diag['llm_leakage_flagged_count']}, "
        f"llm_join_misalignment={aggregate_diag['llm_join_misalignment_count']}, "
        f"non_query_sql_dropped={aggregate_diag['non_query_sql_dropped_count']}, "
        f"sql_preprocess_regen={aggregate_diag['sql_preprocess_regen_count']}, "
        f"sql_preprocess_regen_success={aggregate_diag['sql_preprocess_regen_success_count']}, "
        f"sqlite_dialect_warnings={aggregate_diag['sqlite_dialect_warning_count']}, "
        f"drop_reasons={aggregate_diag['final_drop_reasons']}"
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


def _extract_user_query_for_cleaning(state: LoopAIState) -> str:
    """与 filter_node._extract_user_query 一致，避免循环 import。优先 constructor.user_query。"""
    constructor = state.get("constructor") or {}
    uq = (constructor.get("user_query") or "").strip()
    if uq:
        return uq
    if state.get("automated_query"):
        return str(state.get("automated_query", "")).strip()
    messages = state.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            c = getattr(message, "content", "")
            if c:
                return str(c).strip()
        if isinstance(message, dict):
            msg_type = str(message.get("type", "")).lower()
            msg_role = str(message.get("role", "")).lower()
            if msg_type == "human" or msg_role == "human" or msg_type == "humanmessage":
                c = message.get("content", "")
                if c:
                    return str(c).strip()
        if hasattr(message, "type") and getattr(message, "type", None) == "human":
            c = getattr(message, "content", "")
            if c:
                return str(c).strip()
    return ""


def load_benchmark_raw_for_codegen(state: LoopAIState) -> Optional[Dict[str, Any]]:
    """
    读取用于 code-gen 清洗的 benchmark 原始样本。
    从 constructor.benchmark_pool_path 采样池中随机采样一条。
    如果采样池不存在，返回 None。
    """
    from loopai.agents.Postprocess.tools.benchmark_sampler import sample_from_benchmark_pool

    constructor = state.get("constructor") or {}

    # 从采样池采样
    pool_path = (constructor.get("benchmark_pool_path") or "").strip()
    if not pool_path:
        logger.warning("load_benchmark_raw_for_codegen: benchmark_pool_path not configured")
        return None

    if not os.path.isfile(pool_path):
        logger.warning(f"load_benchmark_raw_for_codegen: benchmark pool not found at {pool_path}")
        return None

    sampled = sample_from_benchmark_pool(pool_path)
    if not sampled:
        logger.warning("load_benchmark_raw_for_codegen: failed to sample from benchmark pool")
        return None

    sr = sampled.get("sample_record")
    bundle: Dict[str, Any] = {"source": "benchmark_pool", "wrapper": sampled}
    if sampled.get("benchmark_name"):
        bundle["benchmark_name"] = sampled.get("benchmark_name")
    if isinstance(sr, dict):
        bundle["raw"] = sr
    else:
        bundle["raw"] = sampled
    return bundle


def _strip_llm_json_text(content: str) -> str:
    content = (content or "").strip()
    content = re.sub(r"^```json\s*", "", content, flags=re.MULTILINE)
    content = re.sub(r"^```\s*", "", content, flags=re.MULTILINE)
    content = re.sub(r"```$", "", content, flags=re.MULTILINE)
    return content.strip()


CODEGEN_INTERMEDIATE_SFT_SCHEMA_DOC = """
中间格式单条 JSON 须满足（与 Postprocess 产出的 SFT 中间 JSONL 一致）：
- id: string，可选；若无则下游可忽略。
- dataset_type: 字符串，一般为 \"sft\"。
- messages: **非空数组，且第一条必须是 role=system**，且 system 的 content 为**非空** string；
  其后为 user、assistant 等；各条可有 loss_mask（system/user 常为 false，assistant 常为 true）。
- meta: 对象，可选。

**system 是强制字段**：须概括助手角色、任务边界、与用户 builder 目标（user_query）一致；即使参考原始行里没有
system，也必须在转写时**补写一条恰当的 system**，并置于 messages 首位。

代码类 user/assistant 分工仍须**严格沿用参考模板**（user 侧重程序桩/题干，assistant 侧重续写/答案），与 system 要求同时满足。

"""


def _default_codegen_system_content(user_query: str) -> str:
    uq = (user_query or "").strip()
    if uq:
        return (
            "You are an expert programming assistant. The dataset builder's goal is: "
            f"{uq}\n"
            "Follow the user/developer message: produce assistant content in the exact completion or answer style "
            "required, without extra chit-chat."
        )
    return (
        "You are an expert programming assistant for code generation, completion, and code understanding. "
        "Follow the user message format; assistant replies match the requested technical style."
    )


def _ensure_record_has_system(record: Dict[str, Any], user_query: str) -> None:
    """保证 messages 首位为含非空内容的 system（与 builder 目标一致）；缺则插入，空则补全。"""
    msgs = record.get("messages")
    if not isinstance(msgs, list):
        return
    base = _default_codegen_system_content(user_query)
    if not msgs:
        record["messages"] = [{"role": "system", "content": base, "loss_mask": False}]
        return
    first = msgs[0]
    if not isinstance(first, dict):
        msgs.insert(0, {"role": "system", "content": base, "loss_mask": False})
        record["messages"] = msgs
        return
    role = str(first.get("role", "")).lower()
    if role == "system":
        if not (str(first.get("content", "")).strip()):
            first["content"] = base
        first.setdefault("loss_mask", False)
        return
    msgs.insert(0, {"role": "system", "content": base, "loss_mask": False})
    record["messages"] = msgs


def parse_codegen_phase_b_response(parsed: Dict[str, Any]) -> str:
    """
    解析 Phase B 顶层 JSON。返回 'accept' | 'reject' | 'parse_error'
    """
    if not isinstance(parsed, dict):
        return "parse_error"
    if parsed.get("accept") is False or parsed.get("reject_mapping") is True:
        return "reject"
    if parsed.get("accept") is True and isinstance(parsed.get("record"), dict):
        return "accept"
    if isinstance(parsed.get("record"), dict) and parsed.get("accept") is not False:
        return "accept"
    return "parse_error"


class CodeGenBenchmarkFormatAgent(BaseAgent):
    """Benchmark 模板规范化（Phase A）与逐条改写 / 拒绝映射（Phase B）。"""

    @property
    def role_name(self) -> str:
        return "CodeGenBenchmarkFormat"

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

    def compute_prompt(self) -> str:
        return (
            "You are an expert in building supervised fine-tuning datasets for code and assistants. "
            "You prefer rewriting over rejecting when content is code-related, but you MUST preserve the "
            "structural contract of the provided reference template (what belongs in user vs assistant, "
            "stub vs completion, language and formatting)—not shallow paraphrases that drop the code framing."
        )

    async def canonicalize_benchmark(
        self,
        user_query: str,
        benchmark_bundle: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        raw = benchmark_bundle.get("raw") or {}
        prompt = (
            "Task: Convert the following **reference row** (raw fields as exported by the user) into ONE intermediate SFT "
            "JSON record. This object is the **only structural exemplar** later steps must imitate.\n\n"
            "Rules for the template:\n"
            "- Mirror how the reference splits information across messages: if the reference keeps imports + signature + "
            "docstring (or partial function) as the 'prompt' side and a code fragment as the 'answer' side, map that into "
            "messages so the **user** turn carries the **full programmatic preamble / stub** (not a one-line natural "
            "language summary), and the **assistant** turn carries the **completion segment** with matching indentation "
            "and style.\n"
            "- **You MUST include a non-empty `system` as the first message**, tuned to the user_query goal (e.g. code "
            "generation / completion / explanation in dataset form). If the raw row has no system, **author one** that "
            "is concise and task-specific—not generic chit-chat, and not empty.\n"
            "- Preserve programming language, typing, docstring language, and doctest/examples placement as in the "
            "reference when those appear in the raw fields.\n"
            "- Do not name or discuss specific public benchmarks or leaderboard names; only use the raw fields.\n\n"
            f"User dataset goal (user_query):\n{user_query}\n\n"
            f"Reference row (JSON):\n{json.dumps(raw, ensure_ascii=False)}\n\n"
            f"{CODEGEN_INTERMEDIATE_SFT_SCHEMA_DOC}\n\n"
            "Output a single JSON object only (no markdown): dataset_type, messages, optional id, meta. "
            "Do not wrap in a code block."
        )
        try:
            messages_list = [
                SystemMessage(content=self.compute_prompt()),
                HumanMessage(content=prompt),
            ]
            response = await _await_llm_with_timeout(
                self.llm, messages_list, DEFAULT_LLM_TIMEOUT_SECONDS, "codegen_canonicalize_benchmark"
            )
            text = _strip_llm_json_text(response.content.strip())
            rec = json.loads(text)
            if isinstance(rec, dict) and rec.get("messages"):
                _ensure_record_has_system(rec, user_query)
                return rec
            logger.warning("canonicalize_benchmark: LLM returned dict without messages")
            return None
        except Exception as e:
            logger.error(f"canonicalize_benchmark failed: {e}")
            return None

    async def rewrite_training_record(
        self,
        user_query: str,
        benchmark_template: Dict[str, Any],
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        max_raw = getattr(self, "_rewrite_record_json_max_chars", None)
        if not isinstance(max_raw, int) or max_raw < 1:
            max_raw = rewrite_record_prompt_json_max_chars()
        record_json = truncate_json_for_llm_prompt(record, max_raw)
        prompt = (
            "**Task: Data Format Conversion for SFT Training**\n"
            "You are performing data format conversion to prepare SFT (Supervised Fine-Tuning) training data. "
            "Rewrite the given training record into ShareGPT-compliant intermediate SFT JSON that:\n"
            "1. Conforms to the user's dataset goal\n"
            "2. **Strictly follows the benchmark template's style and structure**\n"
            "3. Outputs in valid ShareGPT format (messages array with role/content)\n\n"
            "**Critical Requirements:**\n"
            "- **Data Style**: Your output MUST match the benchmark template's style—tone, language, code patterns, "
            "instruction format, and interaction flow. The benchmark defines the target quality and style standard.\n"
            "- **Data Format**: Output MUST be ShareGPT-compliant with a `messages` array containing objects with "
            "`role` (system/user/assistant) and `content` fields.\n"
            "- **Purpose**: This is data format transformation for SFT training, not content generation. Preserve "
            "the source record's semantic content while adapting its format and style to match the benchmark.\n\n"
            "**Template Conformity (Mandatory):**\n"
            "- **System Message**: The `messages` array MUST begin with a non-empty `system` turn. Mirror the "
            "benchmark template's system message style—if it's task-specific, match that; if it's role-based, "
            "follow that pattern. Never omit or leave empty.\n"
            "- **Interaction Pattern**: Study the benchmark template's user/assistant exchange pattern. Replicate "
            "the same structure: what goes in user content vs assistant content, code context placement, completion "
            "granularity (full function vs body-only), indentation style, and formatting conventions.\n"
            "- **User Message**: Must NOT be reduced to vague natural language if the benchmark template includes "
            "substantial code context. Preserve or reconstruct code context from the source to keep the user turn "
            "concrete and program-shaped like the benchmark.\n"
            "- **Language Consistency**: Match the benchmark template's language (English/Chinese/etc.) for "
            "instructions and comments unless the source intrinsically requires otherwise.\n"
            "- **Code Formatting**: Match the benchmark's code presentation style—markdown fences only if the "
            "benchmark uses them, indentation depth, newline conventions, etc.\n"
            "- **Content Boundaries**: Omit hidden tests or solution leaks if the benchmark keeps them out of user "
            "messages. Do not reference specific public benchmarks or datasets by name.\n\n"
            "**Rejection Policy:**\n"
            "- DEFAULT: accept=true and rewrite when source is plausibly relevant to user_query\n"
            "- ONLY reject when clearly non-code or impossible to align with benchmark template without fabricating "
            "entire content\n"
            "- For 'explain this code' records, refactor into benchmark's completion style while preserving code "
            "context in user message\n\n"
            "**Output Format (JSON only, no markdown):**\n"
            "1) Accept and rewrite:\n"
            '{"accept": true, "record": {"dataset_type": "SFT", "messages": [...], "id": "...", "meta": {...}}}\n'
            "2) Reject:\n"
            '{"accept": false, "reject_mapping": true, "reasoning": "<brief explanation>"}\n\n'
            f"User Dataset Goal:\n{user_query}\n\n"
            "Benchmark Template (style and structure reference):\n"
            f"{json.dumps(benchmark_template, ensure_ascii=False)}\n\n"
            f"{CODEGEN_INTERMEDIATE_SFT_SCHEMA_DOC}\n\n"
            "Record to rewrite (full JSON; may be truncated if very long):\n"
            f"{record_json}\n"
        )
        messages_list = [
            SystemMessage(content=self.compute_prompt()),
            HumanMessage(content=prompt),
        ]
        response = await _await_llm_with_timeout(
            self.llm, messages_list, DEFAULT_LLM_TIMEOUT_SECONDS, "codegen_rewrite_training_record"
        )
        text = _strip_llm_json_text(response.content.strip())
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            inner = parsed.get("record")
            if parsed.get("accept") is not False and isinstance(inner, dict):
                _ensure_record_has_system(inner, user_query)
        return parsed


def _merge_record_identity(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    if source.get("id") and not target.get("id"):
        target["id"] = source["id"]
    if source.get("meta") and isinstance(source["meta"], dict):
        tm = target.get("meta")
        if not isinstance(tm, dict):
            target["meta"] = copy.deepcopy(source["meta"])
        else:
            for k, v in source["meta"].items():
                tm.setdefault(k, v)


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
    BATCH_SIZE = 502  # 设置并发 Batch 大小
    agent_config = state.get("constructor", {}) or {}
    
    # 初始化 Agent
    domain_agent = None
    repair_agent = None
    format_agent: Optional[CodeGenBenchmarkFormatAgent] = None
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

    user_query = _extract_user_query_for_cleaning(state)
    benchmark_bundle = load_benchmark_raw_for_codegen(state)
    use_benchmark_pipeline = bool(benchmark_bundle and user_query)
    benchmark_template: Optional[Dict[str, Any]] = None
    run_diag: Dict[str, Any] = {
        "codegen_benchmark_pipeline": False,
        "codegen_phase_a_ok": False,
        "codegen_phase_b_rejected": 0,
        "codegen_phase_b_parse_errors": 0,
        "codegen_phase_b_reject_samples": [],
    }
    if use_benchmark_pipeline:
        try:
            format_agent = CodeGenBenchmarkFormatAgent(
                model_name=agent_config.get("model_path"),
                base_url=agent_config.get("base_url"),
                api_key=agent_config.get("api_key"),
                temperature=0.1,
            )
            format_agent._rewrite_record_json_max_chars = rewrite_record_prompt_json_max_chars(
                agent_config
            )
            benchmark_template = await format_agent.canonicalize_benchmark(
                user_query, benchmark_bundle
            )
            if benchmark_template:
                run_diag["codegen_benchmark_pipeline"] = True
                run_diag["codegen_phase_a_ok"] = True
                logger.info("domain_code_gen_cleaner: Phase A benchmark template ready")
            else:
                use_benchmark_pipeline = False
                logger.warning(
                    "domain_code_gen_cleaner: Phase A failed; using legacy codegen pipeline"
                )
        except Exception as e:
            logger.error(f"domain_code_gen_cleaner: Phase A error {e}; legacy pipeline", exc_info=True)
            use_benchmark_pipeline = False
            format_agent = None
    else:
        if benchmark_bundle and not user_query:
            logger.info(
                "domain_code_gen_cleaner: benchmark present but user_query empty; legacy pipeline"
            )

    # === 内部辅助函数 ===
    
    def _extract_code_from_record(record: Dict[str, Any]) -> Tuple[str, str]:
        """
        从记录中提取代码和用户查询
        
        Returns:
            (code, record_user_text) 元组；后者为条内 user 侧文本，勿与全局任务 user_query 混淆。
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
        
        # Benchmark 路径：Phase B 逐条改写或拒绝映射
        if use_benchmark_pipeline and format_agent and benchmark_template:
            logger.info(f"[{os.path.basename(file_path)}] Phase B: benchmark-guided rewrite...")
            reject_sample_cap = 20

            async def _phase_b_one(item: Dict[str, Any]) -> Dict[str, Any]:
                orig_record = item["record"]
                try:
                    parsed = await format_agent.rewrite_training_record(
                        user_query, benchmark_template, orig_record
                    )
                except Exception as ex:
                    logger.warning(f"Phase B LLM error: {ex}")
                    run_diag["codegen_phase_b_parse_errors"] += 1
                    return {"kind": "keep_original", "item": item}
                if not isinstance(parsed, dict):
                    run_diag["codegen_phase_b_parse_errors"] += 1
                    return {"kind": "keep_original", "item": item}
                kind = parse_codegen_phase_b_response(parsed)
                if kind == "reject":
                    reasoning = str(parsed.get("reasoning", "") or "")
                    run_diag["codegen_phase_b_rejected"] += 1
                    samples_list = run_diag["codegen_phase_b_reject_samples"]
                    if isinstance(samples_list, list) and len(samples_list) < reject_sample_cap:
                        samples_list.append({"reasoning": reasoning[:500]})
                    return {"kind": "reject", "item": item}
                if kind == "accept":
                    new_rec = parsed.get("record")
                    if isinstance(new_rec, dict):
                        _merge_record_identity(new_rec, orig_record)
                        new_item = dict(item)
                        new_item["record"] = new_rec
                        return {"kind": "accept", "item": new_item}
                run_diag["codegen_phase_b_parse_errors"] += 1
                return {"kind": "keep_original", "item": item}

            phase_b_items: List[Dict[str, Any]] = []
            total_cb = len(codegen_records)
            for j in range(0, total_cb, BATCH_SIZE):
                chunk = codegen_records[j : j + BATCH_SIZE]
                pb_results = await asyncio.gather(
                    *[_phase_b_one(it) for it in chunk], return_exceptions=True
                )
                for pr in pb_results:
                    if isinstance(pr, Exception):
                        logger.error(f"Phase B task error: {pr}")
                        continue
                    if pr.get("kind") == "reject":
                        continue
                    phase_b_items.append(pr["item"])
            codegen_records = phase_b_items
            logger.info(
                f"[{os.path.basename(file_path)}] After Phase B: {len(codegen_records)} records "
                f"(rejected_unrelated={run_diag['codegen_phase_b_rejected']})"
            )
        
        # 第二步：使用tree-sitter检查语法
        logger.info(f"[{os.path.basename(file_path)}] Step 2: Checking syntax with tree-sitter...")
        processed_records = []
        
        for item in codegen_records:
            record = item["record"]
            code, record_user_text = _extract_code_from_record(record)
            language = item["language"]
            
            if not code:
                processed_records.append({
                    "record": record,
                    "valid": False,
                    "error": "No code found",
                    "retry_count": 0,
                    "user_query": record_user_text,
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
                        "user_query": record_user_text,
                        "language": language,
                        "is_syntax_error": True
                    })

        # 第三步：修复循环
        logger.info(f"[{os.path.basename(file_path)}] Step 3: Repairing syntax errors...")
        max_retries = 3
        
        async def _repair_single_item(item):
            """处理单条数据的修复逻辑"""
            record = item["record"]
            current_code, record_user_text = _extract_code_from_record(record)
            language = item.get("language", "python")
            
            if not current_code:
                item["valid"] = False
                item["error"] = "No code found in record"
                return False
            
            try:
                new_code, is_codegen = await repair_agent.repair_code(
                    code=current_code,
                    error_msg=item["error"],
                    user_query=record_user_text,
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
    result.diagnostics = run_diag
    logger.info(
        "domain_code_gen_cleaner diagnostics - "
        f"benchmark_pipeline={run_diag.get('codegen_benchmark_pipeline')}, "
        f"phase_a_ok={run_diag.get('codegen_phase_a_ok')}, "
        f"phase_b_rejected={run_diag.get('codegen_phase_b_rejected')}, "
        f"phase_b_parse_errors={run_diag.get('codegen_phase_b_parse_errors')}"
    )

    return result


# ──────────────────────────────────────────────────────────────────────────────
# DataFlow + Alpagasus（normal_data 第二步质量过滤）
# 与 loopai/agents/Constructor/tools/cot_filter_tool.py 中 bootstrap 逻辑对齐
# ──────────────────────────────────────────────────────────────────────────────

_DATAFLOW_ROOT = "/mnt/paper2any/xbr/financial/DataFlow"
_SERVING_PKG = "dataflow.serving"
_SERVING_MODULE = "dataflow.serving.api_llm_serving_request"


def _bootstrap_dataflow_for_alpagasus(api_key: str = "") -> None:
    """向 sys.path 注入 DataFlow，并绕过重量级 serving/__init__.py。"""
    if _DATAFLOW_ROOT not in sys.path:
        sys.path.insert(0, _DATAFLOW_ROOT)

    if api_key:
        os.environ["DF_API_KEY"] = api_key

    if _SERVING_PKG not in sys.modules:
        pkg = types.ModuleType(_SERVING_PKG)
        pkg.__path__ = [os.path.join(_DATAFLOW_ROOT, "dataflow", "serving")]
        pkg.__package__ = _SERVING_PKG
        sys.modules[_SERVING_PKG] = pkg

    if _SERVING_MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _SERVING_MODULE,
            os.path.join(_DATAFLOW_ROOT, "dataflow", "serving", "api_llm_serving_request.py"),
            submodule_search_locations=[],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _SERVING_PKG
        sys.modules[_SERVING_MODULE] = mod
        spec.loader.exec_module(mod)


def _make_chat_completions_url_for_dataflow(base_url: str) -> str:
    """将 OpenAI 兼容 base_url（常以 /v1 结尾）转为 DataFlow APILLMServing 需要的 chat/completions 端点。"""
    url = (base_url or "").rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    return url


def _normal_data_run_alpagasus_filter(
    adapted_rows: List[Dict[str, Any]],
    *,
    api_url: str,
    model_name: str,
    api_key: str,
    min_score: int = 3,
    max_score: int = 5,
    max_workers: int = 502,
) -> Tuple[Set[str], Optional[str]]:
    """
    对已适配为 instruction/input/output/_tmp_id 的行运行 AlpagasusFilter。

    Returns:
        (通过过滤的 _tmp_id 集合, 错误信息或 None)
    """
    if not adapted_rows:
        return set(), None

    try:
        _bootstrap_dataflow_for_alpagasus(api_key=api_key)
        from dataflow.serving.api_llm_serving_request import APILLMServing_request
        from dataflow.operators.text_sft import AlpagasusFilter
        from dataflow.utils.storage import FileStorage
    except Exception as e:
        return set(), f"DataFlow import/bootstrap failed: {e}"

    with tempfile.TemporaryDirectory(prefix="loopai_normal_alpa_") as tmpdir:
        input_tmp = os.path.join(tmpdir, "input.jsonl")
        cache_dir = os.path.join(tmpdir, "cache")
        os.makedirs(cache_dir, exist_ok=True)

        try:
            with open(input_tmp, "w", encoding="utf-8") as f:
                for rec in adapted_rows:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            llm = APILLMServing_request(
                api_url=api_url,
                model_name=model_name,
                max_workers=max_workers,
            )
            # requests 默认读取 HTTP(S)_PROXY；本机代理未启动时会导致 Alpagasus 全失败，故对 LLM 直连。
            if getattr(llm, "session", None) is not None:
                llm.session.trust_env = False
            alpa = AlpagasusFilter(
                llm_serving=llm,
                min_score=min_score,
                max_score=max_score,
                dimension="quality",
            )
            storage = FileStorage(
                first_entry_file_name=input_tmp,
                cache_path=cache_dir,
                file_name_prefix="normal_alpa",
                cache_type="jsonl",
            )
            alpa.run(
                storage=storage.step(),
                input_instruction_key="instruction",
                input_input_key="input",
                input_output_key="output",
                output_key="AlpagasusScore",
            )
        except Exception as e:
            return set(), f"AlpagasusFilter run failed: {e}"

        out_path = os.path.join(cache_dir, "normal_alpa_step1.jsonl")
        if not os.path.isfile(out_path):
            return set(), f"Alpagasus output not found: {out_path}"

        passing: Set[str] = set()
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tid = row.get("_tmp_id")
                    if tid is not None and str(tid).strip() != "":
                        passing.add(str(tid))
        except OSError as e:
            return set(), f"Failed to read Alpagasus output: {e}"

        return passing, None


def domain_normal_data_cleaner(data_path: str, state: LoopAIState) -> BaseCleanResult:
    """
    领域工具：针对常规对话QA数据的特定清洗
    
    清洗流程：
    1. 第一步：并发判断每条记录是否与 user_query 领域相关
    2. 第二步：对相关的记录进行 Alpagasus 质量打分过滤（不改写回答）
    3. 第三步：输出清洗后的数据
    
    Args:
        data_path: 数据文件路径
        state: 当前状态（constructor.user_query 用于领域判断；LLM 配置用于 Alpagasus）
    
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
    agent_config = state.get("constructor", {}) or {}
    user_query = agent_config.get("user_query", "")
    
    if not user_query:
        logger.warning("user_query is empty, cannot perform domain filtering")
        return result
    
    # 初始化 Agent（领域相关性 + query 改写）；第二步质量过滤走 DataFlow AlpagasusFilter
    domain_agent = None
    query_rewrite_agent = None
    try:
        domain_agent = NormalDomainAgent(
            model_name=agent_config.get("model_path"),
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            temperature=0.1
        )
        query_rewrite_agent = NormalDomainQueryRewriteAgent(
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
    async def _process_single_file(file_path: str) -> Tuple[List[Dict], int, int, int, Optional[str]]:
        """
        处理单个JSONL文件，返回 (有效记录列表, 总数, 有效数, 无效数, 错误信息或None)
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
        rewrite_result = await query_rewrite_agent.rewrite_query(user_query)
        effective_query = rewrite_result.get("rewritten_query", user_query) or user_query
        logger.info(f"[{os.path.basename(file_path)}] Original domain query: {user_query}")
        logger.info(f"[{os.path.basename(file_path)}] Effective domain query: {effective_query}")
        if rewrite_result.get("domain_focus"):
            logger.info(f"[{os.path.basename(file_path)}] Query rewrite domain focus: {rewrite_result.get('domain_focus')}")
        if rewrite_result.get("keep_policy_note"):
            logger.info(f"[{os.path.basename(file_path)}] Query rewrite note: {rewrite_result.get('keep_policy_note')}")
        
        # 第一步：并发判断领域相关性
        logger.info(f"[{os.path.basename(file_path)}] Step 1: Analyzing domain relevance...")
        analyzed_records = []
        
        for i in range(0, file_total, BATCH_SIZE):
            batch = all_records[i : i + BATCH_SIZE]
            logger.info(f"  > Processing analysis batch {i//BATCH_SIZE + 1}/{(file_total + BATCH_SIZE - 1)//BATCH_SIZE} (size: {len(batch)})")
            
            async def _analyze_single_record(record):
                """分析单条记录：判断是否与领域相关"""
                try:
                    analysis = await domain_agent.analyze_record(record, effective_query)
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
        
        # 第二步：Alpagasus 质量过滤（不改写回答，仅按分数筛留）
        logger.info(f"[{os.path.basename(file_path)}] Step 2: Alpagasus quality filtering...")
        if not related_records:
            logger.info(f"[{os.path.basename(file_path)}] No related records; skip Alpagasus.")
            return [], file_total, 0, file_total, None

        model_name = (agent_config.get("model_path") or state.get("analyze_model_path") or "").strip()
        base_url = (agent_config.get("base_url") or state.get("analyze_base_url") or "").strip()
        api_key = (agent_config.get("api_key") or state.get("analyze_api_key") or "").strip()
        if not (model_name and base_url and api_key):
            msg = "Missing LLM config (model_path / base_url / api_key) for Alpagasus"
            logger.warning(msg)
            return [], file_total, 0, file_total, msg

        adapted_rows: List[Dict[str, Any]] = []
        id_to_record: Dict[str, Dict[str, Any]] = {}
        for item in related_records:
            rec = item["record"]
            user_content, assistant_content = _extract_content_from_record(rec)
            if not user_content or not assistant_content:
                continue
            tid = str(uuid.uuid4())
            adapted_rows.append(
                {
                    "instruction": user_content,
                    "input": "",
                    "output": assistant_content,
                    "_tmp_id": tid,
                }
            )
            id_to_record[tid] = rec

        if not adapted_rows:
            logger.info(
                f"[{os.path.basename(file_path)}] All related records missing user/assistant content; "
                "skip Alpagasus."
            )
            return [], file_total, 0, file_total, None

        api_url = _make_chat_completions_url_for_dataflow(base_url)
        passing_ids, alpa_err = await asyncio.to_thread(
            _normal_data_run_alpagasus_filter,
            adapted_rows,
            api_url=api_url,
            model_name=model_name,
            api_key=api_key,
        )
        if alpa_err:
            logger.error(f"[{os.path.basename(file_path)}] Alpagasus failed: {alpa_err}")
            return [], file_total, 0, file_total, alpa_err

        final_valid_records: List[Dict[str, Any]] = []
        for row in adapted_rows:
            tid = row["_tmp_id"]
            if tid in passing_ids:
                final_valid_records.append(id_to_record[tid])

        file_valid = len(final_valid_records)
        file_invalid = file_total - file_valid

        # 第三步：结果已在 final_valid_records 中
        logger.info(
            f"[{os.path.basename(file_path)}] Step 3: Alpagasus kept {file_valid}/{file_total} records "
            f"(related_input={len(adapted_rows)})"
        )

        return final_valid_records, file_total, file_valid, file_invalid, None

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
        
        final_valid_records, file_total, file_valid, file_invalid, ferr = await _process_single_file(
            data_path
        )

        result.total_records = file_total
        result.valid_records = file_valid
        result.invalid_records = file_invalid
        if ferr:
            result.success = False
            result.error_message = ferr
            result.cleaned_data_path = data_path
            return result

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
                final_valid_records, file_total, file_valid, file_invalid, ferr = await _process_single_file(
                    input_path
                )

                result.total_records += file_total
                result.valid_records += file_valid
                result.invalid_records += file_invalid
                if ferr:
                    result.success = False
                    result.error_message = ferr
                    logger.warning(f"Alpagasus failed for {input_path}: {ferr}")
                    continue

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
    
    # 获取 benchmark 源目录
    constructor_state = state.get("constructor", {})
    benchmark_path = constructor_state.get("benchmark_source_dir", "")
    if not benchmark_path:
        logger.info("No benchmark_source_dir specified, skipping benchmark cleaning")
        return result

    if not os.path.exists(benchmark_path):
        logger.warning(f"Benchmark source directory not found: {benchmark_path}, skipping benchmark cleaning")
        return result

    # 获取数据类别（PT或SFT）
    category = constructor_state.get("category", "PT").upper()
    if category not in ["PT", "SFT"]:
        logger.warning(f"Unknown category '{category}', defaulting to SFT for benchmark cleaning")
        category = "SFT"

    logger.info(f"benchmark_data_cleaner: Category = {category}, Benchmark source dir = {benchmark_path}")
    
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
    
    def _load_benchmark_record(record: Dict[str, Any]) -> None:
        nonlocal benchmark_count
        benchmark_count += 1
        if category == "SFT":
            user_content, assistant_content, combined_key = _extract_record_signature_sft(record)
            benchmark_sigs.add(combined_key)
            benchmark_full[combined_key] = (user_content, assistant_content)
        else:  # PT
            text_content, text_key = _extract_record_signature_pt(record)
            benchmark_sigs.add(text_key)
            benchmark_full[text_key] = text_content

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
                        _load_benchmark_record(record)
                            
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
