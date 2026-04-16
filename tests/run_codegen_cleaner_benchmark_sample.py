#!/usr/bin/env python3
"""
从 SFT_00005.jsonl 随机采样 N 条，真实调用 domain_code_gen_cleaner（含 benchmark Phase A/B）。

运行前请激活你的 conda 环境，并传入可用的 OpenAI 兼容 API：

  conda activate <your_env>
  cd /path/to/Dataflow-LoopAI
  python tests/run_codegen_cleaner_benchmark_sample.py \\
    --model-path <model_name> \\
    --base-url http://127.0.0.1:8000/v1 \\
    --api-key <key>

默认：
  - 输入: outputs/downloads/processed_output_cleaned/SFT_00005.jsonl
  - 采样: 128 条
  - user_query: 收集python代码生成数据集用于大模型微调
  - benchmark 目录: outputs/benchmark（若无 jsonl，则从目录内首个 parquet 取一行写成临时 benchmark_samples.jsonl）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

USER_QUERY_DEFAULT = "收集python代码生成数据集用于大模型微调"


def _default_input_jsonl() -> Path:
    return PROJECT_ROOT / "outputs" / "downloads" / "processed_output_cleaned" / "SFT_00005.jsonl"


def _default_benchmark_dir() -> Path:
    return PROJECT_ROOT / "outputs" / "benchmark"


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


def _find_first_parquet(root: Path) -> Optional[Path]:
    for p in sorted(root.rglob("*.parquet")):
        if p.is_file():
            return p
    return None


def _find_first_jsonl(root: Path) -> Optional[Path]:
    for p in sorted(root.rglob("*.jsonl")):
        if p.is_file():
            return p
    return None


def build_benchmark_samples_file(benchmark_dir: Path, out_path: Path) -> Tuple[str, Dict[str, Any]]:
    """
    生成一行 benchmark_samples.jsonl（与 Postprocess benchmark_sampler 包装格式一致）。
    优先使用目录下已有 .jsonl 首行；否则读首个 .parquet 的首行。
    """
    benchmark_dir = benchmark_dir.resolve()
    if not benchmark_dir.is_dir():
        raise FileNotFoundError(f"benchmark 目录不存在: {benchmark_dir}")

    jl = _find_first_jsonl(benchmark_dir)
    if jl is not None:
        wrapper: Optional[Dict[str, Any]] = None
        with jl.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                wrapper = {
                    "benchmark_name": jl.parent.name,
                    "source_type": "benchmark_datasets",
                    "dataset_dir": str(jl.parent),
                    "file_path": str(jl),
                    "split_name": "from_jsonl",
                    "sample_record": rec if isinstance(rec, dict) else {"row": rec},
                }
                break
        if wrapper is None:
            raise RuntimeError(f"空 jsonl: {jl}")
        with out_path.open("w", encoding="utf-8") as fo:
            fo.write(json.dumps(wrapper, ensure_ascii=False, default=_json_default) + "\n")
        return "jsonl", wrapper

    pq = _find_first_parquet(benchmark_dir)
    if pq is None:
        raise FileNotFoundError(
            f"{benchmark_dir} 下未找到 .jsonl 或 .parquet，无法构造 benchmark 参考"
        )

    import pandas as pd

    df = pd.read_parquet(pq)
    if len(df) == 0:
        raise RuntimeError(f"空 parquet: {pq}")
    row = df.iloc[0].to_dict()
    wrapper = {
        "benchmark_name": pq.parent.name,
        "source_type": "benchmark_datasets",
        "dataset_dir": str(pq.parent),
        "file_path": str(pq),
        "split_name": "train",
        "sample_record": row,
    }
    with out_path.open("w", encoding="utf-8") as fo:
        fo.write(json.dumps(wrapper, ensure_ascii=False, default=_json_default) + "\n")
    return "parquet", wrapper


def sample_jsonl_lines(
    src: Path,
    dst: Path,
    n: int,
    seed: int,
) -> Dict[str, Any]:
    lines: List[str] = []
    with src.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                lines.append(line.rstrip("\n"))

    rng = random.Random(seed)
    if len(lines) <= n:
        chosen = lines
        note = f"总行数 {len(lines)} <= {n}，使用全部行"
    else:
        chosen = rng.sample(lines, n)
        note = f"从 {len(lines)} 行中随机采样 {n} 行 (seed={seed})"

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        for ln in chosen:
            f.write(ln + "\n")

    return {
        "source_lines": len(lines),
        "written": len(chosen),
        "note": note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="真实调用 code-gen cleaner + benchmark 管线（采样测试）")
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        default=_default_input_jsonl(),
        help="源 JSONL（默认 SFT_00005.jsonl）",
    )
    parser.add_argument("--sample-size", type=int, default=128, help="采样条数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=_default_benchmark_dir(),
        help="benchmark 根目录（默认 outputs/benchmark）",
    )
    parser.add_argument("--model-path", type=str, required=True, help="OpenAI 兼容 API 的 model 名")
    parser.add_argument("--base-url", type=str, required=True, help="如 http://host:port/v1")
    parser.add_argument("--api-key", type=str, required=True, help="API Key")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="运行输出目录（默认 tests/codegen_cleaner_runs/<时间戳>）",
    )
    parser.add_argument("--user-query", type=str, default=USER_QUERY_DEFAULT)
    args = parser.parse_args()

    if not args.input_jsonl.is_file():
        print(f"错误: 输入文件不存在: {args.input_jsonl}", file=sys.stderr)
        return 1

    run_dir = args.output_dir
    if run_dir is None:
        run_dir = PROJECT_ROOT / "tests" / "codegen_cleaner_runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    sampled_path = run_dir / "sampled_input.jsonl"
    sample_stats = sample_jsonl_lines(args.input_jsonl, sampled_path, args.sample_size, args.seed)
    print(sample_stats["note"])

    bench_samples_path = run_dir / "benchmark_samples.jsonl"
    try:
        src_kind, _wrap = build_benchmark_samples_file(args.benchmark_dir, bench_samples_path)
    except Exception as e:
        print(f"构造 benchmark 参考失败: {e}", file=sys.stderr)
        return 1
    print(f"benchmark 参考来源: {src_kind} -> {bench_samples_path}")

    from loopai.agents.Constructor.tools.data_filter_tools import domain_code_gen_cleaner

    state: Dict[str, Any] = {
        "automated_query": args.user_query,
        "banckmark_jsonl_path": str(args.benchmark_dir.resolve()),
        "constructor": {
            "user_query": args.user_query,
            "model_path": args.model_path,
            "base_url": args.base_url.rstrip("/"),
            "api_key": args.api_key,
            "temperature": args.temperature,
            "benchmark_samples_path": str(bench_samples_path),
        },
    }

    print("调用 domain_code_gen_cleaner ...")
    result = domain_code_gen_cleaner(str(sampled_path), state)

    summary = {
        "input_jsonl": str(args.input_jsonl),
        "sampled_jsonl": str(sampled_path),
        "sample_stats": sample_stats,
        "benchmark_dir": str(args.benchmark_dir),
        "benchmark_samples_file": str(bench_samples_path),
        "user_query": args.user_query,
        "clean_result": {
            "success": result.success,
            "cleaned_data_path": result.cleaned_data_path,
            "total_records": result.total_records,
            "valid_records": result.valid_records,
            "invalid_records": result.invalid_records,
            "error_message": result.error_message,
            "diagnostics": dict(result.diagnostics) if getattr(result, "diagnostics", None) else {},
        },
        "model_path": args.model_path,
        "base_url": args.base_url,
    }

    out_json = run_dir / "run_result.json"
    with out_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=" * 72)
    print(f"success: {result.success}")
    print(f"cleaned_data_path: {result.cleaned_data_path}")
    print(f"total / valid / invalid: {result.total_records} / {result.valid_records} / {result.invalid_records}")
    if result.diagnostics:
        print("diagnostics:", json.dumps(dict(result.diagnostics), ensure_ascii=False, indent=2))
    if result.error_message:
        print("error_message:", result.error_message)
    print(f"run_result.json -> {out_json}")
    print("=" * 72)
    return 0 if result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
