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
from pydantic import BaseModel, Field
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

    async def prebuild_schema(self, schema: str, user_query: str, assistant_sql: str) -> str:
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
            response = await self.llm.ainvoke(messages)
            content = response.content
            content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
            content = re.sub(r'```$', '', content, flags=re.MULTILINE)
            return content.strip()
        except Exception as e:
            logger.error(f"Error in schema prebuild: {e}")
            return schema

    async def repair_schema(self, schema: str, error_msg: str, user_query: str, sql: str, alignment_hint: str = "") -> str:
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
        evidence_text: str = ""
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
            response = await self.llm.ainvoke(messages)
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
    llm
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
        response = await llm.ainvoke(messages)
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
    llm
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
        response = await llm.ainvoke(messages)
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
    llm
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
        response = await llm.ainvoke(messages)
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
    llm
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
        response = await llm.ainvoke(messages)
        return response.content.strip()
    except Exception as e:
        logger.warning(f"Evidence generation failed: {e}")
        return ""


async def _regenerate_compliant_sql(
    schema_sql: str,
    user_query: str,
    original_sql: str,
    issue_description: str,
    llm
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
        response = await llm.ainvoke(messages)
        content = response.content
        content = re.sub(r'^```sql\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'^```\s*', '', content, flags=re.MULTILINE)
        content = re.sub(r'```$', '', content, flags=re.MULTILINE)
        return content.strip()
    except Exception as e:
        logger.warning(f"SQL regeneration failed: {e}")
        return original_sql


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
    BATCH_SIZE = 64  # 设置并发 Batch 大小
    agent_config = state.get("constructor", {}) or {}
    
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

    async def _repair_single_item(item):
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
                alignment_hint=item.get("alignment_hint", "")
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
        处理单个JSONL文件，返回 (有效记录列表, 总数, 有效数, 无效数)
        """
        file_diag = {
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
        all_records = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        all_records.append(json.loads(line))
                    except:
                        pass
        
        file_total = len(all_records)
        
        # 第一步：LLM Schema 预构建 + 建库验证
        # 1a) 规则预处理：快速剥离明显泄露
        prebuild_items = []
        for record in all_records:
            system_content = _extract_message_content(record, "system")
            user_content = _extract_message_content(record, "user")
            assistant_content = _strip_sql_markdown(_extract_message_content(record, "assistant"))

            sanitized_schema, leakage_detected, leakage_reason = _sanitize_schema_and_detect_leakage(
                schema_sql=system_content,
                assistant_sql=assistant_content
            )
            if leakage_detected:
                file_diag["leakage_fixed_count"] += 1
                system_content = sanitized_schema
                _upsert_message_content(record, "system", system_content)
                record.setdefault("diagnostics", {})
                record["diagnostics"]["leakage_detected"] = True
                record["diagnostics"]["leakage_reason"] = leakage_reason

            prebuild_items.append({
                "record": record,
                "system_content": system_content,
                "user_content": user_content,
                "assistant_content": assistant_content,
            })

        # 1b) LLM Schema 预构建（去答案泄露、补外键、对齐user需求）
        logger.info(
            f"[{os.path.basename(file_path)}] LLM Schema Prebuild: {len(prebuild_items)} items"
        )

        async def _prebuild_single(item):
            try:
                return await repair_agent.prebuild_schema(
                    schema=item["system_content"],
                    user_query=item["user_content"],
                    assistant_sql=item["assistant_content"]
                )
            except Exception as e:
                logger.warning(f"Schema prebuild failed, keeping original: {e}")
                return item["system_content"]

        total_pb = len(prebuild_items)
        for i in range(0, total_pb, BATCH_SIZE):
            batch = prebuild_items[i : i + BATCH_SIZE]
            logger.info(
                f"  > Prebuild batch {i // BATCH_SIZE + 1}/"
                f"{(total_pb + BATCH_SIZE - 1) // BATCH_SIZE} (size: {len(batch)})"
            )
            prebuild_tasks = [_prebuild_single(it) for it in batch]
            prebuild_results = await asyncio.gather(*prebuild_tasks, return_exceptions=True)
            for it, pb_result in zip(batch, prebuild_results):
                if isinstance(pb_result, Exception):
                    logger.warning(f"Schema prebuild exception, keeping original: {pb_result}")
                else:
                    it["system_content"] = pb_result
                    _upsert_message_content(it["record"], "system", pb_result)
                    file_diag["schema_prebuild_count"] += 1

        # 1c) 建库验证 + Evidence 构建
        processed_records = []
        for item in prebuild_items:
            record = item["record"]
            system_content = item["system_content"]
            user_content = item["user_content"]
            assistant_content = item["assistant_content"]

            evidence_prefix = _build_evidence_prefix(user_content, system_content)
            if evidence_prefix:
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
                    "evidence_prefix": evidence_prefix
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
                    "evidence_prefix": evidence_prefix
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

        # 第 2.5 步：Schema-Query 语义对齐检查 + Schema 回退重生成 + Evidence 注入
        ALIGNMENT_MAX_ROUNDS = 2
        alignment_llm = repair_agent.llm

        for alignment_round in range(ALIGNMENT_MAX_ROUNDS):
            items_to_check = [item for item in processed_records if item["valid"]]
            if not items_to_check:
                break

            logger.info(
                f"[{os.path.basename(file_path)}] Schema-Query Alignment Check round "
                f"{alignment_round + 1}/{ALIGNMENT_MAX_ROUNDS}, items: {len(items_to_check)}"
            )

            ALIGNMENT_BATCH_SIZE = 128
            alignment_results = []
            total_align = len(items_to_check)
            for ai in range(0, total_align, ALIGNMENT_BATCH_SIZE):
                batch = items_to_check[ai : ai + ALIGNMENT_BATCH_SIZE]
                logger.info(
                    f"  > Alignment batch {ai // ALIGNMENT_BATCH_SIZE + 1}/"
                    f"{(total_align + ALIGNMENT_BATCH_SIZE - 1) // ALIGNMENT_BATCH_SIZE} "
                    f"(size: {len(batch)})"
                )
                batch_tasks = []
                for item in batch:
                    record = item["record"]
                    schema_now = _extract_message_content(record, "system")
                    user_now = _extract_message_content(record, "user")
                    asst_now = _strip_sql_markdown(_extract_message_content(record, "assistant"))
                    batch_tasks.append(
                        _check_schema_query_alignment(user_now, schema_now, asst_now, alignment_llm)
                    )
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                alignment_results.extend(batch_results)

            items_needing_regen = []
            for item, aresult in zip(items_to_check, alignment_results):
                if isinstance(aresult, Exception):
                    logger.warning(f"Alignment check exception, skipping: {aresult}")
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
                record["diagnostics"]["schema_user_mismatch_reason"] = "; ".join(mismatch_reasons)

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
                    item["error"] = f"Schema-user mismatch: missing {missing}"
                    item["alignment_hint"] = f"Add missing fields: {', '.join(missing)}"
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
                        user_now, schema_now, gaps, alignment_llm
                    )
                    if extra_evidence:
                        old_evidence = item.get("evidence_prefix", "")
                        item["evidence_prefix"] = (old_evidence + " " + extra_evidence).strip()
                        new_user = f"{item['evidence_prefix']}\nQuestion: {user_now}".strip()
                        item["user_content"] = new_user
                        file_diag["evidence_gap_injected_count"] += 1
                        record.setdefault("diagnostics", {})
                        record["diagnostics"]["evidence_gaps_filled"] = gaps
                        record["diagnostics"]["generated_evidence"] = extra_evidence

            if not items_needing_regen:
                logger.info(
                    f"[{os.path.basename(file_path)}] All items aligned after round {alignment_round + 1}"
                )
                break

            logger.info(
                f"[{os.path.basename(file_path)}] {len(items_needing_regen)} items need schema regen "
                f"(round {alignment_round + 1})"
            )

            for regen_attempt in range(max_retries):
                to_regen = [
                    it for it in items_needing_regen
                    if not it["valid"] and it["retry_count"] < max_retries
                ]
                if not to_regen:
                    break
                logger.info(
                    f"  > Alignment-driven Schema Repair attempt {regen_attempt + 1}/{max_retries}, "
                    f"items: {len(to_regen)}"
                )
                total_regen = len(to_regen)
                for i in range(0, total_regen, BATCH_SIZE):
                    batch = to_regen[i : i + BATCH_SIZE]
                    regen_tasks = []
                    for it in batch:
                        it["retry_count"] += 1
                        regen_tasks.append(_repair_single_item(it))
                    regen_results = await asyncio.gather(*regen_tasks, return_exceptions=True)
                    s_count = sum(1 for r in regen_results if r is True)
                    logger.info(f"  > Regen batch done. Success: {s_count}/{len(batch)}")

        for item in processed_records:
            ar = item.get("alignment_round", 0)
            if ar > 0 and not item["valid"]:
                file_diag["schema_alignment_final_drop_count"] += 1
                reason = item.get("error", "alignment_regen_exhausted") or "alignment_regen_exhausted"
                reason_key = f"alignment_drop:{reason[:60]}"
                file_diag["final_drop_reasons"][reason_key] = file_diag["final_drop_reasons"].get(reason_key, 0) + 1

        # 第三步：SQL 预处理审核 + 重生 + SQL 执行验证 + Strict Grouping + 语义同步
        schema_valid_items = [item for item in processed_records if item["valid"]]
        sql_items = []

        # 3a+3b) SQL 预处理：审核合规性，不合规者 LLM 重生（不直接丢弃）
        SQL_REGEN_MAX_RETRIES = 3

        # 3a) 收集需要重生的项（非 SELECT/WITH 的 DML/DDL）
        items_needing_sql_regen = []
        compliant_items = []
        for item in schema_valid_items:
            record = item["record"]
            assistant_content = _strip_sql_markdown(_extract_message_content(record, "assistant"))
            sql_op = _sql_op_type(assistant_content)
            if sql_op not in ("SELECT", "UNKNOWN"):
                file_diag["sql_preprocess_regen_count"] += 1
                record.setdefault("diagnostics", {})
                record["diagnostics"]["original_sql_op"] = sql_op
                items_needing_sql_regen.append({
                    "item": item,
                    "issue": f"SQL is {sql_op}, not a query. BIRD requires SELECT-only.",
                    "regen_retry": 0,
                })
            else:
                compliant_items.append(item)

        # 3b) LLM 语义审核（泄露 / JOIN / 方言），并行批量
        llm_audit_tasks = []
        for item in compliant_items:
            record = item["record"]
            system_content = _extract_message_content(record, "system")
            assistant_content = _strip_sql_markdown(_extract_message_content(record, "assistant"))
            user_content = _extract_message_content(record, "user")
            llm_audit_tasks.append(
                _llm_validate_text2sql_semantics(user_content, system_content, assistant_content, alignment_llm)
            )

        llm_audit_results = await asyncio.gather(*llm_audit_tasks, return_exceptions=True)
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
                regen_issues.append("LLM audit: SQL is not a pure query (SELECT/WITH)")

            if audit.get("has_leakage", False):
                file_diag["llm_leakage_flagged_count"] += 1
                record["diagnostics"]["llm_leakage_flagged"] = True
                record["diagnostics"]["llm_leakage_issues"] = audit.get("issues", [])
                needs_regen = True
                regen_issues.append("LLM audit: schema leakage detected, SQL needs regeneration")

            if not audit.get("join_alignment_ok", True):
                file_diag["llm_join_misalignment_count"] += 1
                record["diagnostics"]["llm_join_misalignment"] = True
                record["diagnostics"]["llm_join_issues"] = audit.get("issues", [])
                needs_regen = True
                regen_issues.append("LLM audit: JOIN misalignment with schema FK")

            dialect_issues = audit.get("dialect_issues", [])
            if dialect_issues:
                file_diag["sqlite_dialect_warning_count"] += 1
                record["diagnostics"]["sqlite_dialect_warnings"] = dialect_issues

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
                f"[{os.path.basename(file_path)}] SQL Preprocess: {len(items_needing_sql_regen)} items "
                f"need SQL regeneration"
            )

        async def _do_sql_regen(regen_info):
            it = regen_info["item"]
            rec = it["record"]
            schema_sql = _extract_message_content(rec, "system")
            user_q = _extract_message_content(rec, "user")
            old_sql = _strip_sql_markdown(_extract_message_content(rec, "assistant"))
            new_sql = await _regenerate_compliant_sql(
                schema_sql=schema_sql,
                user_query=user_q,
                original_sql=old_sql,
                issue_description=regen_info["issue"],
                llm=alignment_llm
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
                r for r in items_needing_sql_regen
                if r["regen_retry"] < SQL_REGEN_MAX_RETRIES
                and r["item"].get("valid", True)
            ]
            if not to_regen:
                break

            logger.info(
                f"  > SQL Regen attempt {regen_attempt + 1}/{SQL_REGEN_MAX_RETRIES}, "
                f"items: {len(to_regen)}"
            )

            total_regen = len(to_regen)
            for i in range(0, total_regen, BATCH_SIZE):
                batch = to_regen[i : i + BATCH_SIZE]
                regen_tasks = []
                for ri in batch:
                    ri["regen_retry"] += 1
                    regen_tasks.append(_do_sql_regen(ri))
                regen_results = await asyncio.gather(*regen_tasks, return_exceptions=True)
                s_count = sum(1 for r in regen_results if r is True)
                logger.info(
                    f"  > SQL Regen batch done. Success: {s_count}/{len(batch)}"
                )

        for regen_info in items_needing_sql_regen:
            it = regen_info["item"]
            rec = it["record"]
            final_sql = _strip_sql_markdown(_extract_message_content(rec, "assistant"))
            final_op = _sql_op_type(final_sql)
            if final_op in ("SELECT", "UNKNOWN"):
                file_diag["sql_preprocess_regen_success_count"] += 1
                query_only_items.append(it)
            else:
                file_diag["non_query_sql_dropped_count"] += 1
                file_diag["final_drop_reasons"][f"non_query_sql_regen_exhausted:{final_op}"] = \
                    file_diag["final_drop_reasons"].get(f"non_query_sql_regen_exhausted:{final_op}", 0) + 1
                rec.setdefault("diagnostics", {})
                rec["diagnostics"]["non_query_regen_exhausted"] = True
                it["valid"] = False

        if items_needing_sql_regen:
            regen_success = file_diag["sql_preprocess_regen_success_count"]
            regen_total = file_diag["sql_preprocess_regen_count"]
            logger.info(
                f"[{os.path.basename(file_path)}] SQL Preprocess regen summary: "
                f"{regen_success}/{regen_total} regenerated successfully"
            )

        # 3c) SQL 执行验证 + Strict Grouping
        valid_query_items = [item for item in query_only_items if item.get("valid", True)]

        for item in valid_query_items:
            record = item["record"]
            system_content = _extract_message_content(record, "system")
            assistant_content = _strip_sql_markdown(_extract_message_content(record, "assistant"))
            user_content = _extract_message_content(record, "user")
            expected_operation = item.get("expected_operation", _classify_user_intent(user_content))
            evidence_prefix = item.get("evidence_prefix", "")

            constraints = _extract_alignment_constraints(record, evidence_prefix)

            sql_ok = True
            sql_err = ""

            exec_ok, exec_err = _test_sql_execution(system_content, assistant_content)
            if not exec_ok:
                sql_ok = False
                sql_err = exec_err

            if sql_ok:
                grouping_bad, grouping_detail = _violates_strict_grouping(assistant_content)
                if grouping_bad:
                    sql_ok = False
                    sql_err = f"strict_grouping_violation: {grouping_detail}"
                    file_diag["strict_grouping_fixed_count"] += 1

            sql_items.append({
                "record": record,
                "sql_valid": sql_ok,
                "sql_error": sql_err,
                "sql_retry_count": 0,
                "schema": system_content,
                "current_sql": assistant_content,
                "user_content": user_content,
                "user_query_with_evidence": item.get("user_content", user_content),
                "expected_operation": expected_operation,
                "evidence_prefix": evidence_prefix,
                "alignment_constraints": constraints,
            })

        sql_to_repair = [it for it in sql_items if not it["sql_valid"]]
        if sql_to_repair:
            logger.info(f"[{os.path.basename(file_path)}] SQL execution: {len(schema_valid_items) - len(sql_to_repair)} passed, {len(sql_to_repair)} failed -> entering repair loop")

        async def _repair_single_sql(item):
            """修复单条SQL的执行错误，含 strict grouping 与 LLM 语义复检"""
            try:
                new_sql = await sql_repair_agent.repair_sql(
                    schema=item["schema"],
                    sql=item["current_sql"],
                    error_msg=item["sql_error"],
                    user_query=item["user_query_with_evidence"],
                    expected_operation=item.get("expected_operation", ""),
                    evidence_text=item.get("evidence_prefix", "")
                )
                sql_ok, sql_err = _test_sql_execution(item["schema"], new_sql)

                if sql_ok:
                    grouping_bad, grouping_detail = _violates_strict_grouping(new_sql)
                    if grouping_bad:
                        sql_ok, sql_err = False, f"strict_grouping_violation_after_repair: {grouping_detail}"

                if sql_ok:
                    _upsert_message_content(item["record"], "assistant", new_sql)
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
            to_fix = [it for it in sql_items if not it["sql_valid"] and it["sql_retry_count"] < sql_max_retries]
            if not to_fix:
                break

            logger.info(f"[{os.path.basename(file_path)}] SQL Repair Attempt {sql_attempt + 1}/{sql_max_retries}, items to repair: {len(to_fix)}")

            total_fix = len(to_fix)
            for i in range(0, total_fix, BATCH_SIZE):
                batch = to_fix[i : i + BATCH_SIZE]
                logger.info(f"  > Processing SQL repair batch {i//BATCH_SIZE + 1}/{(total_fix + BATCH_SIZE - 1)//BATCH_SIZE} (size: {len(batch)})")

                tasks = []
                for it in batch:
                    it["sql_retry_count"] += 1
                    tasks.append(_repair_single_sql(it))

                results = await asyncio.gather(*tasks, return_exceptions=True)
                success_count = sum(1 for r in results if r is True)
                logger.info(f"  > SQL repair batch finished. Success: {success_count}/{len(batch)}")

        # 第 3.5 步：语义同步终检（Label Desynchronization 硬约束）
        # SQL 通过执行但可能未落实诊断约束 -> LLM 审核 -> 不通过则 hard_drop
        items_needing_sync_check = [
            it for it in sql_items
            if it["sql_valid"] and it.get("alignment_constraints", {}).get("has_constraints", False)
        ]
        if items_needing_sync_check:
            logger.info(
                f"[{os.path.basename(file_path)}] Semantic sync check on "
                f"{len(items_needing_sync_check)} items with alignment constraints"
            )
            sync_tasks = []
            for it in items_needing_sync_check:
                sync_tasks.append(
                    _is_sql_semantically_synchronized(
                        sql=it["current_sql"],
                        constraints=it["alignment_constraints"],
                        schema_sql=it["schema"],
                        user_query=it["user_content"],
                        llm=alignment_llm
                    )
                )
            sync_results = await asyncio.gather(*sync_tasks, return_exceptions=True)

            desync_count = 0
            for it, sr in zip(items_needing_sync_check, sync_results):
                if isinstance(sr, Exception):
                    logger.warning(f"Semantic sync check exception, keeping item: {sr}")
                    continue
                synced, reason = sr
                if not synced:
                    it["sql_valid"] = False
                    it["sql_error"] = f"semantic_desync_after_repair: {reason}"
                    file_diag["semantic_desync_flagged_count"] += 1
                    desync_count += 1
                    it["record"].setdefault("diagnostics", {})
                    it["record"]["diagnostics"]["semantic_desync_reason"] = reason

            if desync_count > 0:
                logger.info(
                    f"[{os.path.basename(file_path)}] Semantic desync hard-drop: "
                    f"{desync_count} items failed sync check"
                )
                file_diag["semantic_desync_final_drop_count"] += desync_count

        # 收集最终有效记录
        final_valid_records = [it["record"] for it in sql_items if it["sql_valid"]]
        for it in sql_items:
            if not it["sql_valid"]:
                reason = it.get("sql_error", "unknown_sql_failure") or "unknown_sql_failure"
                reason_key = reason.split(":")[0][:80]
                file_diag["final_drop_reasons"][reason_key] = file_diag["final_drop_reasons"].get(reason_key, 0) + 1

        file_valid = len(final_valid_records)
        file_invalid = file_total - file_valid
        
        return final_valid_records, file_total, file_valid, file_invalid, file_diag

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
        
        final_valid_records, file_total, file_valid, file_invalid, file_diag = await _process_single_file(data_path)
        
        result.total_records = file_total
        result.valid_records = file_valid
        result.invalid_records = file_invalid
        for key in ("schema_prebuild_count",
                     "leakage_fixed_count", "evidence_injected_count", "intent_mismatch_fixed_count",
                     "hardcode_flagged_count", "schema_alignment_regen_count",
                     "evidence_gap_injected_count", "schema_alignment_final_drop_count",
                     "semantic_desync_flagged_count", "semantic_desync_final_drop_count",
                     "schema_pollution_blocked_count", "strict_grouping_fixed_count",
                     "llm_semantic_checked_count", "llm_leakage_flagged_count",
                     "llm_join_misalignment_count", "non_query_sql_dropped_count",
                     "sql_preprocess_regen_count", "sql_preprocess_regen_success_count",
                     "sqlite_dialect_warning_count"):
            aggregate_diag[key] += file_diag.get(key, 0)
        for k, v in file_diag.get("final_drop_reasons", {}).items():
            aggregate_diag["final_drop_reasons"][k] = aggregate_diag["final_drop_reasons"].get(k, 0) + v

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
                final_valid_records, file_total, file_valid, file_invalid, file_diag = await _process_single_file(input_path)
                
                result.total_records += file_total
                result.valid_records += file_valid
                result.invalid_records += file_invalid
                for key in ("schema_prebuild_count",
                             "leakage_fixed_count", "evidence_injected_count", "intent_mismatch_fixed_count",
                             "hardcode_flagged_count", "schema_alignment_regen_count",
                             "evidence_gap_injected_count", "schema_alignment_final_drop_count",
                             "semantic_desync_flagged_count", "semantic_desync_final_drop_count",
                             "schema_pollution_blocked_count", "strict_grouping_fixed_count",
                             "llm_semantic_checked_count", "llm_leakage_flagged_count",
                             "llm_join_misalignment_count", "non_query_sql_dropped_count",
                             "sql_preprocess_regen_count", "sql_preprocess_regen_success_count",
                             "sqlite_dialect_warning_count"):
                    aggregate_diag[key] += file_diag.get(key, 0)
                for k, v in file_diag.get("final_drop_reasons", {}).items():
                    aggregate_diag["final_drop_reasons"][k] = aggregate_diag["final_drop_reasons"].get(k, 0) + v

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
    agent_config = state.get("constructor", {}) or {}
    
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
    agent_config = state.get("constructor", {}) or {}
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
    constructor_state = state.get("constructor", {})
    category = constructor_state.get("category", "PT").upper()
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
