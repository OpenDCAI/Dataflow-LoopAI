from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

from .datamixer_adapter import datamixer_argv_from_lake
from .download import MAX_ROWS_PER_DATASET, download_manifest
from .errors import ObtainerCliError
from .events import emit_obtainer_event, get_obtainer_event_writer
from .searchagent import run_searchagent
from .sft_export_agent import run_agent as run_sft_export_agent


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _command_node(args: argparse.Namespace) -> str:
    parts = [str(getattr(args, "command", "") or "command")]
    for name in ("lake_command", "ingest_command", "tag_command", "index_command", "download_command"):
        value = getattr(args, name, "")
        if value:
            parts.append(str(value))
    return ".".join(parts)


def _run_datamixer_command(argv: list[str], *, lake: str = "", root: str = "") -> dict:
    from loopai.agents.Obtainer.datamixer import cli as datamixer_cli

    args = list(argv or [])
    if args and args[0] == "--":
        args = args[1:]
    if root and not any(item == "--root" or item.startswith("--root=") for item in args):
        args = ["--root", root, *args]
    if "--json" not in args and not any(item.startswith("--json=") for item in args):
        args = ["--json", *args]
    args = datamixer_argv_from_lake(args, lake or None)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = datamixer_cli.main(args)
    raw = output.getvalue().strip()
    parsed: object
    try:
        parsed = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, IndexError):
        try:
            parsed = json.loads(raw.splitlines()[-1]) if raw else {}
        except (json.JSONDecodeError, IndexError):
            parsed = {"raw": raw}
    if exit_code != 0:
        message = parsed.get("error") if isinstance(parsed, dict) else raw
        raise ObtainerCliError(
            "DATAMIXER_COMMAND_FAILED",
            f"datamixer command failed with exit code {exit_code}",
            hint=str(message or raw),
            exit_code=exit_code,
            details=parsed if isinstance(parsed, dict) else {"raw": raw},
        )
    return {
        "ok": True,
        "command": "dm",
        "status": "success",
        "warnings": [],
        "exit_code": exit_code,
        "argv": args,
        "result": parsed,
    }


def _run_dm_command(argv: list[str], *, lake: str = "", root: str = "") -> dict:
    args = list(argv or [])
    if args and args[0] == "--":
        args = args[1:]
    if args and args[0] == "sft-export-agent":
        result = run_sft_export_agent(args[1:], root=root)
        result.setdefault("ok", True)
        result.setdefault("command", "dm.sft-export-agent")
        result.setdefault("status", "success")
        result.setdefault("warnings", [])
        return result
    return _run_datamixer_command(args, lake=lake, root=root)


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
        "query",
        "query_file",
        "task_json",
        "objective",
        "keywords",
        "output_root",
        "model_name",
        "base_url",
        "search_engine",
        "max_urls",
        "deepsearch",
        "max_deep_queries",
        "max_deep_pages",
        "download_command",
        "manifest",
        "limit",
        "split",
        "max_rows",
        "streaming",
        "version_id",
        "loop_id",
        "dm_args",
        "dm_lake",
        "dm_root",
    )
    return {key: getattr(args, key) for key in safe_keys if hasattr(args, key)}


def _arg_was_supplied(argv: list[str] | None, option: str) -> bool:
    if argv is None:
        return False
    return any(item == option or item.startswith(f"{option}=") for item in argv)


def _agent_version_dir(*, output_dir: str, task_id: str, agent_name: str, version_id: str) -> Path:
    return Path(output_dir) / task_id / agent_name / version_id


def _runtime_version_id(args: argparse.Namespace, writer: object | None) -> str:
    writer_version = getattr(writer, "version_id", "") if writer is not None else ""
    return str(getattr(args, "version_id", "") or writer_version or "").strip()


