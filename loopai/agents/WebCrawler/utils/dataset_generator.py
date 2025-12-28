"""
Dataset Generation Utilities for WebCrawler

This module provides functions to generate SFT and PT format datasets from crawled web content.
"""

import json
from typing import Dict, Any, List
from datetime import datetime, timezone

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


async def generate_sft_records(
    llm: ChatOpenAI,
    prompt_loader: PromptLoader,
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    code_blocks: List[Dict[str, str]],
    max_records: int = 10,
    min_relevance_score: float = 0.6,
    max_content_length: int = 50000,
) -> Dict[str, Any]:
    """
    Generate SFT records from webpage code blocks using LLM
    
    For each code block, generate:
    - user message: description of what the code does (question)
    - assistant message: the code block itself (answer)
    
    Args:
        llm: ChatOpenAI instance for LLM inference
        prompt_loader: PromptLoader instance for loading prompts
        user_query: User's query/goal
        webpage_title: Title of the webpage
        webpage_content: Content of the webpage
        webpage_url: URL of the webpage
        code_blocks: List of code blocks extracted from webpage
        max_records: Maximum number of records to generate
        min_relevance_score: Minimum relevance score for filtering
        max_content_length: Maximum content length for processing
    
    Returns:
        Dict with:
        - records: List of SFT records in intermediate format
        - reason: Reason string if no records generated
    """
    try:
        # Get prompt
        try:
            system_prompt = prompt_loader("system", "webcrawler_dataset_sft_prompt")
            task_prompt_template = prompt_loader("task", "webcrawler_dataset_sft_prompt")
            
            # Format code blocks info for prompt
            code_blocks_info = []
            for i, block in enumerate(code_blocks[:max_records], 1):
                code_preview = block.get("code", "")[:500] if len(block.get("code", "")) > 500 else block.get("code", "")
                code_blocks_info.append(f"代码块 {i} ({block.get('language', 'unknown')}):\n```{block.get('language', '')}\n{code_preview}\n```")
            
            task_prompt = task_prompt_template.format(
                user_query=user_query,
                webpage_title=webpage_title,
                webpage_url=webpage_url,
                code_blocks_info="\n\n".join(code_blocks_info),
                max_records=max_records,
            )
        except Exception as e:
            logger.warning(f"Failed to load SFT prompt, using default: {e}")
            system_prompt = get_default_sft_system_prompt()
            task_prompt = get_default_sft_task_prompt(user_query, webpage_title, webpage_url, code_blocks, max_records)
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_prompt),
        ]
        
        # Invoke LLM
        response = await llm.ainvoke(messages)
        response_content = response.content if hasattr(response, 'content') else str(response)
        
        # Parse JSON response
        clean_response = response_content.strip().replace("```json", "").replace("```", "")
        
        try:
            result = json.loads(clean_response)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise
        
        # Extract records and reason
        records = []
        model_reason = ""
        
        if isinstance(result, dict) and "records" in result:
            records = result.get("records", [])
            model_reason = result.get("reason", "")
        elif isinstance(result, list):
            records = result
        else:
            logger.warning(f"Unexpected SFT result format: {type(result)}")
            return {
                "records": [],
                "reason": f"Unexpected result format: {type(result)}",
            }
        
        # Filter by relevance and validate
        valid_records = []
        for record in records:
            # Check relevance score
            if "relevance_score" in record:
                if record["relevance_score"] < min_relevance_score:
                    continue
            
            # Validate SFT structure
            if "messages" in record and record["messages"] and len(record["messages"]) > 0:
                valid_records.append(record)
        
        # Add metadata to each record
        for record in valid_records:
            if "meta" not in record:
                record["meta"] = {}
            record["meta"]["source"] = record["meta"].get("source", webpage_url)
            record["meta"]["webpage_title"] = webpage_title
            record["meta"]["webpage_url"] = webpage_url
            record["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        final_records = valid_records[:max_records]
        
        reason = model_reason
        if not final_records and not model_reason:
            reason = "Failed to generate valid SFT records from code blocks"
        
        return {
            "records": final_records,
            "reason": reason,
        }
        
    except Exception as e:
        logger.error(f"Error generating SFT records: {e}", exc_info=True)
        return {
            "records": [],
            "reason": f"Error during SFT generation: {str(e)}",
        }


async def generate_pt_records(
    llm: ChatOpenAI,
    prompt_loader: PromptLoader,
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    max_records: int = 10,
    min_relevance_score: float = 0.6,
    max_content_length: int = 50000,
) -> Dict[str, Any]:
    """
    Generate PT records from webpage markdown content using LLM
    
    Args:
        llm: ChatOpenAI instance for LLM inference
        prompt_loader: PromptLoader instance for loading prompts
        user_query: User's query/goal
        webpage_title: Title of the webpage
        webpage_content: Content of the webpage
        webpage_url: URL of the webpage
        max_records: Maximum number of records to generate
        min_relevance_score: Minimum relevance score for filtering
        max_content_length: Maximum content length for processing
    
    Returns:
        Dict with:
        - records: List of PT records in intermediate format
        - reason: Reason string if no records generated
    """
    try:
        # Get prompt
        try:
            system_prompt = prompt_loader("system", "webcrawler_dataset_pt_prompt")
            task_prompt_template = prompt_loader("task", "webcrawler_dataset_pt_prompt")
            task_prompt = task_prompt_template.format(
                user_query=user_query,
                webpage_title=webpage_title,
                webpage_content=webpage_content[:max_content_length],  
                webpage_url=webpage_url,
                max_records=max_records,
            )
        except Exception as e:
            logger.warning(f"Failed to load PT prompt, using default: {e}")
            system_prompt = get_default_pt_system_prompt()
            task_prompt = get_default_pt_task_prompt(user_query, webpage_title, webpage_content, webpage_url, max_records, max_content_length)
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_prompt),
        ]
        
        # Invoke LLM
        response = await llm.ainvoke(messages)
        response_content = response.content if hasattr(response, 'content') else str(response)
        
        # Parse JSON response
        clean_response = response_content.strip().replace("```json", "").replace("```", "")
        
        try:
            result = json.loads(clean_response)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise
        
        # Extract records and reason
        records = []
        model_reason = ""
        
        if isinstance(result, dict) and "records" in result:
            records = result.get("records", [])
            model_reason = result.get("reason", "")
        elif isinstance(result, list):
            records = result
        elif isinstance(result, dict) and "text" in result:
            records = [result]
        else:
            logger.warning(f"Unexpected PT result format: {type(result)}")
            return {
                "records": [],
                "reason": f"Unexpected result format: {type(result)}",
            }
        
        # Filter by relevance and validate
        valid_records = []
        for record in records:
            # Check relevance score
            if "relevance_score" in record:
                if record["relevance_score"] < min_relevance_score:
                    continue
            
            # Validate PT structure
            if "text" in record and record["text"]:
                valid_records.append(record)
        
        # Add metadata to each record
        for record in valid_records:
            if "meta" not in record:
                record["meta"] = {}
            record["meta"]["source"] = record["meta"].get("source", webpage_url)
            record["meta"]["webpage_title"] = webpage_title
            record["meta"]["webpage_url"] = webpage_url
            record["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        final_records = valid_records[:max_records]
        
        reason = model_reason
        if not final_records and not model_reason:
            reason = "Failed to generate valid PT records from webpage content"
        
        return {
            "records": final_records,
            "reason": reason,
        }
        
    except Exception as e:
        logger.error(f"Error generating PT records: {e}", exc_info=True)
        return {
            "records": [],
            "reason": f"Error during PT generation: {str(e)}",
        }


async def generate_webpage_summary_and_relevance(
    llm: ChatOpenAI,
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
) -> Dict[str, Any]:
    """
    Generate summary and relevance score for webpages that produced SFT records
    
    Args:
        llm: ChatOpenAI instance for LLM inference
        user_query: User's query/goal
        webpage_title: Title of the webpage
        webpage_content: Content of the webpage
        webpage_url: URL of the webpage
    
    Returns:
        Dict with:
        - summary: Summary of the webpage content
        - relevance_score: Relevance score with user query (0-10)
    """
    try:
        system_prompt = """你是一个内容分析专家，专门分析网页内容并生成摘要。

你的任务是：
1. 分析网页内容，生成简洁的摘要（3-5句话）
2. 评估网页内容与用户目标的相关性（0-10分）

输出格式：
{
  "summary": "网页内容的简洁摘要",
  "relevance_score": 数字(0-10)
}

相关性评分标准：
- 10分：完全匹配用户目标，包含大量高质量、直接相关的内容
- 7-9分：高度相关，包含用户需要的主要内容
- 4-6分：部分相关，包含一些有用的内容
- 1-3分：低相关性，仅边缘相关
- 0分：完全不相关"""

        task_prompt = f"""用户目标: {user_query}

网页信息:
- 标题: {webpage_title}
- URL: {webpage_url}
- 内容 (前 3000 字符): {webpage_content[:3000]}

任务: 
1. 生成网页内容的简洁摘要（3-5句话，突出核心内容和价值）
2. 评估该网页与用户目标的相关性（0-10分）

返回 JSON 格式（不要markdown代码块）：
{{
  "summary": "网页内容摘要",
  "relevance_score": 8
}}"""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_prompt),
        ]
        
        # Invoke LLM
        response = await llm.ainvoke(messages)
        response_content = response.content if hasattr(response, 'content') else str(response)
        
        # Parse JSON response
        clean_response = response_content.strip().replace("```json", "").replace("```", "")
        
        try:
            result = json.loads(clean_response)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise
        
        summary = result.get("summary", "摘要生成失败")
        relevance_score = result.get("relevance_score", 0)
        
        return {
            "summary": summary,
            "relevance_score": relevance_score,
        }
        
    except Exception as e:
        logger.error(f"Error generating webpage summary and relevance: {e}", exc_info=True)
        return {
            "summary": f"摘要生成失败: {str(e)}",
            "relevance_score": 0,
        }


