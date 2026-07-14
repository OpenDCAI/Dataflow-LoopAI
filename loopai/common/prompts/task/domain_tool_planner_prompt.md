Analyze the following information and determine which domain cleaning tool(s) should be used:

User Query/Requirement: {user_query}

Dataset Background: {datasets_background}

**Critical Analysis Guidelines:**
1. **If the dataset is described as "问答" (Q&A), "对话" (dialogue), or contains question-answer pairs**, even if about programming topics, use **normal_data**
2. **Only use code_generate if the dataset explicitly contains code generation tasks** where assistant responses are actual executable code (functions, scripts, code blocks)
3. **If the dataset background mentions "问答" (Q&A), "对话" (dialogue), "instruction-following", or similar terms**, use **normal_data**, regardless of the topic
4. **Programming Q&A datasets** (where users ask about programming and assistants explain) should use **normal_data**, NOT code_generate

Based on the user query and dataset background, determine which domain tool(s) are most appropriate.
Return a JSON array of tool names, for example: ["text2sql"] or ["code_generate"] or ["normal_data"] or ["text2sql", "code_generate"] if multiple tools are needed.

**Remember: Q&A or dialogue format about programming topics = normal_data, NOT code_generate**
**Remember: norma_filter_and_add_cot is ONLY selected when the user explicitly requests adding CoT/reasoning paths/thinking steps to the dataset. If there is no such explicit request, do NOT include it.**

Only return the JSON array, no other text.