def _loop_id(args: argparse.Namespace, version_id: str) -> str:
    return str(getattr(args, "loop_id", "") or version_id or "").strip()


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
        if item == "--version-id" and index + 1 < len(argv):
            overrides["version_id"] = argv[index + 1]
            index += 2
            continue
        if item.startswith("--version-id="):
            overrides["version_id"] = item.split("=", 1)[1]
            index += 1
            continue
        if item == "--loop-id" and index + 1 < len(argv):
            overrides["loop_id"] = argv[index + 1]
            index += 2
            continue
        if item.startswith("--loop-id="):
            overrides["loop_id"] = item.split("=", 1)[1]
            index += 1
            continue
        remaining.append(item)
        index += 1
    return remaining, overrides


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loopai-obtainercli")
    parser.add_argument("--task-id", default=os.getenv("TASK_ID", ""))
    parser.add_argument("--output-dir", default=os.getenv("OUTPUT_DIR", "./outputs"))
    parser.add_argument("--version-id", default=os.getenv("VERSION_ID") or os.getenv("LOOP_VERSION_ID") or "")
    parser.add_argument("--loop-id", default=os.getenv("LOOP_UUID") or os.getenv("LOOP_ID") or "")
    parser.add_argument("--no-events", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    searchagent = sub.add_parser("searchagent")
    searchagent.add_argument("--query", default="")
    searchagent.add_argument("--query-file", default="")
    searchagent.add_argument("--task-json", default="")
    searchagent.add_argument("--objective", default="")
    searchagent.add_argument("--keywords", default="")
    searchagent.add_argument("--output-root", default="./outputs")
    searchagent.add_argument("--model-name", default="")
    searchagent.add_argument("--base-url", default="")
    searchagent.add_argument("--api-key", default="")
    searchagent.add_argument("--temperature", type=float, default=None)
    searchagent.add_argument("--prompt-template-dir", default="")
    searchagent.add_argument("--starter-config", default="")
    searchagent.add_argument("--search-engine", default="")
    searchagent.add_argument("--max-urls", type=int, default=None)
    searchagent.add_argument("--max-results-per-source", type=int, default=5)
    searchagent.add_argument("--deepsearch", action=argparse.BooleanOptionalAction, default=True)
    searchagent.add_argument("--max-deep-queries", type=int, default=3)
    searchagent.add_argument("--max-deep-pages", type=int, default=3)
    searchagent.add_argument("--deep-context-chars", type=int, default=12000)
    searchagent.add_argument("--tavily-api-key", default=os.getenv("TAVILY_API_KEY", ""))
    searchagent.add_argument("--kaggle-username", default=os.getenv("KAGGLE_USERNAME", ""))
    searchagent.add_argument("--kaggle-key", default=os.getenv("KAGGLE_KEY", ""))
    searchagent.add_argument("--debug", action="store_true")
    searchagent.add_argument("--json", action="store_true")

    download = sub.add_parser("download")
    download_sub = download.add_subparsers(dest="download_command", required=True)
    download_manifest_cmd = download_sub.add_parser("manifest")
    download_manifest_cmd.add_argument("--manifest", required=True)
    download_manifest_cmd.add_argument("--output-root", default="./outputs/downloads")
    download_manifest_cmd.add_argument("--limit", type=int, default=0)
    download_manifest_cmd.add_argument("--split", default="train")
    download_manifest_cmd.add_argument("--max-rows", type=int, default=MAX_ROWS_PER_DATASET)
    download_manifest_cmd.add_argument("--streaming", action=argparse.BooleanOptionalAction, default=True)
    download_manifest_cmd.add_argument("--json", action="store_true")

    dm = sub.add_parser("dm")
    dm.add_argument("--lake", dest="dm_lake", default="")
    dm.add_argument("--root", dest="dm_root", default="")
    dm.add_argument("dm_args", nargs=argparse.REMAINDER)
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parse_argv, event_overrides = _extract_event_args(raw_argv)
    args = parser.parse_args(parse_argv)
    for key, value in event_overrides.items():
        setattr(args, key, value)
    node = _command_node(args)
    writer = get_obtainer_event_writer(
        task_id=args.task_id,
        output_dir=args.output_dir,
        version_id=args.version_id or None,
        enabled=not args.no_events,
    )
    version_id = _runtime_version_id(args, writer)
    loop_id = _loop_id(args, version_id)
    if hasattr(args, "output_root") and args.task_id and version_id and not _arg_was_supplied(raw_argv, "--output-root"):
        args.output_root = str(
            _agent_version_dir(
                output_dir=args.output_dir,
                task_id=args.task_id,
                agent_name="obtainercli",
                version_id=version_id,
            )
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
        if args.command == "searchagent":
            emit_obtainer_event(
                writer,
                node=node,
                status="running",
                progress=0.2,
                message="Running Obtainer SearchAgent",
                data=_command_event_data(args),
            )
            result = run_searchagent(
                query=args.query,
                query_file=args.query_file or None,
                task_json=args.task_json or None,
                objective=args.objective,
                keywords=args.keywords,
                output_root=args.output_root,
                model_name=args.model_name,
                base_url=args.base_url,
                api_key=args.api_key,
                temperature=args.temperature,
                prompt_template_dir=args.prompt_template_dir,
                starter_config=args.starter_config or None,
                search_engine=args.search_engine,
                max_urls=args.max_urls,
                max_results_per_source=args.max_results_per_source,
                tavily_api_key=args.tavily_api_key,
                kaggle_username=args.kaggle_username,
                kaggle_key=args.kaggle_key,
                deepsearch=args.deepsearch,
                max_deep_queries=args.max_deep_queries,
                max_deep_pages=args.max_deep_pages,
                deep_context_chars=args.deep_context_chars,
                debug=args.debug,
            )
        elif args.command == "download" and args.download_command == "manifest":
            emit_obtainer_event(
                writer,
                node=node,
                status="running",
                progress=0.2,
                message="Downloading datasets from SearchAgent manifest",
                data=_command_event_data(args),
            )
            result = download_manifest(
                manifest=args.manifest,
                output_root=args.output_root,
                limit=args.limit,
                split=args.split,
                max_rows=args.max_rows,
                streaming=args.streaming,
            )
        elif args.command == "dm":
            result = _run_dm_command(args.dm_args, lake=args.dm_lake, root=args.dm_root)
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
                "details": exc.details,
            },
        )
        _print_json(
            {
                "ok": False,
                "error_code": exc.error_code,
                "message": exc.message,
                "hint": exc.hint,
                "details": exc.details,
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


if __name__ == "__main__":
    main()
