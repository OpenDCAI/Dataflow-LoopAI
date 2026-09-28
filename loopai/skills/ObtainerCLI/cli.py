from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

from .datamixer_adapter import (
    append_audit_rows,
    datamixer_argv_from_lake,
    init_datamixer_lake,
    start_background_auto_embed,
    warehouse_root,
)
from .download import MAX_BYTES_PER_DATASET, MAX_ROWS_PER_DATASET, download_manifest
from .errors import ObtainerCliError
from .events import emit_obtainer_event, get_obtainer_event_writer
from .lake_bundle import consolidate as lake_consolidate
from .lake_bundle import export_bundle as lake_export_bundle
from .lake_bundle import import_bundle as lake_import_bundle
from .lake_bundle import import_data as lake_import_data
from .lake_manager import (
    current_lake_pointer,
    delete_lake_pointer,
    load_lake_pointer,
    scan_lake_candidates,
    unbind_lake_obtainer_context,
    update_lake_obtainer_context,
)
from .models import sha256_text, utc_now
from .monitor_state import start_background_rebuild, update_monitor_delta


def run_dataset_acquisition_agent(*args, **kwargs):
    """Lazy proxy so basic lake commands do not require Codex dependencies."""
    from .dataset_acquisition_agent import run_agent

    return run_agent(*args, **kwargs)


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
    _update_monitor_after_datamixer_command(args, parsed if isinstance(parsed, dict) else {}, lake=lake, root=root)
    return {
        "ok": True,
        "command": "dm",
        "status": "success",
        "warnings": [],
        "exit_code": exit_code,
        "argv": args,
        "result": parsed,
    }


def _root_from_datamixer_args(args: list[str], *, root: str = "") -> str:
    if root:
        return root
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--root" and index + 1 < len(args):
            return args[index + 1]
        if item.startswith("--root="):
            return item.split("=", 1)[1]
        index += 1
    return ""


def _datamixer_command_key(args: list[str]) -> tuple[str, ...]:
    skip_next = False
    parts: list[str] = []
    for item in args:
        if skip_next:
            skip_next = False
            continue
        if item in {"--root"}:
            skip_next = True
            continue
        if item == "--json" or item.startswith("--root="):
            continue
        if item.startswith("-"):
            continue
        parts.append(item)
        if len(parts) >= 2:
            break
    return tuple(parts)


def _is_monitor_mutating_datamixer_command(key: tuple[str, ...]) -> bool:
    return key[:1] in {("ingest",), ("agent-ingest",), ("export-jsonl",)} or key[:2] in {
        ("op", "run"),
        ("pipeline", "run"),
        ("dataflow", "agent-run"),
        ("index", "build"),
        ("recipe", "export"),
        ("snapshot", "create"),
        ("contam", "add"),
        ("decontaminate",),
    }


def _export_record_from_datamixer_result(key: tuple[str, ...], result: dict) -> dict | None:
    """Build a monitor/audit export row from a datamixer export result.

    Keeps the final "出湖" artifact path (``output_uri``) in the shared lake
    state so the Obtainer task card can surface it without knowing which agent
    produced the export (recipe export vs flat JSONL dump).
    """
    if not isinstance(result, dict):
        return None
    if key[:2] == ("recipe", "export"):
        out_uri = str(result.get("out_dir") or "").strip()
        if not out_uri:
            return None
        return {
            "export_id": str(result.get("export_id") or ""),
            "strategy": "recipe_export",
            "requested_size": 0,
            "actual_size": int(result.get("selected_samples") or 0),
            "output_uri": out_uri,
            "format": str(result.get("format") or "jsonl"),
            "manifest_path": str(result.get("manifest_path") or ""),
            "snapshot_id": str(result.get("snapshot_id") or ""),
            "dataset_digest": str(result.get("dataset_digest") or ""),
            "files": int(result.get("files") or 0),
            "created_at": utc_now(),
        }
    if key[:1] == ("export-jsonl",):
        out_uri = str(result.get("out") or "").strip()
        if not out_uri:
            return None
        export_id = str(result.get("export_id") or "").strip() or (
            "export_" + sha256_text(f"{Path(out_uri).expanduser().resolve()}:{utc_now()}")[:24]
        )
        return {
            "export_id": export_id,
            "strategy": "export_jsonl",
            "requested_size": 0,
            "actual_size": int(result.get("exported") or 0),
            "output_uri": out_uri,
            "format": "jsonl",
            "manifest_path": "",
            "snapshot_id": "",
            "dataset_digest": "",
            "files": 0,
            "created_at": utc_now(),
        }
    return None


