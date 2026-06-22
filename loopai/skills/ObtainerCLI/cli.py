from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import read_lake_config_for_lake
from .errors import ObtainerCliError
from .events import emit_obtainer_event, get_obtainer_event_writer
from .index import index_embeddings
from .ingest import ingest_path
from .lake_init import init_lake
from .lake_status import lake_status
from .sample import sample_records
from .tags import list_tags


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _command_node(args: argparse.Namespace) -> str:
    parts = [str(getattr(args, "command", "") or "command")]
    for name in ("lake_command", "ingest_command", "tag_command", "index_command"):
        value = getattr(args, name, "")
        if value:
            parts.append(str(value))
    return ".".join(parts)


def _command_event_data(args: argparse.Namespace) -> dict:
    safe_keys = (
        "command",
        "lake_command",
        "ingest_command",
        "tag_command",
        "index_command",
        "root",
        "link",
        "lake",
        "input",
        "output",
        "dataset",
        "stage",
        "domain",
        "task_type",
        "processing_level",
        "source_kind",
        "tags",
        "post_index",
        "provider",
        "model",
        "backend",
        "text_field",
        "n",
        "seed",
        "strategy",
        "balance_by",
    )
    return {key: getattr(args, key) for key in safe_keys if hasattr(args, key)}


def _extract_event_args(argv: list[str] | None) -> tuple[list[str] | None, dict[str, object]]:
    if argv is None:
        return None, {}
    remaining: list[str] = []
    overrides: dict[str, object] = {}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--no-events":
            overrides["no_events"] = True
            index += 1
            continue
        if item == "--task-id" and index + 1 < len(argv):
            overrides["task_id"] = argv[index + 1]
            index += 2
            continue
        if item.startswith("--task-id="):
            overrides["task_id"] = item.split("=", 1)[1]
            index += 1
            continue
        if item == "--output-dir" and index + 1 < len(argv):
            overrides["output_dir"] = argv[index + 1]
            index += 2
            continue
        if item.startswith("--output-dir="):
            overrides["output_dir"] = item.split("=", 1)[1]
            index += 1
            continue
        remaining.append(item)
        index += 1
    return remaining, overrides


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loopai-obtainercli")
    parser.add_argument("--task-id", default=os.getenv("TASK_ID", ""))
    parser.add_argument("--output-dir", default=os.getenv("OUTPUT_DIR", "./outputs"))
    parser.add_argument("--no-events", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    lake = sub.add_parser("lake")
    lake_sub = lake.add_subparsers(dest="lake_command", required=True)
    init = lake_sub.add_parser("init")
    init.add_argument("--root", required=True)
    init.add_argument("--link", default=".loopai/lake.yaml")
    init.add_argument("--auto-embed", action=argparse.BooleanOptionalAction, default=True)
    init.add_argument("--embedding-provider", default="openai-compatible")
    init.add_argument("--embedding-base-url", default="http://127.0.0.1:8000/v1")
    init.add_argument("--embedding-api-key", default="")
    init.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    init.add_argument("--embedding-backend", default="local-jsonl")
    init.add_argument("--embedding-text-field", default="text")
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
    path.add_argument("--no-post-index", action="store_true")
    path.add_argument("--embedding-provider", default="")
    path.add_argument("--embedding-base-url", default="")
    path.add_argument("--embedding-api-key", default="")
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
    embed.add_argument("--provider", default="local-hash")
    embed.add_argument("--base-url", default="")
    embed.add_argument("--api-key", default="")
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
    sample.add_argument("--balance-by", default="")
    sample.add_argument("--json", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parse_argv, event_overrides = _extract_event_args(argv)
    args = parser.parse_args(parse_argv)
    for key, value in event_overrides.items():
        setattr(args, key, value)
    node = _command_node(args)
    writer = get_obtainer_event_writer(
        task_id=args.task_id,
        output_dir=args.output_dir,
        enabled=not args.no_events,
    )
    emit_obtainer_event(
        writer,
        node=node,
        status="started",
        progress=0.0,
        message=f"ObtainerCLI command started: {node}",
        data=_command_event_data(args),
    )
    try:
        if args.command == "lake" and args.lake_command == "init":
            result = init_lake(
                root=Path(args.root),
                link_path=Path(args.link),
                if_not_exists=args.if_not_exists,
                auto_embed=args.auto_embed,
                embedding_provider=args.embedding_provider,
                embedding_base_url=args.embedding_base_url,
                embedding_api_key=args.embedding_api_key,
                embedding_model=args.embedding_model,
                embedding_backend=args.embedding_backend,
                embedding_text_field=args.embedding_text_field,
            )
        elif args.command == "lake" and args.lake_command == "status":
            result = lake_status(lake=args.lake)
        elif args.command == "ingest" and args.ingest_command == "path":
            emit_obtainer_event(
                writer,
                node=node,
                status="running",
                progress=0.2,
                message="Ingesting records into the lake",
                data=_command_event_data(args),
            )
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
            config = read_lake_config_for_lake(args.lake)
            auto_embed = str(config.get("auto_embed", "false")).lower() in {"1", "true", "yes", "on"}
            should_index = not args.no_post_index and result.get("rows_written", 0) > 0 and (
                args.post_index == "embedding" or auto_embed
            )
            if should_index:
                provider = args.embedding_provider or config.get("embedding_provider", "local-hash")
                emit_obtainer_event(
                    writer,
                    node=node,
                    status="running",
                    progress=0.7,
                    message="Indexing embeddings after ingest",
                    data={
                        "dataset": args.dataset,
                        "provider": provider,
                        "model": args.embedding_model,
                        "backend": args.embedding_backend,
                    },
                )
                try:
                    index_result = index_embeddings(
                        lake=args.lake,
                        dataset=args.dataset,
                        provider=provider,
                        base_url=args.embedding_base_url or config.get("embedding_base_url", ""),
                        api_key=args.embedding_api_key
                        or config.get("embedding_api_key", "")
                        or os.getenv("OBTAINERCLI_EMBED_API_KEY", ""),
                        model=args.embedding_model
                        if args.embedding_model != "local-hash-v1"
                        else config.get("embedding_model", args.embedding_model),
                        backend=args.embedding_backend
                        if args.embedding_backend != "local-jsonl"
                        else config.get("embedding_backend", args.embedding_backend),
                        text_field=args.embedding_text_field
                        if args.embedding_text_field != "text"
                        else config.get("embedding_text_field", args.embedding_text_field),
                    )
                    result["post_index"] = index_result
                except Exception as exc:
                    result.setdefault("warnings", []).append(
                        {
                            "code": "POST_INDEX_EMBEDDING_FAILED",
                            "message": str(exc),
                        }
                    )
                    result["status"] = "success_with_warnings"
        elif args.command == "tag" and args.tag_command == "list":
            result = list_tags(lake=args.lake)
        elif args.command == "index" and args.index_command == "embed":
            result = index_embeddings(
                lake=args.lake,
                dataset=args.dataset,
                provider=args.provider,
                base_url=args.base_url,
                api_key=args.api_key,
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
                balance_by=args.balance_by,
            )
        else:
            parser.error("Unsupported command")
            return 2
        emit_obtainer_event(
            writer,
            node=node,
            status="completed",
            progress=1.0,
            message=f"ObtainerCLI command completed: {node}",
            data=result,
        )
        _print_json(result)
        return 0
    except ObtainerCliError as exc:
        emit_obtainer_event(
            writer,
            node=node,
            status="failed",
            progress=1.0,
            message=exc.message,
            error={
                "type": type(exc).__name__,
                "code": exc.error_code,
                "detail": exc.message,
                "hint": exc.hint,
            },
        )
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
        emit_obtainer_event(
            writer,
            node=node,
            status="failed",
            progress=1.0,
            message=str(exc),
            error={
                "type": type(exc).__name__,
                "code": "UNEXPECTED_ERROR",
                "detail": str(exc),
            },
        )
        _print_json({"ok": False, "error_code": "UNEXPECTED_ERROR", "message": str(exc), "hint": ""})
        return 1


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))
