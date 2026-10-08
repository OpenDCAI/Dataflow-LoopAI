# Example: Custom-Domain Reasoning Pipeline

## Use Case

Use native reasoning operators with domain-specific filtering, synthesis, and
answer formats when the built-in general or math prompts do not express the
target contract.

## Operator Decision

```json
{
  "ops": ["ReasoningQuestionFilter", "ReasoningQuestionGenerator", "ReasoningAnswerGenerator", "ReasoningAnswerNgramFilter"],
  "field_flow": "instruction -> domain-filtered/synthesized instruction -> generated_cot -> ngram-filtered rows",
  "reason": "DIY prompt wrappers retain native reasoning operator behavior while allowing an explicit vertical-domain contract."
}
```

## Standard Pipeline Core

```python
from dataflow.operators.reasoning import (
    ReasoningAnswerGenerator,
    ReasoningAnswerNgramFilter,
    ReasoningQuestionFilter,
    ReasoningQuestionGenerator,
)
from dataflow.prompts.reasoning.diy import (
    DiyAnswerGeneratorPrompt,
    DiyQuestionFilterPrompt,
    DiyQuestionSynthesisPrompt,
)

FILTER_PROMPT = """Decide whether {question} is a complete task in TARGET_DOMAIN.
Return exactly {\"judgement_test\": true or false, \"error_type\": string or null}."""
SYNTHESIS_PROMPT = """Create one new TARGET_DOMAIN problem grounded in this source:
{question}
Return only the new standalone problem."""
ANSWER_PROMPT = """Solve the task accurately. Give necessary reasoning and a clear final answer."""

question_filter = ReasoningQuestionFilter(
    system_prompt="Evaluate target-domain tasks and return the required JSON.",
    llm_serving=llm_serving,
    prompt_template=DiyQuestionFilterPrompt(FILTER_PROMPT),
)
question_generator = ReasoningQuestionGenerator(
    num_prompts=1,
    llm_serving=llm_serving,
    prompt_template=DiyQuestionSynthesisPrompt(SYNTHESIS_PROMPT),
)
answer_generator = ReasoningAnswerGenerator(
    llm_serving=llm_serving,
    prompt_template=DiyAnswerGeneratorPrompt(ANSWER_PROMPT),
)
ngram_filter = ReasoningAnswerNgramFilter(min_score=0.1, max_score=1.0, ngrams=5)

question_filter.run(storage=storage.step(), input_key="instruction")
question_generator.run(storage=storage.step(), input_key="instruction")
answer_generator.run(storage=storage.step(), input_key="instruction", output_key="generated_cot")
ngram_filter.run(storage=storage.step(), input_question_key="instruction", input_answer_key="generated_cot")
```

## Key Notes

- Replace `TARGET_DOMAIN` and all output constraints with the actual task
  contract; never ship placeholder prompts.
- Preserve the exact JSON schema expected by `DiyQuestionFilterPrompt`.
- Add a domain-appropriate correctness judge after generation. N-gram quality
  alone does not establish factual correctness.
