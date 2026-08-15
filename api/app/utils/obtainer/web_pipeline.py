"""Read-only operational snapshot for the WebAgent -> DataMixer pipeline UI."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loopai.agents.Obtainer.datamixer.store import DataStore
from loopai.agents.Obtainer.datamixer.operators.streaming import PersistentPipelineQueue
from loopai.agents.Obtainer.datamixer.webagents.campaign import CampaignQueue

_LEVELS = (
    ("L1", "原始网页", "采集并保留原始 HTML/网页响应"),
    ("L2", "预处理网页", "正文提取、PT 清洗与领域分类"),
    ("L3", "初级 SFT", "基于 L2 的 grounded QA 与格式校验"),
)
_ACTIVE_CAMPAIGN_STATES = {"queued", "running", "paused"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _short(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _pid_alive(value: Any) -> bool:
    try:
        pid = int(value)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (TypeError, ValueError, OSError):
        return False
    return True


def _nested_run_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("run_id", "campaign_id"):
        if value.get(key):
            return str(value[key])
    for key in ("result", "data", "output"):
        found = _nested_run_id(value.get(key))
        if found:
            return found
    return ""


def _acquisition_phase(
    *,
    status: dict[str, Any],
    run_dir: Path,
    final_report: dict[str, Any],
    download: dict[str, Any],
    webagent_started: bool,
) -> tuple[str, str]:
    state = str(status.get("state") or "unknown").lower()
    if final_report:
        return "completed", "已生成最终采集报告"
    if state in {"failed", "interrupted"}:
        return state, str(status.get("error") or "采集 worker 已停止")
    if download.get("state") == "running":
        return "download", "正在下载并规范化候选数据集"
    if download.get("state") == "completed":
        if (run_dir / "manifest" / "ingest_results.json").exists():
            return "ingest", "正在写入或校验 DataMixer 数据湖"
        return "prepare_ingest", "下载完成，正在筛选、制卡并准备入湖"
    if webagent_started:
        return "dual_discovery", "Web 垂域采集与数据集搜索并行执行"
    return "planning", "正在规划垂域数据发现任务"


def _acquisition_snapshot(run_dir: str | Path, *, bound_to_lake: bool = True) -> dict[str, Any] | None:
    path = Path(run_dir).expanduser().resolve()
    status = _read_json(path / "status.json")
    if not status:
        return None
    thread = _read_json(path / "thread.json")
    final_report = _read_json(path / "final_report.json")
    download = _read_json(path / "downloads" / "download_progress.json")
    webagent_start = _read_json(path / "manifest" / "webagent_start.json")
    webagent_status = _read_json(path / "manifest" / "webagent_campaign_status.json")
    process_alive = _pid_alive(status.get("pid"))
    stored_state = str(status.get("state") or "unknown")
    active = process_alive and stored_state.lower() not in {"completed", "failed", "interrupted"}
    phase, phase_detail = _acquisition_phase(
        status=status,
        run_dir=path,
        final_report=final_report,
        download=download,
        webagent_started=bool(webagent_start),
    )
    return {
        "run_dir": str(path),
        "task_id": str(thread.get("task_id") or status.get("task_id") or ""),
        "warehouse": str(thread.get("warehouse") or status.get("warehouse") or ""),
        "state": "running" if active else stored_state,
        "stored_state": stored_state,
        "active": active,
        "process_alive": process_alive,
        "pid": status.get("pid"),
        "updated_at": status.get("updated_at"),
        "phase": phase,
        "phase_detail": phase_detail,
        "bound_to_lake": bound_to_lake,
        "objective": _short(thread.get("objective"), 180),
        "download": {
            "state": download.get("state") or "",
            "processed": _as_number(download.get("processed")),
            "total": _as_number(download.get("total")),
            "completed": _as_number(download.get("completed")),
            "failed": _as_number(download.get("failed")),
            "percent": _as_number(download.get("percent")),
        },
        "webagent": {
            "run_id": _nested_run_id(webagent_status) or _nested_run_id(webagent_start),
            "start_recorded": bool(webagent_start),
            "status_recorded": bool(webagent_status),
        },
        "final_report": str(path / "final_report.json") if final_report else "",
    }


def _run_belongs_to_task(acquisition: dict[str, Any] | None, task_id: str) -> bool:
    if not acquisition or not task_id:
        return False
    recorded = str(acquisition.get("task_id") or "").strip()
    if recorded:
        return recorded == task_id
    short_id = task_id[:8]
    run_path = Path(str(acquisition.get("run_dir") or ""))
    return bool(short_id and any(short_id in part for part in run_path.parts))


def _dataflow_status_path(root: Path, project_root: str | Path | None = None) -> Path:
    """Locate the most recent DataFlowAgent status file.

    Agents launched through ``dm dataflow agent-run`` write their status under
    their ``--work-dir`` (e.g. ``<project>/outputs/<name>/status.json``), while
    older launches wrote to ``<warehouse>/runs/dataflow_agent/status.json``.
    Without this discovery the dashboard kept showing a stale legacy run while
    the real run was in the work dir.
    """
    legacy = root / "runs" / "dataflow_agent" / "status.json"
    candidates: list[Path] = [legacy]
    bases: list[Path] = []
    if project_root:
        bases.append(Path(project_root).expanduser().resolve())
    # Infer the project root when the warehouse lives at
    # <project>/.datamixer/<lake>/warehouse.
    if len(root.parts) >= 3 and root.parents[1].name == ".datamixer":
        bases.append(root.parents[2])
    for base in bases:
        candidates.extend(base.glob("outputs/*/status.json"))
        # ``dm dataflow agent-run --work-dir dataflow_work`` writes its status
        # under the project root; without this the dashboard keeps showing idle.
        candidates.append(base / "dataflow_work" / "status.json")
        candidates.append(base / "runs" / "dataflow_agent" / "status.json")
    best: Path | None = None
    best_mtime = -1.0
    for candidate in candidates:
        if not candidate.is_file():
            continue
        status = _read_json(candidate)
        # DataFlowAgent statuses carry a phase + state; other workers
        # (acquisition / sft export) must not be picked up here.
        if not (isinstance(status, dict) and "phase" in status and "state" in status):
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best = candidate
    return best if best is not None else legacy


def _dataflow_agent_snapshot(root: Path, project_root: str | Path | None = None) -> dict[str, Any]:
    status_path = _dataflow_status_path(root, project_root)
    status = _read_json(status_path)
    if not status:
        return {
            "state": "idle",
            "phase": "waiting",
            "input_rows": 0,
            "selected_rows": 0,
            "output_rows": 0,
            "dropped_rows": 0,
            "failed_rows": 0,
            "applied_rows": 0,
            "feedback": "等待 DataFlowAgent 任务",
            "error": "",
        }
    return {
        "state": status.get("state") or "unknown",
        "phase": status.get("phase") or "unknown",
        "target": _short(status.get("target"), 180),
        "dataset": status.get("dataset") or "",
        "current_operator": status.get("current_operator") or "",
        "input_rows": _as_number(status.get("input_rows")),
        "full_input_rows": _as_number(status.get("full_input_rows")),
        "full_output_rows": _as_number(status.get("full_output_rows")),
        "selected_rows": _as_number(status.get("selected_rows", status.get("input_rows"))),
        "output_rows": _as_number(status.get("output_rows")),
        "dropped_rows": _as_number(status.get("dropped_rows")),
        "failed_rows": _as_number(status.get("failed_rows")),
        "applied_rows": _as_number(status.get("applied_rows")),
        "attempt": _as_number(status.get("attempt")),
        "feedback": _short(status.get("feedback"), 220),
        "error": _short(status.get("error"), 300),
        "updated_at": status.get("updated_at"),
        "status_path": str(status_path),
    }


def _discover_active_acquisitions(project_root: str | Path | None, *, limit: int = 8) -> list[dict[str, Any]]:
    if not project_root:
        return []
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        return []
    ignored = {".git", "node_modules", ".venv", "__pycache__", "dist"}
    snapshots: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            depth = 0
        if depth >= 4:
            dirs[:] = []
        else:
            dirs[:] = [item for item in dirs if item not in ignored]
        if "status.json" not in files:
            continue
        snapshot = _acquisition_snapshot(current_path, bound_to_lake=False)
        if snapshot and snapshot["active"]:
            snapshots.append(snapshot)
    snapshots.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
    return snapshots[:limit]


def _processing_queue_snapshot(root: Path, run_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Read live per-stage counts from the persistent streaming queues."""
    queue_path = root / "pipeline_queue.sqlite"
    live: dict[str, Any] = {}
    if queue_path.exists():
        queue = PersistentPipelineQueue(queue_path)
        try:
            live = queue.summary(run_id)
        except Exception as exc:  # The dashboard must remain read-only and available.
            live = {"status": "unknown", "error": _short(exc, 200), "stages": []}
        finally:
            queue.close()

    source_stages = live.get("stages") if isinstance(live.get("stages"), list) else []
    if not source_stages:
        source_stages = report.get("stages") if isinstance(report.get("stages"), list) else []
    queues = []
    for index, raw in enumerate(source_stages):
        if not isinstance(raw, dict):
            continue
        pending = _as_number(raw.get("queued", raw.get("pending")))
        running = _as_number(raw.get("running"))
        succeeded = _as_number(raw.get("succeeded"))
        dropped = _as_number(raw.get("dropped"))
        failed = _as_number(raw.get("failed"))
        queues.append({
            "key": f"pipeline-{raw.get('stage_index', index)}",
            "name": raw.get("name") or f"stage-{index + 1}",
            "status": raw.get("status") or raw.get("state") or "waiting",
            "pending": pending,
            "running": running,
            "succeeded": succeeded,
            "dropped": dropped,
            "failed": failed,
            "processed": succeeded + dropped + failed,
            "rows_in": _as_number(raw.get("rows_in")) or pending + running + succeeded + dropped + failed,
            "rows_out": _as_number(raw.get("rows_out")) or succeeded,
            "output_dataset": raw.get("output_dataset") or "",
            "error": _short(raw.get("error"), 200),
            "updated_at": raw.get("updated_at"),
        })
    return {
        "status": live.get("status") or report.get("status") or "waiting",
        "source_done": bool(live.get("source_done", report.get("source_done"))),
        "error": _short(live.get("error") or report.get("error"), 300),
        "selected": _as_number(live.get("selected", report.get("selected"))),
        "queues": queues,
    }


