#!/usr/bin/env python
"""Command-line interface for task-scoped Configer updates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_updates(args: argparse.Namespace) -> dict:
    if bool(args.updates_json) == bool(args.updates_file):
        raise ValueError("provide exactly one of --updates-json or --updates-file")
    if args.updates_json:
        value = json.loads(args.updates_json)
    else:
        with Path(args.updates_file).expanduser().open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        # Accept either a raw section update object or a full config file.
        if isinstance(value, dict) and isinstance(value.get(args.section), dict):
            value = value[args.section]
    if not isinstance(value, dict):
        raise ValueError("updates must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LoopAI Configer CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    update = subparsers.add_parser("update-task", help="update a task-scoped state section")
    update.add_argument("--section", required=True, help="state section, e.g. judger")
    update.add_argument("--task-id", required=True)
    update.add_argument("--updates-json")
    update.add_argument("--updates-file")
    args = parser.parse_args(argv)

    if args.command == "update-task":
        try:
            updates = _load_updates(args)
            from loopai.skills.Configer import update_configer_task_state_config

            result = update_configer_task_state_config(args.section, updates, task_id=args.task_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok", False) else 1
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
