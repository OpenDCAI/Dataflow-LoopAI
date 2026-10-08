# Example: High-Quality Math Reasoning Pipeline

## Use Case

Build or clean math SFT data whose target capability requires explicit,
verifiable reasoning. Normalize heterogeneous source records before this
pipeline so each input row has `instruction` and `golden_answer`.

## Operator Decision

```json
{
  "ops": ["ReasoningQuestionFilter", "ReasoningQuestionGenerator", "ReasoningQuestionFilter", "ReasoningQuestionDifficultySampleEvaluator", "ReasoningQuestionCategorySampleEvaluator", "ReasoningAnswerGenerator", "ReasoningAnswerFormatterFilter", "ReasoningAnswerTokenLengthFilter", "ReasoningAnswerGroundTruthFilter", "ReasoningAnswerNgramFilter"],
  "field_flow": "instruction+golden_answer -> screened/synthesized instruction -> question_difficulty+question_category -> generated_cot -> format/length/ground-truth/ngram filters -> retained math SFT rows",
  "reason": "Math-specific reasoning operators are preferred over generic prompted operators. The pipeline screens and characterizes the problem, generates CoT with the native reasoning generator, then validates answer format, length, mathematical agreement with the gold answer, and reasoning-text quality."
}
```

## Standard Pipeline

This example is adapted from DataFlow's bundled
`statics/pipelines/api_pipelines/reasoning_math_pipeline.py`.

```python
import os

from dataflow.operators.reasoning import (
    ReasoningAnswerFormatterFilter,
    ReasoningAnswerGenerator,
    ReasoningAnswerGroundTruthFilter,
    ReasoningAnswerNgramFilter,
    ReasoningAnswerTokenLengthFilter,
    ReasoningQuestionCategorySampleEvaluator,
    ReasoningQuestionDifficultySampleEvaluator,
    ReasoningQuestionFilter,
    ReasoningQuestionGenerator,
)
from dataflow.prompts.reasoning.math import (
    MathAnswerGeneratorPrompt,
    MathQuestionFilterPrompt,
    MathQuestionSynthesisPrompt,
)
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage


class ReasoningMathPipeline:
    def __init__(self):
        self.storage = FileStorage(
            first_entry_file_name=os.environ.get(
                "DATAFLOW_INPUT", "math_reasoning_input.jsonl"
            ),
            cache_path=os.environ.get("DATAFLOW_CACHE_DIR", "./cache_math"),
            file_name_prefix=os.environ.get(
                "DATAFLOW_PREFIX", "reasoning_math_step"
            ),
            cache_type="jsonl",
        )
        self.llm_serving = APILLMServing_request(
            api_url=os.environ.get(
                "DATAFLOW_API_URL",
                "http://127.0.0.1:8855/responseProxy/v1/chat/completions",
            ),
            key_name_of_api_key="DF_API_KEY",
            model_name=os.environ.get("DATAFLOW_MODEL", "dataflow"),
            max_workers=10,
        )

        self.initial_question_filter = ReasoningQuestionFilter(
            system_prompt=(
                "You are an expert in evaluating mathematical problems. "
                "Follow the instructions and return the required JSON."
            ),
            llm_serving=self.llm_serving,
            prompt_template=MathQuestionFilterPrompt(),
        )
        self.question_generator = ReasoningQuestionGenerator(
            num_prompts=3,
            llm_serving=self.llm_serving,
            prompt_template=MathQuestionSynthesisPrompt(),
        )
        self.generated_question_filter = ReasoningQuestionFilter(
            system_prompt=(
                "You are an expert in evaluating mathematical problems. "
                "Follow the instructions and return the required JSON."
            ),
            llm_serving=self.llm_serving,
            prompt_template=MathQuestionFilterPrompt(),
        )
        self.difficulty_evaluator = ReasoningQuestionDifficultySampleEvaluator(
            llm_serving=self.llm_serving
        )
        self.category_evaluator = ReasoningQuestionCategorySampleEvaluator(
            llm_serving=self.llm_serving
        )
        self.answer_generator = ReasoningAnswerGenerator(
            llm_serving=self.llm_serving,
            prompt_template=MathAnswerGeneratorPrompt(),
        )
        self.answer_format_filter = ReasoningAnswerFormatterFilter()
        self.answer_length_filter = ReasoningAnswerTokenLengthFilter(
            max_answer_token_length=8192,
            tokenizer_dir="Qwen/Qwen2.5-0.5B-Instruct",
        )
        self.answer_ground_truth_filter = ReasoningAnswerGroundTruthFilter()
        self.answer_ngram_filter = ReasoningAnswerNgramFilter(
            min_score=0.1,
            max_score=1.0,
            ngrams=5,
        )

    def forward(self):
        self.initial_question_filter.run(
            storage=self.storage.step(), input_key="instruction"
        )
        self.question_generator.run(
            storage=self.storage.step(), input_key="instruction"
        )
        self.generated_question_filter.run(
            storage=self.storage.step(), input_key="instruction"
        )
        self.difficulty_evaluator.run(
            storage=self.storage.step(),
            input_key="instruction",
            output_key="question_difficulty",
        )
        self.category_evaluator.run(
            storage=self.storage.step(),
            input_key="instruction",
            output_key="question_category",
        )
        self.answer_generator.run(
            storage=self.storage.step(),
            input_key="instruction",
            output_key="generated_cot",
        )
        self.answer_format_filter.run(
            storage=self.storage.step(), input_key="generated_cot"
        )
        self.answer_length_filter.run(
            storage=self.storage.step(), input_key="generated_cot"
        )
        self.answer_ground_truth_filter.run(
            storage=self.storage.step(),
            input_test_answer_key="generated_cot",
            input_gt_answer_key="golden_answer",
        )
        self.answer_ngram_filter.run(
            storage=self.storage.step(),
            input_question_key="instruction",
            input_answer_key="generated_cot",
        )


if __name__ == "__main__":
    ReasoningMathPipeline().forward()
```

## Key Notes

- Use this chain only after mapping source schemas to `instruction` and
  `golden_answer`; do not guess field roles from names alone.
- When input mixes trace-bearing and answer-only supervision, route only rows
  that require constructed reasoning into `ReasoningAnswerGenerator`.
- `ReasoningAnswerGroundTruthFilter` is the key correctness gate: generated
  reasoning must resolve to the supplied gold answer.
- Do not catch and ignore operator failures. Missing scores or generated fields
  must fail closed or cause the affected row to be filtered.
