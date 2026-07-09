from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from loopai.agents.Obtainer.datamixer import codex
from loopai.agents.Obtainer.datamixer.models import ModelPool
from loopai.utils.model_pool import StarterModelPool

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


def _apply_runtime_env(*, python_executable: str = "", node_bin_dir: str = "") -> None:
    if python_executable:
        os.environ["LOOPAI_PYTHON_EXECUTABLE"] = python_executable
    if node_bin_dir:
        os.environ["LOOPAI_NODE_BIN_DIR"] = node_bin_dir


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _starter_config_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.getenv("STARTER_CONFIG") or os.getenv("STARTER_CONFIG_PATH")
    if explicit:
        candidates.append(Path(explicit))
    workspace = _workspace()
    candidates.extend([
        Path.cwd() / "starter.yaml",
        workspace / "starter.yaml",
        workspace / "examples" / "config" / "starter.yaml",
    ])
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser().resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        try:
            from omegaconf import OmegaConf

            loaded = OmegaConf.to_container(OmegaConf.load(str(path)), resolve=True)
        except ImportError:
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_starter_system_from_db() -> dict[str, Any]:
    db_path = _workspace() / "api" / "db" / "db.sqlite3"
    if not db_path.exists():
        return {}
    try:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "select config from starterconfig where name=?",
                ("starter",),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    if not row or not row[0]:
        return {}
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        return {}
    system = payload.get("system", {}) if isinstance(payload, dict) else {}
    return system if isinstance(system, dict) else {}


def _load_starter_system_from_yaml() -> dict[str, Any]:
    for path in _starter_config_candidates():
        if not path.exists():
            continue
        loaded = _load_yaml_config(path)
        system = loaded.get("system", {}) if isinstance(loaded, dict) else {}
        if isinstance(system, dict) and system:
            return system
    return {}


def _explicit_starter_config_set() -> bool:
    return bool(os.getenv("STARTER_CONFIG") or os.getenv("STARTER_CONFIG_PATH"))


def _starter_system_sources(*, model_pool: bool = False) -> list[tuple[str, dict[str, Any]]]:
    db_source = "starter_db:system.model" if model_pool else "starter_db:system"
    yaml_source = "starter_yaml:system.model" if model_pool else "starter_yaml:system"
    db_item = (db_source, _load_starter_system_from_db())
    yaml_item = (yaml_source, _load_starter_system_from_yaml())
    return [yaml_item] if _explicit_starter_config_set() else [db_item, yaml_item]


def _runtime_provider_options() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []

    def add_option(*, source: str, base_url: Any, model: Any, api_key: Any) -> None:
        if not base_url or not model:
            return
        options.append({
            "source": source,
            "base_url": str(base_url),
            "model": str(model),
            "api_key": str(api_key or ""),
            "api_key_source": source if api_key else "empty",
        })

    add_option(
        source="env:CODEX",
        base_url=os.environ.get("CODEX_BASE_URL"),
        model=os.environ.get("CODEX_MODEL"),
        api_key=os.environ.get("CODEX_API_KEY"),
    )
    add_option(
        source="env:DEEPSEEK",
        base_url=os.environ.get("DEEPSEEK_BASE_URL"),
        model=os.environ.get("DEEPSEEK_MODEL"),
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
    )

    for source, system in _starter_system_sources():
        add_option(
            source=f"{source}.codex",
            base_url=system.get("codex_base_url"),
            model=system.get("codex_model"),
            api_key=system.get("codex_api_key"),
        )
        add_option(
            source=f"{source}.starter",
            base_url=system.get("starter_base_url"),
            model=_first_non_empty(
                system.get("starter_model_path"),
                system.get("starter_model_name"),
            ),
            api_key=system.get("starter_api_key"),
        )

    return options


def _has_explicit_starter_pool(system: dict[str, Any]) -> bool:
    model_value = system.get("model")
    return (
        isinstance(model_value, list)
        or (isinstance(model_value, dict) and isinstance(model_value.get("pool") or model_value.get("models"), list))
    )


def _provider_from_starter_model_pool(model: str | None) -> tuple[dict, dict] | None:
    for source, system in _starter_system_sources(model_pool=True):
        if not _has_explicit_starter_pool(system):
            continue
        pool = StarterModelPool(system)
        provider = pool.resolve_proxy_provider(model, tier=pool.default_tier)
        if provider is None:
            continue
        return provider.as_provider(), {
            **provider.meta(),
            "source": source,
            "requested_model": model or "",
        }
    return None


