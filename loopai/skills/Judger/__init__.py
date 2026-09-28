"""Judger skill entry points.

The worker is benchmark-pluggable: one benchmark skill is resolved from the
registry and executed by the Codex SDK.  The old step runner remains importable
for legacy integrations, but is not called from this module.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loopai.skills.benchmarks import get_benchmark, resolve_benchmark_from_lake
from loopai.skills.benchmarks.codex_worker import run_worker, worker_status
from loopai.skills.benchmarks.protocol import BenchmarkPlugin


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    return [value] if isinstance(value, dict) else []


def _load_state_from_configer() -> dict[str, Any]:
    task_id = os.getenv("TASK_ID") or os.getenv("task_id")
    if not task_id or not os.getenv("DB_PATH"):
        return {}
    try:
        from loopai.skills.Judger.runner import _load_task_state
        value = _load_task_state(task_id)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _legacy_plugin(entry: dict[str, Any]) -> BenchmarkPlugin:
    """Adapt a Configer bench entry while it is being migrated to SKILL.md."""
    name = str(entry.get("name") or entry.get("bench_name") or "benchmark")
    task_type = str(entry.get("task_type") or "generic")
    return BenchmarkPlugin(
        name=name,
        task_type=task_type,
        version="legacy",
        guard_name=name,
        manifest={"legacy": True, **entry},
        eval_capabilities=[str(entry.get("eval_type") or "")],
    )


def _resolve_plugin(entry: dict[str, Any], *, explicit: bool, benchmark_paths: Any = None) -> tuple[BenchmarkPlugin | None, dict[str, Any] | None]:
    name = str(entry.get("name") or entry.get("bench_name") or "").strip()
    if not name:
        return None, {"status": "failed", "error_code": "BENCHMARK_REQUIRED", "error": "benchmark name is required"}
    try:
        return get_benchmark(name, paths=benchmark_paths), None
    except KeyError as exc:
        # Existing Configer users may have a gallery-only benchmark. Keep that
        # path usable, while explicit SDK calls still get a strict error.
        if explicit:
            return None, {"status": "failed", "benchmark": name, "error_code": "UNKNOWN_BENCHMARK", "error": str(exc)}
        return _legacy_plugin(entry), None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "benchmark"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)


def _persist_configer_results(state: dict[str, Any], *, task_id: str | None) -> None:
    """Best-effort sync of SDK results into the legacy Configer task state.

    Configer remains the source used by the existing WebUI.  The SDK worker
    must not require a database (standalone/offline runs are valid), and a
    persistence failure must never invalidate an already written report.
    """
    if not task_id or not os.getenv("DB_PATH"):
        return
    judger = state.get("judger") if isinstance(state, dict) else None
    if not isinstance(judger, dict):
        return
    updates = {
        key: judger[key]
        for key in (
            "bench_result", "extra_bench_result", "vllm_model_pool_entries",
            "vllm_keep_alive",
        )
        if key in judger and judger[key] is not None
    }
    if not updates:
        return
    try:
        from loopai.skills.Configer import update_configer_task_state_config
        update_configer_task_state_config("judger", updates, task_id=task_id)
    except Exception:
        # Report files and the returned state are still authoritative for this
        # invocation; Configer is a compatibility projection only.
        return


def _ensure_vllm_registered(
    current: dict[str, Any],
    *,
    root: Path,
    thread_id: str | None,
) -> dict[str, Any] | None:
    """Start (when needed) and register the evaluated model's vLLM endpoint.

    The SDK Judger path does not use the deprecated step runner.  Keep the
    lifecycle semantics here: an explicitly supplied endpoint is registered as
    is; otherwise a local ``eval_model_path`` starts the standard vLLM server.
    The process is deliberately never stopped by this skill.
    """
    judger = current.get("judger") if isinstance(current.get("judger"), dict) else {}
    if not isinstance(judger, dict):
        return None
    model_path = str(judger.get("eval_model_path") or judger.get("vllm_model_path") or "").strip()
    base_url = str(judger.get("eval_base_url") or judger.get("vllm_base_url") or "").strip()
    pid = judger.get("vllm_pid")
    port = judger.get("vllm_port")
    command = judger.get("vllm_command")

    if not model_path:
        return None
    if not base_url and port:
        base_url = f"http://127.0.0.1:{port}/v1"
    if not base_url and not pid:
        try:
            from loopai.skills.Judger.utils.vllm_starter import (
                DEFAULT_VLLM_PORT,
                start_vllm_openai_api_server,
            )
            vllm_proc, _stop_event = start_vllm_openai_api_server(
                int(judger.get("eval_vllm_tensor_parallel_size") or 1),
                float(judger.get("eval_vllm_gpu_memory_utilization") or 0.9),
                model_path,
            )
            pid = getattr(vllm_proc, "pid", None)
            port = DEFAULT_VLLM_PORT
            command = str(getattr(vllm_proc, "args", "") or "")
            base_url = f"http://127.0.0.1:{DEFAULT_VLLM_PORT}/v1"
        except Exception:
            # A remote/externally managed endpoint may be supplied later by a
            # caller; do not make SDK benchmark runs without local vLLM fail.
            return None
    if not base_url:
        return None
    try:
        from loopai.schema.model_pool import register_running_vllm
        registered = register_running_vllm(
            model_path,
            base_url,
            name=str(judger.get("rollout_model") or f"eval:{thread_id or current.get('task_id') or 'current'}"),
            task_id=thread_id or current.get("task_id"),
            pid=pid,
            port=port,
            command=command,
            workspace=Path.cwd(),
            persist=True,
        )
    except Exception:
        return None
    judger["eval_base_url"] = base_url
    judger["vllm_base_url"] = base_url
    judger["vllm_model_path"] = model_path
    judger["vllm_pid"] = pid
    judger["vllm_port"] = port
    judger["vllm_command"] = command or ""
    judger["vllm_keep_alive"] = True
    judger["vllm_model_pool_entry"] = registered.config_dict(include_secret=False)
    current["judger"] = judger
    return judger["vllm_model_pool_entry"]


def _run_one(
    entry: dict[str, Any],
    *,
    root: Path,
    explicit: bool,
    predictions: Any,
    references: Any,
    lake: str | Path | None,
    dataset: str | None,
    model: str | None,
    thread_id: str | None,
    resume: bool,
    timeout: int,
    snapshot_id: str | None,
    benchmark_paths: Any,
) -> dict[str, Any]:
    plugin, error = _resolve_plugin(entry, explicit=explicit, benchmark_paths=benchmark_paths)
    if error:
        return error
    assert plugin is not None
    # Use the registry's canonical name for worker/report keys.  Aliases are
    # accepted at the boundary but should not create duplicate lake guards or
    # aggregate entries (e.g. ``human-eval`` vs ``humaneval``).
    name = str(plugin.name or entry.get("name") or entry.get("bench_name") or "benchmark")
    bench_lake = _first(lake, entry.get("lake"), entry.get("warehouse"))
    mount = None
    if bench_lake:
        try:
            mount = resolve_benchmark_from_lake(
                bench_lake,
                name,
                dataset_name=_first(dataset, entry.get("dataset"), entry.get("dataset_name")),
                snapshot_id=_first(snapshot_id, entry.get("snapshot_id")),
            )
        except Exception as exc:
            return {"status": "failed", "benchmark": name, "error_code": "BENCHMARK_DATASET_NOT_FOUND", "error": str(exc)}

    pred = _first(predictions, entry.get("predictions"), entry.get("prediction_path"), entry.get("output_result_path"))
    ref = _first(references, entry.get("references"), entry.get("reference_path"), entry.get("problem_path"))
    item_run_dir = root / _safe_name(name) if not explicit or root.name != _safe_name(name) else root
    result = run_worker(
        role="judger",
        benchmark=name,
        run_dir=item_run_dir,
        inputs={
            "predictions": pred,
            "references": ref,
            "problem_path": entry.get("problem_path"),
            "task_type": entry.get("task_type") or plugin.task_type,
            "eval_type": entry.get("eval_type"),
            "model_path": entry.get("model_path") or entry.get("eval_model_path"),
            "case_num": entry.get("case_num"),
        },
        plugin=plugin,
        mount=mount,
        model=_first(model, entry.get("model")),
        thread_id=thread_id,
        resume=resume,
        timeout=timeout,
    )
    # Keep the evaluated model's vLLM endpoint alive for subsequent DataFlow
    # rollout/difficulty screening.  Registration is best-effort so a pure
    # remote benchmark run is unaffected.
    eval_model_path = entry.get("model_path") or entry.get("eval_model_path")
    eval_base_url = entry.get("eval_base_url") or entry.get("vllm_base_url")
    if eval_model_path and eval_base_url:
        try:
            from loopai.schema.model_pool import register_running_vllm
            registered = register_running_vllm(
                str(eval_model_path),
                str(eval_base_url),
                name=str(entry.get("rollout_model") or f"eval:{thread_id or 'current'}"),
                task_id=thread_id,
                pid=entry.get("vllm_pid"),
                port=entry.get("vllm_port"),
                command=entry.get("vllm_command"),
                workspace=Path.cwd(),
                persist=True,
            )
            result["vllm_model_pool_entry"] = registered.config_dict(include_secret=False)
            result["vllm_keep_alive"] = True
        except Exception:
            pass
    result.setdefault("bench_name", name)
    result.setdefault("task_type", entry.get("task_type") or plugin.task_type)
    result.setdefault("input", {"predictions": pred, "references": ref})
    result.setdefault("report_path", str((item_run_dir / "final_report.json").resolve()))
    result.setdefault("final_report_path", result["report_path"])
    if mount is not None:
        result.setdefault("dataset_id", mount.dataset_id)
        result.setdefault("snapshot_id", mount.snapshot_id)
        result.setdefault("lineage", mount.lineage)
        result.setdefault("benchmark_guard", mount.guard)
    return result


def run(
    state: Optional[Dict[str, Any]] = None,
    *,
    benchmark: str | None = None,
    run_dir: str | Path | None = None,
    predictions: Any = None,
    references: Any = None,
    lake: str | Path | None = None,
    dataset: str | None = None,
    model: str | None = None,
    thread_id: str | None = None,
    resume: bool = False,
    timeout: int = 900,
    snapshot_id: str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run one or more benchmark skills through Codex SDK.

    ``state`` may be omitted when ``DB_PATH``/``TASK_ID`` are set; in that case
    the current Judger section is loaded through Configer for compatibility.
    """
    current = state if isinstance(state, dict) else _load_state_from_configer()
    judger = current.get("judger") if isinstance(current.get("judger"), dict) else {}
    try:
        from loopai.schema.model_pool import StarterModelPool, load_starter_system_config_sync
        codex_provider = StarterModelPool(load_starter_system_config_sync(prefer_db=True) or {}).resolve_role_provider("codex")
    except Exception:
        codex_provider = None
    explicit = benchmark is not None or bool(judger.get("benchmark") or current.get("benchmark"))
    benchmark = benchmark or judger.get("benchmark") or current.get("benchmark")
    if benchmark:
        entries = [{"name": benchmark, "task_type": judger.get("eval_task_type")}]
    else:
        entries = _as_list(judger.get("benchlist")) + _as_list(judger.get("extra_benchlist"))
    if not entries:
        return {"status": "failed", "error_code": "BENCHMARK_REQUIRED", "error": "benchmark or judger.benchlist is required"}

    root = Path(run_dir or judger.get("runtime_output_dir") or current.get("output_dir") or "outputs/judger").expanduser()
    registered_vllm = _ensure_vllm_registered(
        current,
        root=root,
        thread_id=thread_id or current.get("task_id") or os.getenv("TASK_ID"),
    )
    primary_count = len(_as_list(judger.get("benchlist"))) if not benchmark else 1
    primary: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            entry = {"name": str(entry)}
        # Carry task-scoped vLLM lifecycle metadata into each benchmark item;
        # the SDK worker itself never shuts this endpoint down.
        entry = {
            **entry,
            "eval_base_url": entry.get("eval_base_url") or judger.get("eval_base_url") or judger.get("vllm_base_url"),
            "vllm_pid": entry.get("vllm_pid") or judger.get("vllm_pid"),
            "vllm_port": entry.get("vllm_port") or judger.get("vllm_port"),
            "vllm_command": entry.get("vllm_command") or judger.get("vllm_command"),
            "model_path": entry.get("model_path") or entry.get("eval_model_path") or judger.get("eval_model_path"),
        }
        if registered_vllm:
            entry["eval_base_url"] = entry.get("eval_base_url") or judger.get("eval_base_url")
            entry["vllm_pid"] = entry.get("vllm_pid") or judger.get("vllm_pid")
            entry["vllm_port"] = entry.get("vllm_port") or judger.get("vllm_port")
            entry["vllm_command"] = entry.get("vllm_command") or judger.get("vllm_command")
        item = _run_one(
            entry,
            root=root,
            explicit=explicit,
            predictions=predictions,
            references=references,
            lake=lake or judger.get("lake") or judger.get("warehouse"),
            dataset=dataset,
            model=model or judger.get("model"),
            thread_id=thread_id or current.get("task_id") or os.getenv("TASK_ID"),
            resume=resume,
            timeout=timeout,
            snapshot_id=snapshot_id,
            benchmark_paths=kwargs.get("benchmark_paths"),
        )
        (primary if index < primary_count else extra).append(item)

    metrics: dict[str, Any] = {}
    guards: dict[str, Any] = {}
    lineages: dict[str, Any] = {}
    skills: dict[str, Any] = {}
    vllm_entries: dict[str, Any] = {}
    for item in primary + extra:
        name = item.get("bench_name") or item.get("benchmark") or "unknown"
        if item.get("metrics"):
            metrics[str(name)] = item["metrics"]
        if item.get("benchmark_guard") is not None:
            guards[str(name)] = item.get("benchmark_guard") or {}
        if item.get("lineage") is not None:
            lineages[str(name)] = item.get("lineage") or {}
        if item.get("benchmark_skill") is not None:
            skills[str(name)] = item.get("benchmark_skill") or {}
        if item.get("vllm_model_pool_entry") is not None:
            vllm_entries[str(name)] = item.get("vllm_model_pool_entry")
    failed_primary = any(str(item.get("status") or item.get("eval_status") or "").lower() == "failed" for item in primary)
    aggregate: dict[str, Any] = {
        "ok": not failed_primary,
        "status": "failed" if failed_primary else "completed",
        "role": "judger",
        "thread_id": thread_id or current.get("task_id") or os.getenv("TASK_ID"),
        "bench_result": primary,
        "extra_bench_result": extra,
        "metrics": metrics,
        "benchmark_guards": guards,
        "benchmark_lineage": lineages,
        "benchmark_skills": skills,
        "vllm_model_pool_entries": vllm_entries,
        "vllm_keep_alive": bool(vllm_entries) or bool(registered_vllm) or bool(judger.get("vllm_keep_alive")),
        "model_resolution": {
            "codex": codex_provider.meta() if codex_provider else {"resolved": False},
        },
        "benchmark": benchmark if benchmark else None,
        "report_path": str((root / "final_report.json").resolve()),
    }
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "final_report.json", aggregate)
    _write_json(root / "status.json", {"status": aggregate["status"], "role": "judger", "report": str(root / "final_report.json"), "thread_id": aggregate["thread_id"]})
    if isinstance(current, dict):
        if benchmark:
            current.setdefault("judger", {})["benchmark"] = str(benchmark)
        current.setdefault("judger", {})["bench_result"] = primary
        current["judger"]["extra_bench_result"] = extra
        current["judger"]["sdk_worker"] = True
        current["judger"]["final_report_path"] = str((root / "final_report.json").resolve())
        current["judger"]["benchmark_guards"] = guards
        current["judger"]["benchmark_lineage"] = lineages
        current["judger"]["vllm_model_pool_entries"] = vllm_entries
        if registered_vllm:
            current["judger"]["vllm_model_pool_entries"].setdefault(
                str(judger.get("benchmark") or "current"), registered_vllm
            )
        current["judger"]["vllm_keep_alive"] = aggregate["vllm_keep_alive"]
        current["judger"]["model_resolution"] = aggregate["model_resolution"]
        _persist_configer_results(
            current,
            task_id=thread_id or current.get("task_id") or os.getenv("TASK_ID"),
        )
        aggregate["state"] = current
    return aggregate


def status(run_dir: str | Path = "outputs/judger") -> Dict[str, Any]:
    return worker_status(run_dir)


def load_events(task_id: str, output_dir: str = "./outputs") -> List[Dict[str, Any]]:
    try:
        from loopai.common.event_tool import dump_stream_events_json
    except Exception:
        return []
    return dump_stream_events_json(name="judger", context_id=task_id, log_file_path=output_dir)


__all__ = ["run", "status", "load_events"]
