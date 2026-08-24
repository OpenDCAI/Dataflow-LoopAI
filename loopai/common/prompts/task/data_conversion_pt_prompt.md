You are given a dataset from HuggingFace. Your task is to identify field mappings for language model pretraining, including the main text content and metadata fields.

[User Requirements]
User's original request: {user_target}

[Dataset Information]
Dataset Columns: {column_names}
Sample Data: {sample_rows}

[Instruction]
1. **Relevance**: Determine whether the dataset content aligns with {user_target}. As long as the topic/semantics match, treat it as relevant—even if task types differ. Only output null when the sample clearly has nothing to do with the requested domain. **If the dataset does not match the user requirements or the data information consists of pre-processed tokens, return null.**

2. **Text Field Mapping**: Identify the most appropriate field (column or nested path) containing long-form text useful for pretraining.
   - Read the actual values, not just field names
   - Support nested structures using dot/bracket notation (e.g., `posts[*].body`, `metadata.description`)
   - Support multi-field concatenation: return an array of field paths (e.g., `["title", "body"]`) when multiple fields should be combined
   - Prefer fields with rich natural-language content

3. **Metadata Field Mapping**: Map metadata fields that provide context about the data:
   - **source** (required if meta exists): Data source identifier. Can be a field path or a direct string value
   - **language** (recommended): Language code (ISO 639-1). Can be a field path or a direct string value
   - **timestamp** (optional): Time field path
   - **token_count** (optional): Pre-computed token count field path
   - **quality_score** (optional): Quality score field path (0.0-1.0)
   - **original_id** (optional): Original dataset ID field path

[OUTPUT]
Return a JSON object in ```json block following this structure:
{{
  "text": "field_path | [field_path, ...] | null",
  "meta": {{
    "source": "field_path | string_value | null",
    "language": "field_path | string_value | null",
    "timestamp": "field_path | null",
    "token_count": "field_path | null",
    "quality_score": "field_path | null",
    "original_id": "field_path | null"
  }}
}}

If the dataset is irrelevant, return {{"text": null, "meta": null}}.