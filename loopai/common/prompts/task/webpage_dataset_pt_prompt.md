User Objective: {user_query}

Webpage Information:
- Title: {webpage_title}
- URL: {webpage_url}
- Content (first 8000 chars):
```text
{webpage_content}
```

Task: Extract up to {max_records} high-quality text records from this webpage that are HIGHLY relevant to the user's objective.

**CRITICAL REQUIREMENTS:**
1. **High Relevance**: Extract ONLY content that is DIRECTLY and HIGHLY relevant to: {user_query}
   - If content is not relevant, return an empty array in "records" and provide a detailed "reason" explaining why
   - Each record must have relevance_score >= 0.7
2. **Text Quality**: Each record should contain continuous, coherent text suitable for pre-training
   - Avoid fragmented or incomplete sentences
   - Prefer well-structured, complete paragraphs or sections
3. **Multiple Records**: If the webpage contains multiple relevant sections, create separate records for each
4. **Metadata**: Include proper metadata (source, language, etc.) when available

**RETURN FORMAT:**
Return a JSON object with the following structure:
{{
  "records": [
    {{
      "text": "extracted text content (string, not field path)",
      "meta": {{
        "source": "{webpage_url}",
        "language": "detected language code (zh/en/mix) or null",
        "timestamp": null,
        "token_count": null,
        "quality_score": null,
        "original_id": null
      }},
      "relevance_score": 0.0-1.0
    }}
  ],
  "reason": "Explanation of why records were or were not generated. If records array is empty, this field is REQUIRED and should explain why no relevant content was found."
}}

**IMPORTANT:**
- Return actual text content in "text" field, NOT field paths
- If no relevant content found, return: {{"records": [], "reason": "详细说明为什么没有找到相关内容"}}
- Only include records with relevance_score >= 0.7
- The "reason" field is REQUIRED when records array is empty