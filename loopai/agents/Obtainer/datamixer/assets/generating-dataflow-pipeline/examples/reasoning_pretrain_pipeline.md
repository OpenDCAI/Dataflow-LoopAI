# Example: Reasoning SFT-to-Pretrain Pipeline

## Use Case

Generate verified math reasoning and convert the question-answer pair into a
single pretraining `text` field. Inputs require `instruction`, `output`, and
`golden_answer`.

## Operator Decision

```json
{
  "ops": ["ReasoningQuestionFilter", "ReasoningQuestionGenerator", "ReasoningAnswerPipelineRootFilter", "ReasoningAnswerGenerator", "ReasoningAnswerNgramFilter", "ReasoningPretrainFormatConvertGenerator"],
  "field_flow": "instruction+output+golden_answer -> screened/synthesized instruction -> answer-root route -> generated_cot -> ngram filter -> text",
  "reason": "The pipeline checks the existing answer branch, generates native math reasoning, filters degenerate traces, then explicitly converts SFT fields to pretraining text."
}
```

## Standard Pipeline Core

```python
from dataflow.operators.reasoning import (
    ReasoningAnswerGenerator,
    ReasoningAnswerNgramFilter,
    ReasoningAnswerPipelineRootFilter,
    ReasoningPretrainFormatConvertGenerator,
    ReasoningQuestionFilter,
    ReasoningQuestionGenerator,
)
from dataflow.prompts.reasoning.math import (
    MathAnswerGeneratorPrompt,
    MathQuestionFilterPrompt,
    MathQuestionSynthesisPrompt,
)

question_filter = ReasoningQuestionFilter(
    system_prompt="Evaluate this mathematical problem and return the required JSON.",
    llm_serving=llm_serving,
    prompt_template=MathQuestionFilterPrompt(),
)
question_generator = ReasoningQuestionGenerator(
    num_prompts=3,
    llm_serving=llm_serving,
    prompt_template=MathQuestionSynthesisPrompt(),
)
answer_root = ReasoningAnswerPipelineRootFilter()
answer_generator = ReasoningAnswerGenerator(
    llm_serving=llm_serving,
    prompt_template=MathAnswerGeneratorPrompt(),
)
ngram_filter = ReasoningAnswerNgramFilter(min_score=0.1, max_score=1.0, ngrams=5)
to_pretrain = ReasoningPretrainFormatConvertGenerator()

question_filter.run(storage=storage.step(), input_key="instruction")
question_generator.run(storage=storage.step(), input_key="instruction")
answer_root.run(storage=storage.step(), input_answer_key="output", input_gt_key="golden_answer")
answer_generator.run(storage=storage.step(), input_key="instruction", output_key="generated_cot")
ngram_filter.run(storage=storage.step(), input_question_key="instruction", input_answer_key="generated_cot")
to_pretrain.run(
    storage=storage.step(),
    input_read_key_question="instruction",
    input_read_key_answer="generated_cot",
    output_key="text",
)
```

## Key Notes

- Use this only when the requested output is pretraining text; do not erase SFT
  structure when the downstream trainer expects messages or instruction/output.
- Keep the pre-conversion fields for provenance and auditing.
- Apply mathematical correctness verification before conversion when reliable
  `golden_answer` values are available.