def _pipeline_status_snapshot(campaign: dict[str, Any] | None) -> dict[str, Any]:
    campaign = campaign or {}
    queue = campaign.get("queue") or {}
    pipeline = campaign.get("pipeline") or {}
    processing = pipeline.get("processing") or {}
    processing_queues = processing.get("queues") or []
    feedback = []
    if campaign:
        feedback.append({
            "source": "webagent",
            "state": campaign.get("status") or "unknown",
            "message": (
                f"{_as_number(queue.get('succeeded'))} completed, "
                f"{_as_number(queue.get('running'))} running, "
                f"{_as_number(queue.get('pending'))} pending"
            ),
        })
    for item in processing_queues:
        if not isinstance(item, dict):
            continue
        message = item.get("error") or (
            f"{_as_number(item.get('processed'))} processed, "
            f"{_as_number(item.get('pending'))} pending"
        )
        feedback.append({
            "source": item.get("name") or "pipeline",
            "state": item.get("status") or "waiting",
            "message": _short(message, 180),
        })
    status = processing.get("status") or pipeline.get("status") or campaign.get("status") or "idle"
    if processing.get("error") or pipeline.get("error"):
        status = "failed"
    elif (
        str(campaign.get("status") or "").lower() in _ACTIVE_CAMPAIGN_STATES
        or any(_as_number(item.get("pending")) or _as_number(item.get("running")) for item in processing_queues)
    ):
        status = "running"
    return {
        "status": status,
        "continuous": bool(campaign),
        "source_done": bool(processing.get("source_done")),
        "selected": _as_number(processing.get("selected")),
        "queues": processing_queues,
        "feedback": feedback[:8],
        "error": processing.get("error") or pipeline.get("error") or "",
    }


