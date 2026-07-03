from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from loopai.agents.Obtainer.datamixer import codex

from .errors import ObtainerCliError
from .sft_export_agent import _json_read, _json_write, _resolve_provider, _workspace

DEFAULT_TARGET_DATASETS = 1
DEFAULT_MAX_ROWS_PER_DATASET = 100000
STATUS_FILE = "status.json"
STATE_FILE = "thread.json"


def _policy_text() -> str:
    return """# Dataset acquisition worker policy

You are the isolated inner Codex SDK worker for an Obtainer dataset acquisition,
download, and DataMixer ingest task.

Hard rules:

1. All lakehouse operations after downloaded files exist must use:
   python3 -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} <datamixer-command> --json
2. Do not use legacy Obtainer lake/table/sample/index commands.
3. You may use Obtainer SearchAgent and `download manifest` as the acquisition
   bridge when appropriate:
   python3 -m loopai.skills.ObtainerCLI.cli searchagent ...
   python3 -m loopai.skills.ObtainerCLI.cli download manifest ...
4. If direct web/Hugging Face/Kaggle discovery is more appropriate for the
   caller's instruction, write an equivalent manifest yourself and continue.
5. Before downloading, compare the candidate list against the original user
   request and Analyzer report. Remove clearly unrelated datasets and write
   both a filtered manifest and a rejection report with exact reasons.
6. Each single dataset is capped at {max_rows_per_dataset} rows. Do not bypass
   this cap. Smaller sampled downloads are allowed for broad acquisition, but
   record sampled_rows and cap in the manifest/report.
7. Normalize each downloaded dataset to JSONL before ingest. Each row must
   preserve source_uri, source_dataset/source_dataset_id, split, and enough
   payload fields for later SFT/PT processing.
8. Ingest every accepted dataset through DataMixer `ingest` or `agent-ingest`
   with complete tags: source platform, source dataset id, source URL or URI,
   license if known, language if known, domain, task_type, processing_level,
   source_kind, split, loop_uuid/version_id when provided, and acquisition_run.
9. After ingest, run DataMixer status, dataset list, stats, representative query,
   and index build when useful for downstream recall.
10. Write final_report.json with candidates, filtered list, rejections,
    downloads, ingests, DataMixer command summaries, before/after counts,
    lineage/manifest paths, and blockers.
11. Do not mark ok=true if no dataset was ingested, if accepted datasets are
    unrelated to the request, or if required source/provenance tags are missing.
12. Do not read or print secret/key files.
"""


def _analysis_block(paths: list[str]) -> str:
    if not paths:
        return "- No analysis report paths were provided.\n"
    return "\n".join(f"- {p}" for p in paths) + "\n"


def build_start_prompt(
    *,
    warehouse: Path,
    run_dir: Path,
    analysis_reports: list[str],
    objective: str,
    keywords: str,
    target_datasets: int,
    max_rows_per_dataset: int,
    discovery_mode: str,
    extra_message: str,
) -> str:
    policy = _policy_text().format(
        warehouse=str(warehouse),
        max_rows_per_dataset=max_rows_per_dataset,
    )
    return f"""{policy}

# Task

Read the Analyzer report(s) and caller objective, discover relevant dataset
candidates, prune unrelated candidates before download, download selected
datasets, normalize them to JSONL, and ingest them into the existing DataMixer
warehouse.

Analyzer report paths:
{_analysis_block(analysis_reports)}
Warehouse:
- {warehouse}

Run directory:
- {run_dir}

Objective:
- {objective or 'Infer from Analyzer report.'}

Keywords:
- {keywords or 'Infer from Analyzer report.'}

Target datasets:
- {target_datasets}

Per-dataset row cap:
- {max_rows_per_dataset}

Discovery mode:
- {discovery_mode}

Required artifacts:
- candidates manifest: {run_dir}/manifest/candidates.json
- filtered manifest: {run_dir}/manifest/filtered_manifest.json
- rejections: {run_dir}/manifest/rejections.json
- downloads: {run_dir}/downloads/
- ingest report: {run_dir}/manifest/ingest_results.json
- final report: {run_dir}/final_report.json

Extra caller instruction:
{extra_message or '- none'}

Proceed end-to-end. Return concise JSON with ok, final_report, datasets_ingested,
warehouse, and blockers.
"""


