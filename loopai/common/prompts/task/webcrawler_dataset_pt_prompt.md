User Objective: {user_query}

Webpage Information:
- Title: {webpage_title}
- URL: {webpage_url}
- Content (first 8000 characters): {webpage_content}

Task: Extract up to {max_records} high-quality PT (pre-training) text records from this webpage.

Requirements:
1. Only extract content that is directly relevant to the user objective "{user_query}"
2. Each record should contain coherent, complete text paragraphs
3. If the webpage contains multiple relevant topic sections, they can be split into multiple records
4. If content is not relevant, return an empty array
5. Include metadata (source, language, etc.)

Return a JSON object in the following format:
{{
  "records": [
    {{
      "text": "extracted text content (actual text, not field path)",
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
  "reason": "Explanation of why records were or were not generated. This field is REQUIRED if the records array is empty."
}}

If no relevant content is found, return: {{"records": [], "reason": "Detailed explanation of why no relevant content was found"}}