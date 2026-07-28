"""Read-only operational snapshot for the WebAgent -> DataMixer pipeline UI."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loopai.agents.Obtainer.datamixer.store import DataStore
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
        "pipeline": campaign.get("pipeline") or None,
    }


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
) -> dict[str, Any]:
    """Build the dashboard payload without starting agents or rebuilding data."""
    root = Path(warehouse).expanduser().resolve()
    context = lake_context or {}
    discovered_acquisitions = _discover_active_acquisitions(project_root)
    acquisition = _acquisition_snapshot(acquisition_run) if acquisition_run else None
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
    if acquisition is None and discovered_acquisitions:
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
        "warehouse": str(root),
        "refreshed_at": _now(),
        "lake_context": context,
        "acquisition": acquisition,
        "other_active_acquisitions": [
            item for item in discovered_acquisitions
            if not acquisition or item.get("run_dir") != acquisition.get("run_dir")
        ],
        "campaign": campaign,
        "layers": layers,
        "stages": stages,
        "domain_classes": _domain_class_count(root),
    }


def _domain_class_count(root: Path) -> int:
    store = DataStore.open(root)
    try:
        return len(store.catalog.list_domain_classes())
    finally:
        store.close()
