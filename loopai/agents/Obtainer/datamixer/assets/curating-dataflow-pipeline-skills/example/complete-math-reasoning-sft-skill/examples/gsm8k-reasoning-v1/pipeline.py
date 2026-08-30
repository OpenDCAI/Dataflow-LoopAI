import os
import re

from dataflow.operators.core_text import (
    GeneralFilter,
    PandasOperator,
    PromptedEvaluator,
    PromptedGenerator,
)
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage


class MathReasoningSFTPipeline:
    def __init__(self):
        self.storage = FileStorage(
            first_entry_file_name=os.environ["DATAFLOW_INPUT"],
            cache_path=os.environ.get("DATAFLOW_CACHE_DIR", "./cache"),
            file_name_prefix=os.environ.get("DATAFLOW_PREFIX", "math_reasoning"),
            cache_type="jsonl",
        )
        serving = APILLMServing_request(
            api_url=os.environ["DF_API_URL"],
            key_name_of_api_key="DF_API_KEY",
            model_name=os.environ.get("DF_MODEL_NAME", "qwen-plus"),
            max_workers=8,
        )
        def normalize(df):
            df = df.copy()
            df["canonical_question"] = df.get("question").fillna(df.get("problem"))
            df["canonical_gold"] = df.get("answer").fillna(df.get("final_answer"))
            return df

        def validate_and_format(df):
            df = df.copy()
            number = r"[-+]?\d+(?:\.\d+)?"
            df["derived_answer"] = df["reasoning"].astype(str).map(
                lambda value: (re.findall(number, value.replace(",", "")) or [""])[-1]
            )
            df["gold_matches"] = (
                df["derived_answer"].str.strip()
                == df["canonical_gold"].astype(str).str.replace(",", "", regex=False).str.strip()
            )
            df["instruction"] = df["canonical_question"]
            df["output"] = df["canonical_gold"].astype(str)
            df["quality_audit_text"] = (
                "Question: " + df["canonical_question"].astype(str)
                + "\nReasoning: " + df["reasoning"].astype(str)
                + "\nVerified answer: " + df["canonical_gold"].astype(str)
            )
            return df

        self.normalize = PandasOperator([normalize])
        self.structure_filter = GeneralFilter([
            lambda df: df["sample_id"].notna(),
            lambda df: df["canonical_question"].astype(str).str.len() > 10,
            lambda df: df["canonical_gold"].astype(str).str.len() > 0,
        ])
        self.reason = PromptedGenerator(
            serving,
            system_prompt="Solve the problem carefully. Return reasoning followed by a final answer.",
            user_prompt="",
        )
        self.quality = PromptedEvaluator(
            serving,
            system_prompt="Score correctness, coherence, and suitability for math SFT from 1 to 4. Return one integer.",
        )
        self.validate = PandasOperator([validate_and_format])
        self.correctness_filter = GeneralFilter([lambda df: df["gold_matches"]])
        self.quality_filter = GeneralFilter([lambda df: df["quality_score"] >= 3])

    def forward(self):
        self.normalize.run(storage=self.storage.step())
        self.structure_filter.run(storage=self.storage.step())
        self.reason.run(
            storage=self.storage.step(), input_key="canonical_question", output_key="reasoning"
        )
        self.validate.run(storage=self.storage.step())
        self.correctness_filter.run(storage=self.storage.step())
        self.quality.run(
            storage=self.storage.step(),
            input_key="quality_audit_text",
            output_key="quality_score",
        )
        self.quality_filter.run(storage=self.storage.step())


if __name__ == "__main__":
    MathReasoningSFTPipeline().forward()
