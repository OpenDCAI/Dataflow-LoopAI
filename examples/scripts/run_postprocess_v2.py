#!/usr/bin/env python3
"""
单独运行 Postprocess Agent v2 的测试脚本。

用法:
    python run_postprocess.py

可通过环境变量或下方 CONFIG 区块修改参数。
"""
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loopai.logger import add_file_handler, get_logger
from loopai.agents.Postprocess import run_postprocess_agent_v2

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG — 按需修改
# ═══════════════════════════════════════════════════════════════════════

DOWNLOAD_DIR = os.getenv(
    "DOWNLOAD_DIR",
   "/mnt/paper2any/xbr/commit/debug1205/Dataflow-LoopAI/outputs/downloads",
)

USER_QUERY = os.getenv(
    "USER_QUERY",
    "收集 text-to-sql 相关的训练数据集，包含 SQL 生成、自然语言转 SQL 等任务",
)

CATEGORY = os.getenv("CATEGORY", "SFT")          # "PT" 或 "SFT"

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o")
BASE_URL   = os.getenv("BASE_URL",   "http://172.96.160.199:3000/v1")
API_KEY    = os.getenv("API_KEY",     "sk-...")         # ← 必填，或通过环境变量传入

TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))

DATASETS_BACKGROUND = os.getenv(
    "DATASETS_BACKGROUND",
    "text-to-sql 领域数据集，用于训练将自然语言问题转为 SQL 查询的模型。"
    "数据通常包含 question/context/answer 或 instruction/input/output 等字段。",
)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "") or None

MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "3"))
LOG_DIR = os.getenv(
    "LOG_DIR",
    "/mnt/paper2any/xbr/commit/debug1205/Dataflow-LoopAI/outputs/log",
)
ENABLE_MAIN_LOG_FILE = os.getenv("ENABLE_MAIN_LOG_FILE", "1").lower() in ("1", "true", "yes")

# ═══════════════════════════════════════════════════════════════════════

def main():
    if not API_KEY:
        print("错误: 请设置 API_KEY（环境变量或直接在脚本 CONFIG 区填写）")
        sys.exit(1)

    print("=" * 60)
    print("  Postprocess Agent v2 — 独立测试")
    print("=" * 60)
    print(f"  download_dir : {DOWNLOAD_DIR}")
    print(f"  user_query   : {USER_QUERY[:60]}...")
    print(f"  category     : {CATEGORY}")
    print(f"  model        : {MODEL_NAME}")
    print(f"  base_url     : {BASE_URL}")
    print(f"  temperature  : {TEMPERATURE}")
    print(f"  tavily_key   : {'已设置' if TAVILY_API_KEY else '未设置'}")
    print(f"  max_concurrent: {MAX_CONCURRENT}")
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(
        LOG_DIR,
        f"postprocess_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    if ENABLE_MAIN_LOG_FILE:
        add_file_handler(get_logger(), log_file, no_context_only=True)
        print(f"  main_log     : {log_file} (summary only)")
    else:
        print("  main_log     : disabled")
    print("=" * 60)

    start = time.time()

    result = run_postprocess_agent_v2(
        download_dir=DOWNLOAD_DIR,
        user_query=USER_QUERY,
        category=CATEGORY,
        model_name=MODEL_NAME,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=TEMPERATURE,
        datasets_background=DATASETS_BACKGROUND,
        tavily_api_key=TAVILY_API_KEY,
        store=None,
        thread_id="test_run",
        event_name="test.postprocess_v2",
        max_concurrent=MAX_CONCURRENT,
    )

    elapsed = time.time() - start

    print()
    print("=" * 60)
    print("  结果")
    print("=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n耗时: {elapsed:.1f}s")
    dataset_logs = result.get("dataset_log_files", []) if isinstance(result, dict) else []
    if dataset_logs:
        print("\nDataset 日志文件:")
        for p in dataset_logs[:10]:
            print(f"  - {p}")
        if len(dataset_logs) > 10:
            print(f"  ... 还有 {len(dataset_logs) - 10} 个")
    unqualified_total = result.get("total_unqualified_records", 0) if isinstance(result, dict) else 0
    unqualified_dir = result.get("unqualified_output_dir", "") if isinstance(result, dict) else ""
    if unqualified_dir:
        print(f"\n不合格输出目录: {unqualified_dir}")
        print(f"不合格记录数: {unqualified_total}")

    if "exception" in result:
        print("\n⚠ 执行出错，请查看上方日志")
        sys.exit(1)

    out_dir = result.get("output_dir", "")
    if out_dir and os.path.isdir(out_dir):
        files = [f for f in os.listdir(out_dir) if f.endswith(".jsonl")]
        print(f"\n输出目录: {out_dir}")
        print(f"JSONL 文件数: {len(files)}")
        for f in sorted(files)[:5]:
            fp = os.path.join(out_dir, f)
            line_count = sum(1 for _ in open(fp))
            print(f"  {f}: {line_count} 条记录")
            with open(fp) as fh:
                first = fh.readline().strip()
                if first:
                    sample = json.loads(first)
                    print(f"    样例 keys: {list(sample.keys())}")
        if len(files) > 5:
            print(f"  ... 还有 {len(files) - 5} 个文件")


if __name__ == "__main__":
    main()
