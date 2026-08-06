from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..models.body import response_body
from ..utils.obtainer.monitor import probe_embedding_health
from ..utils.obtainer.web_pipeline import build_web_pipeline_overview
from loopai.skills.ObtainerCLI.monitor_state import read_monitor_state, start_background_rebuild
from loopai.skills.ObtainerCLI.datamixer_adapter import warehouse_root
from loopai.skills.ObtainerCLI.lake_manager import (
    current_lake_pointer,
    delete_lake_pointer,
    load_lake_pointer,
    scan_lake_candidates,
)

router = APIRouter(tags=["obtainer"])

REPO_ROOT = Path(__file__).resolve().parents[3]


class DataMixerCliRequest(BaseModel):
    argv: list[str] | None = None
    line: str | None = None
    files: dict[str, str] | None = None
    lake: str | None = None
    root: str | None = None
    timeout_seconds: float | None = 20.0


class DataMixerLakeLoadRequest(BaseModel):
    warehouse: str
    link: str | None = None
    lake_root: str | None = None


class DataMixerLakeDeleteRequest(BaseModel):
    link: str | None = None
    delete_warehouse: bool = False
    yes: bool = False


def _resolve_repo_path(raw: str | None, default: str) -> Path:
    path = Path(raw or default).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _resolve_lake_path(lake: str | None) -> Path:
    path = _resolve_repo_path(lake, ".loopai/lake.yaml")
    if path.suffix in {".yaml", ".yml"} and not path.exists():
        raise FileNotFoundError(f"Lake config not found: {path}")
    return path


def _resolve_datamixer_root(*, lake: str | None = None, root: str | None = None) -> Path:
    if root:
        path = Path(root).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path
    return warehouse_root(_resolve_lake_path(lake))


def _datamixer_command_payload() -> dict[str, Any]:
    return {
        "entrypoint": "loopai-obtainercli dm --lake .loopai/lake.yaml",
        "backend": "datamixer",
        "groups": [
            {
                "key": "lake",
                "label": "Lake Management",
                "commands": [
                    "lake scan",
                    "lake init --root /path/to/lake-root",
                    "lake current",
                    "lake load --warehouse /path/to/warehouse",
                    "lake context",
                    "lake unbind",
                    "lake delete",
                    "lake monitor rebuild",
                ],
            },
            {
                "key": "catalog",
                "label": "Catalog",
                "commands": [
                    "dataset list",
                    "query --limit 20",
                    "columns",
                    "schema",
                    "index stats",
                    "status",
                    "stats",
                ],
            },
            {
                "key": "ingest",
                "label": "Ingest",
                "commands": [
                    "ingest DATASET --file /path/to/input.jsonl --quality-level L3 --dataset-card /path/to/DATASET.md --source-row-count N --derived-field FIELD",
                    "agent-ingest /path/to/input.jsonl --dataset DATASET --quality-level L2",
                    "dataset-acquisition-agent start --run outputs/obtainer_dataset_run --analysis-report outputs/analyzer_report.md",
                    "dataset-acquisition-agent status --run outputs/obtainer_dataset_run",
                ],
            },
            {
                "key": "operators",
                "label": "Processing",
                "commands": [
                    "op list",
                    "op run OPERATOR --dataset DATASET",
                    "pipeline run /path/to/pipeline.yaml",
                    "dataflow agent-run --target \"clean and enrich records\" --model MODEL --dataset DATASET",
                ],
            },
            {
                "key": "benchmark",
                "label": "Benchmark Guard",
                "commands": [
                    "contam list",
                    "contam add --name BENCH --file /path/to/benchmark.jsonl --text-field text --workers 8  # registers and removes existing overlaps",
                    "decontaminate --against BENCH --threshold 0.8",
                    "decontaminate --against BENCH --threshold 0.8 --apply",
                ],
            },
            {
                "key": "index",
                "label": "Index & Recall",
                "commands": [
                    "index stats",
                    "recall --query \"example\" --limit 10",
                    "index build",
                ],
            },
            {
                "key": "recipe",
                "label": "Recipe Export",
                "commands": [
                    "recipe validate /path/to/recipe.yaml",
                    "recipe plan /path/to/recipe.yaml",
                    "recipe preview /path/to/recipe.yaml",
                    "recipe export /path/to/recipe.yaml --snapshot",
                    "sft-export-agent start --run outputs/obtainer_sft_export --analysis-report outputs/analyzer_report.md",
                    "sft-export-agent status --run outputs/obtainer_sft_export",
                ],
            },
            {
                "key": "lineage",
                "label": "Lineage",
                "commands": ["snapshot list", "snapshot create", "lineage list"],
            },
        ],
    }