def _update_monitor_after_datamixer_command(args: list[str], result: dict, *, lake: str = "", root: str = "") -> None:
    key = _datamixer_command_key(args)
    if not _is_monitor_mutating_datamixer_command(key):
        return
    warehouse = _root_from_datamixer_args(args, root=root)
    if not warehouse:
        return
    written = int(result.get("written") or result.get("rows_written") or 0)
    indexed = int(result.get("indexed") or result.get("rows_indexed") or 0)
    is_export = key[:2] == ("recipe", "export") or key[:1] == ("export-jsonl",)
    exports = 1 if is_export else 0
    delta = {
        "summary_delta": {
            "records": written,
            "embeddings": indexed,
            "exports": exports,
        }
    }
    export_row = _export_record_from_datamixer_result(key, result) if is_export else None
    if export_row:
        delta["latest"] = {"exports": [export_row]}
    if export_row:
        try:
            append_audit_rows(warehouse, "exports", [export_row])
        except Exception:
            pass
    try:
        update_monitor_delta(
            warehouse,
            delta,
            operation_id="dm." + ".".join(key),
            trigger_background=False,
            lake=lake or None,
        )
        if written > 0 and lake:
            dataset = key[1] if key[:1] in {("ingest",), ("agent-ingest",)} and len(key) > 1 else None
            start_background_auto_embed(
                lake=lake,
                dataset=dataset,
                operation_id="dm." + ".".join(key),
            )
    except Exception:
        pass


def _has_option(args: list[str], option: str) -> bool:
    return any(item == option or item.startswith(f"{option}=") for item in args)


def _lake_warehouse_for_agent(*, lake: str, root: str) -> str:
    if root:
        warehouse = Path(root).expanduser().resolve()
    elif lake:
        warehouse = warehouse_root(lake)
    else:
        return ""
    if not warehouse.is_dir() or not (warehouse / "datamixer.toml").is_file():
        raise ObtainerCliError(
            "LAKE_NOT_LOADED",
            f"DataMixer lake is not loaded: no initialized warehouse at {warehouse}",
            hint=(
                "Load or initialize the lake first with `dm lake init --root <lake-root>` "
                "or `dm lake load --warehouse <warehouse>`, then retry the worker."
            ),
            exit_code=2,
        )
    return str(warehouse)


def _inject_lake_runtime_defaults(args: list[str], *, lake: str, root: str) -> tuple[list[str], str]:
    """Resolve agent-only boilerplate from the loaded lake context."""
    if not args or not lake:
        return args, root
    command = args[0]
    effective_root = root
    if command == "dataset-acquisition-agent":
        effective_root = _lake_warehouse_for_agent(lake=lake, root=root)
    return args, effective_root


def _persist_lake_runtime_context(
    *, lake: str, args: list[str], result: dict, task_id: str = ""
) -> None:
    if not lake or not args:
        return
    updates: dict[str, str] = {}
    if args[0] == "dataset-acquisition-agent" and result.get("run_dir"):
        updates["obtainer_active_acquisition_run"] = str(result["run_dir"])
        if task_id:
            updates["obtainer_active_task_id"] = task_id
        if result.get("resolved_model"):
            updates["obtainer_resolved_model"] = str(result["resolved_model"])
        if result.get("model_source"):
            updates["obtainer_model_source"] = str(result["model_source"])
    if updates:
        update_lake_obtainer_context(link_path=lake, updates=updates)


