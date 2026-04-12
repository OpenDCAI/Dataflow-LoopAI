#!/usr/bin/env python3
"""
单独运行 Postprocess Agent v2 的测试脚本（相关性判定 + related 数据集原样 JSONL 导出）。

用法（在仓库根目录）:
    python examples/scripts/run_postprocess_v2.py

Benchmark 参考样本从哪里来
--------------------------
1. 默认：扫描 ``DOWNLOAD_DIR/benchmark/`` 下的**子目录**（每个子目录视为一个 benchmark 数据源），
   对其中的数据文件采样，写入 ``processed_output/`` 下的参考文件，并注入各 Dataset 子 Agent 的 prompt。
2. 若设置环境变量 ``BENCHMARK_DIR`` 为**存在的目录**：则**只**从该目录**递归**查找数据文件
   （.json/.jsonl/.csv/.tsv/.parquet/.txt），并**替换**上述(1)的发现结果（与主流程里
   ``benchmark_dir`` 参数行为一致）。
3. ``ENABLE_BENCHMARK_REFERENCE=0`` 可关闭采样与 prompt 注入（仍会做相关性判定）。

可通过环境变量或下方 CONFIG 区块修改参数。
"""
import json
import os
import sys
import time
from datetime import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

_DEFAULT_DOWNLOAD_DIR = "/mnt/paper2any/xbr/commit/debug1205/Dataflow-LoopAI/outputs/downloads"
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", _DEFAULT_DOWNLOAD_DIR)
# 在导入可能触发 HuggingFace 依赖的模块之前，将 HF 缓存指到 processed_output/.cache
_POSTPROCESS_OUTPUT_DIR = os.path.join(os.path.abspath(DOWNLOAD_DIR), "processed_output")
os.makedirs(_POSTPROCESS_OUTPUT_DIR, exist_ok=True)
from loopai.agents.Postprocess.hf_datasets_cache import ensure_postprocess_hf_cache_env

ensure_postprocess_hf_cache_env(_POSTPROCESS_OUTPUT_DIR)

from loopai.logger import add_file_handler, get_logger
from loopai.agents.Postprocess import run_postprocess_agent_v2

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG — 按需修改（DOWNLOAD_DIR 已在上方解析，勿重复定义）
# ═══════════════════════════════════════════════════════════════════════

USER_QUERY = os.getenv(
    "USER_QUERY",
    "收集 python代码生成数据集",
)

CATEGORY = os.getenv("CATEGORY", "SFT")          # "PT" 或 "SFT"

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
BASE_URL   = os.getenv("BASE_URL",   "http://123.129.219.111:3000/v1")
API_KEY    = os.getenv("API_KEY",     "sk-...")         # ← 必填，或通过环境变量传入

TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))

DATASETS_BACKGROUND = os.getenv(
    "DATASETS_BACKGROUND",
    "code代码领域数据集，语言为python"
    "数据通常包含 question/context/answer 或 instruction/input/output 等字段。",
)

# v2 相关性子 Agent 当前不使用 Tavily；保留参数仅为与 run_postprocess_agent_v2 签名一致。
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "") or None
# 非空且为目录时：递归读取该目录下数据文件作为 benchmark；为空则用 DOWNLOAD_DIR/benchmark/ 子目录扫描。
BENCHMARK_DIR = os.getenv("BENCHMARK_DIR", "")
ENABLE_BENCHMARK_REFERENCE = os.getenv("ENABLE_BENCHMARK_REFERENCE", "1").lower() in ("1", "true", "yes")

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
    print(f"  benchmark_dir: {BENCHMARK_DIR or '(默认从download_dir/benchmark发现)'}")
    print(f"  benchmark_ref: {'启用' if ENABLE_BENCHMARK_REFERENCE else '禁用'}")
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
        benchmark_dir=BENCHMARK_DIR,
        enable_benchmark_reference=ENABLE_BENCHMARK_REFERENCE,
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
    benchmark_source_count = result.get("benchmark_source_count", 0) if isinstance(result, dict) else 0
    benchmark_sampled_count = result.get("benchmark_sampled_count", 0) if isinstance(result, dict) else 0
    benchmark_samples_file = result.get("benchmark_samples_file", "") if isinstance(result, dict) else ""
    benchmark_sampling_failures = result.get("benchmark_sampling_failures", 0) if isinstance(result, dict) else 0
    if benchmark_source_count or benchmark_sampled_count or benchmark_samples_file:
        print("\nBenchmark 参考采样:")
        print(f"  识别 benchmark 数据源: {benchmark_source_count}")
        print(f"  成功采样条数: {benchmark_sampled_count}")
        print(f"  采样失败次数: {benchmark_sampling_failures}")
        if benchmark_samples_file:
            print(f"  采样文件: {benchmark_samples_file}")
    if unqualified_total or (unqualified_dir and os.path.isdir(unqualified_dir)):
        print(f"\n不合格输出目录: {unqualified_dir}")
        print(f"不合格记录数: {unqualified_total}")

    if "exception" in result:
        print("\n⚠ 执行出错，请查看上方日志")
        sys.exit(1)

    out_dir = result.get("output_dir", "")
    related_dir = result.get("related_jsonl_dir", "") or (
        os.path.join(out_dir, "related_jsonl") if out_dir else ""
    )
    scan_dir = related_dir if related_dir and os.path.isdir(related_dir) else out_dir
    if scan_dir and os.path.isdir(scan_dir):
        files = [f for f in os.listdir(scan_dir) if f.endswith(".jsonl")]
        label = "related_jsonl（判定为相关的原始行）" if scan_dir == related_dir else "输出目录"
        print(f"\n{label}: {scan_dir}")
        print(f"JSONL 文件数: {len(files)}")
        for f in sorted(files)[:5]:
            fp = os.path.join(scan_dir, f)
            line_count = sum(1 for _ in open(fp, encoding="utf-8"))
            print(f"  {f}: {line_count} 条记录")
            with open(fp, encoding="utf-8") as fh:
                first = fh.readline().strip()
                if first:
                    sample = json.loads(first)
                    print(f"    样例 keys: {list(sample.keys())}")
        if len(files) > 5:
            print(f"  ... 还有 {len(files) - 5} 个文件")
    elif out_dir:
        print(f"\n输出目录: {out_dir}（尚未生成 related_jsonl 或目录不存在）")


if __name__ == "__main__":
    main()
