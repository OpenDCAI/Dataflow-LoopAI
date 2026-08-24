from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import time
from pathlib import Path

from loopai.agents.Obtainer.datamixer import codex
from loopai.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from loopai.schema.model_pool import responses_url

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


def _resolved_model_metadata(
    provider: dict,
    provider_meta: dict,
    *,
    requested_model: str = "",
) -> dict:
    """Make default-vs-override resolution explicit and durable."""
    meta = dict(provider_meta)
    resolved = str(meta.get("upstream_model_name") or provider.get("model") or "").strip()
    webagent_model = str(meta.get("model_pool_name") or requested_model or "").strip()
    if not resolved or not webagent_model:
        raise ObtainerCliError(
            "OBTAINERCLI_MODEL_RESOLUTION_FAILED",
            "could not resolve a Codex-default model for the acquisition worker",
            hint="Configure the Starter model pool with a Codex default before starting acquisition.",
            exit_code=2,
        )
    meta.update({
        "resolved_model": resolved,
        "webagent_model": webagent_model,
        "model_source": "operator_override" if requested_model else "codex_default",
    })
    return meta



def _focus_keywords_arg(focus_keywords: list[str] | None) -> str:
    """Render the --focus-keywords CLI lines, or nothing when undeclared."""
    values = [str(item).strip() for item in (focus_keywords or []) if str(item).strip()]
    return "".join(f"    --focus-keywords {value} \\\n" for value in values)


def _ensure_webagent_model(warehouse: Path, provider: dict, provider_meta: dict) -> str:
    """Register the resolved Codex provider under the WebAgent pool name.

    DataMixer WebAgent accepts a warehouse model-pool name, whereas the inner
    worker receives a resolved provider.  Keeping this bridge in one place
    prevents a blank context from silently selecting a local vLLM model.
    """
    warehouse.mkdir(parents=True, exist_ok=True)
    name = str(provider_meta["webagent_model"])
    spec = ModelSpec(
        name=name,
        api_url=responses_url(str(provider.get("base_url") or "")),
        api_key=str(provider.get("api_key") or ""),
        response_format="response",
        model=str(provider_meta["resolved_model"]),
        note="Managed by ObtainerCLI from the resolved Codex default model.",
    )
    pool = ModelPool(warehouse)
    pool.add(spec)
    pool.set_default(name)
    return name


def _with_model_resolution(result: dict, provider_meta: dict) -> dict:
    """Expose the same resolution record from start, resume, and worker runs."""
    result.update({
        key: provider_meta.get(key, "")
        for key in ("resolved_model", "webagent_model", "model_source")
    })
    return result


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
   the same time:
   - Run Obtainer SearchAgent to discover hosted datasets and construct the
     provider-download candidate manifest.
   - Run DataMixer's registered `domain_data_acquisition` campaign (legacy alias
     `webcrawler_dm`) to collect authoritative vertical-domain resource pages as
     L1 raw HTML in the target warehouse.
   SearchAgent and WebAgent cover different source types; neither substitutes
   for the other. Start WebAgent with `--detach --auto-process`: its persistent
   L1 -> L2 -> L3 queues begin consuming each accepted page immediately. Do not
   wait for the WebAgent campaign to finish before downloading, normalizing, or
   ingesting the hosted-dataset stream. Wait only for the SearchAgent artifact
   needed by `download manifest`, then keep polling the WebAgent campaign while
   the other work continues. Keep their artifacts, failures, and accepted
   outputs separate in the final report.
   WebAgent terminal state is not a completion gate for this worker. Continuously
   evaluate current per-bucket lake record/token counts and quality gates. Once
   they satisfy the plan, record `lake_ready=true` plus the observed counts and
   gate evidence in `final_report.json`, then finish the worker's bounded work so
   the outer agent can immediately start postprocessing, indexing, recipe
   planning, and export while WebAgent continues producing data. Never wait for
   empty WebAgent queues or a terminal campaign state.
   The outer CLI resolves and registers the Codex-default DataMixer model
   before this worker starts. Read its durable name from `thread.json`; never
   select a local model or invent credentials. Do not begin `download manifest`
   until `webagent_start.json` exists; a WebAgent launch blocker is terminal.
