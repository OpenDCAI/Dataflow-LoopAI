# Example: Math Question Fusion Pipeline

## Use Case

Create harder and more diverse math questions by pairing semantically similar
source problems and applying sequential, parallel, and condition fusion.
Input rows require a normalized `question` field.

## Operator Decision

```json
{
  "ops": ["EmbeddingGenerator", "PandasOperator", "ReasoningQuestionFusionGenerator", "ReasoningQuestionFusionGenerator", "ReasoningQuestionFusionGenerator", "PandasOperator", "ReasoningQuestionSolvableSampleEvaluator", "PandasOperator"],
  "field_flow": "question -> embeddings -> most_similar_problem -> three fusion families -> questions -> question_solvability -> refined_question",
  "reason": "Embedding-based pairing grounds synthesis in related problems; three native fusion prompts create controlled complexity, followed by solvability evaluation."
}
```

## Reference Pipeline Shape

```python
from dataflow.operators.core_text import EmbeddingGenerator, PandasOperator
from dataflow.operators.reasoning import (
    ReasoningQuestionFusionGenerator,
    ReasoningQuestionSolvableSampleEvaluator,
)
from dataflow.prompts.reasoning.math import (
    MathQuestionConditionFusionGeneratorPrompt,
    MathQuestionEvaluatorPrompt,
    MathQuestionParallelFusionGeneratorPrompt,
    MathQuestionSequentialFusionGeneratorPrompt,
)

# Ordered operator construction after FileStorage and serving setup:
embedding = EmbeddingGenerator(embedding_serving=embedding_serving)
sequential = ReasoningQuestionFusionGenerator(
    num_prompts=1, llm_serving=llm_serving,
    prompt_template=MathQuestionSequentialFusionGeneratorPrompt(),
)
parallel = ReasoningQuestionFusionGenerator(
    num_prompts=1, llm_serving=llm_serving,
    prompt_template=MathQuestionParallelFusionGeneratorPrompt(),
)
condition = ReasoningQuestionFusionGenerator(
    num_prompts=2, llm_serving=llm_serving,
    prompt_template=MathQuestionConditionFusionGeneratorPrompt(),
)
solvability = ReasoningQuestionSolvableSampleEvaluator(
    llm_serving=llm_serving,
    prompt_template=MathQuestionEvaluatorPrompt(),
)

# Ordered forward flow:
embedding.run(storage=storage.step(), input_key="question", output_key="embeddings")
most_similar_matcher.run(storage=storage.step())
drop_embeddings.run(storage=storage.step())
sequential.run(storage=storage.step(), input_problem_1="question", input_problem_2="most_similar_problem", output_key="sequential_fusion")
parallel.run(storage=storage.step(), input_problem_1="question", input_problem_2="most_similar_problem", output_key="parallel_fusion")
condition.run(storage=storage.step(), input_problem_1="question", input_problem_2="most_similar_problem", output_key="condition_fusion")
combine_questions.run(storage=storage.step())
solvability.run(storage=storage.step(), input_key="questions", output_key="question_solvability")
extract_refined_question.run(storage=storage.step())
```

## Key Notes

- The two `PandasOperator` transforms must pair rows by embedding similarity and
  reshape generated `*_question_N` columns into a `questions` column.
- This pipeline generates questions, not answers. Feed accepted
  `refined_question` rows into the math reasoning pipeline to generate and
  verify `generated_cot`.
- The official implementation uses a GPU similarity matrix. For large pools,
  batch or index embeddings instead of materializing an unbounded dense matrix.
