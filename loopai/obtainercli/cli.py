from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import ObtainerCliError
from .ingest import ingest_path
from .lake_init import init_lake
from .sample import sample_records


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obtainercli")
    sub = parser.add_subparsers(dest="command", required=True)

    lake = sub.add_parser("lake")
    lake_sub = lake.add_subparsers(dest="lake_command", required=True)
    init = lake_sub.add_parser("init")
    init.add_argument("--root", required=True)
    init.add_argument("--link", default=".loopai/lake.yaml")
    init.add_argument("--if-not-exists", action="store_true")
    init.add_argument("--json", action="store_true")

    ingest = sub.add_parser("ingest")
    ingest_sub = ingest.add_subparsers(dest="ingest_command", required=True)
    path = ingest_sub.add_parser("path")
    path.add_argument("--lake", required=True)
    path.add_argument("--input", required=True)
    path.add_argument("--dataset", required=True)
    path.add_argument("--stage", default="bronze")
    path.add_argument("--domain", default="general")
    path.add_argument("--task-type", default="PT")
    path.add_argument("--processing-level", default="raw_web")
    path.add_argument("--source-kind", default="local")
    path.add_argument("--tags", default="")
    path.add_argument("--idempotency-key", default="")
    path.add_argument("--json", action="store_true")

    sample = sub.add_parser("sample")
    sample.add_argument("--lake", required=True)
    sample.add_argument("--output", required=True)
    sample.add_argument("--domain")
    sample.add_argument("--processing-level")
    sample.add_argument("--source-kind")
    sample.add_argument("--task-type")
    sample.add_argument("--include-tag", action="append", default=[])
    sample.add_argument("--exclude-tag", action="append", default=[])
    sample.add_argument("--n", type=int, default=100)
    sample.add_argument("--allow-smaller", action="store_true")
    sample.add_argument("--seed", type=int, default=42)
    sample.add_argument("--strategy", default="random")
    sample.add_argument("--json", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "lake" and args.lake_command == "init":
            result = init_lake(
                root=Path(args.root),
                link_path=Path(args.link),
                if_not_exists=args.if_not_exists,
            )
        elif args.command == "ingest" and args.ingest_command == "path":
            result = ingest_path(
                lake=args.lake,
                input_path=args.input,
                dataset=args.dataset,
                stage=args.stage,
                domain=args.domain,
                task_type=args.task_type,
                processing_level=args.processing_level,
                source_kind=args.source_kind,
                tags=[args.tags] if args.tags else [],
                idempotency_key=args.idempotency_key or None,
            )
        elif args.command == "sample":
            result = sample_records(
                lake=args.lake,
                output=args.output,
                domain=args.domain,
                processing_level=args.processing_level,
                source_kind=args.source_kind,
                task_type=args.task_type,
                include_tags=args.include_tag,
                exclude_tags=args.exclude_tag,
                n=args.n,
                allow_smaller=args.allow_smaller,
                seed=args.seed,
                strategy=args.strategy,
            )
        else:
            parser.error("Unsupported command")
            return 2
        _print_json(result)
        return 0
    except ObtainerCliError as exc:
        _print_json(
            {
                "ok": False,
                "error_code": exc.error_code,
                "message": exc.message,
                "hint": exc.hint,
            }
        )
        return exc.exit_code
    except Exception as exc:
        _print_json({"ok": False, "error_code": "UNEXPECTED_ERROR", "message": str(exc), "hint": ""})
        return 1


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))
