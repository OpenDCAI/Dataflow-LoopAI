from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loopai.common.exception import ErrorCode, emit_error, emit_success

# Event persistence pulls the optional DB/Tortoise stack.  Keep it lazy so the
# SDK benchmark worker can be imported and used in a minimal worker image.
try:
    from loopai.common.event_tool import StreamEvent, get_event_writer, load_stream_events
except Exception:  # pragma: no cover - exercised in dependency-light workers
    StreamEvent = None  # type: ignore
    get_event_writer = None  # type: ignore
    load_stream_events = None  # type: ignore


def _sdk_worker_requested(
    state: Optional[Dict[str, Any]],
    *,
    benchmark: Optional[str],
    lake: Optional[str | Path],
    run_dir: Optional[str | Path],
    judger_report: Any,
    sdk_worker: bool,
) -> bool:
    if sdk_worker or benchmark or lake or run_dir or judger_report is not None:
        return True
    if not isinstance(state, dict):
        return False
    analyzer = state.get("analyzer")
    judger = state.get("judger")
    return (
        isinstance(analyzer, dict)
        and bool(
            analyzer.get("benchmark")
            or analyzer.get("lake")
            or analyzer.get("sdk_worker")
            or analyzer.get("judger_report")
        )
    ) or (
        isinstance(judger, dict)
        and bool(judger.get("sdk_worker"))
    )


