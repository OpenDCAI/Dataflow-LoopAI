"""Obtainer Orchestrator agent.

Wraps ObtainerCLI/DataMixer at the outer orchestration layer: it owns lake
bootstrap and the dispatch/poll/gate lifecycle of the managed sub-agents
(``dataset-acquisition-agent`` -> ``dataflow agent-run`` ->
``sft-export-agent``), and exposes a fine-grained, machine-readable status
contract for the main agent (starter) to poll.

Layout of a run directory (``--run <dir>``)::

    <run>/
      thread.json        durable intent / provider / runtime state
      phase.json         worker-owned orchestration state (LLM-narrated)
      status.json        aggregated, deterministic status (``status`` command)
      final_report.json  terminal deliverable report
      worker_prompt.md / resume_prompt.md / policy.md
      logs/worker_stdout.ndjson, logs/worker_stderr.log

The worker (a Codex SDK long loop) updates ``phase.json`` through the hidden
``obtainer-orchestrator phase`` command, which also emits ``obtainercli``
events; the ``status`` command then rolls phase + real sub-agent metrics +
lake metrics into the structured contract documented in ``status.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from loopai.agents.Obtainer.datamixer import codex

from .errors import ObtainerCliError
from .events import emit_obtainer_event, get_obtainer_event_writer
from .sft_export_agent import (
    _apply_runtime_env,
    _json_read,
    _json_write,
    _resolve_provider,
    _workspace,
)

PHASE_FILE = "phase.json"
STATUS_FILE = "status.json"
STATE_FILE = "thread.json"
FINAL_REPORT_FILE = "final_report.json"
STATUS_SCHEMA_VERSION = 1
STALE_AFTER_SECONDS = 600
MAX_ACCEPTANCE_REVISIONS = 3
REPO_ROOT = Path(__file__).resolve().parents[3]
OBTAINER_SKILL_PATH = REPO_ROOT / "skills" / "obtainer" / "SKILL.md"

# Overall progress anchors per phase; sub-agent metrics refine within a phase.
PHASE_BASE_PROGRESS = {
    "bootstrap": 0.05,
    "acquiring": 0.10,
    "gating": 0.45,
    "dataflow": 0.55,
    "exporting": 0.70,
    "finalizing": 0.95,
    "completed": 1.0,
}

PHASE_LABELS = {
    "bootstrap": "数据湖引导",
    "acquiring": "数据集获取",
    "gating": "门控校验",
    "dataflow": "DataFlow 后处理",
    "exporting": "SFT 出库",
    "finalizing": "整理交付物",
    "completed": "已完成",
    "failed": "失败",
}

GENERIC_STATE_PROGRESS = {
    "completed": 1.0,
    "completed_with_errors": 0.95,
    "succeeded": 1.0,
    "running": 0.5,
    "background_started": 0.4,
    "prepared": 0.1,
    "dry_run": 0.05,
    "waiting": 0.0,
    "idle": 0.0,
    "failed": 0.0,
}


def _policy_text() -> str:
    """The orchestration layer shell; the obtainer skill is embedded in prompts."""
    return """# Obtainer Orchestrator policy

You are LoopAI's Obtainer Orchestrator agent. You are the OUTER layer that
owns the whole obtainer workflow: lake bootstrap, sub-agent dispatch,
progress gating and final deliverable reporting. You do NOT do the inner
work yourself (no SearchAgent, WebAgent, download, ingest, dataflow operator
authoring or export authoring) - you dispatch managed sub-agents and poll.

Hard rules:

1. All lake / sub-agent operations must go through the ObtainerCLI command
   surface: `{python_executable} -m loopai.skills.ObtainerCLI.cli dm ... --json`.
2. Read the obtainer skill (skills/obtainer/SKILL.md) as your domain policy
   and follow it for intent parsing, gates and artifact requirements.
3. After every meaningful step, persist your orchestration state with:
   {python_executable} -m loopai.skills.ObtainerCLI.cli dm obtainer-orchestrator phase \\
     --run {run_dir} --state running --phase <phase> --message "<msg>" \\
     --next-action poll [--subtask <name> --subtask-run-dir <dir> --subtask-state <s>] \\
     [--gate <name> --gate-ok true|false --gate-detail "<detail>"] [--warehouse <path>]
   Phases: bootstrap -> acquiring -> gating -> dataflow -> exporting -> finalizing.
4. Dispatch, never micromanage: start `dataset-acquisition-agent`, poll it and
   the lake metrics; when the volume/mix/quality gates pass, run
   `dataflow agent-run`; when the L4 gate passes, start `sft-export-agent`.
   Poll each worker by run_dir and resume it when it reports a resumable state.
5. On completion write final_report.json with warehouse, datasets, record
   counts, recipe/export artifacts, lineage, manifests and snapshots, then
   set `--state completed --phase finalizing --next-action report`.
6. On a blocker set `--state failed --next-action blocked --error "<reason>"`
   and report the failing gate; never claim success without final_report.json.