4. Before discovery, write `manifest/data_mix_plan.json`. It must contain the
   current objective/failure taxonomy, `target_datasets`, a non-empty `buckets`
   list, each bucket's `name`, `weight`, `target_datasets`, `search_objectives`,
   `quality_gates`, and a concrete `rationale`. Weights must sum to 1.0. Use it
   to make SearchAgent task JSON domain-specific and to prevent one capability
   from crowding out the planned mix. Include the plan and observed acquisition
   mix in `final_report.json`.
5. Use Obtainer SearchAgent and `download manifest` as the acquisition
   bridge when appropriate:
   {python_executable} -m loopai.skills.ObtainerCLI.cli searchagent ...
   {python_executable} -m loopai.skills.ObtainerCLI.cli download manifest ...
   For multi-domain acquisition requests, first write a task JSON with one
   isolated task per capability domain (for example text2sql, math, code), then
   call SearchAgent once with `--task-json <path> --parallelism <n>`. Keep each
   task's objective and search_keywords domain-specific so one domain cannot
   crowd out another.
6. If direct web/Hugging Face/Kaggle discovery is more appropriate for the
   caller's instruction, write an equivalent manifest yourself and continue.
7. Before downloading, compare the candidate list against the original user
   request and Analyzer report. Remove clearly unrelated datasets and write
   both a filtered manifest and a rejection report with exact reasons.
8. Each single dataset is capped at {max_rows_per_dataset} rows and
   {max_bytes_per_dataset} output bytes. Do not bypass these caps. Smaller
   sampled downloads are allowed for broad acquisition, but record sampled_rows,
   rows_written, bytes_written, max_rows_effective, max_bytes_effective, and
   cap/truncation status in the manifest/report. For `download manifest`,
   `--limit` caps candidate items to try, `--max-rows` caps rows per dataset,
   and `--max-bytes-per-dataset` caps local JSONL output bytes per dataset. If
   the byte cap is reached, keep the partial JSONL and report `truncated`,
   `truncated_reason`, `rows_written`, and `bytes_written`.
9. Normalize each downloaded dataset to JSONL before ingest. Each row must
   preserve source_uri, source_dataset/source_dataset_id, split, and enough
   payload fields for later SFT/PT processing.
10. For every accepted dataset, write a Markdown dataset card before ingest and
   register it during ingest with `--dataset-card <path>`. The card must live in
   this run's manifest directory first and describe source, license, split,
   row count, original fields, derived fields, derivation rules, validation
   checks, intended training use, and known risks.
11. You may add dataset-specific derived fields during normalization, but only
   by adding fields. Never drop, overwrite, or rename original payload fields.
   The normalized JSONL must preserve the same row count as the selected source
   rows. For complex embedded formats, derive explicit training-ready fields:
   parse step traces into reasoning fields, flatten multi-turn conversations
   into messages/dialogue/instruction-response fields, combine question with
   options/evidence/schema blocks into prompt/input fields, and keep gold
   labels/answers as separate fields.
12. If derived fields are added, every derived field must be non-empty for every
   row. Pass `--derived-field <name>` for each derived field and
   `--source-row-count <n>` to `dm ingest` so DataMixer validates this before
   writing. If validation fails, fix the normalizer or reject the dataset; do
   not ingest partial rows.
13. If the user request or Analyzer report explicitly names a benchmark type to
   collect (for example "collect HumanEval-style code problems", "BIRD SQL
   pairs", or any named eval/test set), treat that dataset as evaluation-only
   and register it in the DataMixer benchmark registration layer BEFORE any
   acquisition or ingest:
   {python_executable} -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} contam add --name <benchmark> --file <file> --json
   The registration makes downstream ingest and export decontamination exclude
   those rows and prevents benchmark leakage into training data. Do not rely
   on a later `decontaminate` pass alone to decide what to register.
