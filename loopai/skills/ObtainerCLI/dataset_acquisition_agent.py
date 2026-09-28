from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import time
from pathlib import Path

from loopai.agents.Obtainer.datamixer import codex

from .errors import ObtainerCliError
from .download import MAX_BYTES_PER_DATASET
from .worker_runtime import _json_read, _json_write, _resolve_provider, _workspace

DEFAULT_TARGET_DATASETS = 3
MIN_TARGET_DATASETS = 2
DEFAULT_MAX_ROWS_PER_DATASET = 100000
DEFAULT_MAX_BYTES_PER_DATASET = MAX_BYTES_PER_DATASET
DEFAULT_MIN_TIMEOUT_SECONDS = 3600
DEFAULT_MAX_TIMEOUT_SECONDS = 6 * 3600
STATUS_FILE = "status.json"
STATE_FILE = "thread.json"


def _resolved_model_metadata(
    provider: dict,
    provider_meta: dict,
    *,
    requested_model: str = "",
) -> dict:
    """Make default-vs-override resolution explicit and durable."""
    meta = dict(provider_meta)
    resolved = str(meta.get("upstream_model_name") or provider.get("model") or "").strip()
    if not resolved:
        raise ObtainerCliError(
            "OBTAINERCLI_MODEL_RESOLUTION_FAILED",
            "could not resolve a Codex-default model for the acquisition worker",
            hint="Configure the Starter model pool with a Codex default before starting acquisition.",
            exit_code=2,
        )
    meta.update({
        "resolved_model": resolved,
        "model_source": "operator_override" if requested_model else "codex_default",
    })
    return meta


def _with_model_resolution(result: dict, provider_meta: dict) -> dict:
    """Expose the same resolution record from start, resume, and worker runs."""
    result.update({
        key: provider_meta.get(key, "")
        for key in ("resolved_model", "model_source")
    })
    return result


def _policy_text() -> str:
    return """# Hugging Face dataset acquisition

Find several relevant datasets on the Hugging Face Hub, favoring datasets
created or updated during 2025-2026. Use the Hub dataset catalog and metadata:
`huggingface_hub.HfApi().list_datasets(search=..., sort="lastModified", direction=-1)`
and inspect each candidate with `dataset_info`. Record the dataset id, config,
split, revision, `created_at`/`last_modified`, license, and why it matches the
objective. Prefer recent candidates; use an older one only when its metadata
shows it is the best available match.

For each selected dataset, use `datasets.load_dataset` (streaming when useful)
or the ObtainerCLI HF manifest downloader, then write a normalized JSONL file.
Keep the original row fields and add stable `source_dataset`, `source_uri`, and
`split` fields. Keep one JSONL and one ingest record per source dataset.

Use the DataMixer CLI for lake operations. Register every normalized JSONL as a
separate dataset, preserve its HF provenance, and run `index build` after all
selected datasets are ingested so the complete multi-dataset lake is searchable.

Ingest every accepted dataset with an explicit quality_level, normally L3 for
normalized source records: `ingest <dataset_name> --file <normalized.jsonl>
--content-key <content_field> --quality-level L3`. On a high-latency filesystem,
`--io-workers 16` permits bounded concurrent immutable blob writes while keeping
catalog transactions and sample ordering on one thread. Preserve source domain
values in raw_content/source_domain if they conflict with the requested lake domain.

Write `manifest/candidates.json`, `manifest/filtered_manifest.json`, and
`manifest/rejections.json` before downloading, then write download/ingest/index
reports and `final_report.json` under the run directory. The final report should
include selected dataset ids, freshness metadata, normalized JSONL paths, row
counts, ingest results, and index results.

DataMixer command form: `{python_executable} -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} ... --json`.
Honor the per-dataset row and byte limits supplied by the caller.
"""



def _worker_codex_home() -> Path:
    # Canonical location under outputs/obtainer/.codex/worker (obtainer-only).
    return _workspace() / "outputs" / "obtainer" / ".codex" / "worker"


def _apply_runtime_env(*, python_executable: str = "", node_bin_dir: str = "") -> None:
    if python_executable:
        os.environ["LOOPAI_PYTHON_EXECUTABLE"] = python_executable
    if node_bin_dir:
        os.environ["LOOPAI_NODE_BIN_DIR"] = node_bin_dir