"""


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="loopai-obtainercli dm obtainer-orchestrator")
    sub = parser.add_subparsers(dest="agent_command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--run", required=True, help="run directory for worker state and artifacts")
    start.add_argument("--objective", default="", help="data shape objective for the orchestration")
    start.add_argument("--keywords", action="append", default=[], help="search keywords; repeatable")
    start.add_argument("--target-datasets", type=int, default=1)
    start.add_argument("--message", default="", help="free-form intent/message for the worker")
    start.add_argument("--analysis-report", action="append", default=[])
    start.add_argument("--lake", default=".datamixer/lake.yaml")
    start.add_argument("--event-output-dir", default="", help="event output dir (default: run_dir parent)")
    start.add_argument("--model", default="")
    start.add_argument("--timeout", type=int, default=3600)
    start.add_argument("--python-executable", default="", help="Python executable for the isolated worker")
    start.add_argument("--node-bin-dir", default="", help="Directory containing node/corepack for codex-runner")
    start.add_argument("--dry-run", action="store_true", help="write prompt/state only; do not launch Codex SDK")
    start.add_argument("--foreground", action="store_true", help="run the inner Codex worker synchronously")
    start.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    resume = sub.add_parser("resume")
    resume.add_argument("--run", required=True)
    resume.add_argument("--message", required=True, help="why/from where to resume")
    resume.add_argument("--model", default="")
    resume.add_argument("--timeout", type=int, default=3600)
    resume.add_argument("--python-executable", default="", help=argparse.SUPPRESS)
    resume.add_argument("--node-bin-dir", default="", help=argparse.SUPPRESS)
    resume.add_argument("--dry-run", action="store_true")
    resume.add_argument("--foreground", action="store_true")
    resume.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    status = sub.add_parser("status")
    status.add_argument("--run", required=True)
    status.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    stop = sub.add_parser("stop")
    stop.add_argument("--run", required=True)
    stop.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    phase = sub.add_parser("phase", help=argparse.SUPPRESS)
    phase.add_argument("--run", required=True)
    phase.add_argument("--state", default="")
    phase.add_argument("--phase", default="")
    phase.add_argument("--message", default="")
    phase.add_argument("--next-action", default="")
    phase.add_argument("--next-action-hint", default="")
    phase.add_argument("--progress", type=float, default=None)
    phase.add_argument("--error", default="")
    phase.add_argument("--warehouse", default="")
    phase.add_argument("--final-report", default="")
    phase.add_argument("--subtask", default="")
    phase.add_argument("--subtask-run-dir", default="")
    phase.add_argument("--subtask-state", default="")
    phase.add_argument("--subtask-message", default="")
    phase.add_argument("--subtask-progress", type=float, default=None)
    phase.add_argument("--gate", default="")
    phase.add_argument("--gate-ok", default="", help="true/false")
    phase.add_argument("--gate-detail", default="")
    phase.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    worker = sub.add_parser("worker-run", help=argparse.SUPPRESS)
    worker.add_argument("--run", required=True)
    worker.add_argument("--prompt", required=True)
    worker.add_argument("--timeout", type=int, default=3600)
    worker.add_argument("--thread-id", default="")
    worker.add_argument("--model", default="")
    worker.add_argument("--python-executable", default="", help=argparse.SUPPRESS)
    worker.add_argument("--node-bin-dir", default="", help=argparse.SUPPRESS)
    worker.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# phase persistence + events
# ---------------------------------------------------------------------------

def _emit_orchestrator_event(
    run_dir: Path,
    *,
    state: str,
    phase: str,
    message: str,
    progress: float | None = None,
    data: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    thread = _json_read(run_dir / STATE_FILE)
    task_id = str(thread.get("task_id") or "").strip()
    output_dir = str(thread.get("output_dir") or run_dir.parent)
    try:
        writer = get_obtainer_event_writer(
            task_id=task_id or None,
            output_dir=output_dir,
        )
        emit_obtainer_event(
            writer,
            node="obtainer-orchestrator",
            status=state,
            message=message,
            progress=progress,
            data=data,
            error=error or None,
        )
    except Exception:
        # Event streaming must never break the worker loop.
        pass


def _write_phase(
    run_dir: Path,
    *,
    state: str,
    phase: str,
    message: str,
    next_action: str = "poll",
    next_action_hint: str = "",
    error: str = "",
    progress: float | None = None,
    warehouse: str = "",
    final_report: str = "",
    subtask: dict[str, Any] | None = None,
    gate: dict[str, Any] | None = None,
    emit: bool = True,
) -> dict[str, Any]:
    """Merge an update into phase.json (worker-owned orchestration state)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    current = _json_read(run_dir / PHASE_FILE)
    now = time.time()
    updates: dict[str, Any] = {
        "state": state or current.get("state") or "idle",
        "phase": phase or current.get("phase") or "idle",
        "message": message if message is not None else current.get("message", ""),
        "next_action": next_action or current.get("next_action", "poll"),
        "updated_at": now,
    }
    if next_action_hint is not None:
        updates["next_action_hint"] = next_action_hint
    if error is not None:
        updates["error"] = error
    if progress is not None:
        updates["progress"] = float(progress)
    if warehouse:
        updates["warehouse"] = warehouse
    if final_report:
        updates["final_report"] = final_report
    if subtask:
        subtasks = dict(current.get("subtasks") or {})
        entry = dict(subtasks.get(subtask.get("name") or "") or {})
        for key in ("run_dir", "state", "message", "progress"):
            if subtask.get(key) is not None:
                entry[key] = subtask[key]
        entry["updated_at"] = now
        subtasks[subtask["name"]] = entry
        updates["subtasks"] = subtasks
    if gate:
        gates = list(current.get("gates") or [])
        index = next(
            (i for i, item in enumerate(gates) if item.get("name") == gate.get("name")),
            None,
        )
        entry = {"name": gate.get("name") or "", "ok": bool(gate.get("ok")), "detail": gate.get("detail") or ""}
        if index is None:
            gates.append(entry)
        else:
            gates[index] = entry
        updates["gates"] = gates
    merged = {**current, **updates}
    _json_write(run_dir / PHASE_FILE, merged)
    if emit:
        _emit_orchestrator_event(
            run_dir,
            state=merged.get("state") or "",
            phase=merged.get("phase") or "",
            message=merged.get("message") or "",
            progress=progress,
            data={"subtask": subtask, "gate": gate} if (subtask or gate) else None,
            error=error,
        )
    return merged


