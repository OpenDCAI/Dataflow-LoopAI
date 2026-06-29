Research objective: {research_objective}

Candidate URLs (from current page):
{url_list}

Webpage content (truncated to 8000 chars):
{webpage_content}

Goal: Select up to {topk} URLs that are most likely to contain information or data relevant to the research objective.

Selection rules:
1) Prefer resource-rich links (datasets, papers, code repos, tutorials, docs, forum threads) over generic navigation/ads/login.
2) Use page context to judge relevance; avoid obviously off-topic domains.
3) Return only URLs from the provided list.

Return JSON: {{"urls": ["url1", "url2", ...]}} (length <= {topk}). If nothing is relevant, return an empty array.