def _worker_env(
    base: dict[str, str] | None = None,
    prov: dict | None = None,
    *,
    python_executable: str = "",
    node_bin_dir: str = "",
) -> dict[str, str]:
    env = dict(base or os.environ)
    for key in (
        "CODEX_THREAD_ID",
        "CODEX_USE_PROJECT_CONFIG",
        "TASK_ID",
        "task_id",
        "DB_PATH",
    ):
        env.pop(key, None)
    env["CODEX_HOME"] = str(_worker_codex_home())
    env["LOOPAI_WORKER_KIND"] = "dataset-acquisition-agent"
    worker_python = python_executable or codex.loopai_python_executable()
    env["LOOPAI_PYTHON_EXECUTABLE"] = worker_python
    env["PATH"] = codex.runner_process_path(worker_python, env.get("PATH"))
    if node_bin_dir:
        env["LOOPAI_NODE_BIN_DIR"] = node_bin_dir
        entries = [node_bin_dir, *env["PATH"].split(os.pathsep)]
        env["PATH"] = os.pathsep.join(dict.fromkeys(filter(None, entries)))
    from loopai.utils.hf_endpoints import DEFAULT_HF_ENDPOINTS

    hf_endpoint = (
        env.get("HF_ENDPOINT")
        or env.get("HF_HUB_ENDPOINT")
        or DEFAULT_HF_ENDPOINTS[0]
    )
    env["HF_ENDPOINT"] = hf_endpoint
    env["HF_HUB_ENDPOINT"] = hf_endpoint
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("STARTER_CONFIG", (base or os.environ).get("STARTER_CONFIG", ""))
    return env


@contextlib.contextmanager
def _worker_environ(prov: dict | None = None):
    previous = os.environ.copy()
    os.environ.clear()
    os.environ.update(_worker_env(previous, prov=prov))
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _pid_alive(pid: object) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _active_run_status(run_dir: Path) -> dict | None:
    status = _json_read(run_dir / STATUS_FILE)
    if isinstance(status, dict) and _pid_alive(status.get("pid")):
        return status
    return None


def _record_thread_started(run_dir: Path, payload: dict) -> None:
    if payload.get("type") != "event":
        return
    event = payload.get("event")
    if not isinstance(event, dict):
        return
    if event.get("type") != "thread.started" or not event.get("thread_id"):
        return
    thread_id = str(event["thread_id"])
    state = _json_read(run_dir / STATE_FILE)
    if state.get("thread_id") != thread_id:
        state["thread_id"] = thread_id
        state["updated_at"] = time.time()
    _json_write(run_dir / STATE_FILE, state)
    status = _json_read(run_dir / STATUS_FILE)
    status["thread_id"] = thread_id
    status["updated_at"] = time.time()
    status.setdefault("state", "running")
    _json_write(run_dir / STATUS_FILE, status)


def _analysis_block(paths: list[str]) -> str:
    if not paths:
        return "- No analysis report paths were provided.\n"
    return "\n".join(f"- {p}" for p in paths) + "\n"


def _default_timeout_for_target(target_datasets: int) -> int:
    target = max(int(target_datasets or DEFAULT_TARGET_DATASETS), DEFAULT_TARGET_DATASETS)
    return min(
        DEFAULT_MAX_TIMEOUT_SECONDS,
        max(DEFAULT_MIN_TIMEOUT_SECONDS, 1800 + target * 180),
    )


def _resolve_timeout(requested_timeout: int, *, target_datasets: int) -> int:
    if requested_timeout and requested_timeout > 0:
        return requested_timeout
    return _default_timeout_for_target(target_datasets)


def _compact_runner_warning(message: str) -> str:
    text = str(message or "").strip()
    marker = "timed out after "
    if marker in text:
        tail = text[text.rfind(marker):].strip().strip("'\"")
        return f"Codex runner {tail}"
    if len(text) > 1000:
        return text[:1000].rstrip() + "..."
    return text