def _run_benchmark_sdk_worker(
    state: Optional[Dict[str, Any]],
    *,
    benchmark: Optional[str],
    run_dir: Optional[str | Path],
    judger_report: Any,
    baseline_result_path: Optional[str],
    lake: Optional[str | Path],
    dataset: Optional[str],
    model: Optional[str],
    thread_id: Optional[str],
    resume: bool,
    timeout: int,
    benchmark_paths: Any = None,
    snapshot_id: Optional[str] = None,
) -> Dict[str, Any]:
    from loopai.skills.benchmarks import get_benchmark, resolve_benchmark_from_lake
    from loopai.skills.benchmarks.codex_worker import run_worker

    current = state if isinstance(state, dict) else {}
    if not current and os.getenv("DB_PATH") and (thread_id or os.getenv("TASK_ID")):
        try:
            from loopai.skills.Analyzer.state_bridge import load_analyzer_state_from_configer
            current = load_analyzer_state_from_configer(task_id=thread_id or os.getenv("TASK_ID"))
        except Exception:
            current = {}
    analyzer = current.get("analyzer") if isinstance(current.get("analyzer"), dict) else {}
    judger = current.get("judger") if isinstance(current.get("judger"), dict) else {}
    selected = benchmark or analyzer.get("benchmark") or judger.get("benchmark") or current.get("benchmark")
    if not selected and judger.get("sdk_worker"):
        # A Judger SDK state can be handed directly to Analyzer.  For a
        # single-benchmark run infer the name from the structured result;
        # multi-benchmark aggregates still require an explicit benchmark.
        candidates = judger.get("bench_result") or []
        if isinstance(candidates, list) and len(candidates) == 1 and isinstance(candidates[0], dict):
            selected = candidates[0].get("bench_name") or candidates[0].get("benchmark")
    if not selected:
        # Legacy Analyzer state remains on the deterministic pipeline unless
        # the caller explicitly requests SDK mode.
        return {
            "ok": False,
            "status": "failed",
            "error_code": "BENCHMARK_REQUIRED",
            "message": "benchmark is required for Analyzer SDK worker",
        }
    try:
        plugin = get_benchmark(str(selected), paths=benchmark_paths)
    except KeyError as exc:
        return {"ok": False, "status": "failed", "error_code": "UNKNOWN_BENCHMARK", "message": str(exc)}
    selected = plugin.name

    report = judger_report
    if report is None:
        report = (
            analyzer.get("judger_report")
            or analyzer.get("eval_result_path")
            or judger.get("report")
            or judger.get("final_report_path")
        )
    if report is None and isinstance(judger, dict) and (
        isinstance(judger.get("bench_result"), list) or isinstance(judger.get("extra_bench_result"), list)
    ):
        # Keep the in-memory aggregate available when Configer has projected
        # only the result lists instead of the report path.
        report = {
            "bench_result": judger.get("bench_result") or [],
            "extra_bench_result": judger.get("extra_bench_result") or [],
            "metrics": judger.get("metrics") or {},
        }
    if isinstance(report, (str, Path)):
        path = Path(report).expanduser()
        if path.is_file():
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                report = str(path)
    # Judger writes an aggregate report when several benchmarks are configured.
    # Analyzer operates on one selected benchmark, so pass the matching item
    # to the worker while retaining the aggregate for cross-benchmark context.
    aggregate_report = report if isinstance(report, dict) else None
    report_for_worker = report
    if isinstance(aggregate_report, dict):
        aggregate_items = []
        report_variants = [aggregate_report]
        nested_data = aggregate_report.get("data")
        if isinstance(nested_data, dict):
            report_variants.append(nested_data)
        for variant in report_variants:
            for key in ("bench_result", "extra_bench_result"):
                value = variant.get(key)
                if isinstance(value, list):
                    aggregate_items.extend(item for item in value if isinstance(item, dict))
        if aggregate_items:
            selected_key = str(selected).strip().lower()
            aliases = {selected_key}
            aliases.update(str(alias).strip().lower() for alias in getattr(plugin, "aliases", []) or [])
            for item in aggregate_items:
                item_names = {
                    str(item.get(key)).strip().lower()
                    for key in ("benchmark", "bench_name", "name")
                    if item.get(key)
                }
                if item_names & aliases:
                    report_for_worker = item
                    break
    mount = None
    lake_location = lake or analyzer.get("lake") or analyzer.get("warehouse")
    if lake_location:
        try:
            mount = resolve_benchmark_from_lake(
                lake_location, str(selected), dataset_name=dataset or analyzer.get("dataset"),
                snapshot_id=snapshot_id or analyzer.get("snapshot_id"),
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "failed",
                "error_code": "BENCHMARK_DATASET_NOT_FOUND",
                "message": str(exc),
            }
    worker_root = run_dir or analyzer.get("runtime_output_dir") or analyzer.get("output_dir") or "outputs/analyzer"
    try:
        from loopai.schema.model_pool import StarterModelPool, load_starter_system_config_sync
        pool = StarterModelPool(load_starter_system_config_sync(prefer_db=True) or {})
        codex_provider = pool.resolve_role_provider("codex")
        mid_provider = pool.resolve_role_provider("medium")
    except Exception:
        codex_provider = None
        mid_provider = None
    result = run_worker(
        role="analyzer",
        benchmark=str(selected),
        run_dir=worker_root,
        inputs={
            "judger_report": report_for_worker,
            "judger_report_aggregate": aggregate_report if report_for_worker is not aggregate_report else None,
            "baseline_result_path": baseline_result_path,
            "dataset": dataset or analyzer.get("dataset"),
            "model_roles": {
                "codex": codex_provider.meta() if codex_provider else {},
                "medium": mid_provider.meta() if mid_provider else {},
            },
        },
        plugin=plugin,
        mount=mount,
        # SDK orchestration is always Codex; the medium provider above is
        # reserved for concrete Analyzer metric/label calls.
        model=codex_provider.name if codex_provider else None,
        thread_id=thread_id or current.get("task_id") or analyzer.get("thread_id"),
        resume=resume,
        timeout=timeout,
    )
    if aggregate_report is not None and report_for_worker is not aggregate_report:
        # Keep the full Judger aggregate alongside the selected benchmark
        # input so downstream consumers can audit sibling benchmark results.
        result.setdefault("judger_report", aggregate_report)
        try:
            report_path = Path(worker_root).expanduser() / "final_report.json"
            report_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass
    if isinstance(current, dict):
        current.setdefault("analyzer", {})["benchmark"] = str(selected)
        current["analyzer"]["sdk_worker"] = True
        current["analyzer"]["codex_model"] = codex_provider.model if codex_provider else ""
        current["analyzer"]["mid_model"] = mid_provider.model if mid_provider else ""
        current["analyzer"]["model_resolution"] = {
            "codex": codex_provider.meta() if codex_provider else {"resolved": False},
            "medium": mid_provider.meta() if mid_provider else {"resolved": False},
        }
        current["analyzer"]["runtime_output_dir"] = str(Path(worker_root).expanduser().resolve())
        current["analyzer"]["final_report_path"] = str(Path(worker_root).expanduser().resolve() / "final_report.json")
        if lake_location:
            current["analyzer"]["lake"] = str(lake_location)
        if dataset or analyzer.get("dataset"):
            current["analyzer"]["dataset"] = dataset or analyzer.get("dataset")
        if snapshot_id or analyzer.get("snapshot_id"):
            current["analyzer"]["snapshot_id"] = snapshot_id or analyzer.get("snapshot_id")
        if isinstance(judger_report, (str, Path)):
            current["analyzer"]["judger_report"] = str(Path(judger_report).expanduser())
        current["analyzer"]["benchmark_skill"] = {
            "name": plugin.name,
            "version": plugin.version,
            "task_type": plugin.task_type,
            "analysis_dimensions": list(plugin.analysis_dimensions),
            "eval_capabilities": list(plugin.eval_capabilities),
            "guard_name": plugin.guard_name,
            "manifest": dict(plugin.manifest or {}),
        }
        if result.get("benchmark_guard") is not None:
            current["analyzer"]["benchmark_guard"] = result.get("benchmark_guard") or {}
        if result.get("lineage") is not None:
            current["analyzer"]["benchmark_lineage"] = result.get("lineage") or {}
        # Project the schema-compatible fields into Configer when this run is
        # attached to a task.  Standalone SDK runs simply keep the returned
        # state; persistence is deliberately best effort.
        if os.getenv("DB_PATH") and (thread_id or current.get("task_id") or os.getenv("TASK_ID")):
            try:
                from loopai.skills.Analyzer.state_bridge import update_analyzer_state_via_configer
                update_analyzer_state_via_configer(
                    current,
                    task_id=thread_id or current.get("task_id") or os.getenv("TASK_ID"),
                )
            except Exception:
                pass
        result.setdefault("state", current)
    # Keep the historical ``data.report`` convenience field without creating
    # a self-referential dictionary (which breaks CLI JSON serialization).
    if "data" not in result:
        result["data"] = {"report": dict(result)}
    return result