def _normalize_obtainercli_dm_args(*, argv: list[str] | None = None, line: str | None = None) -> list[str]:
    args = list(argv or shlex.split(line or ""))
    if args[:1] == ["loopai-obtainercli"]:
        args = args[1:]
    if args[:3] == ["python", "-m", "loopai.skills.ObtainerCLI.cli"]:
        args = args[3:]
    if args[:1] == ["dm"]:
        args = args[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    return args


def _run_obtainercli_dm_for_webui(request: DataMixerCliRequest) -> dict[str, Any]:
    args = _normalize_obtainercli_dm_args(argv=request.argv, line=request.line)
    tmp_paths: list[str] = []
    files = request.files or {}
    if files:
        for index, item in enumerate(args):
            if item not in files:
                continue
            fd, path = tempfile.mkstemp(suffix=".tmp", prefix="obtainercli-webui-")
            os.close(fd)
            Path(path).write_text(files[item], encoding="utf-8")
            tmp_paths.append(path)
            args[index] = path
    lake = request.lake or ".loopai/lake.yaml"
    cli_argv = ["dm", "--lake", lake]
    if request.root:
        cli_argv.extend(["--root", request.root])
    cli_argv.extend(args)
    display = "loopai-obtainercli " + " ".join(shlex.quote(item) for item in cli_argv)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in [str(REPO_ROOT), env.get("PYTHONPATH", "")] if part
    )
    process_argv = [sys.executable, "-m", "loopai.skills.ObtainerCLI.cli", *cli_argv]
    timeout = max(1.0, min(float(request.timeout_seconds or 20.0), 300.0))
    try:
        completed = subprocess.run(
            process_argv,
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        raw = (completed.stdout or "").strip()
        err = (completed.stderr or "").strip()
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        message = f"ObtainerCLI command timed out after {timeout:g}s"
        return {
            "command": display,
            "exit": 124,
            "output": {"error": message},
            "raw": "\n".join(part for part in [raw, err, message] if part),
            "argv": cli_argv,
            "timed_out": True,
        }
    except Exception as exc:
        raw_error = str(exc)
        return {
            "command": display,
            "exit": 1,
            "output": {"error": raw_error},
            "raw": raw_error,
            "argv": cli_argv,
        }
    finally:
        for path in tmp_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
    parsed: Any = None
    if raw:
        for line in reversed(raw.splitlines()):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if isinstance(parsed, dict) and "result" in parsed:
        output = parsed.get("result")
    elif isinstance(parsed, dict) and parsed.get("ok") is False:
        output = {
            "error": parsed.get("message") or parsed.get("error_code") or err or raw,
            "error_code": parsed.get("error_code"),
            "hint": parsed.get("hint"),
            "details": parsed.get("details"),
        }
    else:
        output = parsed
    if output is None:
        output = {"error": err or raw or f"command exited with code {exit_code}"}
    return {
        "command": display,
        "exit": exit_code,
        "output": output,
        "raw": "\n".join(part for part in [raw, err] if part),
        "argv": cli_argv,
        "obtainercli": parsed,
    }


@router.get("/lake/monitor", operation_id="getObtainerLakeMonitor", summary="获取 Obtainer 数据湖监控数据")
async def get_lake_monitor(lake: str | None = None):
    try:
        lake_path = _resolve_lake_path(lake)
        data = read_monitor_state(warehouse_root(lake_path), lake=lake_path)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get(
    "/webagent/overview",
    operation_id="getObtainerWebAgentOverview",
    summary="获取 WebAgent 到 L3 数据流水线的实时概览",
)
async def get_webagent_overview(
    lake: str | None = None,
    root: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
):
    """Read current queue/pipeline state for the active-task dashboard."""
    try:
        lake_path = _resolve_lake_path(lake)
        lake_pointer = current_lake_pointer(link_path=lake_path)
        context = lake_pointer.get("obtainer_context") or {}
        warehouse = _resolve_datamixer_root(lake=lake, root=root)
        data = build_web_pipeline_overview(
            warehouse,
            run_id=run_id,
            acquisition_run=str(context.get("obtainer_active_acquisition_run") or "") or None,
            lake_context=context,
            project_root=REPO_ROOT,
            task_id=task_id,
            explicit_binding=bool(root or run_id),
        )
        initialized = bool(data.get("initialized"))
        bound_warehouse = str(data.get("warehouse") or "")
        pointer_warehouse = str(lake_pointer.get("warehouse") or "")
        pointer_matches = bool(
            initialized
            and bound_warehouse
            and pointer_warehouse
            and Path(bound_warehouse).expanduser().resolve()
            == Path(pointer_warehouse).expanduser().resolve()
        )
        data["lake"] = {
            "lake_config": lake_pointer.get("lake_config") if pointer_matches else None,
            "lake_root": (
                lake_pointer.get("lake_root") if pointer_matches
                else str(Path(bound_warehouse).parent) if initialized and bound_warehouse
                else None
            ),
            "warehouse": bound_warehouse if initialized else None,
            "loaded": bool(
                initialized and bound_warehouse
                and (Path(bound_warehouse) / "datamixer.toml").is_file()
            ),
        }
        data["monitor"] = (
            read_monitor_state(
                bound_warehouse,
                lake=lake_path if pointer_matches else None,
            )
            if data.get("initialized") else None
        )
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.post("/lake/monitor/rebuild", operation_id="rebuildObtainerLakeMonitor", summary="异步重建 Obtainer 数据湖监控 cache")
async def rebuild_lake_monitor(lake: str | None = None):
    try:
        lake_path = _resolve_lake_path(lake)
        data = start_background_rebuild(warehouse_root(lake_path), lake=lake_path)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get("/lake/embedding_health", operation_id="getObtainerEmbeddingHealth", summary="探测 Obtainer embedding 服务状态")
async def get_embedding_health(lake: str | None = None, timeout_seconds: float = 3.0):
    try:
        data = probe_embedding_health(lake=_resolve_lake_path(lake), timeout_seconds=timeout_seconds)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get(
    "/datamixer/lake/current",
    operation_id="getObtainerDataMixerLakeCurrent",
    summary="获取当前 DataMixer 数据湖指针",
)
async def get_datamixer_lake_current(lake: str | None = None):
    try:
        data = current_lake_pointer(link_path=_resolve_repo_path(lake, ".loopai/lake.yaml"))
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get(
    "/datamixer/lake/scan",
    operation_id="scanObtainerDataMixerLakes",
    summary="扫描项目与缓存目录中的 DataMixer 数据湖",
)
async def scan_datamixer_lakes(lake: str | None = None, max_depth: int = 6):
    try:
        data = scan_lake_candidates(
            project_root=REPO_ROOT,
            active_link=_resolve_repo_path(lake, ".loopai/lake.yaml"),
            max_depth=max_depth,
        )
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.post(
    "/datamixer/lake/load",
    operation_id="loadObtainerDataMixerLake",
    summary="加载已有 DataMixer warehouse 为当前数据湖",
)
async def load_datamixer_lake(request: DataMixerLakeLoadRequest):
    try:
        warehouse = _resolve_repo_path(request.warehouse, request.warehouse)
        link = _resolve_repo_path(request.link, ".loopai/lake.yaml")
        lake_root = _resolve_repo_path(request.lake_root, request.lake_root) if request.lake_root else None
        data = load_lake_pointer(warehouse=warehouse, link_path=link, lake_root=lake_root)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.post(
    "/datamixer/lake/delete",
    operation_id="deleteObtainerDataMixerLake",
    summary="卸载当前 DataMixer 数据湖指针",
)
async def delete_datamixer_lake(request: DataMixerLakeDeleteRequest):
    try:
        link = _resolve_repo_path(request.link, ".loopai/lake.yaml")
        data = delete_lake_pointer(
            link_path=link,
            delete_warehouse=request.delete_warehouse,
            yes=request.yes,
        )
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=data)()


@router.get(
    "/datamixer/commands",
    operation_id="getObtainerDataMixerCommands",
    summary="获取 DataMixer 命令面",
)
async def get_datamixer_commands():
    return response_body(data=_datamixer_command_payload())()


@router.post(
    "/datamixer/cli",
    operation_id="runObtainerDataMixerCli",
    summary="通过 DataMixer 命令面执行数据湖操作",
)
async def run_datamixer_cli(request: DataMixerCliRequest):
    try:
        result = await asyncio.to_thread(_run_obtainercli_dm_for_webui, request)
    except Exception as exc:
        return response_body(code=400, status="error", message=str(exc))()
    return response_body(data=result)()