def build_start_prompt(
    *,
    warehouse: Path,
    run_dir: Path,
    analysis_reports: list[str],
    objective: str,
    keywords: str,
    target_datasets: int,
    max_rows_per_dataset: int,
    max_bytes_per_dataset: int,
    extra_message: str,
) -> str:
    return f"""{_policy_text().format(
        warehouse=str(warehouse),
        max_rows_per_dataset=max_rows_per_dataset,
        max_bytes_per_dataset=max_bytes_per_dataset,
        python_executable=codex.loopai_python_executable(),
    )}

# Acquisition task

Search the Hugging Face Hub for {target_datasets} relevant datasets for this
objective. Start with `HfApi.list_datasets(search=..., sort="lastModified",
direction=-1)`, inspect each result with `HfApi.dataset_info`, and prioritize
datasets whose `created_at` or `lastModified` is in 2025 or 2026. Use the
dataset page at `https://huggingface.co/datasets/<id>` to confirm the card,
configs, splits, revision and license. Record the freshness evidence and
selection reason for every candidate.

Download the selected datasets with `datasets.load_dataset` or the HF manifest
downloader, normalize each source split to its own JSONL file, then register
each JSONL as a separate DataMixer dataset. Preserve the original fields and
add `source_dataset`, `source_uri`, and `split` fields. Run `index build` after
all datasets are ingested so the complete multi-dataset lake is indexed.

Analyzer report paths:
{_analysis_block(analysis_reports)}
Objective: {objective or 'Infer from Analyzer report.'}
Keywords: {keywords or 'Infer from Analyzer report.'}
Target datasets: {target_datasets}
Extra caller instruction: {extra_message or '- none'}

Write `manifest/candidates.json`, `manifest/filtered_manifest.json`, and
`manifest/rejections.json` before downloading. Keep download, ingest, and index
reports plus `final_report.json` under the run directory. Honor the per-dataset
row and byte limits: {max_rows_per_dataset} rows and
{max_bytes_per_dataset} bytes.
"""



