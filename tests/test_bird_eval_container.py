"""Exercise the standalone BIRD evaluator with a real temporary SQLite DB."""

import json
import importlib.util
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


EVALUATOR = (
    Path(__file__).resolve().parents[1]
    / "loopai/skills/Judger/docker/bird_eval/bird_eval.py"
)
ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "loopai/skills/Judger/utils/evaluate_bird.py"
)


def load_adapter():
    # The local smoke test needs only Python's standard library; importing the
    # whole LoopAI package would require the training/server environment.
    spec = importlib.util.spec_from_file_location("judger_bird_adapter", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_database(tmp_path):
    root = tmp_path / "dev_databases"
    path = root / "tiny" / "tiny.sqlite"
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("CREATE TABLE values_table (value INTEGER)")
            connection.executemany("INSERT INTO values_table VALUES (?)", [(1,), (2,)])
    return root


def run_evaluator(tmp_path, database_root, samples):
    samples_path = tmp_path / "samples.jsonl"
    samples_path.write_text(
        "".join(json.dumps(row) + "\n" for row in samples), encoding="utf-8"
    )
    results_path = tmp_path / "output" / "results.jsonl"
    summary_path = tmp_path / "output" / "summary.json"
    completed = subprocess.run(
        [
            sys.executable, str(EVALUATOR), "--samples", str(samples_path),
            "--databases", str(database_root), "--results", str(results_path),
            "--summary", str(summary_path),
        ],
        capture_output=True, text=True, check=False,
    )
    return completed, results_path, summary_path


def check_grades_real_sql_and_writes_compatible_result_fields(tmp_path):
    root = make_database(tmp_path)
    host_db_path = "/server/dev_databases/tiny/tiny.sqlite"
    samples = [
        {
            "task_id": "right", "question": "List both values",
            "db_file": host_db_path,
            "ground_truth": "SELECT value FROM values_table ORDER BY value",
            "completion": "```sql\nSELECT value FROM values_table ORDER BY value DESC\n```",
        },
        {
            "task_id": "wrong", "question": "Find value one",
            "db_file": host_db_path,
            "ground_truth": "SELECT value FROM values_table WHERE value = 1",
            "completion": "SELECT value FROM values_table WHERE value = 2",
        },
        {
            "task_id": "invalid", "question": "Find value two",
            "db_file": host_db_path,
            "ground_truth": "SELECT value FROM values_table WHERE value = 2",
            "completion": "SELECT missing_column FROM values_table",
        },
    ]

    completed, results_path, summary_path = run_evaluator(tmp_path, root, samples)

    assert completed.returncode == 0, completed.stderr
    results = [json.loads(line) for line in results_path.read_text().splitlines()]
    assert [row["passed"] for row in results] == [True, False, False]
    assert results[0]["completion"] == samples[0]["completion"]
    assert results[0]["completion_id"] == 0
    assert results[0]["error"] is None
    assert results[1]["error"] == "Result mismatch"
    assert "missing_column" in results[2]["error"]

    summary = json.loads(summary_path.read_text())
    assert summary["num_questions"] == 3
    assert summary["num_samples"] == 3
    assert summary["pass_at_k"]["pass@1"] == 1 / 3
    assert math.isclose(summary["execution_accuracy_percent"], 100 / 3)
    assert summary["sqlite_version"]


def check_repeated_answers_report_pass_at_one_without_official_ex(tmp_path):
    root = make_database(tmp_path)
    base = {
        "task_id": "repeated", "db_id": "tiny",
        "ground_truth": "SELECT value FROM values_table WHERE value = 1",
    }
    samples = [
        {**base, "completion": "SELECT value FROM values_table WHERE value = 2"},
        {**base, "completion": "SELECT value FROM values_table WHERE value = 1"},
    ]

    completed, results_path, summary_path = run_evaluator(tmp_path, root, samples)

    assert completed.returncode == 0, completed.stderr
    results = [json.loads(line) for line in results_path.read_text().splitlines()]
    assert [row["completion_id"] for row in results] == [0, 1]
    summary = json.loads(summary_path.read_text())
    assert summary["pass_at_k"] == {"pass@1": 0.5}
    assert summary["execution_accuracy_percent"] is None


def check_missing_database_is_not_scored_as_model_failure(tmp_path):
    root = make_database(tmp_path)
    samples = [{
        "task_id": "missing", "db_id": "absent",
        "ground_truth": "SELECT 1", "completion": "SELECT 1",
    }]

    completed, results_path, summary_path = run_evaluator(tmp_path, root, samples)

    assert completed.returncode != 0
    assert "database is missing" in completed.stderr
    assert not results_path.exists()
    assert not summary_path.exists()


class BirdEvaluatorTest(unittest.TestCase):
    def _run_case(self, check):
        with tempfile.TemporaryDirectory() as temp_dir:
            check(Path(temp_dir))

    def test_grades_real_sql_and_writes_compatible_result_fields(self):
        self._run_case(check_grades_real_sql_and_writes_compatible_result_fields)

    def test_repeated_answers_report_pass_at_one_without_official_ex(self):
        self._run_case(check_repeated_answers_report_pass_at_one_without_official_ex)

    def test_missing_database_is_not_scored_as_model_failure(self):
        self._run_case(check_missing_database_is_not_scored_as_model_failure)

    def test_adapter_rejects_incomplete_model_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            databases = make_database(root)
            problems = root / "problems.jsonl"
            problems.write_text(
                json.dumps({"task_id": "one"}) + "\n"
                + json.dumps({"task_id": "two"}) + "\n", encoding="utf-8"
            )
            samples = root / "samples.jsonl"
            samples.write_text(json.dumps({"task_id": "one"}) + "\n", encoding="utf-8")
            state = {
                "task_id": "t1", "output_dir": str(root / "outputs"),
                "judger": {
                    "eval_problem_path": str(problems),
                    "eval_text2sql_dir": str(databases),
                    "eval_case_num": 1,
                    "output_case_path": str(samples),
                },
            }
            with self.assertRaisesRegex(ValueError, "missing=\\['two'\\]"):
                load_adapter().run_evaluate_bird(state)

    def test_adapter_rejects_empty_model_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            databases = make_database(root)
            problems = root / "problems.jsonl"
            problems.write_text(json.dumps({"task_id": "one"}) + "\n", encoding="utf-8")
            samples = root / "samples.jsonl"
            samples.write_text(json.dumps({"task_id": "one", "completion": ""}) + "\n", encoding="utf-8")
            state = {
                "task_id": "t1", "output_dir": str(root / "outputs"),
                "judger": {
                    "eval_problem_path": str(problems),
                    "eval_text2sql_dir": str(databases),
                    "eval_case_num": 1,
                    "output_case_path": str(samples),
                },
            }
            previous_output = root / "outputs" / "t1" / "judger" / "run" / "problems"
            previous_output.mkdir(parents=True)
            previous_result = previous_output / "problems_result.jsonl"
            previous_result.write_text("old score\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "completions are empty"):
                load_adapter().run_evaluate_bird(state)
            self.assertFalse(previous_result.exists())

    def test_adapter_builds_image_when_missing(self):
        adapter = load_adapter()
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1 if "inspect" in command else 0)

        with patch.object(adapter.subprocess, "run", side_effect=fake_run):
            adapter._ensure_bird_eval_image("/fake/docker", {"PATH": "/fake"})
        self.assertEqual(commands[0][:3], ["/fake/docker", "image", "inspect"])
        self.assertEqual(commands[1][:4], ["/fake/docker", "build", "-t", adapter.BIRD_EVAL_IMAGE])
        self.assertEqual(Path(commands[1][-1]), adapter.BIRD_EVAL_CONTEXT)

    def test_adapter_saves_result_and_metrics_from_container(self):
        if not os.environ.get("BIRD_EVAL_IMAGE"):
            self.skipTest("set BIRD_EVAL_IMAGE to test a built Docker image")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            databases = make_database(root)
            problems = root / "problems.jsonl"
            problems.write_text(
                json.dumps({"task_id": "one"}) + "\n"
                + json.dumps({"task_id": "two"}) + "\n", encoding="utf-8"
            )
            samples = root / "samples.jsonl"
            rows = [
                {
                    "task_id": "one", "db_id": "tiny",
                    "ground_truth": "SELECT value FROM values_table WHERE value = 1",
                    "completion": "SELECT value FROM values_table WHERE value = 1",
                },
                {
                    "task_id": "two", "db_id": "tiny",
                    "ground_truth": "SELECT value FROM values_table WHERE value = 2",
                    "completion": "SELECT value FROM values_table WHERE value = 1",
                },
            ]
            samples.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            state = {
                "task_id": "t1", "output_dir": str(root / "outputs"),
                "version_id": "v1", "judger": {
                    "bench_name": "bird", "eval_problem_path": str(problems),
                    "eval_text2sql_dir": str(databases),
                    "eval_case_num": 1, "output_case_path": str(samples),
                },
            }
            result = load_adapter().run_evaluate_bird(state)
            self.assertEqual(result["metrics"], {"pass@1": 0.5})
            self.assertEqual(result["summary"]["execution_accuracy_percent"], 50.0)
            self.assertEqual(
                [json.loads(line)["passed"] for line in Path(result["result_path"]).read_text().splitlines()],
                [True, False],
            )
            self.assertTrue(Path(result["summary_path"]).is_file())

    def test_docker_image_grades_mounted_database(self):
        image = os.environ.get("BIRD_EVAL_IMAGE")
        if not image:
            self.skipTest("set BIRD_EVAL_IMAGE to test a built Docker image")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            databases = make_database(root)
            sample = {
                "task_id": "container", "db_id": "tiny",
                "ground_truth": "SELECT value FROM values_table WHERE value = 1",
                "completion": "SELECT value FROM values_table WHERE value = 1",
            }
            samples_path = root / "samples.jsonl"
            samples_path.write_text(json.dumps(sample) + "\n", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            completed = subprocess.run(
                [
                    "docker", "run", "--rm", "--network", "none",
                    "--mount", f"type=bind,source={samples_path},target=/input/samples.jsonl,readonly",
                    "--mount", f"type=bind,source={databases},target=/databases,readonly",
                    "--mount", f"type=bind,source={output},target=/output",
                    image, "--samples", "/input/samples.jsonl",
                    "--databases", "/databases",
                    "--results", "/output/results.jsonl",
                    "--summary", "/output/summary.json",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["execution_accuracy_percent"], 100.0)
            results = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
            self.assertTrue(results[0]["passed"])