def _run_dm_command(
    argv: list[str], *, lake: str = "", root: str = "", task_id: str = ""
) -> dict:
    args = list(argv or [])
    if args and args[0] == "--":
        args = args[1:]
    if args and args[0] == "lake":
        return _run_dm_lake_command(args[1:], lake=lake)
    args, root = _inject_lake_runtime_defaults(args, lake=lake, root=root)
    if args[:1] == ["dataset-acquisition-agent"]:
        action = args[1] if len(args) > 1 else ""
        if action in {"start", "status", "resume"} and not _has_option(args, "--run"):
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_RUN_REQUIRED",
                f"dataset-acquisition-agent {action} requires --run <run_dir>",
                hint="Pass the same explicit --run directory to start, status, and resume.",
                exit_code=2,
            )
    if args and args[0] in {
        "obtainer-orchestrator",
        "sft-export-agent",
        "searchagent",
        "webagent",
    }:
        retired = args[0]
        raise ObtainerCliError(
            "OBTAINER_AGENT_RETIRED",
            f"{retired} is not available in the lite Obtainer workflow",
            hint=(
                "The main agent controls dataset-acquisition-agent and "
                "dataflow agent-run. Dataset acquisition is Hugging Face-only; "
                "use DataMixer recipe commands directly for validation and export."
            ),
            exit_code=2,
        )
    if args and args[0] == "dataset-acquisition-agent":
        result = run_dataset_acquisition_agent(args[1:], root=root, task_id=task_id)
        result.setdefault("ok", True)
        result.setdefault("command", "dm.dataset-acquisition-agent")
        result.setdefault("status", "success")
        result.setdefault("warnings", [])
        _persist_lake_runtime_context(lake=lake, args=args, result=result, task_id=task_id)
        return result
    result = _run_datamixer_command(args, lake=lake, root=root)
    _persist_lake_runtime_context(lake=lake, args=args, result=result, task_id=task_id)
    return result


