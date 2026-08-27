# Example: Agentic RAG Text Pipelines

## Use Case

Build grounded single-hop or multi-hop QA from text corpora and retrieved text
documents. Inputs are textual `contents` or a retrieval query plus text corpus.

## Official Pipeline Choices

### Atomic QA

```text
contents
  -> AgenticRAGAtomicTaskGenerator
  -> refined_answer + golden_doc_answer
  -> AgenticRAGQAF1SampleEvaluator
  -> F1Score
```

Use this for document-grounded atomic questions with an explicit reference
answer. Required fields after generation are `refined_answer` and
`golden_doc_answer`.

### Verified Multi-Hop QA

```text
query -> RetrievalGenerator -> retrieved_docs -> explode text documents
  -> FormatStrPromptedGenerator (atomic facts)
  -> merge facts into multi-hop task
  -> GeneralFilter
  -> refine question/answer
  -> independent reasoning, shortcut, and final-answer verification branches
  -> retained grounded QA
```

Core operators are `RetrievalGenerator`, `FormatStrPromptedGenerator`,
`PandasOperator`, and `GeneralFilter`. The retrieval operator is asynchronous;
its pipeline entry point must await `run()`.

## Key Notes

- Keep retrieved document identifiers and supporting text for provenance.
- Reject questions answerable without the intended evidence hop.
- Do not use this pipeline when no text retriever or grounded corpus exists.
