#!/usr/bin/env python3
"""
真实调用 LLM + DataFlow Alpagasus，测试 domain_normal_data_cleaner（normal_data 工具）。

默认数据：
    outputs/downloads/processed_output/related_jsonl/
    hf_datasets__Congliu_Chinese-DeepSeek-R1-Distill-data-110k.jsonl

安全说明：
    请勿把 API Key 写进本文件或提交到 git。使用环境变量或命令行临时传入。

运行示例：
    export API_KEY='你的密钥'
    export BASE_URL='http://123.129.219.111:3000/v1'
    export MODEL_NAME='gpt-4o-mini'
    conda activate <你的环境>
    python tests/run_domain_normal_data_cleaner_live.py --sample 3

或（不推荐，会进 shell 历史）：
    python tests/run_domain_normal_data_cleaner_live.py --api-key "$API_KEY" --sample 5
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_INPUT = (
    PROJECT_ROOT
    / "outputs/downloads/processed_output/related_jsonl"
    / "hf_datasets__Congliu_Chinese-DeepSeek-R1-Distill-data-110k.jsonl"
)


def _sample_jsonl(src: Path, n: int, dst: Path) -> int:
    count = 0
    with open(src, "r", encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            fout.write(line)
            count += 1
            if count >= n:
                break
    return count


def _count_lines(path: Path) -> int:
    n = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def main() -> None:
    p = argparse.ArgumentParser(description="Live test for domain_normal_data_cleaner")
    p.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="输入 JSONL 路径",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        metavar="N",
        help="只取前 N 条写入临时文件再测；0 表示用完整文件副本",
    )
    p.add_argument(
        "--model",
        default=os.getenv("MODEL_NAME", "gpt-4o-mini"),
        help="模型名（或环境变量 MODEL_NAME）",
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", ""),
        help="OpenAI 兼容 API base，如 http://host:3000/v1（或环境变量 BASE_URL）",
    )
    p.add_argument(
        "--api-key",
        default=os.getenv("API_KEY") or os.getenv("CONSTRUCTOR_TEST_API_KEY", ""),
        help="API Key（优先用环境变量 API_KEY / CONSTRUCTOR_TEST_API_KEY）",
    )
    p.add_argument(
        "--user-query",
        default=(
            "与中文或英文对话、新闻政策评论、Python 代码实现、数据可视化、"
            "产品设计、支付欺诈分类等相关的问答与指令数据"
        ),
        help="constructor.user_query，用于第一步领域相关性筛选",
    )
    args = p.parse_args()

    if not args.input.is_file():
        print(f"[ERROR] 输入文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)
    if not (args.base_url and str(args.base_url).strip()):
        print("[ERROR] 请设置 BASE_URL 或传入 --base-url", file=sys.stderr)
        sys.exit(1)
    if not (args.api_key and str(args.api_key).strip()):
        print(
            "[ERROR] 请设置环境变量 API_KEY（或 CONSTRUCTOR_TEST_API_KEY）或传入 --api-key",
            file=sys.stderr,
        )
        sys.exit(1)

    tmpdir = tempfile.mkdtemp(prefix="loopai_normal_data_test_")
    work_jsonl = Path(tmpdir) / "test_input.jsonl"
    try:
        if args.sample and args.sample > 0:
            n = _sample_jsonl(args.input, args.sample, work_jsonl)
            print(f"[info] 已从源文件取前 {n} 条 -> {work_jsonl}")
        else:
            shutil.copy2(args.input, work_jsonl)
            n = _count_lines(work_jsonl)
            print(f"[info] 已复制完整文件（{n} 条有效行）-> {work_jsonl}")

        state = {
            "constructor": {
                "user_query": args.user_query,
                "model_path": args.model,
                "base_url": args.base_url.rstrip("/"),
                "api_key": args.api_key,
                "temperature": 0.1,
            },
        }

        print("[info] 导入 domain_normal_data_cleaner ...")
        try:
            from loopai.agents.Constructor.tools.data_filter_tools import domain_normal_data_cleaner
        except ImportError as e:
            print(f"[ERROR] 导入失败（请确认已 pip install -e . 且依赖齐全）: {e}", file=sys.stderr)
            sys.exit(1)

        print("[info] 开始清洗（真实 LLM + Alpagasus，可能较慢）...")
        result = domain_normal_data_cleaner(str(work_jsonl), state)

        print()
        print("========== 结果 ==========")
        print(f"  success         : {result.success}")
        print(f"  total_records   : {result.total_records}")
        print(f"  valid_records   : {result.valid_records}")
        print(f"  invalid_records : {result.invalid_records}")
        print(f"  cleaned_data_path: {result.cleaned_data_path}")
        if result.error_message:
            print(f"  error_message   : {result.error_message}")

        out_path = Path(result.cleaned_data_path)
        if out_path.is_file() and result.valid_records > 0:
            print(f"\n[info] 输出文件行数: {_count_lines(out_path)}")
            with open(out_path, "r", encoding="utf-8") as f:
                first = f.readline()
            if first.strip():
                rec = json.loads(first)
                u = next(
                    (m.get("content", "")[:120] for m in rec.get("messages", []) if m.get("role") == "user"),
                    str(rec.get("instruction", ""))[:120],
                )
                a = next(
                    (m.get("content", "")[:120] for m in rec.get("messages", []) if m.get("role") == "assistant"),
                    str(rec.get("assistant", ""))[:120],
                )
                print(f"  首条 user 预览    : {u!r}...")
                print(f"  首条 assistant 预览: {a!r}...")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"\n[info] 已删除临时目录: {tmpdir}")


if __name__ == "__main__":
    main()