# =============================================================================
# Default Prompt Functions (used as fallback when prompt templates are not found)
# =============================================================================

def get_default_sft_system_prompt() -> str:
    """Default system prompt for SFT dataset generation from code blocks"""
    return """你是一个数据提取专家，专门从网页代码块中生成高质量的 SFT（监督微调）训练数据。

你的任务是分析网页中的代码块，为每个代码块生成一个问答对：
- system 消息（仅对text2sql任务有效）：根据SQL语句推断并生成对应的数据库Schema定义
- user 消息：对代码功能的描述（例如："生成能够实现XXX功能的Python代码"）
- assistant 消息：代码块本身

特殊处理 - SQL/数据库相关代码：
对于SQL查询、数据库操作等代码，你需要：
1. 分析SQL语句中涉及的表、字段、关联关系
2. 根据SQL语句推断并构造合理的数据库Schema，包括：
   - 语义直观的表名和字段命名
   - 显式定义的主键（PRIMARY KEY）
   - 外键关联关系（FOREIGN KEY REFERENCES）
   - 字段类型和注释说明
3. 将Schema放入 system 角色的 content 中

Schema示例格式：
```sql
-- Table storing user profile information
CREATE TABLE users (
    user_id INT PRIMARY KEY,
    full_name VARCHAR(50),
    -- Account status: 1=Active, 0=Inactive, -1=Banned
    account_status INT 
);

-- Table storing transaction history
CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    user_id INT,
    total_amount DECIMAL(10, 2), -- Amount in USD
    created_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

输出格式必须符合以下中间态 JSON Schema：

{
  "messages": [
    {
      "role": "user",
      "content": "string",
      "loss_mask": false
    },
    {
      "role": "assistant",
      "content": "string",
      "loss_mask": true
    },
        {
      "role": "system",
      "content": "数据库Schema定义（仅SQL类代码需要）",
      "loss_mask": false
    }
  ],
  "system": "string | null",
  "meta": {
    "source": "string | null",
    "language": "string | null",
    "timestamp": "string | null",
    "token_count": "string | null",
    "quality_score": "string | null",
    "original_id": "string | null"
  }
}

关键要求：
1. **高相关性**：只提取与用户目标高度相关的代码块
2. **准确的功能描述**：user 消息应准确描述代码的功能和用途
3. **完整的代码**：assistant 消息应包含完整的代码块
4. **SQL专项处理**：对于SQL代码，必须在system消息中提供推断出的数据库Schema
5. **多条记录**：一个网页如果有多个代码块，可以生成多条记录
6. **质量优先**：优先处理高质量、有实用价值的代码示例"""