def run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    resume: bool = False,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    analyze_batch_size: Optional[int] = None,
    critique_samples_per_tag: Optional[Any] = None,
    benchmark: Optional[str] = None,
    run_dir: Optional[str | Path] = None,
    judger_report: Any = None,
    lake: Optional[str | Path] = None,
    dataset: Optional[str] = None,
    model: Optional[str] = None,
    sdk_worker: bool = False,
    timeout: int = 900,
    benchmark_paths: Any = None,
    snapshot_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run Analyzer skill (Codex / subprocess entry point).

    ``DB_PATH`` 和 ``TASK_ID`` 从环境变量自动获取，未设置时 emit_error
    退出。事件和产物按 ``output_dir/task_id/analyzer/version_id`` 隔离。
    成功时输出 JSON 到 stdout + sys.exit(0)。不要在进程内直接调用；
    直接调用 ``loopai.skills.Analyzer.runner.run_analyzer_standalone``。
    """
    if _sdk_worker_requested(
        state,
        benchmark=benchmark,
        lake=lake,
        run_dir=run_dir,
        judger_report=judger_report,
        sdk_worker=sdk_worker,
    ):
        return _run_benchmark_sdk_worker(
            state,
            benchmark=benchmark,
            run_dir=run_dir,
            judger_report=judger_report,
            baseline_result_path=baseline_result_path,
            lake=lake,
            dataset=dataset,
            model=model,
            thread_id=thread_id,
            resume=resume,
            timeout=timeout,
            benchmark_paths=benchmark_paths or kwargs.get("benchmark_paths"),
            snapshot_id=snapshot_id,
        )

    if not os.getenv("DB_PATH"):
        emit_error(
            ValueError("DB_PATH env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="DB_PATH environment variable is not set.",
        )

    task_id = thread_id or os.getenv("TASK_ID")
    if not task_id:
        emit_error(
            ValueError("TASK_ID env is required"),
            code=ErrorCode.CONFIG_ERROR,
            message="TASK_ID environment variable is not set.",
        )

    if get_event_writer is None or StreamEvent is None:
        # The deterministic pipeline needs the optional event/DB stack.  Do
        # not let a lazy import turn into a confusing ``NoneType`` call;
        # callers receive the same structured error contract as other runtime
        # configuration failures.
        return emit_error(
            RuntimeError("Analyzer event persistence dependencies are unavailable"),
            code=ErrorCode.DEPENDENCY_ERROR,
            recoverable=False,
            message="Analyzer deterministic pipeline dependencies are unavailable; use SDK benchmark mode or install the event stack.",
            exit_process=False,
        )

    from .runtime_config import resolve_analyzer_runtime_config
    from .runtime_config import get_version_checkpoint_path
    from .runtime_config import find_latest_version_checkpoint
    from .runtime_config import cleanup_old_analyzer_checkpoints
    from .pipeline_runner import load_analyzer_checkpoint, _is_finished
    from .state_bridge import load_analyzer_state_from_configer
    from loopai.skills.Analyzer.runner import (
        find_latest_incomplete_version_checkpoint,
        run_analyzer_standalone,
    )

    try:
        if state is None:
            state = load_analyzer_state_from_configer(task_id=task_id)
        runtime = resolve_analyzer_runtime_config(
            state,
            thread_id=task_id,
            baseline_result_path=baseline_result_path,
            analyze_batch_size=analyze_batch_size,
            **kwargs,
        )
        explicit_version = (
            kwargs.get("version_id")
            or kwargs.get("run_id")
            or os.getenv("ANALYZER_VERSION_ID")
            or os.getenv("VERSION_ID")
            or (state.get("version_id") if isinstance(state, dict) else None)
            or ((state.get("analyzer") or {}).get("version_id") if isinstance(state, dict) else None)
        )
        if resume and not explicit_version:
            latest_checkpoint = find_latest_version_checkpoint(
                runtime["output_dir"], runtime["thread_id"]
            )
            if latest_checkpoint:
                runtime["version_id"], runtime["checkpoint_path"] = latest_checkpoint
            else:
                runtime["version_id"] = ""
        elif not explicit_version:
            if not kwargs.get("new_version") and not kwargs.get("force_new_version"):
                latest_checkpoint = find_latest_incomplete_version_checkpoint(
                    runtime["output_dir"], runtime["thread_id"]
                )
                if latest_checkpoint:
                    runtime["version_id"], runtime["checkpoint_path"] = latest_checkpoint
                    resume = True
                else:
                    runtime["version_id"] = ""
            else:
                runtime["version_id"] = ""
        if runtime.get("version_id"):
            runtime["checkpoint_path"] = get_version_checkpoint_path(
                runtime["output_dir"], runtime["thread_id"], runtime["version_id"]
            )
            if not resume and os.path.exists(runtime["checkpoint_path"]):
                candidate_state = load_analyzer_checkpoint(
                    runtime["thread_id"],
                    runtime["checkpoint_path"],
                    version_id=runtime["version_id"],
                )
                if candidate_state and not _is_finished(candidate_state):
                    resume = True
                elif candidate_state and _is_finished(candidate_state):
                    runtime["version_id"] = ""
        writer_version_id = runtime["version_id"]
        if writer_version_id in ("", "default") and not explicit_version:
            writer_version_id = None
        writer = get_event_writer(
            name="analyzer",
            context_id=runtime["thread_id"],
            log_file_path=runtime["output_dir"],
            version_id=writer_version_id,
        )
        resume_progress = 0.0
        if resume and runtime.get("version_id") and os.path.exists(runtime["checkpoint_path"]):
            checkpoint_state = load_analyzer_checkpoint(
                runtime["thread_id"],
                runtime["checkpoint_path"],
                version_id=runtime["version_id"],
            )
            resume_progress = float(
                (checkpoint_state.get("_analyzer_checkpoint") or {}).get(
                    "node_progress", 0.0
                )
                or 0.0
            )
        writer.set_running({
            "current": "analyzer.initializing",
            "progress": resume_progress,
            "message": "Analyzer resuming." if resume and resume_progress else "Analyzer initializing.",
        })
        runtime["version_id"] = str(writer.version_id)
        cleanup_old_analyzer_checkpoints(
            runtime["output_dir"],
            runtime["thread_id"],
            runtime["version_id"],
        )
        runtime["checkpoint_path"] = get_version_checkpoint_path(
            runtime["output_dir"],
            runtime["thread_id"],
            runtime["version_id"],
        )
        state["version_id"] = runtime["version_id"]
        state.setdefault("analyzer", {})["version_id"] = runtime["version_id"]
        state["analyzer"]["checkpoint_path"] = runtime["checkpoint_path"]
        state["analyzer"]["runtime_output_dir"] = str(
            Path(runtime["output_dir"])
            / runtime["thread_id"]
            / "analyzer"
            / runtime["version_id"]
        )
        runner_kwargs = dict(kwargs)
        for control_key in (
            "version_id",
            "run_id",
            "new_version",
            "force_new_version",
            "resume",
            "from_node",
            "checkpoint_path",
            "baseline_result_path",
            "analyze_batch_size",
            "writer",
            "emit_status",
        ):
            runner_kwargs.pop(control_key, None)
        runner_kwargs["version_id"] = runtime["version_id"]
        final_state = run_analyzer_standalone(
            state=state,
            thread_id=runtime["thread_id"],
            resume=resume,
            from_node=from_node,
            baseline_result_path=baseline_result_path,
            analyze_batch_size=analyze_batch_size,
            critique_samples_per_tag=critique_samples_per_tag,
            writer=writer,
            emit_status=False,
            **runner_kwargs,
        )
    except (ValueError, TypeError) as exc:
        if locals().get("writer") is not None:
            writer.set_failed(StreamEvent(
                current="analyzer.failed",
                progress=1.0,
                message="Analyzer input contract validation failed.",
                data={"error": str(exc)},
            ))
        emit_error(
            exc,
            code=ErrorCode.INVALID_INPUT,
            recoverable=False,
            message="Analyzer input contract validation failed.",
            stream_writer=locals().get("writer"),
        )
    except RuntimeError as exc:
        if locals().get("writer") is not None:
            writer.set_failed(StreamEvent(
                current="analyzer.failed",
                progress=1.0,
                message="Analyzer runtime configuration is incomplete.",
                data={"error": str(exc)},
            ))
        emit_error(
            exc,
            code=ErrorCode.CONFIG_ERROR,
            recoverable=True,
            message="Analyzer runtime configuration is incomplete.",
            stream_writer=locals().get("writer"),
        )
    except Exception as exc:
        if locals().get("writer") is not None:
            writer.set_failed(StreamEvent(
                current="analyzer.failed",
                progress=1.0,
                message="Analyzer crashed with an unhandled exception.",
                data={"error": str(exc)},
            ))
        emit_error(
            exc,
            code=ErrorCode.UNHANDLED_EXCEPTION,
            recoverable=True,
            message="Analyzer crashed with an unhandled exception.",
            stream_writer=locals().get("writer"),
        )

    analyzer = final_state.get("analyzer", {}) if isinstance(final_state, dict) else {}
    # common.emit_success passes a response envelope without ``current``.
    # Emit the terminal StreamEvent explicitly so the shared writer records it.
    writer.set_completed(StreamEvent(
        current="analyzer.completed",
        progress=1.0,
        message="Analyzer pipeline completed.",
        data={"task_id": task_id},
    ))
    emit_success(
        data={
            "task_id": final_state.get("task_id") if isinstance(final_state, dict) else task_id,
            "version_id": final_state.get("version_id") if isinstance(final_state, dict) else None,
            "current": final_state.get("current") if isinstance(final_state, dict) else None,
            "last_completed": final_state.get("last_completed") if isinstance(final_state, dict) else None,
            "output_dir": analyzer.get("runtime_output_dir") or analyzer.get("output_dir"),
            "historical_comparison": analyzer.get("historical_comparison", {}),
            "state": final_state,
        },
        stream_writer=writer,
        message="Analyzer pipeline completed.",
    )


def load_events(
    task_id: str,
    output_dir: str = "./outputs",
    version_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """读取指定任务的 analyzer 事件列表。

    事件在流水线执行期间实时写入 pickle 文件（``analyzer.pkl``），
    执行完成后可调用此函数获取完整事件列表，用于前端展示或日志分析。
    """
    if load_stream_events is None:
        return []
    return [event.json() for event in load_stream_events(
        name="analyzer",
        context_id=task_id,
        log_file_path=output_dir,
    ) if version_id is None or event.version_id == version_id]


def status(run_dir: str | Path = "outputs/analyzer") -> Dict[str, Any]:
    """Read the optional Codex worker status file without touching DB state."""
    from loopai.skills.benchmarks.codex_worker import worker_status
    return worker_status(run_dir)


def resume_run(
    state: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    from_node: Optional[str] = None,
    baseline_result_path: Optional[str] = None,
    analyze_batch_size: Optional[int] = None,
    critique_samples_per_tag: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Explicit continuation entry point; always resumes the latest checkpoint."""
    kwargs.pop("resume", None)
    return run(
        state=state,
        thread_id=thread_id,
        resume=True,
        from_node=from_node,
        baseline_result_path=baseline_result_path,
        analyze_batch_size=analyze_batch_size,
        critique_samples_per_tag=critique_samples_per_tag,
        **kwargs,
    )


__all__ = ["run", "resume_run", "load_events", "status"]
