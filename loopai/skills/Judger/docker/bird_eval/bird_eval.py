"""Grade LoopAI's generated Text2SQL JSONL against BIRD SQLite databases.

The EX comparison follows BIRD's execution accuracy rule: execute both SQL
queries and compare their result sets, without considering row order or
duplicate rows. This entry point is standalone so it can run in a small image.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any


class EvaluationError(Exception):
    """The benchmark input or environment is invalid; do not report a score."""


def extract_sql(completion: str) -> str:
    """Use the last SQL fence, matching the existing Judger extraction rule."""
    blocks = re.findall(r"```sql\s*(.*?)```", completion, flags=re.IGNORECASE | re.DOTALL)
    return (blocks[-1] if blocks else completion).strip()


def database_path(sample: dict[str, Any], database_root: Path) -> Path:
    db_id = sample.get("db_id")
    if db_id is None:
        db_file = sample.get("db_file")
        if not isinstance(db_file, str) or not db_file:
            raise EvaluationError("sample needs db_id or db_file")
        db_id = Path(db_file).stem

    if (not isinstance(db_id, str) or not db_id or db_id in {".", ".."}
            or "/" in db_id or "\\" in db_id):
        raise EvaluationError(f"invalid db_id: {db_id!r}")

    path = (database_root / db_id / f"{db_id}.sqlite").resolve()
    if not path.is_relative_to(database_root):
        raise EvaluationError(f"database path escapes root: {db_id!r}")
    if not path.is_file():
        raise EvaluationError(f"database is missing: {path}")
    return path


def execute_sql(connection: sqlite3.Connection, sql: str, timeout: float) -> list[tuple]:
    deadline = time.monotonic() + timeout
    expired = False

    def check_deadline() -> int:
        nonlocal expired
        expired = time.monotonic() >= deadline
        return int(expired)

    connection.set_progress_handler(check_deadline, 1000)
    try:
        return connection.execute(sql).fetchall()
    except sqlite3.OperationalError as exc:
        if expired:
            raise TimeoutError(f"SQL timed out after {timeout:g}s") from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


def grade(sample: dict[str, Any], database_root: Path, timeout: float) -> dict[str, Any]:
    for field in ("task_id", "completion", "ground_truth"):
        if field not in sample:
            raise EvaluationError(f"sample is missing {field}")
    if not isinstance(sample["task_id"], (str, int)):
        raise EvaluationError("task_id must be a string or integer")
    if not isinstance(sample["completion"], str):
        raise EvaluationError("completion must be a string")
    if not isinstance(sample["ground_truth"], str) or not sample["ground_truth"].strip():
        raise EvaluationError(f"ground_truth is empty for task {sample['task_id']}")

    path = database_path(sample, database_root)
    prediction = extract_sql(sample["completion"])
    output = dict(sample)
    output.update(passed=False, result="", error=None)

    # Both queries use one read-only connection, as in the existing scorer.
    # query_only also rejects writes against an attached database.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        try:
            expected = execute_sql(connection, sample["ground_truth"], timeout)
        except (sqlite3.Error, TimeoutError) as exc:
            raise EvaluationError(
                f"ground_truth failed for task {sample['task_id']}: {exc}"
            ) from exc

        if not prediction:
            output["error"] = "Empty SQL prediction"
            return output

        try:
            actual = execute_sql(connection, prediction, timeout)
        except (sqlite3.Error, TimeoutError) as exc:
            output["error"] = str(exc)
            return output

    output["result"] = str(actual)[:200]
    output["passed"] = set(actual) == set(expected)
    if not output["passed"]:
        output["error"] = "Result mismatch"
    return output


def pass_at_k(number: int, correct: int, k: int) -> float:
    """Unbiased pass@k estimator used by the current Judger."""
    if number - correct < k:
        return 1.0
    failure_probability = 1.0
    for denominator in range(number - correct + 1, number + 1):
        failure_probability *= 1.0 - k / denominator
    return 1.0 - failure_probability


def evaluate(
    samples_path: Path,
    database_root: Path,
    results_path: Path,
    summary_path: Path,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if timeout <= 0:
        raise EvaluationError("timeout must be positive")
    if not samples_path.is_file():
        raise EvaluationError(f"samples file is missing: {samples_path}")
    database_root = database_root.resolve()
    if not database_root.is_dir():
        raise EvaluationError(f"database directory is missing: {database_root}")
    if results_path.resolve() == summary_path.resolve():
        raise EvaluationError("results and summary paths must differ")

    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    task_inputs: dict[str, tuple[Path, str, Any]] = {}
    with samples_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                sample = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvaluationError(f"invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(sample, dict):
                raise EvaluationError(f"line {line_number} must be a JSON object")
            try:
                result = grade(sample, database_root, timeout)
            except EvaluationError as exc:
                raise EvaluationError(f"line {line_number}: {exc}") from exc
            task_id = str(result["task_id"])
            task_input = (
                database_path(sample, database_root),
                sample["ground_truth"],
                sample.get("question"),
            )
            if task_id in task_inputs and task_inputs[task_id] != task_input:
                raise EvaluationError(
                    f"line {line_number}: task {task_id!r} has inconsistent question, "
                    "database, or ground_truth"
                )
            task_inputs[task_id] = task_input
            result["completion_id"] = counts[task_id]
            counts[task_id] += 1
            correct[task_id] += int(result["passed"])
            rows.append(result)

    if not rows:
        raise EvaluationError("samples file contains no rows")

    ks = (1, 10, 100)
    scores = {
        f"pass@{k}": sum(pass_at_k(counts[task], correct[task], k) for task in counts)
        / len(counts)
        for k in ks if all(number >= k for number in counts.values())
    }
    one_sample_per_question = all(number == 1 for number in counts.values())
    summary = {
        "benchmark": "bird",
        "num_questions": len(counts),
        "num_samples": len(rows),
        "passed_samples": sum(correct.values()),
        "pass_at_k": scores,  # fractions, like the existing Judger text2sql metrics
        "execution_accuracy_percent": 100.0 * scores["pass@1"] if one_sample_per_question else None,
        "python_version": sys.version.split()[0],
        "sqlite_version": sqlite3.sqlite_version,
    }

    results_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8") as result_file:
        for row in rows:
            result_file.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate LoopAI Text2SQL samples on BIRD databases")
    parser.add_argument("--samples", required=True, type=Path, help="generated *_sample.jsonl")
    parser.add_argument("--databases", required=True, type=Path, help="BIRD dev_databases directory")
    parser.add_argument("--results", required=True, type=Path, help="per-sample output JSONL")
    parser.add_argument("--summary", required=True, type=Path, help="aggregate output JSON")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per SQL query")
    args = parser.parse_args()
    try:
        summary = evaluate(args.samples, args.databases, args.results, args.summary, args.timeout)
    except (EvaluationError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"BIRD evaluation failed: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