def build_resume_prompt(*, run_dir: Path, message: str) -> str:
    return f"""{_policy_text().format(warehouse='the warehouse recorded in thread.json', max_rows_per_dataset=DEFAULT_MAX_ROWS_PER_DATASET, max_bytes_per_dataset=DEFAULT_MAX_BYTES_PER_DATASET, python_executable=codex.loopai_python_executable())}

# Resume task

Continue the dataset acquisition worker run recorded at:
- {run_dir}

Read the existing run state, HF manifests, normalized JSONL files, download /
ingest / index reports, and logs. Apply this caller instruction:

{message}

Continue the HF search/acquisition, normalization, multi-dataset ingest, and
index build from the last consistent step. Return concise JSON.
"""


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="loopai-obtainercli dm dataset-acquisition-agent")
    sub = parser.add_subparsers(dest="agent_command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--run", required=True)
    start.add_argument("--analysis-report", action="append", default=[])
    start.add_argument("--objective", default="")
    start.add_argument("--keywords", default="")
    start.add_argument(
        "--target-datasets",
        type=int,
        default=DEFAULT_TARGET_DATASETS,
        help="number of Hugging Face datasets to collect (minimum 2)",
    )
    start.add_argument(
        "--max-rows-per-dataset",
        type=int,
        default=DEFAULT_MAX_ROWS_PER_DATASET,
        help="maximum rows to write per dataset; 0 and oversized values are capped",
    )
    start.add_argument(
        "--max-bytes-per-dataset",
        type=int,
        default=DEFAULT_MAX_BYTES_PER_DATASET,
        help="maximum local JSONL output bytes per dataset; partial files are kept and reported when capped",
    )
    start.add_argument("--model", default="")
    start.add_argument("--timeout", type=int, default=0, help="Codex worker timeout in seconds; 0 means scale by target datasets")
    start.add_argument("--message", default="")
    start.add_argument("--python-executable", default="", help="Python executable for the isolated worker")
    start.add_argument("--node-bin-dir", default="", help="Directory containing node/corepack for codex-runner")
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--foreground", action="store_true")
    start.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    resume = sub.add_parser("resume")
    resume.add_argument("--run", required=True)
    resume.add_argument("--message", required=True)
    resume.add_argument("--model", default="")
    resume.add_argument("--timeout", type=int, default=0, help="Codex worker timeout in seconds; 0 means reuse scaled run default")
    resume.add_argument("--python-executable", default="", help="Python executable for the isolated worker")
    resume.add_argument("--node-bin-dir", default="", help="Directory containing node/corepack for codex-runner")
    resume.add_argument("--dry-run", action="store_true")
    resume.add_argument("--foreground", action="store_true")
    resume.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    status = sub.add_parser("status")
    status.add_argument("--run", required=True)
    status.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    worker = sub.add_parser("worker-run", help=argparse.SUPPRESS)
    worker.add_argument("--run", required=True)
    worker.add_argument("--prompt", required=True)
    worker.add_argument("--timeout", type=int, default=0)
    worker.add_argument("--thread-id", default="")
    worker.add_argument("--model", default="")
    worker.add_argument("--python-executable", default="", help=argparse.SUPPRESS)
    worker.add_argument("--node-bin-dir", default="", help=argparse.SUPPRESS)
    worker.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _validate_successful_run_artifacts(run_dir: Path) -> dict:
    # The SDK worker must leave a report, a resolved runtime model, and the
    # multi-dataset/index artifacts promised by the acquisition contract.
    state = _json_read(run_dir / STATE_FILE)
    _json_write(run_dir / STATE_FILE, state)
    final_report = _json_read(run_dir / "final_report.json")
    issues: list[dict] = []
    if not final_report:
        issues.append({"code": "final_report_missing", "artifact": "final_report.json"})
    elif final_report.get("ok") is not True:
        issues.append({"code": "final_report_not_ok"})
    if not state.get("resolved_model"):
        issues.append({"code": "resolved_model_missing", "artifact": STATE_FILE})
    datasets = final_report.get("datasets_ingested")
    if datasets is None:
        datasets = final_report.get("datasets")
    if isinstance(datasets, list):
        dataset_count = len(datasets)
    else:
        try:
            dataset_count = int(datasets or 0)
        except (TypeError, ValueError):
            dataset_count = 0
    if dataset_count < 2:
        issues.append({"code": "multiple_datasets_missing", "minimum": 2})
    index_result = (
        final_report.get("index")
        or final_report.get("index_result")
        or final_report.get("index_build")
        or final_report.get("index_stats")
    )
    if not index_result and final_report.get("index_built") is not True:
        issues.append({"code": "index_build_missing"})
    evidence = {
        "ok": not issues,
        "resolved_model": state.get("resolved_model") or "",
        "model_source": state.get("model_source") or "",
        "issues": issues,
        "warnings": [],
    }
    _json_write(run_dir / "acceptance_report.json", evidence)
    if final_report.get("ok") is True:
        final_report.update({
            "resolved_model": evidence["resolved_model"],
            "model_source": evidence["model_source"],
            "acquisition_acceptance": evidence,
        })
        _json_write(run_dir / "final_report.json", final_report)
    return evidence



def _status_payload(run_dir: Path) -> dict:
    status = _json_read(run_dir / STATUS_FILE)
    state = _json_read(run_dir / STATE_FILE)
    if state:
        _json_write(run_dir / STATE_FILE, state)
    final_report = _json_read(run_dir / "final_report.json")
    active_pid = status.get("pid") if isinstance(status, dict) else None
    worker_alive = _pid_alive(active_pid) if active_pid else False
    if (
        final_report.get("ok") is True
        and status.get("state") != "completed"
        and not worker_alive
    ):
        _complete_from_successful_final_report(
            run_dir,
            thread_id=str(state.get("thread_id") or status.get("thread_id") or ""),
            runner_warning=str(status.get("error") or ""),
        )
        status = _json_read(run_dir / STATUS_FILE)
    elif isinstance(status.get("runner_warning"), str):
        compact_warning = _compact_runner_warning(status["runner_warning"])
        if compact_warning != status["runner_warning"]:
            status["runner_warning"] = compact_warning
            status["updated_at"] = time.time()
            _json_write(run_dir / STATUS_FILE, status)
    pid = status.get("pid") if isinstance(status, dict) else None
    if pid:
        status["process_alive"] = _pid_alive(pid)
    payload = {
        "ok": True,
        "command": "dm.dataset-acquisition-agent.status",
        "run_dir": str(run_dir),
        "status": status or {"state": "unknown"},
        "thread": {key: value for key, value in state.items() if key != "provider"},
        "final_report": final_report or None,
    }
    if (
        status.get("state") in {"background_started", "running"}
        and pid
        and not status.get("process_alive")
        and final_report.get("ok") is not True
    ):
        status.update({
            "state": "failed",
            "updated_at": time.time(),
            "error": (
                "acquisition worker exited with ok=false in final_report.json"
                if final_report else
                "acquisition worker exited before writing final_report.json"
            ),
        })
        _json_write(run_dir / STATUS_FILE, status)
        payload["status"] = status
    if payload["status"].get("state") == "failed" or payload["status"].get("worker_ok") is False:
        raise ObtainerCliError(
            "DATASET_ACQUISITION_AGENT_FAILED",
            str(payload["status"].get("error") or "dataset acquisition worker failed"),
            hint="Inspect the run status, final report, and worker logs; do not use a direct-download fallback.",
            exit_code=1,
            details=payload,
        )
    return payload