def get_default_sft_task_prompt(
    user_query: str,
    webpage_title: str,
    webpage_url: str,
    code_blocks: List[Dict[str, str]],
    max_records: int,
) -> str:
    """Default task prompt for SFT dataset generation"""
    # Format code blocks info
    code_blocks_info = []
    for i, block in enumerate(code_blocks[:max_records], 1):
        code_preview = block.get("code", "")[:500] if len(block.get("code", "")) > 500 else block.get("code", "")
        code_blocks_info.append(f"代码块 {i} ({block.get('language', 'unknown')}):\n```{block.get('language', '')}\n{code_preview}\n```")
    
    return f"""用户目标: {user_query}

网页信息:
- 标题: {webpage_title}
- URL: {webpage_url}

代码块信息:
{chr(10).join(code_blocks_info)}

任务: 从这些代码块中提取最多 {max_records} 个高质量的 SFT 训练样本。

要求:
1. 只提取与用户目标 "{user_query}" 直接相关的代码块
2. 为每个代码块生成一个问答对：
   - user 消息：描述代码的功能（例如："编写一个Python函数实现XXX功能"、"生成能够完成XXX任务的SQL语句"）
   - assistant 消息：完整的代码块
3. **SQL专项处理**：如果代码是SQL语句，必须额外添加一个 system 消息：
   - 分析SQL中涉及的所有表名、字段名、关联关系
   - **根据SQL推断并构造**合理的数据库Schema（CREATE TABLE语句）
   - Schema应包含：语义直观的命名、主键定义、外键关联、字段类型、必要的注释
   - 将Schema放入messages数组的第一个位置，role设为"system"
4. 如果代码块不相关或质量不高，可以跳过
5. loss_mask: system 和 user 消息设为 false, assistant 消息设为 true

返回 JSON 对象，格式如下：

**对于SQL类代码（必须包含system消息和推断的Schema）：**
{{
  "records": [
    {{
      "messages": [
        {{
          "role": "user",
          "content": "对SQL功能的描述",
          "loss_mask": false
        }},
        {{
          "role": "assistant",
          "content": "完整的SQL代码",
          "loss_mask": true
        }},
                {{
          "role": "system",
          "content": "根据SQL推断的数据库Schema，格式如下：\\n-- Table description\\nCREATE TABLE table_name (\\n    column_name DATA_TYPE PRIMARY KEY,\\n    ...\\n    FOREIGN KEY (col) REFERENCES other_table(col)\\n);",
          "loss_mask": false
        }}
      ],
      "system": null,
      "meta": {{
        "source": "{webpage_url}",
        "language": "sql",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "说明"
}}

**对于非SQL类代码（不需要system消息）：**
{{
  "records": [
    {{
      "messages": [
        {{
          "role": "user",
          "content": "对代码功能的描述",
          "loss_mask": false
        }},
        {{
          "role": "assistant",
          "content": "完整的代码块",
          "loss_mask": true
        }}
      ],
      "system": null,
      "meta": {{
        "source": "{webpage_url}",
        "language": "检测到的编程语言",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "说明"
}}

如果没有找到相关内容，返回: {{"records": [], "reason": "详细说明为什么没有找到相关代码"}}"""