def _campaign_snapshot(root: Path, run_id: str | None = None) -> dict[str, Any] | None:
    queue_path = root / "webagent_queue.sqlite"
    if not queue_path.exists():
        return None
    queue = CampaignQueue(queue_path)
    try:
        requested_run_id = str(run_id or "").strip()
        if requested_run_id:
            campaign = queue.get_campaign(requested_run_id)
            if campaign is None:
                return None
        else:
            campaigns = queue.list_campaigns(limit=12)
            if not campaigns:
                return None
            campaign = next(
                (item for item in campaigns if item.get("status") in _ACTIVE_CAMPAIGN_STATES),
                campaigns[0],
            )
        selected_run_id = str(campaign["run_id"])
        queue_summary = queue.summary(selected_run_id)
        running_tasks = queue.list_tasks(selected_run_id, status="running", limit=32)
        recent_tasks = queue.recent_tasks(selected_run_id, limit=8)
    finally:
        queue.close()

    workers = [
        {
            "worker_id": task.get("worker_id") or "unassigned",
            "task_id": task.get("task_id"),
            "query": _short(task.get("query"), 110),
            "goal": _short(task.get("goal"), 90),
            "started_at": task.get("started_at"),
            "attempts": _as_number(task.get("attempts")),
        }
        for task in running_tasks
    ]
    tasks = [
        {
            "task_id": task.get("task_id"),
            "status": task.get("status") or "unknown",
            "worker_id": task.get("worker_id") or "",
            "query": _short(task.get("query"), 110),
            "goal": _short(task.get("goal"), 90),
            "attempts": _as_number(task.get("attempts")),
            "error": _short(task.get("error"), 180),
            "updated_at": task.get("finished_at") or task.get("started_at") or task.get("created_at"),
        }
        for task in recent_tasks
    ]
    config = campaign.get("config") or {}
    configured_workers = _as_number(config.get("workers")) or 4
    pipeline_report = campaign.get("pipeline") or {}
    processing = _processing_queue_snapshot(root, selected_run_id, pipeline_report)
    if processing["queues"] or pipeline_report:
        pipeline_report = {
            **pipeline_report,
            "processing": processing,
            "status": processing.get("status") or pipeline_report.get("status") or "waiting",
            "error": processing.get("error") or pipeline_report.get("error") or "",
        }
    return {
        "run_id": selected_run_id,
        "status": campaign.get("status") or "unknown",
        "root_query": _short(campaign.get("root_query"), 220),
        "dataset": campaign.get("dataset") or "",
        "webagent": campaign.get("webagent") or "webcrawler_dm",
        "updated_at": campaign.get("updated_at"),
        "created_at": campaign.get("created_at"),
        "error": _short(campaign.get("error"), 300),
        "configured_workers": configured_workers,
        "config": {
            "l2_dataset": config.get("l2_dataset") or "",
            "l3_dataset": config.get("l3_dataset") or "",
            "auto_pipeline": bool(config.get("auto_pipeline")),
            "pipeline_batch_size": _as_number(config.get("pipeline_batch_size")),
        },
        "queue": queue_summary,
        "workers": workers,
        "recent_tasks": tasks,
        "pipeline": pipeline_report or None,
    }