def _complete_from_successful_final_report(
    run_dir: Path,
    *,
    thread_id: str = "",
    runner_warning: str = "",
) -> dict | None:
    final_report = _json_read(run_dir / "final_report.json")
    if final_report.get("ok") is not True:
        return None
    status = _json_read(run_dir / STATUS_FILE)
    try:
        started_at = float(status.get("worker_started_at") or 0.0)
        report_mtime = Path(run_dir / "final_report.json").stat().st_mtime
    except (OSError, TypeError, ValueError):
        started_at = 0.0
        report_mtime = 0.0
    if started_at and report_mtime < started_at - 1.0:
        # final_report predates the current worker generation (e.g. resume);
        # do not mark completed until the running worker rewrites it.
        return None
    acceptance = _validate_successful_run_artifacts(run_dir)
    if not acceptance.get("ok"):
        _json_write(run_dir / STATUS_FILE, {
            "state": "failed",
            "updated_at": time.time(),
            "thread_id": thread_id or None,
            "final_report": str(run_dir / "final_report.json"),
            "worker_ok": False,
            "error": "acquisition acceptance failed: " + ", ".join(
                str(item.get("code") or "unknown") for item in acceptance.get("issues", [])
            ),
            "acceptance_report": str(run_dir / "acceptance_report.json"),
        })
        return None
    state = _json_read(run_dir / STATE_FILE)
    saved_thread_id = state.get("thread_id") or thread_id or None
    status = {
        "state": "completed",
        "updated_at": time.time(),
        "thread_id": saved_thread_id,
        "final_report": str(run_dir / "final_report.json"),
        "worker_ok": True,
    }
    if runner_warning:
        runner_warning = _compact_runner_warning(runner_warning)
        status["runner_warning"] = runner_warning
    _json_write(run_dir / STATUS_FILE, status)
    return {
        "ok": True,
        "status": "completed",
        "run_dir": str(run_dir),
        "thread_id": saved_thread_id,
        "final_report": str(run_dir / "final_report.json"),
        "worker_result": {
            "ok": True,
            "final_report": str(run_dir / "final_report.json"),
            "warning": runner_warning or None,
        },
    }


