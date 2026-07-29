#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Judger 集成测试 — 传入 bench 配置，跑完整 pipeline。

用法：
    conda activate loopai_zx
    DB_PATH=api/db/db.sqlite3 TASK_ID=<task_id> python tests/test_judger.py \
        --bench-config bench_config.json

bench_config.json 格式：
{
  "benchlist": [
    {"name": "gsm8k", "task_type": "general_text",
     "problem_path": "/data/gsm8k/test.jsonl", "eval_type": "key2_qa"}
  ],
  "extra_benchlist": [
    {"name": "mmlu", "task_type": "general_text",
     "problem_path": "/data/mmlu/test.jsonl", "eval_type": "key3_q_choices_a"}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def load_bench_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Judger 集成测试")
    default_config = str(Path(__file__).parent / "bench_config.json")
    parser.add_argument("--bench-config", type=str, default=default_config,
                        help=f"Bench 配置 JSON 文件路径（默认: {default_config}）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验不走流水线")
    args = parser.parse_args()

    db_path = os.getenv("DB_PATH", "api/db/db.sqlite3")
    task_id = os.getenv("TASK_ID")
    if not task_id:
        print("❌ 请设置 TASK_ID 环境变量", file=sys.stderr)
        sys.exit(1)

    # 加载 bench 配置
    benches = load_bench_config(args.bench_config)
    primary = benches.get("benchlist", [])
    secondary = benches.get("extra_benchlist", [])

    if not primary and not secondary:
        print("❌ bench_config.json 中 benchlist 或 extra_benchlist 至少需要一个", file=sys.stderr)
        sys.exit(1)

    print(f"benchlist:   {[b.get('name') for b in primary]}")
    print(f"extra_benchlist: {[b.get('name') for b in secondary]}")

    if args.dry_run:
        # 只校验 bench entry 结构
        from loopai.skills.Judger.runner import _validate_bench
        all_ok = True
        for b in primary + secondary:
            try:
                _validate_bench(b, writer=None)
                print(f"  ✅ {b.get('name')}")
            except SystemExit:
                print(f"  ❌ {b.get('name')} 校验失败")
                all_ok = False
        if all_ok:
            print("\n✅ 所有 bench 校验通过")
        else:
            sys.exit(1)
        return

    # 写 Configer：如果 task 还没配置，写入模型路径和 benches
    # （模型路径从 state 或 env 读取）
    from loopai.skills.Configer import update_configer_task_state_config
    updates = {
        "benchlist": primary,
        "extra_benchlist": secondary,
    }
    model_path = os.getenv("JUDGER_MODEL_PATH")
    if model_path:
        updates["eval_model_path"] = model_path
    update_configer_task_state_config("judger", updates, task_id=task_id)
    print("✅ bench 配置已写入 Configer")

    # 跑 pipeline
    os.environ["DB_PATH"] = db_path
    os.environ["TASK_ID"] = task_id
    from loopai.skills.Judger import run
    print("🚀 启动 Judger pipeline...\n")
    run()


if __name__ == "__main__":
    main()
