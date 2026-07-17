User query: {user_query}

Research objective: {objective}

Please analyze the user's query and objective to determine if they need:
- SFT data (question-answer pairs, instruction-following data, conversational data)
- PT data (raw text corpus, documents, code, continuous text)

Consider:
- Does the user mention questions, answers, instructions, conversations? → SFT
- Does the user mention raw text, documents, corpus, code datasets? → PT
- What is the primary goal: teaching models to follow instructions (SFT) or building foundational understanding (PT)?

Return a JSON object with:
{
    "category": "SFT" or "PT",
    "reasoning": "Brief explanation"
}

Or simply return "SFT" or "PT" as a string.