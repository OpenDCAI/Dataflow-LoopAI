"""Create a tiny, reusable Text2SQL example for trying the Docker image."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a small BIRD-style SQL grading example")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/private/tmp/loopai-bird-demo"),
        help="directory for the example database, samples, and output",
    )
    args = parser.parse_args()
    root = args.output_dir.expanduser().resolve()
    marker = root / ".loopai-bird-demo"
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        parser.error(f"{root} is not an empty demo directory; choose another --output-dir")

    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("Created by bird_eval/make_demo.py\n", encoding="utf-8")
    database = root / "dev_databases" / "tiny" / "tiny.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER, name TEXT)")
        connection.execute("DELETE FROM people")
        connection.executemany(
            "INSERT INTO people (id, name) VALUES (?, ?)",
            [(1, "小王"), (2, "小李")],
        )

    # db_file deliberately resembles a server path. The evaluator finds the
    # matching database by its ID under the mounted dev_databases directory.
    db_file = "/server/bird/dev_databases/tiny/tiny.sqlite"
    samples = [
        {
            "task_id": "demo-1", "question": "1 号是谁？", "db_file": db_file,
            "ground_truth": "SELECT name FROM people WHERE id = 1",
            "completion": "```sql\nSELECT name FROM people WHERE id = 1\n```",
        },
        {
            "task_id": "demo-2", "question": "2 号是谁？", "db_file": db_file,
            "ground_truth": "SELECT name FROM people WHERE id = 2",
            "completion": "SELECT name FROM people WHERE id = 1",
        },
        {
            "task_id": "demo-3", "question": "共有几个人？", "db_file": db_file,
            "ground_truth": "SELECT COUNT(*) FROM people",
            "completion": "SELECT missing_column FROM people",
        },
    ]
    samples_path = root / "samples.jsonl"
    samples_path.write_text(
        "".join(json.dumps(sample, ensure_ascii=False) + "\n" for sample in samples),
        encoding="utf-8",
    )
    output = root / "output"
    output.mkdir(exist_ok=True)
    for name in ("bird_result.jsonl", "bird_summary.json"):
        (output / name).unlink(missing_ok=True)
    print(f"示例已准备：{root}")
    print(f"数据库：{database}")
    print(f"题目和模型答案：{samples_path}")


if __name__ == "__main__":
    main()
