from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from loopai.agents.Obtainer.datamixer import codex
from loopai.agents.Obtainer.datamixer.models import ModelPool

from .errors import ObtainerCliError


DEFAULT_TARGET_RECORDS = 100000
STATE_FILE = "thread.json"
STATUS_FILE = "status.json"


def _json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _json_read(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _workspace() -> Path:
    runner = codex.runner_dir()
    return runner.parent if runner else Path.cwd()


def _resolve_provider(root: Path, model: str | None) -> tuple[dict, dict]:
    if model:
        spec = ModelPool(root).get(model)
        prov = codex.provider_from_model(spec)
        return prov, {
            "source": "model_pool",
            "model_pool_name": model,
            "base_url": prov["base_url"],
            "model": prov["model"],
            "api_key_source": "model_pool",
        }
    base_url = os.environ.get("CODEX_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL")
    model_name = os.environ.get("CODEX_MODEL") or os.environ.get("DEEPSEEK_MODEL")
    api_key = os.environ.get("CODEX_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    if not base_url or not model_name:
        raise ObtainerCliError(
            "SFT_EXPORT_AGENT_MODEL_REQUIRED",
            "sft-export-agent requires --model or CODEX_BASE_URL/CODEX_MODEL in the environment",
            hint="Register a model with `dm model add` and pass --model, or export CODEX_BASE_URL/CODEX_MODEL.",
            exit_code=2,
        )
    prov = {"base_url": base_url, "api_key": api_key, "model": model_name}
    return prov, {
        "source": "env",
        "base_url": base_url,
        "model": model_name,
        "api_key_source": "env" if api_key else "empty",
    }


def _policy_text() -> str:
    return """# SFT export worker policy

You are the isolated inner Codex SDK worker for an Obtainer DataMixer SFT export.

Hard rules:

1. All lakehouse operations must use this command surface only:
   python3 -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} <datamixer-command> --json
2. Do not use legacy Obtainer lake/table/sample/index commands.
3. Final training data must be produced by DataMixer `recipe export`; never concatenate samples manually.
4. For Alpaca SFT, final JSONL training rows must contain exactly:
   instruction, input, output
   No `_dm`, `source_uri`, `source_dataset_id`, `split`, `question`, `answer`, `text`, or other training-row keys.
   Provenance belongs in the manifest/final_report, not in the training JSONL rows.
5. The recipe must declare `recipe.export.schema.fields`.
6. For Alpaca output mapping:
   - `instruction.sources` may include instruction, INSTRUCTION, question, prompt.
   - `input` is optional and may default to "".
   - `output.sources` must only include real response/answer keys such as output,
     RESPONSE, answer, solution, result.
   - `output.sources` must not include text, raw_content, content, prompt,
     question, instruction, input, or any whole-record fallback.
7. If records store a conversation or Q/A pair inside one text field, normalize
   them first with DataMixer/DataFlow into explicit instruction/output keys, or
   exclude that bucket. Do not use the whole text as output.
8. Before export, run status, stats, dataset list, sample/key inspection,
   recipe validate, recipe plan, and recipe preview.
9. If no explicit SFT size is provided, target at least 100000 records.
10. Export with `recipe export --snapshot`.
11. After export, independently validate all JSONL shards:
    - total record count
    - every row has non-empty instruction and output
    - every row has input as a string
    - every row key set is exactly instruction/input/output
    - instruction != output for every row
    - output was not sourced from text/raw_content/content fallback
    - manifest has snapshot_id, dataset_digest, recipe_fingerprint, export_schema
12. If validation fails, do not mark ok=true. Patch the recipe, normalize data,
    exclude invalid buckets, or write a blocker final_report with exact reasons.
13. Do not read or print secret/key files.
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
    export_format: str,
    target_records: int,
    export_out: Path,
    extra_message: str,
) -> str:
    policy = _policy_text().format(warehouse=str(warehouse))
    return f"""{policy}

# Task

Read the Analyzer report(s), inspect the existing DataMixer warehouse, and
create a production SFT export through DataMixer recipe export.

Analyzer report paths:
{_analysis_block(analysis_reports)}
Warehouse:
- {warehouse}

Run directory:
- {run_dir}

Requested final format:
- {export_format}

Target records:
- {target_records}

Export output directory:
- {export_out}

Expected artifacts:
- recipe: {run_dir}/recipe/
- export: {export_out}
- final report: {run_dir}/final_report.json
- worker command summaries and validation evidence in final_report.json

Extra caller instruction:
{extra_message or "- none"}

Proceed end-to-end. Return a concise JSON object in the final response with
ok, final_report, export_path, snapshot_id, dataset_digest, and blockers.
"""


def build_resume_prompt(*, run_dir: Path, message: str) -> str:
    return f"""{_policy_text().format(warehouse='the warehouse recorded in thread.json')}

# Resume task

Continue the SFT export worker run recorded at:
- {run_dir}

Read thread.json, status.json, final_report.json if present, the current recipe,
manifest, and logs. Apply this caller instruction:

{message}

Keep the same hard policy. If a previous export violated policy, leave it
unused, patch the recipe or data processing, re-run validate/plan/preview/export,
and write a new final_report.json. Return concise JSON in the final response.
"""


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="loopai-obtainercli dm sft-export-agent")
    sub = parser.add_subparsers(dest="agent_command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--run", required=True, help="run directory for worker state and artifacts")
    start.add_argument("--analysis-report", action="append", default=[])
    start.add_argument("--format", default="alpaca", choices=["alpaca"])
    start.add_argument("--target-records", type=int, default=DEFAULT_TARGET_RECORDS)
    start.add_argument("--out", default="")
    start.add_argument("--model", default="")
    start.add_argument("--timeout", type=int, default=1800)
    start.add_argument("--message", default="")
    start.add_argument("--dry-run", action="store_true", help="write prompt/state only; do not launch Codex SDK")
    start.add_argument("--foreground", action="store_true", help="run the inner Codex worker synchronously")
    start.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    resume = sub.add_parser("resume")
    resume.add_argument("--run", required=True)
    resume.add_argument("--message", required=True)
    resume.add_argument("--model", default="")
    resume.add_argument("--timeout", type=int, default=1800)
    resume.add_argument("--dry-run", action="store_true")
    resume.add_argument("--foreground", action="store_true", help="run the inner Codex worker synchronously")
    resume.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    status = sub.add_parser("status")
    status.add_argument("--run", required=True)
    status.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    worker = sub.add_parser("worker-run", help=argparse.SUPPRESS)
    worker.add_argument("--run", required=True)
    worker.add_argument("--prompt", required=True)
    worker.add_argument("--timeout", type=int, default=1800)
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
        "command": "dm.sft-export-agent.status",
        "run_dir": str(run_dir),
        "status": status or {"state": "unknown"},
        "thread": {
            key: value
            for key, value in state.items()
            if key not in {"provider"}
        },
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
        "sft-export-agent",
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


def _save_initial_state(
    *,
    run_dir: Path,
    warehouse: Path,
    analysis_reports: list[str],
    export_out: Path,
    target_records: int,
    export_format: str,
    provider_meta: dict,
    mode: str,
) -> None:
    now = time.time()
    state = _json_read(run_dir / STATE_FILE)
    state.update({
        "created_at": state.get("created_at") or now,
        "updated_at": now,
        "mode": mode,
        "warehouse": str(warehouse),
        "analysis_reports": analysis_reports,
        "export_out": str(export_out),
        "target_records": target_records,
        "format": export_format,
        "provider": provider_meta,
    })
    _json_write(run_dir / STATE_FILE, state)
    _json_write(run_dir / STATUS_FILE, {
        "state": "prepared",
        "updated_at": now,
        "run_dir": str(run_dir),
        "warehouse": str(warehouse),
    })


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
    (run_dir / "policy.md").write_text(_policy_text().format(warehouse="see thread.json"), encoding="utf-8")

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
        "worker_result": {
            key: value
            for key, value in result.items()
            if key != "runner_result"
        },
    }


def run_agent(argv: list[str], *, root: str) -> dict:
    args = _parse(argv)
    run_dir = Path(args.run).expanduser().resolve()

    if args.agent_command == "status":
        return _status_payload(run_dir)

    if args.agent_command == "start":
        if not root:
            raise ObtainerCliError(
                "SFT_EXPORT_AGENT_ROOT_REQUIRED",
                "sft-export-agent start requires `dm --root <warehouse>`",
                hint="Pass `loopai-obtainercli dm --root /path/to/warehouse sft-export-agent start ...`.",
                exit_code=2,
            )
        warehouse = Path(root).expanduser().resolve()
        export_out = Path(args.out).expanduser().resolve() if args.out else (run_dir / "export")
        prov, provider_meta = _resolve_provider(warehouse, args.model or None)
        _save_initial_state(
            run_dir=run_dir,
            warehouse=warehouse,
            analysis_reports=args.analysis_report,
            export_out=export_out,
            target_records=max(args.target_records, DEFAULT_TARGET_RECORDS),
            export_format=args.format,
            provider_meta=provider_meta,
            mode="start",
        )
        prompt = build_start_prompt(
            warehouse=warehouse,
            run_dir=run_dir,
            analysis_reports=args.analysis_report,
            export_format=args.format,
            target_records=max(args.target_records, DEFAULT_TARGET_RECORDS),
            export_out=export_out,
            extra_message=args.message,
        )
        if not args.dry_run:
            prompt_path = run_dir / "worker_prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            (run_dir / "policy.md").write_text(_policy_text().format(warehouse=str(warehouse)), encoding="utf-8")
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
                "SFT_EXPORT_AGENT_RUN_NOT_FOUND",
                f"sft-export-agent run not found: {run_dir}",
                hint="Use `sft-export-agent start --run ...` first, or pass the correct --run path.",
                exit_code=2,
            )
        warehouse = Path(state.get("warehouse") or root or "").expanduser().resolve()
        prov, provider_meta = _resolve_provider(warehouse, args.model or state.get("provider", {}).get("model_pool_name"))
        thread_id = str(state.get("thread_id") or "")
        if not thread_id and not args.dry_run:
            raise ObtainerCliError(
                "SFT_EXPORT_AGENT_THREAD_MISSING",
                f"run has no saved Codex thread_id: {run_dir}",
                hint="Start a new worker, or use --dry-run to inspect the resume prompt.",
                exit_code=2,
            )
        prompt = build_resume_prompt(run_dir=run_dir, message=args.message)
        if not args.dry_run:
            prompt_path = run_dir / "resume_prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            (run_dir / "policy.md").write_text(_policy_text().format(warehouse=str(warehouse)), encoding="utf-8")
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
                "SFT_EXPORT_AGENT_RUN_NOT_FOUND",
                f"sft-export-agent run not found: {run_dir}",
                hint="worker-run is internal; use start/resume from the outer process.",
                exit_code=2,
            )
        warehouse = Path(state.get("warehouse") or root or "").expanduser().resolve()
        prov, provider_meta = _resolve_provider(warehouse, args.model or state.get("provider", {}).get("model_pool_name"))
        prompt_path = Path(args.prompt)
        if not prompt_path.exists():
            raise ObtainerCliError(
                "SFT_EXPORT_AGENT_PROMPT_NOT_FOUND",
                f"worker prompt not found: {prompt_path}",
                hint="Use start/resume to create worker prompt files.",
                exit_code=2,
            )
        prompt = prompt_path.read_text(encoding="utf-8")
        return _run_worker(
            run_dir=run_dir,
            prompt=prompt,
            prov=prov,
            provider_meta=provider_meta,
            timeout=args.timeout,
            thread_id=args.thread_id,
        )

    raise AssertionError(args.agent_command)
