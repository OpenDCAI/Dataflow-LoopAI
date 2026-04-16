#!/mnt/paper2any/xbr/commit/debug1205/.venv/bin/python
"""
测试 norma_filter_and_add_cot 工具

数据来源：outputs/downloads/processed_output/related_jsonl/
         hf_datasets__Congliu_Chinese-DeepSeek-R1-Distill-data-110k_part_001.jsonl
（messages 格式：system / user / assistant）

运行方式：
    /mnt/paper2any/xbr/commit/debug1205/.venv/bin/python tests/test_norma_filter_and_add_cot.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ──────────────────────────────────────────────────────────────────────────────
# 配置（按需修改）
# ──────────────────────────────────────────────────────────────────────────────

SRC_JSONL = str(
    PROJECT_ROOT
    / "outputs/downloads/processed_output/related_jsonl"
    / "hf_datasets__Congliu_Chinese-DeepSeek-R1-Distill-data-110k_part_001.jsonl"
)

# 只取前 N 条做快速验证
SAMPLE_SIZE = 5

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
BASE_URL    = os.getenv("BASE_URL",   "http://123.129.219.111:3000/v1")
API_KEY     = os.getenv("API_KEY",    "sk-...")

# ──────────────────────────────────────────────────────────────────────────────


def _build_mock_state(model: str, base_url: str, api_key: str) -> dict:
    return {
        "constructor": {
            "model_path": model,
            "base_url":   base_url,
            "api_key":    api_key,
            "temperature": 0.0,
        },
        "messages": [],
        "current": "",
    }


def _sample_jsonl(src: str, n: int, dst: str) -> int:
    """把 src 的前 n 条有效行写入 dst，返回实际写入行数。"""
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


def _show_record(rec: dict, idx: int) -> None:
    top_keys = list(rec.keys())
    print(f"  [{idx}] keys: {top_keys}")

    # messages 格式
    if "messages" in rec:
        for msg in rec["messages"]:
            role = msg.get("role", "?")
            content = str(msg.get("content") or "")
            print(f"       {role:10s}: {content[:80].replace(chr(10),' ')}{'…' if len(content)>80 else ''}")
    else:
        # Alpaca 格式
        for k in ("instruction", "input", "output"):
            v = str(rec.get(k) or "")
            if v:
                print(f"       {k:12s}: {v[:80].replace(chr(10),' ')}{'…' if len(v)>80 else ''}")

    # reasoning_steps（如果有）
    steps = rec.get("reasoning_steps")
    if isinstance(steps, list):
        print(f"       reasoning_steps ({len(steps)} steps):")
        for i, s in enumerate(steps[:3]):
            step_text = s.get("step", "") if isinstance(s, dict) else str(s)
            print(f"         step {i+1}: {step_text[:100]}{'…' if len(step_text)>100 else ''}")
        if len(steps) > 3:
            print(f"         ... (+{len(steps)-3} more)")


def main() -> None:
    print("=" * 60)
    print("  norma_filter_and_add_cot 工具测试")
    print("=" * 60)

    # ── 检查源文件 ────────────────────────────────────────────────────────
    if not os.path.isfile(SRC_JSONL):
        print(f"[ERROR] 源文件不存在: {SRC_JSONL}")
        sys.exit(1)

    # ── 展示原始格式 ──────────────────────────────────────────────────────
    print(f"\n[1] 源文件格式检测（前 {SAMPLE_SIZE} 条）")
    print(f"    路径: {SRC_JSONL}")
    raw_records = []
    with open(SRC_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    raw_records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            if len(raw_records) >= SAMPLE_SIZE:
                break

    print(f"    读到 {len(raw_records)} 条记录")
    for i, rec in enumerate(raw_records[:2]):
        _show_record(rec, i)

    # 判断格式
    sample = raw_records[0] if raw_records else {}
    if "messages" in sample:
        fmt = "messages (ShareGPT)"
    elif "instruction" in sample:
        fmt = "alpaca (instruction/input/output)"
    else:
        fmt = f"未知（top-level keys: {list(sample.keys())}）"
    print(f"\n    检测到格式: {fmt}")

    # ── 准备测试数据（复制到临时目录，避免污染原文件）────────────────────
    print(f"\n[2] 准备测试数据（取前 {SAMPLE_SIZE} 条写入临时目录）")
    tmpdir = tempfile.mkdtemp(prefix="loopai_cot_test_")
    test_jsonl = os.path.join(tmpdir, "test_input.jsonl")
    n_written = _sample_jsonl(SRC_JSONL, SAMPLE_SIZE, test_jsonl)
    print(f"    临时文件: {test_jsonl}")
    print(f"    写入条数: {n_written}")

    # ── 导入并运行工具 ────────────────────────────────────────────────────
    print(f"\n[3] 运行 norma_filter_and_add_cot")
    print(f"    model  : {MODEL_NAME}")
    print(f"    base_url: {BASE_URL}")

    try:
        from loopai.agents.Constructor.tools.cot_filter_tool import norma_filter_and_add_cot
    except ImportError as e:
        print(f"[ERROR] 导入工具失败: {e}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        sys.exit(1)

    state = _build_mock_state(MODEL_NAME, BASE_URL, API_KEY)

    result = norma_filter_and_add_cot(test_jsonl, state)

    # ── 打印结果摘要 ──────────────────────────────────────────────────────
    print(f"\n[4] 结果摘要")
    print(f"    success        : {result.success}")
    print(f"    total_records  : {result.total_records}")
    print(f"    valid_records  : {result.valid_records}")
    print(f"    invalid_records: {result.invalid_records}")
    print(f"    cleaned_path   : {result.cleaned_data_path}")
    if result.error_message:
        print(f"    error_message  : {result.error_message}")

    # ── 展示输出记录 ──────────────────────────────────────────────────────
    if result.valid_records > 0 and os.path.isfile(test_jsonl):
        print(f"\n[5] 输出记录（前 2 条）")
        out_records = []
        with open(test_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        out_records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                if len(out_records) >= 2:
                    break

        for i, rec in enumerate(out_records):
            _show_record(rec, i)

        # 验证 reasoning_steps 存在且格式正确
        print(f"\n[6] 格式验证")
        ok = all(
            isinstance(rec.get("reasoning_steps"), list) and len(rec["reasoning_steps"]) > 0
            for rec in out_records
        )
        # 验证原始键被保留
        if raw_records and out_records:
            orig_keys = set(raw_records[0].keys())
            out_keys  = set(out_records[0].keys())
            preserved = orig_keys.issubset(out_keys)
            added     = out_keys - orig_keys
            print(f"    原始键全部保留 : {preserved}")
            print(f"    新增键         : {added}")
        print(f"    reasoning_steps 格式正确: {ok}")
        print(f"\n{'✓ PASS' if ok else '✗ FAIL'}")
    else:
        print("\n[5] 无输出记录（可能全部被过滤，或工具执行失败）")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n临时目录已清理: {tmpdir}")


if __name__ == "__main__":
    main()
