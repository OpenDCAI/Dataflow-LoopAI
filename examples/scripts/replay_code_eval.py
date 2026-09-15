#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拿已有的生成结果重跑「提取 + 判分」，不需要重新调模型。

生成要跑几小时 GPU，而提取和判分是纯 CPU 的。改完提取逻辑后想知道效果，
用这个脚本对旧的 `<bench>_sample.jsonl` 重放一遍就够了 —— 几万条样本几分钟出结果。

用法::

    PYTHONPATH=$PWD python examples/scripts/replay_code_eval.py \
        --problems outputs/<task_id>/judger/<version_id>/<bench>/<bench>_format.jsonl \
        --samples  outputs/<task_id>/judger/<version_id>/<bench>/<bench>_sample.jsonl \
        --limit 500

``--limit`` 只跑前 N 条样本（按原顺序取），快速看趋势用；不传就跑全部。
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger.utils.execution import check_correctness
from loopai.skills.Judger.utils.sanitize import sanitize

TIMEOUT = 3.0
WORKERS = 8


def _read_jsonl(path: str) -> list:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problems", required=True, help="格式化后的问题文件")
    parser.add_argument("--samples", required=True, help="generate 产出的样本文件")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--show", type=int, default=5, help="打印前 N 条失败的细节，0 表示不打印")
    args = parser.parse_args()

    problems = {p["task_id"]: p for p in _read_jsonl(args.problems)}
    samples = _read_jsonl(args.samples)
    if args.limit:
        samples = samples[: args.limit]

    rows = []
    methods = collections.Counter()
    for sample in samples:
        problem = problems.get(sample["task_id"], {})
        result = sanitize(
            sample["completion"],
            entry_point=problem.get("entry_point"),
            prompt=problem.get("prompt"),
        )
        methods[result["extract_method"]] += 1
        rows.append((problem, result))

    print(f"样本数: {len(rows)}")
    print(f"提取方式: {dict(methods)}")

    passed = 0
    errors = collections.Counter()
    failures = []
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(check_correctness, p, r["solution"], TIMEOUT): (p, r)
            for p, r in rows if p
        }
        for future in as_completed(futures):
            problem, result = futures[future]
            outcome = future.result()
            if outcome["passed"]:
                passed += 1
            else:
                errors[outcome["error_type"] or "?"] += 1
                if len(failures) < args.show:
                    failures.append((problem, result, outcome))

    total = len(futures) or 1
    print(f"\npass@1 = {passed / total * 100:.2f}%  ({passed}/{total})")
    print("失败原因分布:")
    for key, count in errors.most_common():
        print(f"  {key:20} {count:6}  {count / total * 100:5.1f}%")

    for problem, result, outcome in failures:
        print(f"\n--- task {problem.get('task_id')} [{outcome['error_type']}] ---")
        print(f"  提取方式: {result['extract_method']}  丢掉顶层语句: {result['dropped_statements']}")
        print(f"  提取出的代码前 200 字:\n{result['solution'][:200]}")


if __name__ == "__main__":
    main()
