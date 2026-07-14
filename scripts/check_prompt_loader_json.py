#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loopai.common.prompts import PromptLoader


def _default_prompt_dir() -> Path:
    return PROJECT_ROOT / "loopai" / "common" / "prompts"


def collect_json_prompt_keys(prompt_dir: Path) -> list[tuple[str, str, Path]]:
    keys: list[tuple[str, str, Path]] = []
    for path in sorted(prompt_dir.glob("*_prompt.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        prefix = path.name.replace("_prompt.json", "")
        if not isinstance(payload, dict):
            continue
        if any(key in payload for key in ("system", "task")):
            for prompt_type, prompts in payload.items():
                if not isinstance(prompts, dict):
                    continue
                for prompt_name in sorted(prompts):
                    keys.append((str(prompt_type), str(prompt_name), path))
        else:
            for prompt_name in sorted(payload):
                keys.append((prefix, str(prompt_name), path))
    return keys


def run_check(prompt_dir: Path) -> dict[str, Any]:
    loader = PromptLoader(str(prompt_dir))
    checked: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    for prompt_type, prompt_name, source in collect_json_prompt_keys(prompt_dir):
        try:
            value = loader(prompt_type, prompt_name)
            if not isinstance(value, str):
                raise TypeError(f"prompt value is {type(value).__name__}, expected str")
            checked.append(
                {
                    "prompt_type": prompt_type,
                    "prompt_name": prompt_name,
                    "source": str(source),
                    "chars": str(len(value)),
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "prompt_type": prompt_type,
                    "prompt_name": prompt_name,
                    "source": str(source),
                    "error": str(exc),
                }
            )

    return {
        "ok": not failures,
        "prompt_dir": str(prompt_dir.resolve()),
        "checked_count": len(checked),
        "failure_count": len(failures),
        "checked": checked,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check all JSON prompts can be loaded through PromptLoader.")
    parser.add_argument("--prompt-dir", default=str(_default_prompt_dir()))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = run_check(Path(args.prompt_dir))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"prompt_dir={result['prompt_dir']}")
        print(f"checked={result['checked_count']} failures={result['failure_count']}")
        for failure in result["failures"]:
            print(
                "FAIL "
                f"{failure['prompt_type']}/{failure['prompt_name']} "
                f"from {failure['source']}: {failure['error']}"
            )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
