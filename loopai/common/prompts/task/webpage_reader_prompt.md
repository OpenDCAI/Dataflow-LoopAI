Your Current Subtask Objective: '{objective}'

Analyze the following webpage text and hyperlinks to decide on the best action. If current goal is downloading datasets, prioritize finding all relevant direct download links.

Discovered Hyperlinks (absolute URLs):
{urls_block}

Visible text content:
```text
{text_content}
```

Return a JSON object with the keys: "action", "description", and depending on the action, either "urls" (for download) or "url" (for navigate). Also include the keys "discovered_urls" (list of links you considered) and "is_relevant": true or false depending on whether the content contains information related to the user's objective.