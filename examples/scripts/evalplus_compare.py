#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拿已有的生成结果，用 **evalplus 的 MBPP+ / HumanEval+** 重新判分。

为什么需要它：现在的评测用的是数据集自带的 **3 行 assert**，太弱 —— 函数写歪了
也可能过，写对了也可能挂。MBPP+ 每题 ~108 个测试（base + plus 两套），是同一份
生成结果换个尺子量。

**这一步不需要 GPU** —— 生成已经完成了，这里只做提取 + 判分，纯 CPU。

用法::

    pip install evalplus           # 第一次要装（会拉 tree-sitter 等依赖）
    python examples/scripts/evalplus_compare.py \\
        --samples outputs/<task_id>/judger/<version_id>/mbpp/mbpp_sample.jsonl \\
        --dataset mbpp

首次运行 evalplus 会从 GitHub Releases 下载 MBPP+ 并缓存到
``~/.cache/evalplus``（没有外网的话设 ``MBPP_OVERRIDE_PATH`` 指本地文件）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# evalplus 的 task_id 是 "Mbpp/<原始整数 id>"，和 Judger 样本里的整数对不上，
# 这里按最后一段数字建映射。
_ID_KEY = lambda task_id: int(str(task_id).split("/")[-1])  # noqa: E731


def _read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, help="Judger 的 <bench>_sample.jsonl")
    parser.add_argument("--dataset", default="mbpp", choices=["mbpp", "humaneval"])
    parser.add_argument("--out", default=None, help="中间产物目录，默认放在 samples 旁边")
    parser.add_argument("--workers", type=int, default=0, help="并行进程数，0 = 让 evalplus 自己定")
    args = parser.parse_args()

    try:
        from evalplus.data import get_human_eval_plus, get_mbpp_plus
        from evalplus.evaluate import evaluate
        from evalplus.sanitize import sanitize
    except ImportError as exc:
        print(f"需要 evalplus：pip install evalplus\n  ({exc})", file=sys.stderr)
        raise SystemExit(1)

    print(f"加载 {args.dataset}+ …")
    problems = get_human_eval_plus() if args.dataset == "humaneval" else get_mbpp_plus()
    by_number = {_ID_KEY(task_id): task_id for task_id in problems}
    print(f"  数据集 {len(problems)} 题（官方筛过的子集，比原始 MBPP 少）")

    samples_path = Path(args.samples)
    out_dir = Path(args.out) if args.out else samples_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, unmatched = [], 0
    for sample in _read_jsonl(samples_path):
        key = by_number.get(_ID_KEY(sample["task_id"]))
        if key is None:
            unmatched += 1
            continue
        rows.append({"task_id": key, "completion": sample["completion"]})
    print(f"样本 {len(rows)} 条，不在 {args.dataset}+ 里的 {unmatched} 条（跳过）")

    missing = set(problems) - {r["task_id"] for r in rows}
    if missing:
        # evalplus 会 assert 每题都有样本；缺的直接失败，先说清楚缺多少
        print(f"⚠️  有 {len(missing)} 题没有样本（evalplus 要求覆盖全部），例如 "
              f"{sorted(missing)[:5]}", file=sys.stderr)
        raise SystemExit(1)

    remapped = out_dir / f"{args.dataset}_samples_evalplus.jsonl"
    _write_jsonl(remapped, rows)

    # 用 evalplus 自己的提取（tree-sitter + entrypoint 依赖裁剪）替换掉 Judger 的
    print("提取中（evalplus sanitize）…")
    sanitized_rows = []
    for row in rows:
        problem = problems[row["task_id"]]
        sanitized_rows.append({
            "task_id": row["task_id"],
            "solution": sanitize(
                code=problem["prompt"] + "\n" + row["completion"],
                entrypoint=problem.get("entry_point"),
            ),
        })
    sanitized = out_dir / f"{args.dataset}_samples_evalplus-sanitized.jsonl"
    _write_jsonl(sanitized, sanitized_rows)

    print("判分中（会先跑一遍标准答案算期望输出，慢一点）…")
    evaluate(dataset=args.dataset, samples=str(sanitized),
             parallel=args.workers or None)

    result_path = Path(str(sanitized).replace(".jsonl", "_eval_results.json"))
    if not result_path.is_file():
        print(f"没找到结果文件 {result_path}", file=sys.stderr)
        raise SystemExit(1)

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    print(f"\n===== 结果（{args.dataset}+）=====")
    for key, value in payload.items():
        if key == "eval":
            continue
        print(f"  {key}: {value}")
    print(f"\n逐题明细: {result_path}")


if __name__ == "__main__":
    main()
