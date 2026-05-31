from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import ObtainerCliError
from .index import index_embeddings
from .ingest import ingest_path
from .lake_init import init_lake
from .lake_status import lake_status
from .sample import sample_records
from .tags import list_tags


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
    status = lake_sub.add_parser("status")
    status.add_argument("--lake", required=True)
    status.add_argument("--json", action="store_true")

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
    path.add_argument("--post-index", choices=["embedding"], default="")
    path.add_argument("--embedding-model", default="local-hash-v1")
    path.add_argument("--embedding-backend", default="local-jsonl")
    path.add_argument("--embedding-text-field", default="text")
    path.add_argument("--json", action="store_true")

    tag = sub.add_parser("tag")
    tag_sub = tag.add_subparsers(dest="tag_command", required=True)
    tag_list = tag_sub.add_parser("list")
    tag_list.add_argument("--lake", required=True)
    tag_list.add_argument("--json", action="store_true")

    index = sub.add_parser("index")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    embed = index_sub.add_parser("embed")
    embed.add_argument("--lake", required=True)
    embed.add_argument("--dataset")
    embed.add_argument("--model", default="local-hash-v1")
    embed.add_argument("--backend", default="local-jsonl")
    embed.add_argument("--text-field", default="text")
    embed.add_argument("--json", action="store_true")

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
        elif args.command == "lake" and args.lake_command == "status":
            result = lake_status(lake=args.lake)
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
            if args.post_index == "embedding" and result.get("rows_written", 0) > 0:
                index_result = index_embeddings(
                    lake=args.lake,
                    dataset=args.dataset,
                    model=args.embedding_model,
                    backend=args.embedding_backend,
                    text_field=args.embedding_text_field,
                )
                result["post_index"] = index_result
        elif args.command == "tag" and args.tag_command == "list":
            result = list_tags(lake=args.lake)
        elif args.command == "index" and args.index_command == "embed":
            result = index_embeddings(
                lake=args.lake,
                dataset=args.dataset,
                model=args.model,
                backend=args.backend,
                text_field=args.text_field,
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