def _lake_live_summary(root: Path) -> dict[str, Any]:
    """Live catalog counts - the same source the obtainer agents read.

    The dashboard's monitor_state summary is a cache and can be stale; these
    headline numbers (datasets / records) always reflect the current catalog so
    the UI and the agents never disagree.
    """
    store = DataStore.open(root)
    try:
        return {
            "datasets": len(store.catalog.list_datasets()),
            "records": store.catalog.count(),
        }
    finally:
        store.close()

def _layer_snapshot(root: Path, campaign: dict[str, Any] | None) -> list[dict[str, Any]]:
    store = DataStore.open(root)
    try:
        datasets = store.catalog.list_datasets()
        domain_rows = {
            level: store.catalog.distribution("domain", where=f"quality_level = '{level}'")[:4]
            for level, _, _ in _LEVELS
        }
        counts = {
            level: store.catalog.count(where=f"quality_level = '{level}'")
            for level, _, _ in _LEVELS
        }
    finally:
        store.close()

    campaign_levels = ((campaign or {}).get("pipeline") or {}).get("levels") or {}
    campaign_config = (campaign or {}).get("config") or {}
    output = []
    for level, label, description in _LEVELS:
        pipeline_level = campaign_levels.get(level) or {}
        level_datasets = [
            item["name"]
            for item in datasets
            if item.get("name") == pipeline_level.get("dataset")
        ]
        if pipeline_level.get("dataset") and pipeline_level["dataset"] not in level_datasets:
            level_datasets.append(pipeline_level["dataset"])
        configured_dataset = (
            (campaign or {}).get("dataset")
            if level == "L1"
            else campaign_config.get(f"l{level[-1]}_dataset")
        )
        if configured_dataset and configured_dataset not in level_datasets:
            level_datasets.append(str(configured_dataset))
        count = _as_number(pipeline_level.get("count")) if pipeline_level else counts[level]
        output.append(
            {
                "level": level,
                "label": label,
                "description": description,
                "count": count,
                "datasets": level_datasets[:3],
                "domains": [
                    {"name": row.get("value") or "unknown", "count": _as_number(row.get("n"))}
                    for row in domain_rows[level]
                ],
            }
        )
    return output