def _spawn_background(
    *,
    run_dir: Path,
    warehouse: Path,
    prompt_path: Path,
    timeout: int,
    model: str,
    thread_id: str = "",
    python_executable: str = "",
    node_bin_dir: str = "",
) -> dict:
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / "worker_stdout.ndjson"
    stderr_path = logs / "worker_stderr.log"
    worker_python = python_executable or codex.loopai_python_executable()
    cmd = [
        worker_python,
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
    if python_executable:
        cmd.extend(["--python-executable", python_executable])
    if node_bin_dir:
        cmd.extend(["--node-bin-dir", node_bin_dir])
    env = _worker_env(
        python_executable=worker_python,
        node_bin_dir=node_bin_dir,
    )
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
        "worker_started_at": time.time(),
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
            max_bytes_per_dataset=DEFAULT_MAX_BYTES_PER_DATASET,
            python_executable=codex.loopai_python_executable(),
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

    status = _json_read(run_dir / STATUS_FILE)
    status.update({
        "state": "running",
        "updated_at": time.time(),
        "worker_started_at": time.time(),
        "prompt_path": str(prompt_path),
        "thread_id": thread_id or None,
    })
    _json_write(run_dir / STATUS_FILE, status)
    try:
        with _worker_environ(prov):
            result = codex.run_via_sdk(
                prompt,
                prov,
                cwd=str(_workspace()),
                timeout=timeout,
                thread_id=thread_id or None,
                on_event=lambda payload: _record_thread_started(run_dir, payload),
            )
    except KeyboardInterrupt:
        _json_write(run_dir / STATUS_FILE, {
            "state": "interrupted",
            "updated_at": time.time(),
            "error": "KeyboardInterrupt",
            "thread_id": thread_id or None,
            "prompt_path": str(prompt_path),
        })
        raise
    except Exception as exc:
        completed = _complete_from_successful_final_report(
            run_dir,
            thread_id=thread_id,
            runner_warning=str(exc),
        )
        if completed is not None:
            return completed
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
    state["resolved_model"] = provider_meta.get("resolved_model", state.get("resolved_model", ""))
    state["model_source"] = provider_meta.get("model_source", state.get("model_source", ""))
    _json_write(run_dir / STATE_FILE, state)
    _json_write(run_dir / "logs" / f"codex_result_{int(time.time())}.json", result)
    final_report = _json_read(run_dir / "final_report.json")
    if final_report.get("ok") is not True:
        error = (
            "acquisition worker exited without final_report.json"
            if not final_report
            else "acquisition worker reported ok=false in final_report.json"
        )
        _json_write(run_dir / STATUS_FILE, {
            "state": "failed",
            "updated_at": time.time(),
            "thread_id": state.get("thread_id") or thread_id or None,
            "final_report": str(run_dir / "final_report.json") if final_report else None,
            "worker_ok": False,
            "error": error,
        })
        raise ObtainerCliError(
            "DATASET_ACQUISITION_AGENT_FAILED",
            error,
            hint="Inspect final_report.json and worker logs; do not use a direct-download fallback.",
            exit_code=1,
        )
    acceptance = _validate_successful_run_artifacts(run_dir)
    if not acceptance.get("ok"):
        error = "acquisition acceptance failed: " + ", ".join(
            str(item.get("code") or "unknown") for item in acceptance.get("issues", [])
        )
        _json_write(run_dir / STATUS_FILE, {
            "state": "failed",
            "updated_at": time.time(),
            "thread_id": state.get("thread_id") or thread_id or None,
            "final_report": str(run_dir / "final_report.json"),
            "worker_ok": False,
            "error": error,
            "acceptance_report": str(run_dir / "acceptance_report.json"),
        })
        raise ObtainerCliError(
            "DATASET_ACQUISITION_AGENT_ACCEPTANCE_FAILED",
            error,
            hint="Inspect acceptance_report.json; do not continue to download/export fallback paths.",
            exit_code=1,
            details=acceptance,
        )
    _json_write(run_dir / STATUS_FILE, {
        "state": "completed",
        "updated_at": time.time(),
        "thread_id": state.get("thread_id") or thread_id or None,
        "final_report": str(run_dir / "final_report.json") if final_report else None,
        "worker_ok": True,
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
    max_bytes_per_dataset: int,
    objective: str,
    keywords: str,
    provider_meta: dict,
    python_executable: str = "",
    node_bin_dir: str = "",
    task_id: str = "",
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
        "max_bytes_per_dataset": max_bytes_per_dataset,
        "objective": objective,
        "keywords": keywords,
        "provider": provider_meta,
        "resolved_model": provider_meta.get("resolved_model", ""),
        "model_source": provider_meta.get("model_source", ""),
        "task_id": task_id or state.get("task_id", ""),
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
        "warehouse": str(warehouse),
    })


def run_agent(argv: list[str], *, root: str, task_id: str = "") -> dict:
    args = _parse(argv)
    run_dir = Path(args.run).expanduser().resolve()
    if getattr(args, "python_executable", "") or getattr(args, "node_bin_dir", ""):
        _apply_runtime_env(
            python_executable=getattr(args, "python_executable", ""),
            node_bin_dir=getattr(args, "node_bin_dir", ""),
        )

    if args.agent_command == "status":
        return _status_payload(run_dir)

    if args.agent_command == "start":
        if not args.dry_run:
            active = _active_run_status(run_dir)
            if active:
                raise ObtainerCliError(
                    "DATASET_ACQUISITION_AGENT_RUN_ACTIVE",
                    f"dataset-acquisition-agent run is already active: {run_dir}",
                    hint="Poll status or stop the active worker before starting another worker for the same run.",
                    exit_code=2,
                )
        if not root:
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_ROOT_REQUIRED",
                "dataset-acquisition-agent start requires `dm --root <warehouse>`",
                hint="Pass `loopai-obtainercli dm --root /path/to/warehouse dataset-acquisition-agent start ...`.",
                exit_code=2,
            )
        warehouse = Path(root).expanduser().resolve()
        if warehouse.is_file():
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_WAREHOUSE_INVALID",
                f"dataset-acquisition-agent requires a DataMixer warehouse directory, not a file: {warehouse}",
                hint="Use `dm --lake .datamixer/lake.yaml dataset-acquisition-agent start ...` or pass the directory containing datamixer.toml.",
                exit_code=2,
            )
        if not args.dry_run and not (warehouse / "datamixer.toml").is_file():
            raise ObtainerCliError(
                "LAKE_NOT_LOADED",
                f"DataMixer lake is not loaded: no initialized warehouse at {warehouse}",
                hint=(
                    "Load or initialize the DataMixer lake before starting the worker: "
                    "`dm lake init --root <lake-root>` or `dm lake load --warehouse <warehouse>`."
                ),
                exit_code=2,
            )
        max_rows = args.max_rows_per_dataset
        if max_rows <= 0 or max_rows > DEFAULT_MAX_ROWS_PER_DATASET:
            max_rows = DEFAULT_MAX_ROWS_PER_DATASET
        max_bytes = args.max_bytes_per_dataset
        if max_bytes <= 0 or max_bytes > DEFAULT_MAX_BYTES_PER_DATASET:
            max_bytes = DEFAULT_MAX_BYTES_PER_DATASET
        target_datasets = max(args.target_datasets, MIN_TARGET_DATASETS)
        timeout = _resolve_timeout(args.timeout, target_datasets=target_datasets)
        python_executable = args.python_executable or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
        prov, provider_meta = _resolve_provider(warehouse, args.model or None)
        provider_meta = _resolved_model_metadata(
            prov, provider_meta, requested_model=args.model or ""
        )
        _save_initial_state(
            run_dir=run_dir,
            warehouse=warehouse,
            analysis_reports=args.analysis_report,
            target_datasets=target_datasets,
            max_rows_per_dataset=max_rows,
            max_bytes_per_dataset=max_bytes,
            objective=args.objective,
            keywords=args.keywords,
            provider_meta=provider_meta,
            python_executable=python_executable,
            node_bin_dir=node_bin_dir,
            task_id=task_id,
        )
        prompt = build_start_prompt(
            warehouse=warehouse,
            run_dir=run_dir,
            analysis_reports=args.analysis_report,
            objective=args.objective,
            keywords=args.keywords,
            target_datasets=target_datasets,
            max_rows_per_dataset=max_rows,
            max_bytes_per_dataset=max_bytes,
            extra_message=args.message,
        )
        if not args.dry_run:
            prompt_path = run_dir / "worker_prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            (run_dir / "policy.md").write_text(
                _policy_text().format(
                    warehouse=str(warehouse),
                    max_rows_per_dataset=max_rows,
                    max_bytes_per_dataset=max_bytes,
                    python_executable=codex.loopai_python_executable(),
                ),
                encoding="utf-8",
            )
            if not args.foreground:
                return _with_model_resolution(_spawn_background(
                    run_dir=run_dir,
                    warehouse=warehouse,
                    prompt_path=prompt_path,
                    timeout=timeout,
                    model=args.model or "",
                    python_executable=python_executable,
                    node_bin_dir=node_bin_dir,
                ), provider_meta)
        return _with_model_resolution(_run_worker(
            run_dir=run_dir,
            prompt=prompt,
            prov=prov,
            provider_meta=provider_meta,
            timeout=timeout,
            dry_run=args.dry_run,
        ), provider_meta)

    if args.agent_command == "resume":
        state = _json_read(run_dir / STATE_FILE)
        if not state:
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_RUN_NOT_FOUND",
                f"dataset-acquisition-agent run not found: {run_dir}",
                hint="Use `dataset-acquisition-agent start --run ...` first.",
                exit_code=2,
            )
        _json_write(run_dir / STATE_FILE, state)
        warehouse = Path(state.get("warehouse") or root or "").expanduser().resolve()
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        python_executable = args.python_executable or runtime.get("python_executable") or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or runtime.get("node_bin_dir") or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
        _apply_runtime_env(python_executable=python_executable, node_bin_dir=node_bin_dir)
        requested_model = args.model or str(state.get("provider", {}).get("model_pool_name") or "")
        prov, provider_meta = _resolve_provider(warehouse, requested_model or None)
        provider_meta = _resolved_model_metadata(
            prov, provider_meta,
            requested_model=args.model or "",
        )
        if not args.dry_run:
            active = _active_run_status(run_dir)
            if active:
                raise ObtainerCliError(
                    "DATASET_ACQUISITION_AGENT_RUN_ACTIVE",
                    f"dataset-acquisition-agent run is already active: {run_dir}",
                    hint="Poll status or stop the active worker before resuming this run.",
                    exit_code=2,
                )
        thread_id = str(state.get("thread_id") or "")
        timeout = _resolve_timeout(
            args.timeout,
            target_datasets=int(state.get("target_datasets") or DEFAULT_TARGET_DATASETS),
        )
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
                    max_bytes_per_dataset=state.get("max_bytes_per_dataset") or DEFAULT_MAX_BYTES_PER_DATASET,
                    python_executable=codex.loopai_python_executable(),
                ),
                encoding="utf-8",
            )
            if not args.foreground:
                return _with_model_resolution(_spawn_background(
                    run_dir=run_dir,
                    warehouse=warehouse,
                    prompt_path=prompt_path,
                    timeout=timeout,
                    model=args.model or state.get("provider", {}).get("model_pool_name", ""),
                    thread_id=thread_id,
                    python_executable=python_executable,
                    node_bin_dir=node_bin_dir,
                ), provider_meta)
        return _with_model_resolution(_run_worker(
            run_dir=run_dir,
            prompt=prompt,
            prov=prov,
            provider_meta=provider_meta,
            timeout=timeout,
            thread_id=thread_id,
            dry_run=args.dry_run,
        ), provider_meta)

    if args.agent_command == "worker-run":
        state = _json_read(run_dir / STATE_FILE)
        if not state:
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_RUN_NOT_FOUND",
                f"dataset-acquisition-agent run not found: {run_dir}",
                hint="worker-run is internal; use start/resume from the outer process.",
                exit_code=2,
            )
        _json_write(run_dir / STATE_FILE, state)
        warehouse = Path(state.get("warehouse") or root or "").expanduser().resolve()
        requested_model = args.model or str(state.get("provider", {}).get("model_pool_name") or "")
        prov, provider_meta = _resolve_provider(warehouse, requested_model or None)
        provider_meta = _resolved_model_metadata(
            prov, provider_meta,
            requested_model=args.model or "",
        )
        prompt_path = Path(args.prompt)
        if not prompt_path.exists():
            raise ObtainerCliError(
                "DATASET_ACQUISITION_AGENT_PROMPT_NOT_FOUND",
                f"worker prompt not found: {prompt_path}",
                hint="Use start/resume to create worker prompt files.",
                exit_code=2,
            )
        timeout = _resolve_timeout(
            args.timeout,
            target_datasets=int(state.get("target_datasets") or DEFAULT_TARGET_DATASETS),
        )
        return _with_model_resolution(_run_worker(
            run_dir=run_dir,
            prompt=prompt_path.read_text(encoding="utf-8"),
            prov=prov,
            provider_meta=provider_meta,
            timeout=timeout,
            thread_id=args.thread_id,
        ), provider_meta)

    raise AssertionError(args.agent_command)
