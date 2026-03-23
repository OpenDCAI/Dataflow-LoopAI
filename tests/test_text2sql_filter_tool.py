"""
真实执行 Text2SQL SQL 过滤工具的测试脚本。

功能：
1. 从真实 JSONL 数据中抽取前 N 条（默认 20 条）符合 Constructor 工作流格式的记录；
2. 调用 domain_text2sql_cleaner 进行真实建库与 SQL 执行过滤；
3. 输出清洗结果与产物路径。

运行示例：
    conda activate <your_env>
    python tests/test_text2sql_filter_tool.py \
        --input-jsonl /abs/path/to/your_text2sql.jsonl \
        --sample-size 20 \
        --model-path your_model_name_or_path \
        --base-url http://your-llm-api:port/v1 \
        --api-key your_api_key

说明：
- 为了保证“真实数据”，脚本不会构造假样本，只会从输入文件中筛选。
- 数据格式按 Constructor 工作流要求：messages 中至少包含 system/user/assistant。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loopai.agents.Constructor.tools.data_filter_tools import domain_text2sql_cleaner


def _extract_role_content(messages: List[Dict[str, Any]], role: str) -> str:
    for msg in messages:
        if str(msg.get("role", "")).lower() == role:
            content = msg.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                return " ".join(str(x) for x in content).strip()
            return str(content).strip()
    return ""


def _is_constructor_text2sql_record(record: Dict[str, Any]) -> Tuple[bool, str]:
    messages = record.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return False, "messages 为空或不存在"

    system_content = _extract_role_content(messages, "system")
    user_content = _extract_role_content(messages, "user")
    assistant_content = _extract_role_content(messages, "assistant")

    if not system_content:
        return False, "缺少 system(schema) 内容"
    if not user_content:
        return False, "缺少 user 内容"
    if not assistant_content:
        return False, "缺少 assistant(SQL) 内容"
    return True, ""


def sample_real_constructor_records(
    input_jsonl: Path,
    output_jsonl: Path,
    sample_size: int = 20,
) -> Dict[str, int]:
    stats = {
        "total_lines": 0,
        "json_ok": 0,
        "constructor_format_ok": 0,
        "sampled": 0,
    }
    sampled_records: List[Dict[str, Any]] = []

    with input_jsonl.open("r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue

            stats["total_lines"] += 1
            try:
                record = json.loads(line)
                stats["json_ok"] += 1
            except json.JSONDecodeError:
                continue

            ok, _ = _is_constructor_text2sql_record(record)
            if not ok:
                continue

            stats["constructor_format_ok"] += 1
            sampled_records.append(record)
            if len(sampled_records) >= sample_size:
                break

    if len(sampled_records) < sample_size:
        raise ValueError(
            f"输入数据中仅找到 {len(sampled_records)} 条符合 Constructor Text2SQL 格式的真实记录，"
            f"不足 {sample_size} 条。请更换更合适的数据文件。"
        )

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as fout:
        for record in sampled_records:
            fout.write(json.dumps(record, ensure_ascii=False) + "\n")

    stats["sampled"] = len(sampled_records)
    return stats


def _build_state_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "obtainer": {
            "category": "SFT",
            "model_path": args.model_path or None,
            "base_url": args.base_url or None,
            "api_key": args.api_key or None,
            "temperature": args.temperature,
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="真实执行 Text2SQL SQL 过滤 tool 的测试脚本")
    parser.add_argument("--input-jsonl", required=True, help="输入的真实 Text2SQL JSONL 文件路径")
    parser.add_argument("--sample-size", type=int, default=20, help="抽样条数，默认 20")
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "tests" / "artifacts" / "text2sql_tool_test"),
        help="测试中间产物输出目录",
    )
    parser.add_argument("--model-path", default=os.getenv("TEXT2SQL_MODEL_PATH", ""))
    parser.add_argument("--base-url", default=os.getenv("TEXT2SQL_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("TEXT2SQL_API_KEY", ""))
    parser.add_argument("--temperature", type=float, default=0.1)
    args = parser.parse_args()

    input_jsonl = Path(args.input_jsonl).resolve()
    if not input_jsonl.exists():
        print(f"[错误] 输入文件不存在: {input_jsonl}")
        return 1
    if input_jsonl.suffix.lower() != ".jsonl":
        print(f"[错误] 输入文件必须是 .jsonl: {input_jsonl}")
        return 1

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir).resolve() / run_id
    sampled_jsonl = output_dir / f"sampled_{args.sample_size}.jsonl"

    print("=" * 80)
    print("Text2SQL SQL Filter Tool - 真实执行测试")
    print("=" * 80)
    print(f"输入文件: {input_jsonl}")
    print(f"输出目录: {output_dir}")
    print(f"抽样条数: {args.sample_size}")
    print("-" * 80)

    try:
        sample_stats = sample_real_constructor_records(
            input_jsonl=input_jsonl,
            output_jsonl=sampled_jsonl,
            sample_size=args.sample_size,
        )
        print("[1/2] 抽样完成")
        print(f"  - 总行数: {sample_stats['total_lines']}")
        print(f"  - JSON 合法: {sample_stats['json_ok']}")
        print(f"  - Constructor 格式合法: {sample_stats['constructor_format_ok']}")
        print(f"  - 已抽样: {sample_stats['sampled']}")
        print(f"  - 抽样文件: {sampled_jsonl}")
    except Exception as e:
        print(f"[错误] 抽样失败: {e}")
        return 1

    state = _build_state_from_args(args)
    print("[2/2] 开始真实执行 domain_text2sql_cleaner ...")

    try:
        result = domain_text2sql_cleaner(str(sampled_jsonl), state)
    except Exception as e:
        print(f"[错误] 执行 domain_text2sql_cleaner 失败: {e}")
        return 1

    print("-" * 80)
    print("执行结果:")
    print(f"  - success: {result.success}")
    print(f"  - cleaned_data_path: {result.cleaned_data_path}")
    print(f"  - total_records: {result.total_records}")
    print(f"  - valid_records: {result.valid_records}")
    print(f"  - invalid_records: {result.invalid_records}")
    if getattr(result, "diagnostics", None):
        print("  - diagnostics:")
        for k, v in result.diagnostics.items():
            print(f"      {k}: {v}")
    if result.error_message:
        print(f"  - error_message: {result.error_message}")

    result_json = output_dir / "run_result.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    with result_json.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "input_jsonl": str(input_jsonl),
                "sampled_jsonl": str(sampled_jsonl),
                "sample_stats": sample_stats,
                "clean_result": {
                    "success": result.success,
                    "cleaned_data_path": result.cleaned_data_path,
                    "total_records": result.total_records,
                    "valid_records": result.valid_records,
                    "invalid_records": result.invalid_records,
                    "error_message": result.error_message,
                    "diagnostics": getattr(result, "diagnostics", {}),
                },
                "config": {
                    "sample_size": args.sample_size,
                    "temperature": args.temperature,
                    "model_path": args.model_path,
                    "base_url": args.base_url,
                    "api_key_set": bool(args.api_key),
                },
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"  - result_json: {result_json}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