def build_resume_prompt(*, run_dir: Path, message: str) -> str:
    return f"""{_policy_text().format(warehouse='the warehouse recorded in thread.json', max_rows_per_dataset=DEFAULT_MAX_ROWS_PER_DATASET)}

# Resume task

Continue the dataset acquisition worker run recorded at:
- {run_dir}

Read thread.json, status.json, final_report.json if present, manifests,
download results, ingest results, and logs. Apply this caller instruction:

{message}

Keep the same hard policy. If a previous candidate list or ingest was wrong,
write corrected manifests/rejections and continue from the safest consistent
step. Return concise JSON in the final response.
"""


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="loopai-obtainercli dm dataset-acquisition-agent")
    sub = parser.add_subparsers(dest="agent_command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--run", required=True)
    start.add_argument("--analysis-report", action="append", default=[])
    start.add_argument("--objective", default="")
    start.add_argument("--keywords", default="")
    start.add_argument("--target-datasets", type=int, default=DEFAULT_TARGET_DATASETS)
    start.add_argument("--max-rows-per-dataset", type=int, default=DEFAULT_MAX_ROWS_PER_DATASET)
    start.add_argument("--discovery-mode", choices=["auto", "searchagent", "codex-web"], default="auto")
    start.add_argument("--model", default="")
    start.add_argument("--timeout", type=int, default=3600)
    start.add_argument("--message", default="")
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--foreground", action="store_true")
    start.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    resume = sub.add_parser("resume")
    resume.add_argument("--run", required=True)
    resume.add_argument("--message", required=True)
    resume.add_argument("--model", default="")
    resume.add_argument("--timeout", type=int, default=3600)
    resume.add_argument("--dry-run", action="store_true")
    resume.add_argument("--foreground", action="store_true")
    resume.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    status = sub.add_parser("status")
    status.add_argument("--run", required=True)
    status.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    worker = sub.add_parser("worker-run", help=argparse.SUPPRESS)
    worker.add_argument("--run", required=True)
    worker.add_argument("--prompt", required=True)
    worker.add_argument("--timeout", type=int, default=3600)
    worker.add_argument("--thread-id", default="")
    worker.add_argument("--model", default="")
    worker.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _status_payload(run_dir: Path) -> dict:
    status = _json_read(run_dir / STATUS_FILE)
    state = _json_read(run_dir / STATE_FILE)
    final_report = _json_read(run_dir / "final_report.json")
    pid = status.get("pid") if isinstance(status, dict) else None
    if pid:
        try:
            os.kill(int(pid), 0)
            status["process_alive"] = True
        except OSError:
            status["process_alive"] = False
    return {
        "ok": True,
        "command": "dm.dataset-acquisition-agent.status",
        "run_dir": str(run_dir),
        "status": status or {"state": "unknown"},
        "thread": {key: value for key, value in state.items() if key != "provider"},
        "final_report": final_report or None,
    }


