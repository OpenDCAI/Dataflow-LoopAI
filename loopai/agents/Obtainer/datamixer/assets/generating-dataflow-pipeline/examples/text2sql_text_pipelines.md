# Example: Text-to-SQL and VectorSQL Pipelines

## Use Case

Generate or refine executable SQL training data grounded in an actual database
schema, then construct natural-language questions, prompts, CoT, and difficulty labels.

## SQL Generation

```text
DatabaseManager schema
  -> SQLGenerator -> sql + db_id + sql_complexity_type
  -> SQLExecutabilityFilter
  -> Text2SQLQuestionGenerator -> question + evidence
  -> Text2SQLCorrespondenceFilter
  -> Text2SQLPromptGenerator -> prompt
  -> Text2SQLCoTGenerator -> cot_responses
  -> Text2SQLCoTVotingGenerator -> cot_reasoning
  -> SQLComponentClassifier + SQLExecutionClassifier
```

## SQL Refinement

```text
sql + db_id -> SQLExecutabilityFilter
  -> SQLVariationGenerator -> varied sql + sql_variation_type
  -> SQLExecutabilityFilter
  -> question/evidence/correspondence/prompt/CoT/voting/difficulty chain
```

## VectorSQL Generation

```text
database columns -> SQLByColumnGenerator
  -> SQLExecutionFilter
  -> Text2SQLQuestionGenerator
  -> Text2SQLPromptGenerator
  -> SQLComponentClassifier + SQLExecutionClassifier
```

## Key Notes

- A configured `DatabaseManager` and reachable databases are mandatory.
- Execute SQL before accepting it; text similarity cannot establish correctness.
- Keep `db_id`, schema snapshot, evidence, execution result, and variation type.