def get_default_pt_system_prompt() -> str:
    """Default system prompt for PT dataset generation"""
    return """你是一个数据提取专家，专门从网页内容中提取适合语言模型预训练（PT）的文本数据。

你的任务是从网页的 Markdown 内容中提取高质量、连贯的文本，用于预训练数据集。

输出格式必须符合以下中间态 JSON Schema：

{
  "text": "string | array<string> | null",
  "meta": {
    "source": "string | null",
    "language": "string | null",
    "timestamp": "string | null",
    "token_count": "string | null",
    "quality_score": "string | null",
    "original_id": "string | null"
  }
}

关键要求：
1. **高相关性**：只提取与用户目标高度相关的内容
2. **文本提取**：提取连贯、完整的文本段落，适合语言模型预训练
3. **多条记录**：如果网页包含多个相关主题部分，可以拆分成多条记录
4. **质量优先**：优先提取结构良好、信息丰富的文本内容"""


def get_default_pt_task_prompt(
    user_query: str,
    webpage_title: str,
    webpage_content: str,
    webpage_url: str,
    max_records: int,
    max_content_length: int = 50000,
) -> str:
    """Default task prompt for PT dataset generation"""
    return f"""用户目标: {user_query}

网页信息:
- 标题: {webpage_title}
- URL: {webpage_url}
- 内容 (前 {max_content_length} 字符): {webpage_content[:max_content_length]}

任务: 从这个网页中提取最多 {max_records} 个高质量的 PT（预训练）文本记录。

要求:
1. 只提取与用户目标 "{user_query}" 直接相关的内容
2. 每条记录应包含连贯、完整的文本段落
3. 如果网页包含多个相关主题部分，可以拆分成多条记录
4. 如果内容不相关，返回空数组
5. 包含元数据（source、language 等）

返回 JSON 对象，格式如下：
{{
  "records": [
    {{
      "text": "提取的文本内容（实际文本，不是字段路径）",
      "meta": {{
        "source": "{webpage_url}",
        "language": "检测到的语言代码 (zh/en/mix) 或 null",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "生成或未生成记录的原因说明。如果 records 数组为空，此字段必填。"
}}

如果没有找到相关内容，返回: {{"records": [], "reason": "详细说明为什么没有找到相关内容"}}"""

