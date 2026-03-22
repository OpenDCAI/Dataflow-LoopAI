"""
从真实 JSONL 数据中生成 Text2SQL 测试集（默认 20 条）。

该脚本不会生成伪造数据，只会从输入文件中筛选并标准化为
Constructor 工作流可直接消费的记录格式。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _to_str_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(str(x) for x in content).strip()
    return str(content).strip()


def _extract_role(messages: List[Dict[str, Any]], role: str) -> str:
    role = role.lower()
    for msg in messages:
        if str(msg.get("role", "")).lower() == role:
            return _to_str_content(msg.get("content"))
    return ""


def _normalize_record(record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    """
    标准化成 Constructor Text2SQL 记录：
    - messages 中必须有 system/user/assistant
    - assistant 视为 SQL
    """
    messages = record.get("messages", [])
    if not isinstance(messages, list):
        messages = []

    system_content = _extract_role(messages, "system") or _to_str_content(record.get("system"))
    user_content = _extract_role(messages, "user") or _to_str_content(record.get("instruction")) or _to_str_content(record.get("user"))
    assistant_content = _extract_role(messages, "assistant") or _to_str_content(record.get("output")) or _to_str_content(record.get("assistant"))

    if not system_content:
        return False, {}, "missing system(schema)"
    if not user_content:
        return False, {}, "missing user question"
    if not assistant_content:
        return False, {}, "missing assistant sql"

    normalized = {
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ],
        "system": system_content,
    }

    # 尽可能保留来源元信息
    if "id" in record:
        normalized["id"] = record["id"]
    if "meta" in record:
        normalized["meta"] = record["meta"]
    if "dataset_type" in record:
        normalized["dataset_type"] = record["dataset_type"]

    return True, normalized, ""


def generate_dataset(input_jsonl: Path, output_jsonl: Path, sample_size: int) -> Dict[str, int]:
    stats = {
        "total_lines": 0,
        "json_ok": 0,
        "normalized_ok": 0,
        "written": 0,
    }

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with input_jsonl.open("r", encoding="utf-8") as fin, output_jsonl.open("w", encoding="utf-8") as fout:
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

            ok, normalized, _ = _normalize_record(record)
            if not ok:
                continue

            stats["normalized_ok"] += 1
            fout.write(json.dumps(normalized, ensure_ascii=False) + "\n")
            stats["written"] += 1

            if stats["written"] >= sample_size:
                break

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="生成真实 Text2SQL 测试数据")
    parser.add_argument("--input-jsonl", required=True, help="输入真实数据文件")
    parser.add_argument(
        "--output-jsonl",
        default="tests/artifacts/text2sql_real_20.jsonl",
        help="输出测试数据文件路径",
    )
    parser.add_argument("--sample-size", type=int, default=20, help="输出条数，默认20")
    args = parser.parse_args()

    input_jsonl = Path(args.input_jsonl).resolve()
    output_jsonl = Path(args.output_jsonl).resolve()

    if not input_jsonl.exists():
        print(f"[ERROR] input file not found: {input_jsonl}")
        return 1
    if input_jsonl.suffix.lower() != ".jsonl":
        print(f"[ERROR] input must be .jsonl: {input_jsonl}")
        return 1

    stats = generate_dataset(input_jsonl, output_jsonl, args.sample_size)
    print("[DONE] real Text2SQL test data generated")
    print(f"input_jsonl={input_jsonl}")
    print(f"output_jsonl={output_jsonl}")
    print(f"total_lines={stats['total_lines']}")
    print(f"json_ok={stats['json_ok']}")
    print(f"normalized_ok={stats['normalized_ok']}")
    print(f"written={stats['written']}")

    if stats["written"] < args.sample_size:
        print(f"[WARN] only generated {stats['written']} records (< {args.sample_size})")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
