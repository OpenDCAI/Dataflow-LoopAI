# Example: General Reasoning SFT Pipeline

## Use Case

Generate and validate reasoning answers for non-math or mixed-domain SFT data.
Input rows must be normalized to `instruction` and `golden_answer`.

## Operator Decision

```json
{
  "ops": ["ReasoningQuestionFilter", "ReasoningQuestionGenerator", "ReasoningAnswerGenerator", "ReasoningAnswerModelJudgeFilter", "ReasoningAnswerNgramFilter"],
  "field_flow": "instruction+golden_answer -> screened/synthesized instruction -> generated_cot -> model-judge result -> ngram-filtered rows",
  "reason": "Use general reasoning prompts for mixed-domain questions, judge generated answers against the reference, then reject degenerate reasoning text."
}
```

## Standard Pipeline

```python
import os

from dataflow.operators.reasoning import (
    ReasoningAnswerGenerator,
    ReasoningAnswerModelJudgeFilter,
    ReasoningAnswerNgramFilter,
    ReasoningQuestionFilter,
    ReasoningQuestionGenerator,
)
from dataflow.prompts.model_evaluation.general import AnswerJudgePrompt
from dataflow.prompts.reasoning.general import (
    GeneralAnswerGeneratorPrompt,
    GeneralQuestionFilterPrompt,
    GeneralQuestionSynthesisPrompt,
)
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage


class GeneralReasoningPipeline:
    def __init__(self):
        self.storage = FileStorage(
            first_entry_file_name=os.environ.get("DATAFLOW_INPUT", "reasoning_general.jsonl"),
            cache_path=os.environ.get("DATAFLOW_CACHE_DIR", "./cache_reasoning_general"),
            file_name_prefix=os.environ.get("DATAFLOW_PREFIX", "reasoning_general_step"),
            cache_type="jsonl",
        )
        self.llm = APILLMServing_request(
            api_url=os.environ.get("DATAFLOW_API_URL", "http://127.0.0.1:8855/responseProxy/v1/chat/completions"),
            key_name_of_api_key="DF_API_KEY",
            model_name=os.environ.get("DATAFLOW_MODEL", "dataflow"),
            max_workers=10,
        )
        self.question_filter = ReasoningQuestionFilter(
            system_prompt="Evaluate whether this is a complete, solvable reasoning task and return the required JSON.",
            llm_serving=self.llm,
            prompt_template=GeneralQuestionFilterPrompt(),
        )
        self.question_generator = ReasoningQuestionGenerator(
            num_prompts=1,
            llm_serving=self.llm,
            prompt_template=GeneralQuestionSynthesisPrompt(),
        )
        self.answer_generator = ReasoningAnswerGenerator(
            llm_serving=self.llm,
            prompt_template=GeneralAnswerGeneratorPrompt(),
        )
        self.answer_judge = ReasoningAnswerModelJudgeFilter(
            llm_serving=self.llm,
            prompt_template=AnswerJudgePrompt(),
            keep_all_samples=False,
        )
        self.ngram_filter = ReasoningAnswerNgramFilter(min_score=0.1, max_score=1.0, ngrams=5)

    def forward(self):
        self.question_filter.run(storage=self.storage.step(), input_key="instruction")
        self.question_generator.run(storage=self.storage.step(), input_key="instruction")
        self.answer_generator.run(storage=self.storage.step(), input_key="instruction", output_key="generated_cot")
        self.answer_judge.run(
            storage=self.storage.step(),
            input_question_key="instruction",
            input_answer_key="generated_cot",
            input_reference_key="golden_answer",
        )
        self.ngram_filter.run(
            storage=self.storage.step(),
            input_question_key="instruction",
            input_answer_key="generated_cot",
        )


if __name__ == "__main__":
    GeneralReasoningPipeline().forward()
```

## Key Notes

- Prefer the math-specific example when symbolic or numeric verification is required.
- `keep_all_samples=False` makes the answer judge a real quality gate.
- Do not use a missing reference as if it were a gold answer; route unreferenced rows separately.