14. Ingest every accepted dataset through DataMixer `ingest` or `agent-ingest`
   with complete tags: source platform, source dataset id, source URL or URI,
   license if known, language if known, domain, task_type, processing_level,
   quality_level, source_kind, split, loop_uuid/version_id when provided, and
   acquisition_run. Choose quality_level explicitly for every dataset: L1 for
   raw webpages, L2 for extracted/parsed/basic-cleaned source data, L3
   for standard SFT/DPO/training samples (regular hosted SFT/training datasets
   must be L3, not L1), and L4 only for output explicitly refined by an
   internal data-lake pipeline. When uncertain, choose the lower applicable
   level and explain the uncertainty; never omit the parameter.
15. After ingest, run DataMixer status, dataset list, stats, representative query,
   and index build when useful for downstream recall.
16. Write final_report.json with `lake_ready`, per-bucket planned-versus-observed
    record/token counts, quality-gate evidence, SearchAgent and WebAgent commands/statuses,
    WebAgent campaign id and L1 datasets, candidates, filtered list, rejections,
    downloads, dataset card paths, derived field specs, validation outcomes,
    ingests, each dataset's selected quality_level and selection rationale,
    DataMixer command summaries, before/after counts, lineage/manifest paths,
    and blockers.
17. Do not mark ok=true if no dataset was ingested, if accepted datasets are
    unrelated to the request, if any accepted dataset lacks a registered md
    dataset card, if derived field validation failed, if row count changed
    during derivation, or if required source/provenance tags are missing.