def _stage_snapshot(campaign: dict[str, Any] | None, layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    campaign = campaign or {}
    queue = campaign.get("queue") or {}
    status = str(campaign.get("status") or "").lower()
    report = campaign.get("pipeline") or {}
    result = report.get("result") or {}
    stage_rows = {
        str(row.get("name")): row
        for row in [*(result.get("stages") or []), *(report.get("stages") or [])]
    }
    l2_count = next((item["count"] for item in layers if item["level"] == "L2"), 0)
    l3_count = next((item["count"] for item in layers if item["level"] == "L3"), 0)

    def stage(name: str, title: str, detail: str, *, active: bool = False, complete: bool = False,
              warning: bool = False, progress: float | None = None) -> dict[str, Any]:
        if warning:
            state = "warning"
        elif active:
            state = "running"
        elif complete:
            state = "completed"
        else:
            state = "waiting"
        return {"name": name, "title": title, "detail": detail, "state": state, "progress": progress}

    is_collecting = status in {"queued", "running", "paused"} and (
        _as_number(queue.get("pending")) or _as_number(queue.get("running"))
    )
    total = _as_number(queue.get("total"))
    finished = _as_number(queue.get("succeeded")) + _as_number(queue.get("failed"))
    collection_progress = finished / total if total else None
    has_pipeline = bool(report)
    pipeline_in_progress = str(report.get("status") or "").lower() == "running"
    pipeline_finished = has_pipeline and not pipeline_in_progress
    extractor_row = stage_rows.get("webpage_to_pt")
    classify_row = stage_rows.get("domain_classify")
    qa_row = stage_rows.get("pt_to_sft_qa")
    validate_row = stage_rows.get("sft_validate")
    pipeline_failed = bool(report.get("error")) or str(report.get("status") or "").lower() == "failed"
    classifier_missing = has_pipeline and not classify_row

    return [
        stage(
            "collect", "网页采集", f"{_as_number(queue.get('succeeded'))} 成功 / {total} 子任务",
            active=is_collecting, complete=bool(total and not is_collecting),
            warning=bool(_as_number(queue.get("failed"))), progress=collection_progress,
        ),
        stage(
            "extract", "正文提取", "MinerU/HTML 正文提取与 PT 清洗",
            active=bool(extractor_row and extractor_row.get("state") == "running") or (
                pipeline_in_progress and report.get("current_stage") == "webpage_to_pt"
            ),
            complete=bool(extractor_row and extractor_row.get("state") == "completed") or bool(
                pipeline_finished and l2_count
            ),
            warning=pipeline_failed,
        ),
        stage(
            "classify", "领域分类", "LLM 领域分类，写入 domain 与 domain_labels",
            active=bool(classify_row and classify_row.get("state") == "running") or (
                pipeline_in_progress and report.get("current_stage") == "domain_classify"
            ),
            complete=bool(classify_row and classify_row.get("state") == "completed") or bool(
                pipeline_finished and l2_count and not classifier_missing
            ),
            warning=pipeline_failed or classifier_missing,
        ),
        stage(
            "qa", "SFT QA 生成", "基于 L2 正文生成 grounded QA",
            active=bool(qa_row and qa_row.get("state") == "running") or (
                pipeline_in_progress and report.get("current_stage") == "pt_to_sft_qa"
            ),
            complete=bool(qa_row and qa_row.get("state") == "completed") or bool(
                pipeline_finished and l3_count
            ),
            warning=pipeline_failed,
        ),
        stage(
            "validate", "SFT 校验", "校验 QA 格式、写入 L3 并保留 lineage",
            active=bool(validate_row and validate_row.get("state") == "running") or (
                pipeline_in_progress and report.get("current_stage") == "sft_validate"
            ),
            complete=bool(validate_row and validate_row.get("state") == "completed") or bool(
                pipeline_finished and l3_count
            ),
            warning=pipeline_failed,
        ),
    ]


def build_web_pipeline_overview(
    warehouse: str | Path,
    run_id: str | None = None,
    acquisition_run: str | None = None,
    lake_context: dict[str, Any] | None = None,
    project_root: str | Path | None = None,
    task_id: str | None = None,
    explicit_binding: bool = False,
) -> dict[str, Any]:
    """Build the dashboard payload without starting agents or rebuilding data."""
    root = Path(warehouse).expanduser().resolve()
    context = lake_context or {}
    discovered_acquisitions = _discover_active_acquisitions(project_root)
    acquisition = _acquisition_snapshot(acquisition_run) if acquisition_run else None
    requested_task_id = str(task_id or "").strip()
    if requested_task_id:
        matching_acquisitions = [
            item for item in discovered_acquisitions
            if _run_belongs_to_task(item, requested_task_id)
        ]
        if acquisition is not None and not _run_belongs_to_task(acquisition, requested_task_id):
            acquisition = None
        if acquisition is None and matching_acquisitions:
            acquisition = matching_acquisitions[0]
        # No acquisition is bound to this task (the lake and task ids were
        # unbound on purpose). Do NOT report the lake as uninitialized - the
        # lake itself may be loaded and healthy. task_id only filters which
        # acquisitions are surfaced below.
        discovered_acquisitions = matching_acquisitions
        acquisition_warehouse = str((acquisition or {}).get("warehouse") or "").strip()
        if acquisition_warehouse:
            candidate_root = Path(acquisition_warehouse).expanduser().resolve()
            if (candidate_root / "datamixer.toml").is_file():
                root = candidate_root
    if acquisition is not None:
        acquisition["bound_to_lake"] = True
    if acquisition is None:
        acquisition = next(
            (
                item for item in discovered_acquisitions
                if Path(str(item.get("warehouse") or "")).expanduser().resolve() == root
            ),
            None,
        )
        if acquisition is not None:
            acquisition["bound_to_lake"] = True
    if acquisition is None and discovered_acquisitions and not requested_task_id:
        # Migration fallback: surface a live worker even when an older launcher
        # failed to persist its run on the active lake. The UI marks it unbound.
        acquisition = discovered_acquisitions[0]
    selected_run_id = (
        str(run_id or "").strip()
        or str(context.get("obtainer_active_campaign_id") or "").strip()
        or str((acquisition or {}).get("webagent", {}).get("run_id") or "").strip()
    )
    campaign = _campaign_snapshot(root, run_id=selected_run_id or None)
    layers = _layer_snapshot(root, campaign)
    stages = _stage_snapshot(campaign, layers)
    return {
        "initialized": True,
        "task_id": requested_task_id,
        "warehouse": str(root),
        "refreshed_at": _now(),
        "lake_context": context,
        "acquisition": acquisition,
        "other_active_acquisitions": [
            item for item in discovered_acquisitions
            if not acquisition or item.get("run_dir") != acquisition.get("run_dir")
        ],
        "campaign": campaign,
        "pipeline": _pipeline_status_snapshot(campaign),
        "layers": layers,
        "stages": stages,
        "summary": _lake_live_summary(root),
        "domain_classes": _domain_class_count(root),
        "dataflow_agent": _dataflow_agent_snapshot(root, project_root),
    }


def _domain_class_count(root: Path) -> int:
    store = DataStore.open(root)
    try:
        return len(store.catalog.list_domain_classes())
    finally:
        store.close()