def _provider_from_runtime(model: str | None) -> tuple[dict, dict] | None:
    pooled = _provider_from_starter_model_pool(model)
    if pooled is not None:
        return pooled

    options = _runtime_provider_options()
    if model:
        for option in options:
            if option["model"] == model:
                prov = {
                    "base_url": option["base_url"],
                    "api_key": option["api_key"],
                    "model": option["model"],
                }
                return prov, {
                    "source": option["source"],
                    "requested_model": model,
                    "model_pool_name": model,
                    "base_url": option["base_url"],
                    "model": option["model"],
                    "api_key_source": option["api_key_source"],
                }
        for option in options:
            prov = {
                "base_url": option["base_url"],
                "api_key": option["api_key"],
                "model": model,
            }
            return prov, {
                "source": f"{option['source']}:requested_model",
                "requested_model": model,
                "model_pool_name": model,
                "base_url": option["base_url"],
                "model": model,
                "api_key_source": option["api_key_source"],
            }
        return None

    if not options:
        return None
    option = options[0]
    prov = {
        "base_url": option["base_url"],
        "api_key": option["api_key"],
        "model": option["model"],
    }
    return prov, {
        "source": option["source"],
        "base_url": option["base_url"],
        "model": option["model"],
        "api_key_source": option["api_key_source"],
    }


def _resolve_provider(root: Path, model: str | None) -> tuple[dict, dict]:
    pooled = _provider_from_starter_model_pool(model)
    if pooled is not None:
        return pooled

    if model:
        try:
            spec = ModelPool(root).get(model)
        except KeyError:
            runtime = _provider_from_runtime(model)
            if runtime is not None:
                return runtime
        else:
            prov = codex.provider_from_model(spec)
            return prov, {
                "source": "model_pool",
                "model_pool_name": model,
                "base_url": prov["base_url"],
                "model": prov["model"],
                "api_key_source": "model_pool",
            }
    else:
        runtime = _provider_from_runtime(None)
        if runtime is not None:
            return runtime

    if model:
        raise ObtainerCliError(
            "OBTAINERCLI_MODEL_NOT_FOUND",
            f"model {model!r} is not registered in this warehouse model pool or runtime config",
            hint=(
                "Register it with `dm model add`, or set CODEX_BASE_URL/CODEX_MODEL, "
                "or configure system.codex_* / system.starter_* in starter.yaml."
            ),
            exit_code=2,
        )
    else:
        raise ObtainerCliError(
            "OBTAINERCLI_MODEL_REQUIRED",
            "ObtainerCLI worker requires a model provider",
            hint=(
                "Register a model with `dm model add`, export CODEX_BASE_URL/CODEX_MODEL, "
                "or configure system.codex_* / system.starter_* in starter.yaml."
            ),
            exit_code=2,
        )


