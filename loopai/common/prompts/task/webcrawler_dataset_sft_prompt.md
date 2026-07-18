User Objective: {user_query}

Webpage Information:
- Title: {webpage_title}
- URL: {webpage_url}

Code Blocks Information:
{code_blocks_info}

Task: Extract up to {max_records} high-quality SFT training samples from these code blocks.

Requirements:
1. Only extract code blocks that are directly relevant to the user objective "{user_query}"
2. Generate a question-answer pair for each code block:
   - user message: Describe the code's functionality (e.g., "Write a Python function that implements XXX functionality", "Generate SQL statements that can complete XXX task")
   - assistant message: Complete code block
3. **SQL Special Handling**: If the code is SQL statements, you must additionally add a system message:
   - Analyze all table names, field names, and relationships involved in the SQL
   - **Infer and construct** a reasonable database Schema (CREATE TABLE statements) based on the SQL
   - Schema should include: semantically intuitive naming, primary key definitions, foreign key relationships, field types, and necessary comments
   - Place the Schema in the first position of the messages array with role set to "system"
4. If code blocks are irrelevant or of low quality, they can be skipped
5. loss_mask: Set system and user messages to false, assistant messages to true

Return a JSON object in the following format:

**For SQL-type code (must include system message and inferred Schema):**
{{
  "records": [
    {{
      "messages": [
        {{
          "role": "system",
          "content": "Database Schema inferred from SQL, format as follows:\n-- Table description\nCREATE TABLE table_name (\n    column_name DATA_TYPE PRIMARY KEY,\n    ...\n    FOREIGN KEY (col) REFERENCES other_table(col)\n);",
          "loss_mask": false
        }},
        {{
          "role": "user",
          "content": "Description of SQL functionality",
          "loss_mask": false
        }},
        {{
          "role": "assistant",
          "content": "Complete SQL code",
          "loss_mask": true
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
  "reason": "Explanation"
}}

**For non-SQL code (no system message needed):**
{{
  "records": [
    {{
      "messages": [
        {{
          "role": "user",
          "content": "Description of code functionality",
          "loss_mask": false
        }},
        {{
          "role": "assistant",
          "content": "Complete code block",
          "loss_mask": true
        }}
      ],
      "system": null,
      "meta": {{
        "source": "{webpage_url}",
        "language": "detected programming language",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "Explanation"
}}

If no relevant content is found, return: {{"records": [], "reason": "Detailed explanation of why no relevant code was found"}}