def _cmd_phase(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run).expanduser().resolve()
    subtask = None
    if args.subtask:
        subtask = {
            "name": args.subtask,
            "run_dir": args.subtask_run_dir or "",
            "state": args.subtask_state or "unknown",
            "message": args.subtask_message or "",
        }
        if args.subtask_progress is not None:
            subtask["progress"] = float(args.subtask_progress)
    gate = None
    if args.gate:
        gate = {
            "name": args.gate,
            "ok": str(args.gate_ok).lower() in {"1", "true", "yes", "ok", "passed"},
            "detail": args.gate_detail or "",
        }
    merged = _write_phase(
        run_dir,
        state=args.state or "",
        phase=args.phase or "",
        message=args.message or "",
        next_action=args.next_action or "",
        next_action_hint=args.next_action_hint or "",
        error=args.error or "",
        progress=args.progress,
        warehouse=args.warehouse or "",
        final_report=args.final_report or "",
        subtask=subtask,
        gate=gate,
    )
    return {
        "ok": True,
        "command": "dm.obtainer-orchestrator.phase",
        "run_dir": str(run_dir),
        "phase": merged,
    }


# ---------------------------------------------------------------------------
# status aggregation
# ---------------------------------------------------------------------------

def _subtask_progress(name: str, status: dict[str, Any], sub_run: Path) -> float | None:
    """Derive a sub-agent's progress from authoritative, real counters.

    ``downloads/download_progress.json`` carries the live completed/total counts
    the acquisition worker updates during the long download phase, which its
    status.json does not surface - progress must not rely on LLM narration.
    """
    if not isinstance(status, dict):
        status = {}
    if isinstance(status.get("progress"), (int, float)):
        return float(status["progress"])
    download = status.get("download")
    if isinstance(download, dict):
        total = int(download.get("total") or 0)
        processed = int(download.get("processed") or 0)
        if total > 0:
            return min(processed / total, 1.0)
    if sub_run.is_dir():
        dl = _json_read(sub_run / "downloads" / "download_progress.json")
        if isinstance(dl, dict):
            total = int(dl.get("total") or 0)
            completed = int(dl.get("completed") or 0)
            if total > 0:
                return min(completed / total, 1.0)
    if name == "dataflow" or "dataflow" in name:
        total = int(status.get("input_rows") or status.get("full_input_rows") or 0)
        done = int(status.get("output_rows") or status.get("full_output_rows") or 0)
        if total > 0:
            return min(done / total, 1.0)
    return GENERIC_STATE_PROGRESS.get(str(status.get("state") or "").lower())