def _policy_text() -> str:
    return """# SFT export worker policy

You are the isolated inner Codex SDK worker for an Obtainer DataMixer SFT export.

Hard rules:

1. All lakehouse operations must use this command surface only:
   {python_executable} -m loopai.skills.ObtainerCLI.cli dm --root {warehouse} <datamixer-command> --json
2. Do not use legacy Obtainer lake/table/sample/index commands.
3. Final training data must be produced by DataMixer `recipe export`; never concatenate samples manually.
4. For Alpaca SFT, final JSONL training rows must contain exactly:
   instruction, input, output
   No `_dm`, `source_uri`, `source_dataset_id`, `split`, `question`, `answer`, `text`, or other training-row keys.
   Provenance belongs in the manifest/final_report, not in the training JSONL rows.
5. The recipe must declare `recipe.export.schema.fields`.
   Prefer per-bucket schemas (`recipe.buckets[].schema.fields` or
   `recipe.buckets[].export.schema.fields`) whenever buckets come from
   different datasets or source field conventions. Do not force one global
   fallback order across heterogeneous datasets.
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
8. Schema fields may be composed with templates. Use this when the final
   training field needs multiple original fields, for example:
   - math reasoning output: `template: "<think>{{reasoning}}</think>{{answer}}"`
     or `template: "<think>{{chain}}</think>{{answer}}"`
   - text2sql instruction: `template: "{{question}}"`
   - text2sql input: `template: "{{evidence}}\n{{sql_schema}}\n{{sql_block}}"`
   Inspect the exact source keys per dataset before choosing templates. For
   datasets like AlphaMath where `answer` is the gold scalar answer and `output`
   is a noisy trace, do not globally prefer `output` over `answer`; define that
   bucket's schema explicitly.
9. Before export, run status, stats, dataset list, sample/key inspection,
   recipe validate, recipe plan, and recipe preview.
10. If no explicit SFT size is provided, target at least 100000 records.
11. Export with `recipe export --snapshot`.
12. After export, independently validate all JSONL shards:
    - total record count
    - every row has non-empty instruction and output
    - every row has input as a string
    - every row key set is exactly instruction/input/output
    - instruction != output for every row
    - output was not sourced from text/raw_content/content fallback
    - manifest has snapshot_id, dataset_digest, recipe_fingerprint, export_schema
13. If validation fails, do not mark ok=true. Patch the recipe, normalize data,
    exclude invalid buckets, or write a blocker final_report with exact reasons.
14. Do not read or print secret/key files.
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
    policy = _policy_text().format(warehouse=str(warehouse), python_executable=codex.loopai_python_executable())
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
    return f"""{_policy_text().format(warehouse='the warehouse recorded in thread.json', python_executable=codex.loopai_python_executable())}

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
    start.add_argument("--python-executable", default="", help="Python executable for the isolated worker")
    start.add_argument("--node-bin-dir", default="", help="Directory containing node/corepack for codex-runner")
    start.add_argument("--dry-run", action="store_true", help="write prompt/state only; do not launch Codex SDK")
    start.add_argument("--foreground", action="store_true", help="run the inner Codex worker synchronously")
    start.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    resume = sub.add_parser("resume")
    resume.add_argument("--run", required=True)
    resume.add_argument("--message", required=True)
    resume.add_argument("--model", default="")
    resume.add_argument("--timeout", type=int, default=1800)
    resume.add_argument("--python-executable", default="", help="Python executable for the isolated worker")
    resume.add_argument("--node-bin-dir", default="", help="Directory containing node/corepack for codex-runner")
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
    worker.add_argument("--python-executable", default="", help=argparse.SUPPRESS)
    worker.add_argument("--node-bin-dir", default="", help=argparse.SUPPRESS)
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
    python_executable: str = "",
    node_bin_dir: str = "",
) -> dict:
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
    if python_executable:
        cmd.extend(["--python-executable", python_executable])
    if node_bin_dir:
        cmd.extend(["--node-bin-dir", node_bin_dir])
    env = os.environ.copy()
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CODEX_USE_PROJECT_CONFIG", None)
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
    python_executable: str = "",
    node_bin_dir: str = "",
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
        _policy_text().format(warehouse="see thread.json", python_executable=codex.loopai_python_executable()),
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
        "worker_result": {
            key: value
            for key, value in result.items()
            if key != "runner_result"
        },
    }


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
        if not root:
            raise ObtainerCliError(
                "SFT_EXPORT_AGENT_ROOT_REQUIRED",
                "sft-export-agent start requires `dm --root <warehouse>`",
                hint="Pass `loopai-obtainercli dm --root /path/to/warehouse sft-export-agent start ...`.",
                exit_code=2,
            )
        warehouse = Path(root).expanduser().resolve()
        export_out = Path(args.out).expanduser().resolve() if args.out else (run_dir / "export")
        python_executable = args.python_executable or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
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
            python_executable=python_executable,
            node_bin_dir=node_bin_dir,
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
            (run_dir / "policy.md").write_text(
                _policy_text().format(warehouse=str(warehouse), python_executable=codex.loopai_python_executable()),
                encoding="utf-8",
            )
            if not args.foreground:
                return _spawn_background(
                    run_dir=run_dir,
                    warehouse=warehouse,
                    prompt_path=prompt_path,
                    timeout=args.timeout,
                    model=args.model or "",
                    python_executable=python_executable,
                    node_bin_dir=node_bin_dir,
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
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        python_executable = args.python_executable or runtime.get("python_executable") or os.environ.get("LOOPAI_PYTHON_EXECUTABLE", "")
        node_bin_dir = args.node_bin_dir or runtime.get("node_bin_dir") or os.environ.get("LOOPAI_NODE_BIN_DIR", "")
        _apply_runtime_env(python_executable=python_executable, node_bin_dir=node_bin_dir)
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
            (run_dir / "policy.md").write_text(
                _policy_text().format(warehouse=str(warehouse), python_executable=codex.loopai_python_executable()),
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
                    python_executable=python_executable,
                    node_bin_dir=node_bin_dir,
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
