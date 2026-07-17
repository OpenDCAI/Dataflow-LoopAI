#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from loopai.common.prompts import PromptLoader
from migrate_prompts_to_md import collect_json_prompts, migrate_prompts


def _default_prompt_dir() -> Path:
    return PROJECT_ROOT / "loopai" / "common" / "prompts"


def run_abtest(prompt_dir: Path, md_dir: Path | None = None) -> dict[str, Any]:
    cleanup: tempfile.TemporaryDirectory[str] | None = None
    try:
        if md_dir is None:
            cleanup = tempfile.TemporaryDirectory(prefix="prompt_md_abtest_")
            md_dir = Path(cleanup.name)
            migrate_prompts(prompt_dir, md_dir, force=True)

        json_loader = PromptLoader(str(prompt_dir))
        md_loader = PromptLoader(str(md_dir))
        expected = collect_json_prompts(prompt_dir)

        matched: list[dict[str, str]] = []
        mismatches: list[dict[str, str]] = []
        missing: list[dict[str, str]] = []

        for prompt_type, prompt_name in sorted(expected):
            json_value = json_loader(prompt_type, prompt_name)
            try:
                md_value = md_loader(prompt_type, prompt_name)
            except Exception as exc:
                missing.append(
                    {
                        "prompt_type": prompt_type,
                        "prompt_name": prompt_name,
                        "error": str(exc),
                    }
                )
                continue
            if json_value == md_value:
                matched.append({"prompt_type": prompt_type, "prompt_name": prompt_name})
            else:
                mismatches.append(
                    {
                        "prompt_type": prompt_type,
                        "prompt_name": prompt_name,
                        "json_chars": str(len(json_value)),
                        "md_chars": str(len(md_value)),
                    }
                )

        expected_keys = set(expected)
        md_keys = set(md_loader.iter_prompt_keys())
        extra = [
            {"prompt_type": prompt_type, "prompt_name": prompt_name}
            for prompt_type, prompt_name in sorted(md_keys - expected_keys)
        ]

        return {
            "ok": not mismatches and not missing,
            "prompt_dir": str(prompt_dir.resolve()),
            "md_dir": str(md_dir.resolve()),
            "expected_count": len(expected),
            "matched_count": len(matched),
            "mismatch_count": len(mismatches),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "mismatches": mismatches,
            "missing": missing,
            "extra": extra,
        }
    finally:
        if cleanup is not None:
            cleanup.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B test JSON PromptLoader output against Markdown prompts.")
    parser.add_argument("--prompt-dir", default=str(_default_prompt_dir()))
    parser.add_argument("--md-dir", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = run_abtest(
        Path(args.prompt_dir),
        Path(args.md_dir) if args.md_dir else None,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"prompt_dir={result['prompt_dir']}")
        print(f"md_dir={result['md_dir']}")
        print(
            f"expected={result['expected_count']} matched={result['matched_count']} "
            f"missing={result['missing_count']} mismatches={result['mismatch_count']} "
            f"extra={result['extra_count']}"
        )
        for item in result["missing"]:
            print(f"MISSING {item['prompt_type']}/{item['prompt_name']}: {item['error']}")
        for item in result["mismatches"]:
            print(
                "MISMATCH "
                f"{item['prompt_type']}/{item['prompt_name']} "
                f"json_chars={item['json_chars']} md_chars={item['md_chars']}"
            )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