def _enrich_subtasks(subtasks: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """Roll up each sub-agent's live status.json into the ledger."""
    rows: list[dict[str, Any]] = []
    for name, info in (subtasks or {}).items():
        item = {
            "name": name,
            "run_dir": str(info.get("run_dir") or ""),
            "state": str(info.get("state") or "unknown"),
            "progress": float(info.get("progress") or 0.0),
            "message": str(info.get("message") or ""),
            "updated_at": float(info.get("updated_at") or 0.0),
        }
        sub_run = Path(str(info.get("run_dir") or "")).expanduser()
        status = _json_read(sub_run / STATUS_FILE) if sub_run.is_dir() else {}
        if status:
            item["state"] = str(status.get("state") or item["state"])
            item["message"] = str(
                status.get("message")
                or status.get("phase_detail")
                or status.get("feedback")
                or item["message"]
            )
            item["updated_at"] = float(status.get("updated_at") or item["updated_at"])
        # Derive progress from authoritative counters (status.json and/or the
        # acquisition worker's live download_progress.json) regardless of whether
        # status.json exists yet.
        derived = _subtask_progress(name, status, sub_run)
        if derived is not None:
            item["progress"] = round(derived, 3)
        # The acquisition worker reports download counters outside status.json;
        # surface them as the subtask's live message / heartbeat when available.
        if sub_run.is_dir():
            dl = _json_read(sub_run / "downloads" / "download_progress.json")
            if isinstance(dl, dict) and int(dl.get("total") or 0) > 0:
                if not item["message"]:
                    item["message"] = "下载 {}/{} 个候选数据集".format(
                        int(dl.get("completed") or 0), int(dl.get("total") or 0)
                    )
                dl_updated = dl.get("updated_at")
                if dl_updated:
                    try:
                        import datetime
                        ts = datetime.datetime.fromisoformat(
                            str(dl_updated).replace("Z", "+00:00")
                        ).timestamp()
                        item["updated_at"] = max(item["updated_at"], ts)
                    except (ValueError, TypeError):
                        pass
        rows.append(item)
    return rows


def _gates_fraction(gates: list[dict[str, Any]]) -> float:
    gates = [g for g in (gates or []) if isinstance(g, dict)]
    if not gates:
        return 0.0
    return sum(1 for g in gates if g.get("ok")) / len(gates)


def _compute_progress(
    state: str,
    phase: str,
    subtasks: list[dict[str, Any]],
    gates: list[dict[str, Any]],
) -> float:
    state = str(state or "").lower()
    phase = str(phase or "").lower()
    if state == "completed":
        return 1.0
    if state in {"failed", "stopped", "interrupted"}:
        return PHASE_BASE_PROGRESS.get(phase, 0.0)

    def sub_progress(*names: str) -> float:
        """Match subtask progress by exact or normalized name so worker-reported
        aliases (e.g. ``acquisition`` vs ``dataset-acquisition-agent``) still
        feed the deterministic progress rollup."""
        for item in subtasks:
            item_name = str(item.get("name") or "").lower()
            for name in names:
                key = str(name or "").lower()
                if key and (item_name == key or key in item_name or item_name in key):
                    return float(item.get("progress") or 0.0)
        return 0.0

    if phase == "bootstrap":
        return PHASE_BASE_PROGRESS["bootstrap"]
    if phase == "acquiring":
        return PHASE_BASE_PROGRESS["acquiring"] + 0.30 * sub_progress("dataset-acquisition-agent", "acquisition")
    if phase == "gating":
        return PHASE_BASE_PROGRESS["gating"] + 0.10 * _gates_fraction(gates)
    if phase == "dataflow":
        return PHASE_BASE_PROGRESS["dataflow"] + 0.15 * sub_progress("dataflow")
    if phase == "exporting":
        return PHASE_BASE_PROGRESS["exporting"] + 0.20 * sub_progress("sft-export-agent", "export")
    if phase == "finalizing":
        return PHASE_BASE_PROGRESS["finalizing"]
    return min(PHASE_BASE_PROGRESS.get(phase, 0.0), 1.0)


def _lake_metrics(warehouse: str) -> dict[str, Any]:
    if not warehouse or not Path(warehouse).expanduser().is_dir():
        return {"warehouse": warehouse or "", "initialized": False, "datasets": 0, "records": 0, "quality_levels": {}}
    try:
        from loopai.agents.Obtainer.datamixer.store import DataStore

        store = DataStore.open(warehouse)
        try:
            datasets = store.catalog.list_datasets()
            quality: dict[str, int] = {}
            for ds in datasets:
                for level, count in (ds.get("quality_levels") or {}).items():
                    quality[str(level)] = quality.get(str(level), 0) + int(count or 0)
            return {
                "warehouse": str(warehouse),
                "initialized": True,
                "datasets": len(datasets),
                "records": store.catalog.count(),
                "quality_levels": quality,
            }
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - dashboard must stay read-only/available
        return {
            "warehouse": str(warehouse),
            "initialized": False,
            "datasets": 0,
            "records": 0,
            "quality_levels": {},
            "error": str(exc)[:200],
        }


def _status_payload(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    phase = _json_read(run_dir / PHASE_FILE)
    thread = _json_read(run_dir / STATE_FILE)
    final_report = _json_read(run_dir / FINAL_REPORT_FILE)
    status = _json_read(run_dir / STATUS_FILE)
    now = time.time()

    state = str(phase.get("state") or thread.get("state") or status.get("state") or "idle")
    phase_name = str(phase.get("phase") or thread.get("phase") or "idle")
    if final_report.get("ok") is True and state != "completed":
        state = "completed"
        phase_name = "completed"

    # Detect a dead background worker that never reached a terminal state.
    pid = status.get("pid")
    if state in {"running", "background_started", "prepared"} and pid:
        try:
            os.kill(int(pid), 0)
            worker_alive = True
        except (OSError, ValueError):
            worker_alive = False
        if not worker_alive and final_report.get("ok") is not True:
            state = "failed"
            phase_name = phase_name or "failed"

    subtasks = _enrich_subtasks(phase.get("subtasks") or {}, run_dir)
    gates = [g for g in (phase.get("gates") or []) if isinstance(g, dict)]
    warehouse = str(thread.get("warehouse") or phase.get("warehouse") or "").strip()
    lake = _lake_metrics(warehouse)
    progress = _compute_progress(state, phase_name, subtasks, gates)
    updated_at = float(
        phase.get("updated_at") or thread.get("updated_at") or status.get("updated_at") or now
    )
    stale = bool(
        state in {"running", "background_started", "prepared"}
        and (now - updated_at) > STALE_AFTER_SECONDS
    )
    next_action = str(phase.get("next_action") or "poll")
    if state == "completed":
        next_action = "report"
    elif state == "interrupted":
        next_action = "resume"
    elif state in {"failed", "stopped"}:
        next_action = "blocked"
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "ok": True,
        "command": "dm.obtainer-orchestrator.status",
        "state": state,
        "phase": phase_name,
        "phase_label": PHASE_LABELS.get(phase_name, phase_name),
        "attempt": int(phase.get("attempt") or thread.get("attempt") or status.get("attempt") or 1),
        "progress": round(progress, 3),
        "message": str(phase.get("message") or thread.get("message") or ""),
        "next_action": next_action,
        "next_action_hint": str(phase.get("next_action_hint") or ""),
        "updated_at": updated_at,
        "stale": stale,
        "run_dir": str(run_dir),
        "warehouse": lake.get("warehouse") or "",
        "lake": lake,
        "subtasks": subtasks,
        "gates": gates,
        "final_report": str(run_dir / FINAL_REPORT_FILE) if final_report else "",
        "error": str(phase.get("error") or thread.get("error") or ""),
    }


def _cmd_stop(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run).expanduser().resolve()
    status = _json_read(run_dir / STATUS_FILE)
    pid = status.get("pid")
    killed = False
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
            killed = True
        except (ProcessLookupError, PermissionError, OSError):
            pass
    _write_phase(
        run_dir,
        state="stopped",
        phase=str(_json_read(run_dir / PHASE_FILE).get("phase") or "idle"),
        message="orchestrator stopped by user",
        next_action="blocked",
        emit=False,
    )
    return {"ok": True, "command": "dm.obtainer-orchestrator.stop", "run_dir": str(run_dir), "killed": killed, "pid": pid}


# ---------------------------------------------------------------------------
# prompts + worker lifecycle
# ---------------------------------------------------------------------------

def _obtainer_skill_text() -> str:
    try:
        return OBTAINER_SKILL_PATH.read_text(encoding="utf-8")
    except OSError:
        return "(obtainer skill not found)"


def _build_start_prompt(
    *,
    run_dir: Path,
    objective: str,
    keywords: list[str],
    target_datasets: int,
    message: str,
    analysis_reports: list[str],
    lake: str,
) -> str:
    skill = _obtainer_skill_text()
    keyword_lines = "\n".join(f"- {item}" for item in keywords) or "- (none)"
    reports = "\n".join(f"- {item}" for item in analysis_reports) or "- (none)"
    return f"""You are the Obtainer Orchestrator agent (inner Codex SDK worker).

Run directory: {run_dir}
Lake link: {lake}

## Your policy (obtainer skill)
{skill}

## Orchestration shell
{_policy_text().format(run_dir=run_dir, python_executable=codex.loopai_python_executable())}

## Intent handed over by the main agent
Objective: {objective}
Keywords:
{keyword_lines}
Target datasets: {target_datasets}
Analysis reports:
{reports}
Message:
{message}

## Expected sequence
1. Bootstrap the lake: `dm lake scan`, then `dm lake load --warehouse ...` or
   `dm lake init --root ...`, clear stale task bindings with `dm lake unbind`,
   and persist the resolved warehouse via `phase --warehouse <path>`.
2. Parse the intent into an acquisition objective (sample shape, domains,
   task types, quality gates, proportions) and dispatch:
   `dm --root <warehouse> dataset-acquisition-agent start --run <run>/acquisition --objective ... --keywords ... --target-datasets N --message ...`
3. Poll acquisition status and lake metrics; update phase with subtask and
   gates. When volume/mix/quality gates pass, run
   `dm --root <warehouse> dataflow agent-run --target ... --model ... --dataset ...`.
4. When the L4 gate passes, dispatch
   `dm --root <warehouse> sft-export-agent start --run <run>/export --analysis-report ... --target-records ...`.
5. Poll export until its final_report.json ok=true, then write this run's
   final_report.json (warehouse, datasets, record counts, recipe/export
   artifacts, lineage, manifests, snapshots) and finish with
   `phase --state completed --phase finalizing --next-action report`.

Return a short JSON summary with ok, summary, and the final_report path.
"""


def _build_resume_prompt(*, run_dir: Path, message: str) -> str:
    phase = _json_read(run_dir / PHASE_FILE)
    thread = _json_read(run_dir / STATE_FILE)
    skill = _obtainer_skill_text()
    return f"""You are resuming the Obtainer Orchestrator agent.

Run directory: {run_dir}
Current orchestration state: {json.dumps(phase, ensure_ascii=False, indent=2)}
Current thread state: {json.dumps({k: v for k, v in thread.items() if k != "provider"}, ensure_ascii=False, indent=2)}

## Policy (obtainer skill)
{skill}

## Orchestration shell
{_policy_text().format(run_dir=run_dir, python_executable=codex.loopai_python_executable())}

## Resume instruction
{message}

Continue from the recorded phase. Never redo a completed stage: reuse each
sub-agent's existing run_dir and poll/resume it instead of starting fresh.
Update `phase` after every step. Finish by writing final_report.json and
setting `--state completed --next-action report`.
"""


def _save_initial_state(
    *,
    run_dir: Path,
    lake: str,
    root: str,
    task_id: str,
    objective: str,
    keywords: list[str],
    target_datasets: int,
    message: str,
    analysis_reports: list[str],
    output_dir: str,
    model: str,
    python_executable: str,
    node_bin_dir: str,
    mode: str,
) -> None:
    now = time.time()
    state = _json_read(run_dir / STATE_FILE)
    state.update({
        "created_at": state.get("created_at") or now,
        "updated_at": now,
        "mode": mode,
        "objective": objective,
        "keywords": list(keywords or []),
        "target_datasets": target_datasets,
        "message": message,
        "analysis_reports": list(analysis_reports or []),
        "lake": lake,
        "root": root,
        "task_id": task_id,
        "output_dir": output_dir,
        "model": model,
        "runtime": {
            "python_executable": python_executable,
            "node_bin_dir": node_bin_dir,
        },
    })
    _json_write(run_dir / STATE_FILE, state)
    _json_write(run_dir / STATUS_FILE, {
        "state": "prepared",
        "updated_at": now,
        "run_dir": str(run_dir),
        "attempt": 1,
    })


def _spawn_background(
    *,
    run_dir: Path,
    warehouse: str,
    prompt_path: Path,
    timeout: int,
    model: str,
    thread_id: str = "",
    python_executable: str = "",
    node_bin_dir: str = "",
) -> dict[str, Any]:
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / "worker_stdout.ndjson"
    stderr_path = logs / "worker_stderr.log"
    cmd = [
        codex.loopai_python_executable(),
        "-m",
        "loopai.skills.ObtainerCLI.cli",
        "dm",
        "--root",
        warehouse,
        "obtainer-orchestrator",
        "worker-run",
        "--run",
        str(run_dir),
        "--prompt",
        str(prompt_path),
        "--timeout",
        str(timeout),
        "--json",
    ]
    if model:
        cmd.extend(["--model", model])
    if thread_id:
        cmd.extend(["--thread-id", thread_id])
    if python_executable:
        cmd.extend(["--python-executable", python_executable])
    if node_bin_dir:
        cmd.extend(["--node-bin-dir", node_bin_dir])
    env = os.environ.copy()
    for key in (
        "CODEX_THREAD_ID",
        "CODEX_USE_PROJECT_CONFIG",
        "CODEX_API_KEY",
        "CODEX_BASE_URL",
        "CODEX_MODEL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
    ):
        env.pop(key, None)
    python_executable = codex.loopai_python_executable()
    env["LOOPAI_PYTHON_EXECUTABLE"] = python_executable
    env["PATH"] = codex.runner_process_path(python_executable, env.get("PATH"))
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_workspace()),
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    _json_write(run_dir / STATUS_FILE, {
        "state": "background_started",
        "updated_at": time.time(),
        "pid": proc.pid,
        "prompt_path": str(prompt_path),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "thread_id": thread_id or None,
    })
    return {
        "ok": True,
        "status": "background_started",
        "run_dir": str(run_dir),
        "pid": proc.pid,
        "prompt_path": str(prompt_path),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "thread_id": thread_id or None,
    }


