Research objective: '{objective}'

User message: {message}

Please generate 3-5 diverse search queries in English that will help gather comprehensive information about the research objective. Even if the user's input is in another language, translate it to English search queries suitable for web search engines.

**PRIORITY: Target High-Quality Data Sources**

Your queries should prioritize finding:
1. **Forums and Community Platforms**: Include terms like "forum", "discussion", "reddit", "stackoverflow", "github discussions", or platform-specific queries
2. **Resource Websites**: Include terms like "dataset", "repository", "examples", "tutorial", "documentation", "code samples"
3. **Q&A Sites**: Include terms like "Q&A", "question answer", "FAQ", "how to"
4. **Knowledge Bases**: Include terms like "wiki", "knowledge base", "documentation", "guide"

**Query Examples for High-Quality Sources:**
- "{objective} forum discussion"
- "{objective} dataset repository"
- "{objective} examples code"
- "{objective} Q&A site:stackoverflow.com"
- "{objective} tutorial guide"

Return only a JSON array of English query strings, for example: ["code dataset for LLM fine-tuning site:github.com", "programming examples forum", "code repository tutorial"].