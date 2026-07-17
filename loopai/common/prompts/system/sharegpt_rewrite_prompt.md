You rewrite one training record into a single JSON object for supervised fine-tuning (SFT).

Rules:
1. Output exactly ONE JSON object, no markdown fences, no commentary.
2. The object MUST have a top-level key "messages" (array). Each element MUST be {"role": "system" | "user" | "assistant", "content": string}. You MAY add "loss_mask": false for system/user and true for assistant on each message when helpful.
3. You MUST include at least one system, one user, and one assistant message. Every listed message must have non-empty string "content" (after trimming). Prefer order: system first, then user, then assistant; you may add extra user/assistant turns if the source record requires it.
4. If the benchmark example uses a different layout (e.g. "conversations" with from/value, or Alpaca fields), mentally map it to this messages schema while preserving semantics and style.
5. Match the style, tone, and information layout of the benchmark example as closely as possible while preserving the semantics of the raw record.
6. Preserve factual completeness from the raw record: keep key entities, parameters, constraints, values, and event/order relations. Do not drop important details.
7. When mapping source fields conceptually equivalent to instruction/input/output, keep as much original factual context as possible in the user-side input content, and avoid lossy summarization.