def _final_report_ok(run_dir: Path) -> bool:
    """True when the worker has actually written an ok=true final report."""
    try:
        report = _json_read(run_dir / FINAL_REPORT_FILE)
    except Exception:
        return False
    return bool(isinstance(report, dict) and report.get("ok") is True)


def _has_running_subtask(run_dir: Path) -> bool:
    """True when any managed sub-agent is still mid-flight."""
    phase = _json_read(run_dir / PHASE_FILE)
    for info in (phase.get("subtasks") or {}).values():
        state = str(info.get("state") or "").lower()
        if state in {"running", "background_started", "prepared"}:
            return True
    return False


def _result_ok(result: dict[str, Any] | None) -> bool:
    return bool(isinstance(result, dict) and result.get("ok") is True)


def _result_is_valid_json(result: dict[str, Any] | None) -> bool:
    """True when the agent's final response was the required JSON (has `ok`)."""
    return bool(isinstance(result, dict) and "ok" in result)


def _continuation_prompt(issues: str, *, run_dir: Path) -> str:
    phase = _json_read(run_dir / PHASE_FILE)
    skill = _obtainer_skill_text()
    return f"""Your previous turn ended before returning a valid final result.

Acceptance issues:
{issues}

Current orchestration state:
{json.dumps(phase, ensure_ascii=False, indent=2)}

## Policy (obtainer skill)
{skill}

## Orchestration shell
{_policy_text().format(run_dir=run_dir, python_executable=codex.loopai_python_executable())}

Continue from the recorded phase. If sub-agents are still running, keep polling
them and do NOT conclude - never report completion while a sub-agent is active.
When the workflow is truly finished, write final_report.json (ok=true, warehouse,
datasets, record counts, recipe/export artifacts, lineage, manifests, snapshots)
and finish with `phase --state completed --next-action report`. If the workflow
is genuinely blocked, return the JSON {{"ok": false, "summary": "...", "blockers": [...]}}
instead of a narrative.
"""