def _run_dm_lake_command(argv: list[str], *, lake: str = "") -> dict:
    # ``dm`` always emits the outer JSON envelope. Accept a trailing --json on
    # lake subcommands as well, so documented `dm lake ... --json` commands do
    # not leak that flag into the nested argparse parser.
    argv = [item for item in argv if item != "--json"]
    parser = argparse.ArgumentParser(prog="loopai-obtainercli dm lake")
    sub = parser.add_subparsers(dest="lake_action", required=True)
    current = sub.add_parser("current")
    current.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    init = sub.add_parser("init", help="create a DataMixer lake and make it active")
    init.add_argument("--root", required=True, help="new lake root; warehouse is created below it")
    init.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    init.add_argument("--if-not-exists", action="store_true")
    scan = sub.add_parser("scan")
    scan.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    scan.add_argument("--project-root", default=".")
    scan.add_argument("--root", action="append", default=[])
    scan.add_argument("--max-depth", type=int, default=6)
    load = sub.add_parser("load")
    load.add_argument("--warehouse", required=True)
    load.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    load.add_argument("--lake-root", default="")
    delete = sub.add_parser("delete")
    delete.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    delete.add_argument("--delete-warehouse", action="store_true")
    delete.add_argument("--yes", action="store_true")
    context = sub.add_parser("context", help="show or update persistent Obtainer defaults for this lake")
    context.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    context.add_argument("--set", dest="context_updates", action="append", default=[], metavar="KEY=VALUE")
    unbind = sub.add_parser("unbind", help="clear active task/run bindings (task_id, acquisition run)")
    unbind.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    monitor = sub.add_parser("monitor")
    monitor_sub = monitor.add_subparsers(dest="monitor_action", required=True)
    monitor_rebuild = monitor_sub.add_parser("rebuild")
    monitor_rebuild.add_argument("--link", default=lake or ".datamixer/lake.yaml")
    monitor_rebuild.add_argument("--warehouse", default="")
    consolidate = sub.add_parser("consolidate", help="move scattered lake/obtainer assets into .datamixer/ + outputs/obtainer/")
    consolidate.add_argument("--project-root", default=".")
    export_bundle = sub.add_parser("export-bundle", help="export the lake + obtainer digital assets as a tar.gz bundle")
    export_bundle.add_argument("--out", required=True)
    export_bundle.add_argument("--project-root", default=".")
    export_bundle.add_argument("--link", default="")
    export_bundle.add_argument("--include-runtime", action="store_true")
    import_bundle = sub.add_parser("import-bundle", help="import a bundle tar.gz into a target project root")
    import_bundle.add_argument("--file", required=True)
    import_bundle.add_argument("--target", required=True)
    import_data = sub.add_parser("import-data", help="import a data package into the active lake (incremental/batch)")
    import_data.add_argument("--package", required=True)
    import_data.add_argument("--project-root", default=".")
    import_data.add_argument("--link", default="")
    parsed = parser.parse_args(argv)
    if parsed.lake_action == "current":
        return current_lake_pointer(link_path=parsed.link)
    if parsed.lake_action == "init":
        return init_datamixer_lake(
            root=parsed.root,
            link_path=parsed.link,
            if_not_exists=parsed.if_not_exists,
        )
    if parsed.lake_action == "scan":
        return scan_lake_candidates(
            project_root=parsed.project_root,
            active_link=parsed.link,
            extra_roots=parsed.root,
            max_depth=parsed.max_depth,
        )
    if parsed.lake_action == "load":
        return load_lake_pointer(
            warehouse=parsed.warehouse,
            link_path=parsed.link,
            lake_root=parsed.lake_root or None,
        )
    if parsed.lake_action == "delete":
        return delete_lake_pointer(
            link_path=parsed.link,
            delete_warehouse=parsed.delete_warehouse,
            yes=parsed.yes,
        )
    if parsed.lake_action == "context":
        if not parsed.context_updates:
            return current_lake_pointer(link_path=parsed.link)
        updates: dict[str, str] = {}
        for item in parsed.context_updates:
            if "=" not in item:
                raise ValueError(f"Lake context expects KEY=VALUE, got: {item}")
            key, value = item.split("=", 1)
            updates[key.strip()] = value.strip()
        return update_lake_obtainer_context(link_path=parsed.link, updates=updates)
    if parsed.lake_action == "unbind":
        return unbind_lake_obtainer_context(link_path=parsed.link)
    if parsed.lake_action == "monitor" and parsed.monitor_action == "rebuild":
        warehouse = parsed.warehouse
        if not warehouse:
            current = current_lake_pointer(link_path=parsed.link)
            warehouse = str(current.get("warehouse") or "")
        if not warehouse:
            raise ObtainerCliError(
                "DATAMIXER_WAREHOUSE_NOT_FOUND",
                "No DataMixer warehouse is loaded for monitor rebuild.",
                hint="Use `dm lake load --warehouse <warehouse>` first, or pass --warehouse.",
                exit_code=2,
            )
        return start_background_rebuild(warehouse, lake=parsed.link)
    if parsed.lake_action == "consolidate":
        return lake_consolidate(parsed.project_root)
    if parsed.lake_action == "export-bundle":
        return lake_export_bundle(
            parsed.project_root,
            parsed.out,
            link=parsed.link or None,
            include_runtime=parsed.include_runtime,
        )
    if parsed.lake_action == "import-bundle":
        return lake_import_bundle(parsed.file, parsed.target)
    if parsed.lake_action == "import-data":
        return lake_import_data(parsed.project_root, parsed.package, link=parsed.link or None)
    raise ObtainerCliError("UNSUPPORTED_DM_LAKE_COMMAND", f"Unsupported dm lake command: {parsed.lake_action}")


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
        "output_root",
        "base_url",
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

    download = sub.add_parser("download")
    download_sub = download.add_subparsers(dest="download_command", required=True)
    download_manifest_cmd = download_sub.add_parser("manifest")
    download_manifest_cmd.add_argument("--manifest", required=True)
    download_manifest_cmd.add_argument("--output-root", default="./outputs/downloads")
    download_manifest_cmd.add_argument("--limit", type=int, default=0)
    download_manifest_cmd.add_argument("--split", default="train")
    download_manifest_cmd.add_argument(
        "--max-rows",
        type=int,
        default=MAX_ROWS_PER_DATASET,
        help="maximum rows to write per dataset; 0 and oversized values are capped",
    )
    download_manifest_cmd.add_argument(
        "--max-bytes-per-dataset",
        type=int,
        default=MAX_BYTES_PER_DATASET,
        help="maximum local JSONL output bytes per dataset; partial files are kept and reported when capped",
    )
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
        if args.command == "download" and args.download_command == "manifest":
            emit_obtainer_event(
                writer,
                node=node,
                status="running",
                progress=0.2,
                message="Downloading datasets from manifest",
                data=_command_event_data(args),
            )
            result = download_manifest(
                manifest=args.manifest,
                output_root=args.output_root,
                limit=args.limit,
                split=args.split,
                max_rows=args.max_rows,
                max_bytes_per_dataset=args.max_bytes_per_dataset,
                streaming=args.streaming,
            )
        elif args.command == "dm":
            result = _run_dm_command(
                args.dm_args,
                lake=args.dm_lake,
                root=args.dm_root,
                task_id=args.task_id,
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