def _spawn_background(
    *,
    run_dir: Path,
    warehouse: Path,
    prompt_path: Path,
    timeout: int,
    model: str,
    thread_id: str = "",
) -> dict:
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / "worker_stdout.ndjson"
    stderr_path = logs / "worker_stderr.log"
    cmd = [
        sys.executable,
        "-m",
        "loopai.skills.ObtainerCLI.cli",
        "dm",
        "--root",
        str(warehouse),
        "dataset-acquisition-agent",
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
    env = os.environ.copy()
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CODEX_USE_PROJECT_CONFIG", None)
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


def _run_worker(
    *,
    run_dir: Path,
    prompt: str,
    prov: dict,
    provider_meta: dict,
    timeout: int,
    thread_id: str = "",
    dry_run: bool = False,
) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    prompt_path = run_dir / ("resume_prompt.md" if thread_id else "worker_prompt.md")
    prompt_path.write_text(prompt, encoding="utf-8")
    (run_dir / "policy.md").write_text(
        _policy_text().format(
            warehouse="see thread.json",
            max_rows_per_dataset=DEFAULT_MAX_ROWS_PER_DATASET,
        ),
        encoding="utf-8",
    )
    if dry_run:
        _json_write(run_dir / STATUS_FILE, {
            "state": "dry_run",
            "updated_at": time.time(),
            "prompt_path": str(prompt_path),
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
    try:
        result = codex.run_via_sdk(
            prompt,
            prov,
            cwd=str(_workspace()),
            timeout=timeout,
            thread_id=thread_id or None,
        )
    except Exception as exc:
        _json_write(run_dir / STATUS_FILE, {
            "state": "failed",
            "updated_at": time.time(),
            "error": str(exc),
            "thread_id": thread_id or None,
        })
        raise

    state = _json_read(run_dir / STATE_FILE)
    if result.get("thread_id"):
        state["thread_id"] = result["thread_id"]
    state["updated_at"] = time.time()
    state["provider"] = provider_meta
    _json_write(run_dir / STATE_FILE, state)
    _json_write(run_dir / "logs" / f"codex_result_{int(time.time())}.json", result)
    final_report = _json_read(run_dir / "final_report.json")
    _json_write(run_dir / STATUS_FILE, {
        "state": "completed",
        "updated_at": time.time(),
        "thread_id": state.get("thread_id") or thread_id or None,
        "final_report": str(run_dir / "final_report.json") if final_report else None,
        "worker_ok": final_report.get("ok") if final_report else None,
    })
    return {
        "ok": True,
        "status": "completed",
        "run_dir": str(run_dir),
        "thread_id": state.get("thread_id") or thread_id or None,
        "final_report": str(run_dir / "final_report.json") if final_report else None,
        "worker_result": {key: value for key, value in result.items() if key != "runner_result"},
    }


def _save_initial_state(
    *,
    run_dir: Path,
    warehouse: Path,
    analysis_reports: list[str],
    target_datasets: int,
    max_rows_per_dataset: int,
    objective: str,
    keywords: str,
    discovery_mode: str,
    provider_meta: dict,
) -> None:
    now = time.time()
    state = _json_read(run_dir / STATE_FILE)
    state.update({
        "created_at": state.get("created_at") or now,
        "updated_at": now,
        "mode": "start",
        "warehouse": str(warehouse),
        "analysis_reports": analysis_reports,
        "target_datasets": target_datasets,
        "max_rows_per_dataset": max_rows_per_dataset,
        "objective": objective,
        "keywords": keywords,
        "discovery_mode": discovery_mode,
        "provider": provider_meta,
    })
    _json_write(run_dir / STATE_FILE, state)
    _json_write(run_dir / STATUS_FILE, {
        "state": "prepared",
        "updated_at": now,
        "run_dir": str(run_dir),
        "warehouse": str(warehouse),
    })


def run_agent(argv: list[str], *, root: str) -> dict:
    args = _parse(argv)
    run_dir = Path(args.run).expanduser().resolve()

    if args.agent_command == "status":
        return _status_payload(run_dir)

    if args.agent_command == "start":
        if not root:
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_ROOT_REQUIRED",
                "dataset-acquisition-agent start requires `dm --root <warehouse>`",
                hint="Pass `loopai-obtainercli dm --root /path/to/warehouse dataset-acquisition-agent start ...`.",
                exit_code=2,
            )
        warehouse = Path(root).expanduser().resolve()
        max_rows = args.max_rows_per_dataset
        if max_rows <= 0 or max_rows > DEFAULT_MAX_ROWS_PER_DATASET:
            max_rows = DEFAULT_MAX_ROWS_PER_DATASET
        target_datasets = max(args.target_datasets, DEFAULT_TARGET_DATASETS)
        prov, provider_meta = _resolve_provider(warehouse, args.model or None)
        _save_initial_state(
            run_dir=run_dir,
            warehouse=warehouse,
            analysis_reports=args.analysis_report,
            target_datasets=target_datasets,
            max_rows_per_dataset=max_rows,
            objective=args.objective,
            keywords=args.keywords,
            discovery_mode=args.discovery_mode,
            provider_meta=provider_meta,
        )
        prompt = build_start_prompt(
            warehouse=warehouse,
            run_dir=run_dir,
            analysis_reports=args.analysis_report,
            objective=args.objective,
            keywords=args.keywords,
            target_datasets=target_datasets,
            max_rows_per_dataset=max_rows,
            discovery_mode=args.discovery_mode,
            extra_message=args.message,
        )
        if not args.dry_run:
            prompt_path = run_dir / "worker_prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            (run_dir / "policy.md").write_text(
                _policy_text().format(warehouse=str(warehouse), max_rows_per_dataset=max_rows),
                encoding="utf-8",
            )
            if not args.foreground:
                return _spawn_background(
                    run_dir=run_dir,
                    warehouse=warehouse,
                    prompt_path=prompt_path,
                    timeout=args.timeout,
                    model=args.model or "",
                )
        return _run_worker(
            run_dir=run_dir,
            prompt=prompt,
            prov=prov,
            provider_meta=provider_meta,
            timeout=args.timeout,
            dry_run=args.dry_run,
        )

    if args.agent_command == "resume":
        state = _json_read(run_dir / STATE_FILE)
        if not state:
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_RUN_NOT_FOUND",
                f"dataset-acquisition-agent run not found: {run_dir}",
                hint="Use `dataset-acquisition-agent start --run ...` first.",
                exit_code=2,
            )
        warehouse = Path(state.get("warehouse") or root or "").expanduser().resolve()
        prov, provider_meta = _resolve_provider(warehouse, args.model or state.get("provider", {}).get("model_pool_name"))
        thread_id = str(state.get("thread_id") or "")
        if not thread_id and not args.dry_run:
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_THREAD_MISSING",
                f"run has no saved Codex thread_id: {run_dir}",
                hint="Start a new worker, or use --dry-run to inspect the resume prompt.",
                exit_code=2,
            )
        prompt = build_resume_prompt(run_dir=run_dir, message=args.message)
        if not args.dry_run:
            prompt_path = run_dir / "resume_prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            (run_dir / "policy.md").write_text(
                _policy_text().format(
                    warehouse=str(warehouse),
                    max_rows_per_dataset=state.get("max_rows_per_dataset") or DEFAULT_MAX_ROWS_PER_DATASET,
                ),
                encoding="utf-8",
            )
            if not args.foreground:
                return _spawn_background(
                    run_dir=run_dir,
                    warehouse=warehouse,
                    prompt_path=prompt_path,
                    timeout=args.timeout,
                    model=args.model or state.get("provider", {}).get("model_pool_name", ""),
                    thread_id=thread_id,
                )
        return _run_worker(
            run_dir=run_dir,
            prompt=prompt,
            prov=prov,
            provider_meta=provider_meta,
            timeout=args.timeout,
            thread_id=thread_id,
            dry_run=args.dry_run,
        )

    if args.agent_command == "worker-run":
        state = _json_read(run_dir / STATE_FILE)
        if not state:
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_RUN_NOT_FOUND",
                f"dataset-acquisition-agent run not found: {run_dir}",
                hint="worker-run is internal; use start/resume from the outer process.",
                exit_code=2,
            )
        warehouse = Path(state.get("warehouse") or root or "").expanduser().resolve()
        prov, provider_meta = _resolve_provider(warehouse, args.model or state.get("provider", {}).get("model_pool_name"))
        prompt_path = Path(args.prompt)
        if not prompt_path.exists():
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_PROMPT_NOT_FOUND",
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