def _run_worker(
    *,
    run_dir: Path,
    prompt: str,
    prov: dict,
    provider_meta: dict,
    timeout: int,
    thread_id: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    prompt_path = run_dir / ("resume_prompt.md" if thread_id else "worker_prompt.md")
    prompt_path.write_text(prompt, encoding="utf-8")
    (run_dir / "policy.md").write_text(
        _policy_text().format(run_dir=run_dir, python_executable=codex.loopai_python_executable()),
        encoding="utf-8",
    )
    if dry_run:
        _json_write(run_dir / STATUS_FILE, {
            "state": "dry_run",
            "updated_at": time.time(),
            "prompt_path": str(prompt_path),
            "thread_id": thread_id or None,
        })
        return {
            "ok": True,
            "status": "dry_run",
            "run_dir": str(run_dir),
            "prompt_path": str(prompt_path),
            "thread_id": thread_id or None,
            "provider": provider_meta,
        }
    _json_write(run_dir / STATUS_FILE, {
        "state": "running",
        "updated_at": time.time(),
        "prompt_path": str(prompt_path),
        "thread_id": thread_id or None,
    })

    # Acceptance loop: validate the agent's final result and re-prompt it to
    # continue when it concludes too early (narrative instead of JSON, sub-agents
    # still running, or missing final_report.json) - mirroring the acquisition /
    # sft-export workers.
    current_prompt = prompt
    current_thread_id = thread_id or None
    last_result: dict[str, Any] = {}
    attempts = 0
    while True:
        try:
            result = codex.run_via_sdk(
                current_prompt,
                prov,
                cwd=str(_workspace()),
                timeout=timeout,
                thread_id=current_thread_id,
            )
        except Exception as exc:  # noqa: BLE001 - surface runner failures in status
            _write_phase(
                run_dir,
                state="failed",
                phase=str(_json_read(run_dir / PHASE_FILE).get("phase") or "idle"),
                message="orchestrator worker failed",
                next_action="blocked",
                error=str(exc),
                emit=False,
            )
            return {
                "ok": False,
                "status": "failed",
                "run_dir": str(run_dir),
                "error": str(exc),
                "attempt": attempts,
            }
        last_result = result or {}
        current_thread_id = current_thread_id or str(last_result.get("thread_id") or "")
        attempts += 1

        running = _has_running_subtask(run_dir)
        if not _result_is_valid_json(last_result):
            issues = "final response is not the required JSON {ok, summary, final_report}"
        elif not _result_ok(last_result):
            # Agent deliberately concluded with ok=false (blocker report).
            issues = (
                "sub-agents are still running (not terminal); keep polling instead of concluding"
                if running
                else ""
            )
        elif running:
            issues = "sub-agents are still running (not terminal); keep polling instead of concluding"
        elif not _final_report_ok(run_dir):
            issues = "final_report.json with ok=true is missing; write it before concluding"
        else:
            issues = ""

        if not issues:
            break
        if attempts > MAX_ACCEPTANCE_REVISIONS:
            break
        current_prompt = _continuation_prompt(issues, run_dir=run_dir)

    running = _has_running_subtask(run_dir)
    report_ok = _final_report_ok(run_dir)
    if running:
        state = "interrupted"
        next_action = "resume"
        message = (
            "orchestrator concluded while sub-agents are still running; "
            "resume to keep polling and finish the workflow"
        )
        ok = False
    elif report_ok:
        state = "completed"
        next_action = "report"
        message = str(last_result.get("summary") or "obtainer orchestration finished")
        ok = True
    elif _result_is_valid_json(last_result) and not _result_ok(last_result):
        state = "completed_with_errors"
        next_action = "blocked"
        message = str(last_result.get("summary") or "obtainer orchestration finished with blockers")
        ok = False
    else:
        # valid ok=true JSON but missing final_report, or a non-JSON narrative:
        # the workflow is not actually finished - resume to continue.
        state = "interrupted"
        next_action = "resume"
        message = (
            "orchestrator did not finish the workflow (missing final report or "
            "invalid final result); resume to continue"
        )
        ok = False

    final_report_path = str(run_dir / FINAL_REPORT_FILE) if (run_dir / FINAL_REPORT_FILE).exists() else ""
    _write_phase(
        run_dir,
        state=state,
        phase="finalizing",
        message=message,
        next_action=next_action,
        final_report=final_report_path,
        emit=False,
    )
    _json_write(run_dir / STATUS_FILE, {
        "state": state,
        "updated_at": time.time(),
        "thread_id": current_thread_id,
    })
    return {
        "ok": ok,
        "status": state,
        "run_dir": str(run_dir),
        "result": last_result,
        "attempt": attempts,
    }


def run_agent(argv: list[str], *, root: str = "", lake: str = "", task_id: str = "") -> dict[str, Any]:
    args = _parse(argv)
    run_dir = Path(args.run).expanduser().resolve()

    if args.agent_command == "status":
        return _status_payload(run_dir)

    if args.agent_command == "stop":
        return _cmd_stop(args)

    if args.agent_command == "phase":
        return _cmd_phase(args)

    if getattr(args, "python_executable", "") or getattr(args, "node_bin_dir", ""):
        _apply_runtime_env(
            python_executable=getattr(args, "python_executable", ""),
            node_bin_dir=getattr(args, "node_bin_dir", ""),
        )

    if args.agent_command == "start":
        if not args.objective and not args.message:
            raise ObtainerCliError(
                "OBTAINER_ORCHESTRATOR_INTENT_REQUIRED",
                "obtainer-orchestrator start requires --objective and/or --message",
                hint="Pass the parsed data intent so the orchestrator can dispatch sub-agents.",
                exit_code=2,
            )
        run_dir.mkdir(parents=True, exist_ok=True)
        lake_link = lake or args.lake or ".datamixer/lake.yaml"
        output_dir = args.event_output_dir or str(run_dir.parent)
        python_executable = args.python_executable or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
        _save_initial_state(
            run_dir=run_dir,
            lake=lake_link,
            root=root,
            task_id=task_id,
            objective=args.objective,
            keywords=args.keywords,
            target_datasets=args.target_datasets,
            message=args.message,
            analysis_reports=args.analysis_report,
            output_dir=output_dir,
            model=args.model,
            python_executable=python_executable,
            node_bin_dir=node_bin_dir,
            mode="start",
        )
        _write_phase(
            run_dir,
            state="prepared",
            phase="bootstrap",
            message="编排任务已就绪，等待数据湖引导",
            next_action="poll",
            next_action_hint="orchestrator bootstraps the lake, then dispatches acquisition",
        )
        prompt = _build_start_prompt(
            run_dir=run_dir,
            objective=args.objective,
            keywords=args.keywords,
            target_datasets=args.target_datasets,
            message=args.message,
            analysis_reports=args.analysis_report,
            lake=lake_link,
        )
        prov, provider_meta = _resolve_provider(Path(root) if root else Path.cwd(), args.model or None)
        if args.dry_run:
            return _run_worker(
                run_dir=run_dir, prompt=prompt, prov=prov, provider_meta=provider_meta,
                timeout=args.timeout, dry_run=True,
            )
        if not args.foreground:
            prompt_path = run_dir / "worker_prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            return _spawn_background(
                run_dir=run_dir,
                warehouse=root or str(Path.cwd()),
                prompt_path=prompt_path,
                timeout=args.timeout,
                model=args.model or str(provider_meta.get("model_pool_name") or ""),
                python_executable=python_executable,
                node_bin_dir=node_bin_dir,
            )
        return _run_worker(
            run_dir=run_dir, prompt=prompt, prov=prov, provider_meta=provider_meta,
            timeout=args.timeout,
        )

    if args.agent_command == "resume":
        thread = _json_read(run_dir / STATE_FILE)
        if not thread:
            raise ObtainerCliError(
                "OBTAINER_ORCHESTRATOR_RUN_NOT_FOUND",
                f"obtainer-orchestrator run not found: {run_dir}",
                hint="Start the orchestrator first with `dm obtainer-orchestrator start --run ...`.",
                exit_code=2,
            )
        warehouse = str(thread.get("warehouse") or thread.get("root") or root or Path.cwd())
        prov, provider_meta = _resolve_provider(Path(warehouse), args.model or None)
        prompt = _build_resume_prompt(run_dir=run_dir, message=args.message)
        if args.dry_run:
            return _run_worker(
                run_dir=run_dir, prompt=prompt, prov=prov, provider_meta=provider_meta,
                timeout=args.timeout, thread_id=str(thread.get("thread_id") or ""), dry_run=True,
            )
        if not args.foreground:
            prompt_path = run_dir / "resume_prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            return _spawn_background(
                run_dir=run_dir,
                warehouse=warehouse,
                prompt_path=prompt_path,
                timeout=args.timeout,
                model=args.model or str(provider_meta.get("model_pool_name") or ""),
                thread_id=str(thread.get("thread_id") or ""),
                python_executable=str(
                    args.python_executable
                    or thread.get("runtime", {}).get("python_executable")
                    or ""
                ),
                node_bin_dir=str(
                    args.node_bin_dir
                    or thread.get("runtime", {}).get("node_bin_dir")
                    or ""
                ),
            )
        return _run_worker(
            run_dir=run_dir, prompt=prompt, prov=prov, provider_meta=provider_meta,
            timeout=args.timeout, thread_id=str(thread.get("thread_id") or ""),
        )

    if args.agent_command == "worker-run":
        thread = _json_read(run_dir / STATE_FILE)
        if not thread:
            raise ObtainerCliError(
                "OBTAINER_ORCHESTRATOR_RUN_NOT_FOUND",
                f"obtainer-orchestrator run not found: {run_dir}",
                hint="worker-run is internal; use start/resume from the outer process.",
                exit_code=2,
            )
        warehouse = str(thread.get("warehouse") or root or Path.cwd())
        prov, provider_meta = _resolve_provider(Path(warehouse), args.model or None)
        prompt_path = Path(args.prompt)
        if not prompt_path.exists():
            raise ObtainerCliError(
                "OBTAINER_ORCHESTRATOR_PROMPT_NOT_FOUND",
                f"worker prompt not found: {prompt_path}",
                hint="Use start/resume to create worker prompt files.",
                exit_code=2,
            )
        return _run_worker(
            run_dir=run_dir,
            prompt=prompt_path.read_text(encoding="utf-8"),
            prov=prov,
            provider_meta=provider_meta,
            timeout=args.timeout,
            thread_id=args.thread_id,
        )

    raise AssertionError(args.agent_command)