18. Do not read or print secret/key files.
19. Per-record quality approval belongs to post-processing: the WebAgent L2
    `domain_classify` step receives the campaign `--focus-keywords` and the LLM
    judgement accepts only items directly related to that focus, backed by
    grounded semantic signals; `topic_quality_filter` then enforces a
    confidence/signal threshold on that evidence. No vertical (e.g. finance)
    is hard-wired, so the same chain follows whichever topic the WebAgent
    explored.
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
    focus_keywords: list[str] | None = None,
) -> str:
    focus_keywords_arg = _focus_keywords_arg(focus_keywords)
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
- acquisition mix plan: {run_dir}/manifest/data_mix_plan.json
- WebAgent launch result: {run_dir}/manifest/webagent_start.json
- WebAgent campaign status: {run_dir}/manifest/webagent_campaign_status.json
- dataset cards: {run_dir}/manifest/dataset_cards/*.md
- derived-field specs/validation: {run_dir}/manifest/derived_fields.json
- downloads: {run_dir}/downloads/
- ingest report: {run_dir}/manifest/ingest_results.json
- final report: {run_dir}/final_report.json

Required discovery procedure:
1. Create `{run_dir}/manifest/data_mix_plan.json` before creating discovery
   tasks. It must record the planned capability/domain proportions from the
   current request and Analyzer failure taxonomy, the `target_datasets` budget,
   planned count per bucket, source/quality gates, and rationale. Then create
   `{run_dir}/manifest/tasks.json` with a top-level `tasks` list, where every
   task belongs to a planned bucket. Read
   `{run_dir}/thread.json` and use its non-empty `webagent_model` exactly as
   `$WEBAGENT_MODEL`; it was registered from the resolved Codex default before
   this worker started. Do not select a different entry from `dm model list`.
   If this value is absent, write `webagent_model_missing` to final_report and
   fail the run after recording the blocker; do not continue with SearchAgent
   or a direct-download fallback.
2. When `$WEBAGENT_MODEL` is available, launch SearchAgent and WebAgent in
   parallel. The WebAgent command must return after its durable campaign and
   streaming L1 -> L2 -> L3 consumers are started. Wait for SearchAgent only;
   do not wait for WebAgent campaign completion before continuing:

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
    --auto-process \
    --detach \
{focus_keywords_arg}    --search-provider tavily \
    --json > {run_dir}/manifest/webagent_start.json
) &
WEBAGENT_PID=$!
wait "$SEARCHAGENT_PID"; SEARCHAGENT_EXIT=$?
wait "$WEBAGENT_PID"; WEBAGENT_LAUNCH_EXIT=$?
```

   If `TAVILY_API_KEY` is unavailable, use `--search-provider auto` for the
   WebAgent command and record the provider choice. A failed WebAgent launch or
   a failed persistent processing queue is terminal; a still-running campaign
   is not. It must never be replaced by a direct-download fallback.
3. Read `{run_dir}/manifest/searchagent/searchagent_manifest.json` and
   `{run_dir}/manifest/webagent_start.json`. Use `dm webagent campaign status
   <run-id> --json` to write `{run_dir}/manifest/webagent_campaign_status.json`.
   Preserve the WebAgent campaign id, selected URLs, L1 dataset names, and
   L1/L2/L3 counts if an automatic pipeline was requested. Do not copy WebAgent
   HTML into the provider download manifest; it is already in DataMixer. Continue
   hosted-data filtering/download/ingest as soon as SearchAgent completes. Do
   not defer it until WebAgent reaches a terminal state. After each ingest,
   compare current per-bucket record/token counts and quality evidence with
   `data_mix_plan.json`. When they pass, write `lake_ready=true` and the evidence
   to `final_report.json`; do not wait for WebAgent completion or empty queues
   before completing this worker and handing downstream work back to the caller.
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
     --processing-level <processing_level> \
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
   `dm ingest` is the batch lake path: it writes every normalized row with the
   batch metadata and the chosen `--quality-level`; it does not run per-record
   LLM approval and it never drops rows. Do not attempt to emulate a per-record
   quality gate before ingest, and do not use `--domain finance` as if it
   triggered per-row reclassification - it is only batch-level metadata.
   Production export runs only
   after the DataFlowAgent post-processing stage completes and the L4 dataset
   scale meets the recipe target with at least 1.5x in-lake redundancy per
   bucket. A dataset alias, source name, or URL is never evidence for
   `--domain finance`; provenance remains metadata only. Preserve the final
   export quality report path in final_report.json.

Extra caller instruction:
{extra_message or '- none'}

Proceed end-to-end. Return concise JSON with ok, final_report, datasets_ingested,
warehouse, and blockers.
"""


def build_resume_prompt(*, run_dir: Path, message: str) -> str:
    state = _json_read(run_dir / STATE_FILE)
    focus_keywords_arg = _focus_keywords_arg(state.get("focus_keywords"))
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
    start.add_argument(
        "--focus-keywords",
        action="append",
        default=[],
        help="WebAgent exploration keyword passed to the LLM quality judgement (repeatable)",
    )
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


def _command_result(payload: dict) -> dict:
    """Unwrap ObtainerCLI/DataMixer JSON envelopes without guessing text logs."""
    current = payload if isinstance(payload, dict) else {}
    for _ in range(3):
        nested = current.get("result")
        if not isinstance(nested, dict):
            break
        current = nested
    return current


def _campaign_success_count(payload: dict) -> int:
    queue = payload.get("queue") if isinstance(payload.get("queue"), dict) else {}
    values = (
        queue.get("succeeded"),
        payload.get("succeeded"),
        payload.get("pages_ingested"),
        payload.get("l1_count"),
    )
    for value in values:
        try:
            if int(value or 0) > 0:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _validate_data_mix_plan(run_dir: Path, target_datasets: int) -> tuple[dict, list[dict]]:
    path = run_dir / "manifest" / "data_mix_plan.json"
    plan = _json_read(path)
    issues: list[dict] = []
    if not plan:
        return plan, [{"code": "data_mix_plan_missing", "artifact": str(path)}]

    buckets = plan.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        return plan, [{"code": "data_mix_buckets_missing", "artifact": str(path)}]

    weights = 0.0
    planned_datasets = 0
    names: set[str] = set()
    for index, bucket in enumerate(buckets):
        if not isinstance(bucket, dict):
            issues.append({"code": "data_mix_bucket_invalid", "index": index})
            continue
        name = str(bucket.get("name") or "").strip()
        if not name:
            issues.append({"code": "data_mix_bucket_name_missing", "index": index})
        elif name in names:
            issues.append({"code": "data_mix_bucket_duplicate", "name": name})
        names.add(name)
        try:
            weight = float(bucket.get("weight"))
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0:
            issues.append({"code": "data_mix_bucket_weight_invalid", "name": name, "weight": bucket.get("weight")})
        weights += weight
        try:
            bucket_target = int(bucket.get("target_datasets"))
        except (TypeError, ValueError):
            bucket_target = -1
        if bucket_target < 0:
            issues.append({"code": "data_mix_bucket_target_invalid", "name": name})
        else:
            planned_datasets += bucket_target
        if not bucket.get("search_objectives"):
            issues.append({"code": "data_mix_search_objectives_missing", "name": name})
        if not bucket.get("quality_gates"):
            issues.append({"code": "data_mix_quality_gates_missing", "name": name})
        if not str(bucket.get("rationale") or "").strip():
            issues.append({"code": "data_mix_rationale_missing", "name": name})

    if abs(weights - 1.0) > 0.01:
        issues.append({"code": "data_mix_weights_invalid", "actual": round(weights, 6), "expected": 1.0})
    declared_target = plan.get("target_datasets")
    try:
        declared_target_int = int(declared_target)
    except (TypeError, ValueError):
        declared_target_int = 0
    if declared_target_int != int(target_datasets):
        issues.append({
            "code": "data_mix_target_mismatch",
            "expected": int(target_datasets),
            "actual": declared_target,
        })
    if planned_datasets != int(target_datasets):
        issues.append({
            "code": "data_mix_bucket_targets_mismatch",
            "expected": int(target_datasets),
            "actual": planned_datasets,
        })
    return plan, issues


def _validate_successful_run_artifacts(run_dir: Path) -> dict:
    """Verify that an ``ok=true`` worker really launched both discovery streams."""
    state = _json_read(run_dir / STATE_FILE)
    final_report = _json_read(run_dir / "final_report.json")
    search_manifest = _json_read(run_dir / "manifest" / "searchagent" / "searchagent_manifest.json")
    web_start_raw = _json_read(run_dir / "manifest" / "webagent_start.json")
    web_status_raw = _json_read(run_dir / "manifest" / "webagent_campaign_status.json")
    web_start = _command_result(web_start_raw)
    web_status = _command_result(web_status_raw)
    issues: list[dict] = []
    warnings: list[dict] = []
    data_mix_plan, data_mix_issues = _validate_data_mix_plan(
        run_dir,
        int(state.get("target_datasets") or DEFAULT_TARGET_DATASETS),
    )
    issues.extend(data_mix_issues)

    for key in ("resolved_model", "webagent_model", "model_source"):
        if not state.get(key):
            issues.append({"code": f"{key}_missing", "artifact": STATE_FILE})
    if not search_manifest:
        issues.append({"code": "searchagent_manifest_missing", "artifact": "manifest/searchagent/searchagent_manifest.json"})
    elif search_manifest.get("ok") is False:
        issues.append({"code": "searchagent_failed", "detail": search_manifest.get("errors")})

    start_run_id = str(web_start.get("run_id") or "")
    status_run_id = str(web_status.get("run_id") or "")
    dataset = str(web_start.get("dataset") or web_status.get("dataset") or "")
    if not web_start_raw:
        issues.append({"code": "webagent_start_missing", "artifact": "manifest/webagent_start.json"})
    if not start_run_id:
        issues.append({"code": "webagent_campaign_id_missing", "artifact": "manifest/webagent_start.json"})
    if not web_status_raw:
        issues.append({"code": "webagent_campaign_status_missing", "artifact": "manifest/webagent_campaign_status.json"})
    if start_run_id and status_run_id and start_run_id != status_run_id:
        issues.append({"code": "webagent_campaign_id_mismatch", "start": start_run_id, "status": status_run_id})
    if not dataset:
        issues.append({"code": "webagent_l1_dataset_missing"})
    success_count = max(_campaign_success_count(web_start), _campaign_success_count(web_status))
    if success_count <= 0:
        issues.append({"code": "webagent_l1_evidence_missing", "detail": "campaign has no succeeded task/pages/L1 count"})
    terminal_status = str(web_status.get("status") or web_start.get("status") or "").lower()
    if terminal_status in {"failed", "completed_with_errors", "cancelled"}:
        failure = {
            "code": "webagent_campaign_failed",
            "status": terminal_status,
            "error": web_status.get("error") or web_start.get("error"),
        }
        # A terminal campaign is not a completion gate for the dataset batch
        # path as long as L1 evidence was produced; record it as a warning.
        if success_count > 0:
            warnings.append(failure)
        else:
            issues.append(failure)

    evidence = {
        "ok": not issues,
        "resolved_model": state.get("resolved_model") or "",
        "webagent_model": state.get("webagent_model") or "",
        "model_source": state.get("model_source") or "",
        "searchagent_manifest": str(run_dir / "manifest" / "searchagent" / "searchagent_manifest.json"),
        "campaign_id": start_run_id or status_run_id,
        "l1_dataset": dataset,
        "l1_success_count": success_count,
        "campaign_status": terminal_status,
        "data_mix_plan": str(run_dir / "manifest" / "data_mix_plan.json"),
        "planned_mix": data_mix_plan,
        "issues": issues,
        "warnings": warnings,
    }
    _json_write(run_dir / "acceptance_report.json", evidence)
    if final_report.get("ok") is True:
        final_report.update({
            "resolved_model": evidence["resolved_model"],
            "webagent_model": evidence["webagent_model"],
            "model_source": evidence["model_source"],
            "webagent_campaign_id": evidence["campaign_id"],
            "webagent_l1_dataset": evidence["l1_dataset"],
            "webagent_l1_success_count": evidence["l1_success_count"],
            "data_mix_plan": evidence["data_mix_plan"],
            "planned_mix": evidence["planned_mix"],
            "acquisition_acceptance": evidence,
        })
        _json_write(run_dir / "final_report.json", final_report)
    return evidence


def _status_payload(run_dir: Path) -> dict:
    status = _json_read(run_dir / STATUS_FILE)
    state = _json_read(run_dir / STATE_FILE)
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
    state["webagent_model"] = provider_meta.get("webagent_model", state.get("webagent_model", ""))
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
    discovery_mode: str,
    provider_meta: dict,
    python_executable: str = "",
    node_bin_dir: str = "",
    task_id: str = "",
    focus_keywords: list[str] | None = None,
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
        "focus_keywords": [
            str(item).strip() for item in (focus_keywords or []) if str(item).strip()
        ],
        "provider": provider_meta,
        "resolved_model": provider_meta.get("resolved_model", ""),
        "webagent_model": provider_meta.get("webagent_model", ""),
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
        target_datasets = max(args.target_datasets, DEFAULT_TARGET_DATASETS)
        timeout = _resolve_timeout(args.timeout, target_datasets=target_datasets)
        python_executable = args.python_executable or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
        prov, provider_meta = _resolve_provider(warehouse, args.model or None)
        provider_meta = _resolved_model_metadata(
            prov, provider_meta, requested_model=args.model or ""
        )
        _ensure_webagent_model(warehouse, prov, provider_meta)
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
            task_id=task_id,
            focus_keywords=args.focus_keywords,
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
            focus_keywords=args.focus_keywords,
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
        _ensure_webagent_model(warehouse, prov, provider_meta)
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
        warehouse = Path(state.get("warehouse") or root or "").expanduser().resolve()
        requested_model = args.model or str(state.get("provider", {}).get("model_pool_name") or "")
        prov, provider_meta = _resolve_provider(warehouse, requested_model or None)
        provider_meta = _resolved_model_metadata(
            prov, provider_meta,
            requested_model=args.model or "",
        )
        _ensure_webagent_model(warehouse, prov, provider_meta)
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
