import json
from enum import Enum
from datetime import datetime
from dataclasses import dataclass

from datasets import load_dataset

from lcb_runner.utils.dataset_utils import load_local_dataset


@dataclass
class CodeExecutionProblem:
    question_id: str
    contest_id: str
    contest_date: datetime
    difficulty: str
    function_name: str
    code: str
    input: str
    output: str
    id: str
    problem_id: str
    numsteps: int

    def __post_init__(self):
        # `datasets` hands over a datetime for the Hub dataset, while a local export
        # (`Dataset.to_json`) gives epoch milliseconds or an ISO string; normalise all
        # three so `insert_output` can call isoformat().
        if isinstance(self.contest_date, str):
            self.contest_date = datetime.fromisoformat(self.contest_date)
        elif isinstance(self.contest_date, (int, float)):
            self.contest_date = datetime.fromtimestamp(self.contest_date / 1000)

    def insert_output(self, output_list: list[str], pred_list: list[str]) -> dict:
        return {
            "question_id": self.question_id,
            "contest_id": self.contest_id,
            "contest_date": self.contest_date.isoformat(),
            "difficulty": self.difficulty,
            "function_name": self.function_name,
            "code": self.code,
            "input": self.input,
            "output": self.output,
            "id": self.id,
            "problem_id": self.problem_id,
            "numsteps": self.numsteps,
            "output_list": output_list,
            "pred_list": pred_list,
        }

    def insert_output_evaluation(
        self, output_list: list[str], code_list: list[str], graded_list: list[bool]
    ) -> dict:
        output = self.insert_output(output_list, code_list)
        output["graded_list"] = graded_list
        output["pass@1"] = graded_list.count(True) / len(graded_list)
        return output

    def get_evaluation_sample(self) -> dict:
        return {
            "code": self.code,
            "input": self.input,
            "output": self.output,
        }


def load_code_execution_dataset(release_version="release_v1", local_dataset_path=None) -> list[CodeExecutionProblem]:
    if local_dataset_path is not None:
        dataset = load_local_dataset(local_dataset_path)
    else:
        dataset = load_dataset("livecodebench/execution-v2", split="test")
    dataset = [CodeExecutionProblem(**p) for p in dataset]  # type: ignore
    print(f"Loaded {len(dataset)} problems")
    return dataset


if __name__ == "__main__":
    dataset = load_code_execution_dataset()
