from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import time
from pathlib import Path

from loopai.agents.Obtainer.datamixer import codex

from .errors import ObtainerCliError
from .sft_export_agent import _json_read, _json_write, _resolve_provider, _workspace
from .download import MAX_BYTES_PER_DATASET

DEFAULT_TARGET_DATASETS = 1
DEFAULT_MAX_ROWS_PER_DATASET = 100000
DEFAULT_MAX_BYTES_PER_DATASET = MAX_BYTES_PER_DATASET
DEFAULT_MIN_TIMEOUT_SECONDS = 3600
DEFAULT_MAX_TIMEOUT_SECONDS = 6 * 3600
STATUS_FILE = "status.json"
STATE_FILE = "thread.json"


def _policy_text() -> str:
    return """# Dataset acquisition worker policy

You are LoopAI's dataset acquisition agent. Your job is to discover relevant
dataset candidates, prune unrelated sources, download selected datasets,
normalize records, write dataset cards, validate derived fields, ingest accepted
datasets into DataMixer, and write final_report.json.

Hard rules:

1. All lakehouse operations after downloaded files exist must use:
   {python_executable} -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} <datamixer-command> --json
2. Do not use legacy Obtainer lake/table/sample/index commands.
3. For every normal acquisition, start two complementary discovery streams at
   the same time and wait for both before deciding what to ingest:
   - Run Obtainer SearchAgent to discover hosted datasets and construct the
     provider-download candidate manifest.
   - Run DataMixer's registered `domain_data_acquisition` campaign (legacy alias
     `webcrawler_dm`) to collect authoritative vertical-domain resource pages as
     L1 raw HTML in the target warehouse.
   SearchAgent and WebAgent cover different source types; neither substitutes
   for the other. Keep their artifacts, failures, and accepted outputs separate
   in the final report. Do not make one stream wait for the other to start.
   Select an existing DataMixer model-pool name with `dm model list --json`
   before starting WebAgent. If no model is registered, record a
   `webagent_model_missing` blocker while continuing SearchAgent; never invent
   credentials or silently skip the required WebAgent stream. Do not begin
   `download manifest` until `webagent_start.json` exists, unless a concrete
   `webagent_model_missing`/launch blocker has been written to final_report.
4. Use Obtainer SearchAgent and `download manifest` as the acquisition
   bridge when appropriate:
   {python_executable} -m loopai.skills.ObtainerCLI.cli searchagent ...
   {python_executable} -m loopai.skills.ObtainerCLI.cli download manifest ...
   For multi-domain acquisition requests, first write a task JSON with one
   isolated task per capability domain (for example text2sql, math, code), then
   call SearchAgent once with `--task-json <path> --parallelism <n>`. Keep each
   task's objective and search_keywords domain-specific so one domain cannot
   crowd out another.
5. If direct web/Hugging Face/Kaggle discovery is more appropriate for the
   caller's instruction, write an equivalent manifest yourself and continue.
6. Before downloading, compare the candidate list against the original user
   request and Analyzer report. Remove clearly unrelated datasets and write
   both a filtered manifest and a rejection report with exact reasons.
7. Each single dataset is capped at {max_rows_per_dataset} rows and
   {max_bytes_per_dataset} output bytes. Do not bypass these caps. Smaller
   sampled downloads are allowed for broad acquisition, but record sampled_rows,
   rows_written, bytes_written, max_rows_effective, max_bytes_effective, and
   cap/truncation status in the manifest/report. For `download manifest`,
   `--limit` caps candidate items to try, `--max-rows` caps rows per dataset,
   and `--max-bytes-per-dataset` caps local JSONL output bytes per dataset. If
   the byte cap is reached, keep the partial JSONL and report `truncated`,
   `truncated_reason`, `rows_written`, and `bytes_written`.
8. Normalize each downloaded dataset to JSONL before ingest. Each row must
   preserve source_uri, source_dataset/source_dataset_id, split, and enough
   payload fields for later SFT/PT processing.
9. For every accepted dataset, write a Markdown dataset card before ingest and
   register it during ingest with `--dataset-card <path>`. The card must live in
   this run's manifest directory first and describe source, license, split,
   row count, original fields, derived fields, derivation rules, validation
   checks, intended training use, and known risks.
10. You may add dataset-specific derived fields during normalization, but only
   by adding fields. Never drop, overwrite, or rename original payload fields.
   The normalized JSONL must preserve the same row count as the selected source
   rows. For complex embedded formats, derive explicit training-ready fields:
   parse step traces into reasoning fields, flatten multi-turn conversations
   into messages/dialogue/instruction-response fields, combine question with
   options/evidence/schema blocks into prompt/input fields, and keep gold
   labels/answers as separate fields.
11. If derived fields are added, every derived field must be non-empty for every
   row. Pass `--derived-field <name>` for each derived field and
   `--source-row-count <n>` to `dm ingest` so DataMixer validates this before
   writing. If validation fails, fix the normalizer or reject the dataset; do
   not ingest partial rows.
12. Ingest every accepted dataset through DataMixer `ingest` or `agent-ingest`
   with complete tags: source platform, source dataset id, source URL or URI,
   license if known, language if known, domain, task_type, processing_level,
   quality_level, source_kind, split, loop_uuid/version_id when provided, and
   acquisition_run. Choose quality_level explicitly for every dataset: L1 for
   raw source downloads, L2 for extracted/parsed/basic-cleaned source data, L3
   for standard SFT/DPO/training samples, and L4 only for output explicitly
   refined by an internal data-lake pipeline. When uncertain, choose the lower
   applicable level and explain the uncertainty; never omit the parameter.
13. After ingest, run DataMixer status, dataset list, stats, representative query,
   and index build when useful for downstream recall.
14. Write final_report.json with SearchAgent and WebAgent commands/statuses,
    WebAgent campaign id and L1 datasets, candidates, filtered list, rejections,
    downloads, dataset card paths, derived field specs, validation outcomes,
    ingests, each dataset's selected quality_level and selection rationale,
    DataMixer command summaries, before/after counts, lineage/manifest paths,
    and blockers.
15. Do not mark ok=true if no dataset was ingested, if accepted datasets are
    unrelated to the request, if any accepted dataset lacks a registered md
    dataset card, if derived field validation failed, if row count changed
    during derivation, or if required source/provenance tags are missing.
16. Do not read or print secret/key files.
"""


