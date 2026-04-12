#!/usr/bin/env python3
"""
Remove JSONL rows whose ``instruction`` equals (or overlaps with) HumanEval-style
benchmark fields loaded from Parquet.

The benchmark files here use column ``prompt`` (there is typically no ``question``
column). If ``question`` exists, it is used together with ``prompt``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def load_reference_texts(parquet_paths: list[Path]) -> set[str]:
    texts: set[str] = set()
    for p in parquet_paths:
        df = pd.read_parquet(p)
        cols = []
        if "question" in df.columns:
            cols.append("question")
        if "prompt" in df.columns:
            cols.append("prompt")
        if not cols:
            raise ValueError(
                f"{p}: expected at least one of columns 'question', 'prompt', got {list(df.columns)}"
            )
        for c in cols:
            for v in df[c].dropna().astype(str):
                t = v.strip()
                if t:
                    texts.add(t)
    return texts


def normalize_whitespace(s: str) -> str:
    return " ".join(s.split())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jsonl",
        type=Path,
        required=True,
        help="Input JSONL with an 'instruction' field per line.",
    )
    parser.add_argument(
        "--parquet",
        type=Path,
        nargs="+",
        required=True,
        help="One or more Parquet files (e.g. HumanEval test-*.parquet).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write filtered JSONL here. Default: overwrite --jsonl (after backup).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="When writing in-place, do not create .bak next to the input.",
    )
    parser.add_argument(
        "--normalize-whitespace",
        action="store_true",
        help="Compare after collapsing whitespace (strip + split/join).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print how many lines would be removed; do not write files.",
    )
    args = parser.parse_args()

    missing = [p for p in args.parquet if not p.is_file()]
    if missing:
        print("Missing parquet files:", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        return 2

    refs = load_reference_texts(args.parquet)
    ref_norm = {normalize_whitespace(t) for t in refs} if args.normalize_whitespace else None

    def is_overlap(instr: str) -> bool:
        s = instr.strip()
        if not s:
            return False
        if s in refs:
            return True
        if ref_norm is not None:
            if normalize_whitespace(s) in ref_norm:
                return True
        return False

    out_lines: list[str] = []
    removed = 0
    removed_examples: list[tuple[int, str]] = []

    with args.jsonl.open(encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Line {i}: JSON decode error: {e}", file=sys.stderr)
                return 3
            instr = obj.get("instruction")
            if not isinstance(instr, str):
                removed += 1
                removed_examples.append((i, "<non-string instruction>"))
                continue
            if is_overlap(instr):
                removed += 1
                if len(removed_examples) < 20:
                    preview = instr.replace("\n", "\\n")[:120]
                    removed_examples.append((i, preview))
                continue
            out_lines.append(line.rstrip("\n"))

    total_kept = len(out_lines)
    total_in = total_kept + removed
    print(f"Read lines (non-empty): {total_in}")
    print(f"Would remove (overlap with benchmark): {removed}")
    print(f"Keep: {total_kept}")
    if removed_examples:
        print("Sample removed line numbers / previews:")
        for ln, prev in removed_examples:
            print(f"  {ln}: {prev}")

    if args.dry_run:
        return 0

    out_path = args.output if args.output is not None else args.jsonl
    if out_path == args.jsonl and not args.no_backup:
        bak = args.jsonl.with_suffix(args.jsonl.suffix + ".bak")
        bak.write_text(args.jsonl.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Backup: {bak}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as w:
        for line in out_lines:
            w.write(line + "\n")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
