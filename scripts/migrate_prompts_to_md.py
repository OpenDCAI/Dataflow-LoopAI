#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _default_prompt_dir() -> Path:
    return PROJECT_ROOT / "loopai" / "common" / "prompts"


def collect_json_prompts(prompt_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    prompts: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(prompt_dir.glob("*_prompt.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        prefix = path.name.replace("_prompt.json", "")
        if not isinstance(payload, dict):
            continue
        if any(key in payload for key in ("system", "task")):
            for prompt_type, items in payload.items():
                if not isinstance(items, dict):
                    continue
                for prompt_name, text in items.items():
                    prompts[(str(prompt_type), str(prompt_name))] = {
                        "text": str(text),
                        "source": str(path),
                    }
        else:
            for prompt_name, text in payload.items():
                prompts[(prefix, str(prompt_name))] = {
                    "text": str(text),
                    "source": str(path),
                }
    return prompts


def migrate_prompts(prompt_dir: Path, output_dir: Path, *, force: bool = False) -> dict[str, Any]:
    prompts = collect_json_prompts(prompt_dir)
    written: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for (prompt_type, prompt_name), item in sorted(prompts.items()):
        target = output_dir / prompt_type / f"{prompt_name}.md"
        if target.exists() and not force:
            skipped.append(
                {
                    "prompt_type": prompt_type,
                    "prompt_name": prompt_name,
                    "target": str(target),
                    "reason": "exists",
                }
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["text"], encoding="utf-8")
        written.append(
            {
                "prompt_type": prompt_type,
                "prompt_name": prompt_name,
                "source": item["source"],
                "target": str(target),
            }
        )

    return {
        "ok": True,
        "source_dir": str(prompt_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "prompt_count": len(prompts),
        "written_count": len(written),
        "skipped_count": len(skipped),
        "written": written,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate JSON prompts to <type>/<name>.md files.")
    parser.add_argument("--prompt-dir", default=str(_default_prompt_dir()))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = migrate_prompts(Path(args.prompt_dir), Path(args.output_dir), force=args.force)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"source_dir={result['source_dir']}")
        print(f"output_dir={result['output_dir']}")
        print(
            f"prompts={result['prompt_count']} "
            f"written={result['written_count']} skipped={result['skipped_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