def _worker_codex_home() -> Path:
    return _workspace() / "codex_home_worker"


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
    hf_endpoint = env.get("HF_ENDPOINT") or env.get("HF_HUB_ENDPOINT") or "https://hf-mirror.com"
    env["HF_ENDPOINT"] = hf_endpoint
    env["HF_HUB_ENDPOINT"] = hf_endpoint
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
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
    discovery_mode: str,
    extra_message: str,
) -> str:
    policy = _policy_text().format(
        warehouse=str(warehouse),
        max_rows_per_dataset=max_rows_per_dataset,
        max_bytes_per_dataset=max_bytes_per_dataset,
        python_executable=codex.loopai_python_executable(),
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

Per-dataset output byte cap:
- {max_bytes_per_dataset}

Discovery mode:
- {discovery_mode}

Required artifacts:
- candidates manifest: {run_dir}/manifest/candidates.json
- filtered manifest: {run_dir}/manifest/filtered_manifest.json
- rejections: {run_dir}/manifest/rejections.json
- SearchAgent task JSON: {run_dir}/manifest/tasks.json
- SearchAgent manifest: {run_dir}/manifest/searchagent/searchagent_manifest.json
- WebAgent launch result: {run_dir}/manifest/webagent_start.json
- WebAgent campaign status: {run_dir}/manifest/webagent_campaign_status.json
- dataset cards: {run_dir}/manifest/dataset_cards/*.md
- derived-field specs/validation: {run_dir}/manifest/derived_fields.json
- downloads: {run_dir}/downloads/
- ingest report: {run_dir}/manifest/ingest_results.json
- final report: {run_dir}/final_report.json

Required discovery procedure:
1. Create `{run_dir}/manifest/tasks.json` with a top-level `tasks` list. Run
   `{codex.loopai_python_executable()} -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} model list --json`
   and select one registered model name as `$WEBAGENT_MODEL`. If there is no
   registered model, write `webagent_model_missing` to the final report, then
   still run SearchAgent and report that the required WebAgent stream was blocked.
2. When `$WEBAGENT_MODEL` is available, launch SearchAgent and WebAgent in
   parallel. Use separate output files and wait for both PIDs; do not run one
   only after the other finishes:

```bash
(
  {codex.loopai_python_executable()} -m loopai.skills.ObtainerCLI.cli searchagent \
    --query "{objective or keywords or 'dataset acquisition'}" \
    --task-json {run_dir}/manifest/tasks.json \
    --output-root {run_dir}/manifest/searchagent \
    --parallelism 3 \
    --max-results-per-source {max(target_datasets, 5)} \
    --no-deepsearch \
    --json > {run_dir}/manifest/searchagent_start.json
) &
SEARCHAGENT_PID=$!
(
  {codex.loopai_python_executable()} -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} \
    webagent campaign start domain_data_acquisition \
    --query "{objective or keywords or 'dataset acquisition'}" \
    --dataset {run_dir.name}_web_l1 \
    --model "$WEBAGENT_MODEL" \
    --subquery-count {max(4, min(24, target_datasets))} \
    --workers 4 \
    --search-provider tavily \
    --json > {run_dir}/manifest/webagent_start.json
) &
WEBAGENT_PID=$!
wait "$SEARCHAGENT_PID"; SEARCHAGENT_EXIT=$?
wait "$WEBAGENT_PID"; WEBAGENT_EXIT=$?
```

   If `TAVILY_API_KEY` is unavailable, use `--search-provider auto` for the
   WebAgent command and record the provider choice. A failed stream is not a
   reason to discard successful output from the other stream.
3. Read `{run_dir}/manifest/searchagent/searchagent_manifest.json` and
   `{run_dir}/manifest/webagent_start.json`. Use `dm webagent campaign status
   <run-id> --json` to write `{run_dir}/manifest/webagent_campaign_status.json`.
   Preserve the WebAgent campaign id, selected URLs, L1 dataset names, and
   L1/L2/L3 counts if an automatic pipeline was requested. Do not copy WebAgent
   HTML into the provider download manifest; it is already in DataMixer.
4. Read `{run_dir}/manifest/searchagent/searchagent_manifest.json`. Copy or
   transform its `candidates`/`download_list` into `{run_dir}/manifest/candidates.json`.
   Do not inspect `{run_dir}/manifest/tasks/`; SearchAgent does not write there.
   Preserve SearchAgent metadata. Do not hand-write this file with `echo`.
   Keep an oversampled candidate pool: if SearchAgent produced
   `{max(target_datasets * 5, target_datasets + 10, 10)}` or fewer candidates,
   keep all of them; otherwise keep at least
   `{max(target_datasets * 5, target_datasets + 10, 10)}` relevant candidates
   and record every removed candidate with a concrete reason in
   `{run_dir}/manifest/rejections.json`.
5. Download only through this manifest command shape:

```bash
   {codex.loopai_python_executable()} -m loopai.skills.ObtainerCLI.cli download manifest \
     --manifest {run_dir}/manifest/candidates.json \
     --output-root {run_dir}/downloads \
     --limit {max(target_datasets * 5, target_datasets + 10, 10)} \
     --max-rows {max_rows_per_dataset} \
     --max-bytes-per-dataset {max_bytes_per_dataset} \
     --json
```
6. Wait for the download command to exit. Do not read
   `{run_dir}/downloads/download_results.json` while the command is still
   running. If the command does not exit successfully or the result file is
   missing, write an `ok=false` blocker with the command, status, exit code,
   stdout, and stderr; do not infer success from partial files.
7. After download completes, read `{run_dir}/downloads/download_results.json`.
   Use only non-empty `records_jsonl` paths from that report for ingest. Do not
   guess raw parquet, JSON, or nested download paths. A zero-byte JSONL file is
   not a successful download.
8. For each accepted downloaded JSONL, ingest with this exact DataMixer shape:
   {codex.loopai_python_executable()} -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} ingest <dataset_name> \
     --file <records_jsonl> \
     --dataset-card <dataset_card_md> \
     --source <source_platform> \
     --license <license_or_unknown> \
     --domain <domain> \
     --task-type <task_type> \
     --quality-level <L1|L2|L3|L4> \
     --processing-level raw \
     --source-kind <source_platform> \
     --source-uri <source_url_or_uri> \
     --split <split> \
     --tag source_dataset_id=<source_dataset_id> \
     --tag acquisition_run={run_dir.name} \
     --json
   `<dataset_name>` is required and must appear immediately after `ingest`.
   Do not omit `<dataset_name>`. Top-level
   `{codex.loopai_python_executable()} -m loopai.skills.ObtainerCLI.cli ingest`
   is invalid. Do not use non-existent ingest flags such as
   `--source-dataset-id` or `--source-url`; put those values in
   `--tag source_dataset_id=<source_dataset_id>` and `--source-uri ...`.

Extra caller instruction:
{extra_message or '- none'}

Proceed end-to-end. Return concise JSON with ok, final_report, datasets_ingested,
warehouse, and blockers.
"""


def build_resume_prompt(*, run_dir: Path, message: str) -> str:
    return f"""{_policy_text().format(warehouse='the warehouse recorded in thread.json', max_rows_per_dataset=DEFAULT_MAX_ROWS_PER_DATASET, max_bytes_per_dataset=DEFAULT_MAX_BYTES_PER_DATASET, python_executable=codex.loopai_python_executable())}

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
    start.add_argument("--discovery-mode", choices=["auto", "searchagent", "codex-web"], default="auto")
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


def _status_payload(run_dir: Path) -> dict:
    status = _json_read(run_dir / STATUS_FILE)
    state = _json_read(run_dir / STATE_FILE)
    final_report = _json_read(run_dir / "final_report.json")
    if final_report.get("ok") is True and status.get("state") != "completed":
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
    return {
        "ok": True,
        "command": "dm.dataset-acquisition-agent.status",
        "run_dir": str(run_dir),
        "status": status or {"state": "unknown"},
        "thread": {key: value for key, value in state.items() if key != "provider"},
        "final_report": final_report or None,
    }


def _complete_from_successful_final_report(
    run_dir: Path,
    *,
    thread_id: str = "",
    runner_warning: str = "",
) -> dict | None:
    final_report = _json_read(run_dir / "final_report.json")
    if final_report.get("ok") is not True:
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
    max_bytes_per_dataset: int,
    objective: str,
    keywords: str,
    discovery_mode: str,
    provider_meta: dict,
    python_executable: str = "",
    node_bin_dir: str = "",
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
        "discovery_mode": discovery_mode,
        "provider": provider_meta,
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


def run_agent(argv: list[str], *, root: str) -> dict:
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
                hint="Use `dm --lake .loopai/lake.yaml dataset-acquisition-agent start ...` or pass the directory containing datamixer.toml.",
                exit_code=2,
            )
        max_rows = args.max_rows_per_dataset
        if max_rows <= 0 or max_rows > DEFAULT_MAX_ROWS_PER_DATASET:
            max_rows = DEFAULT_MAX_ROWS_PER_DATASET
        max_bytes = args.max_bytes_per_dataset
        if max_bytes <= 0 or max_bytes > DEFAULT_MAX_BYTES_PER_DATASET:
            max_bytes = DEFAULT_MAX_BYTES_PER_DATASET
        target_datasets = max(args.target_datasets, DEFAULT_TARGET_DATASETS)
        timeout = _resolve_timeout(args.timeout, target_datasets=target_datasets)
        python_executable = args.python_executable or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
        prov, provider_meta = _resolve_provider(warehouse, args.model or None)
        _save_initial_state(
            run_dir=run_dir,
            warehouse=warehouse,
            analysis_reports=args.analysis_report,
            target_datasets=target_datasets,
            max_rows_per_dataset=max_rows,
            max_bytes_per_dataset=max_bytes,
            objective=args.objective,
            keywords=args.keywords,
            discovery_mode=args.discovery_mode,
            provider_meta=provider_meta,
            python_executable=python_executable,
            node_bin_dir=node_bin_dir,
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
            discovery_mode=args.discovery_mode,
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
                return _spawn_background(
                    run_dir=run_dir,
                    warehouse=warehouse,
                    prompt_path=prompt_path,
                    timeout=timeout,
                    model=args.model or "",
                    python_executable=python_executable,
                    node_bin_dir=node_bin_dir,
                )
        return _run_worker(
            run_dir=run_dir,
            prompt=prompt,
            prov=prov,
            provider_meta=provider_meta,
            timeout=timeout,
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
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        python_executable = args.python_executable or runtime.get("python_executable") or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or runtime.get("node_bin_dir") or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
        _apply_runtime_env(python_executable=python_executable, node_bin_dir=node_bin_dir)
        prov, provider_meta = _resolve_provider(warehouse, args.model or state.get("provider", {}).get("model_pool_name"))
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
                return _spawn_background(
                    run_dir=run_dir,
                    warehouse=warehouse,
                    prompt_path=prompt_path,
                    timeout=timeout,
                    model=args.model or state.get("provider", {}).get("model_pool_name", ""),
                    thread_id=thread_id,
                    python_executable=python_executable,
                    node_bin_dir=node_bin_dir,
                )
        return _run_worker(
            run_dir=run_dir,
            prompt=prompt,
            prov=prov,
            provider_meta=provider_meta,
            timeout=timeout,
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
        timeout = _resolve_timeout(
            args.timeout,
            target_datasets=int(state.get("target_datasets") or DEFAULT_TARGET_DATASETS),
        )
        return _run_worker(
            run_dir=run_dir,
            prompt=prompt_path.read_text(encoding="utf-8"),
            prov=prov,
            provider_meta=provider_meta,
            timeout=timeout,
            thread_id=args.thread_id,
        )

    raise AssertionError(args.agent_command)
