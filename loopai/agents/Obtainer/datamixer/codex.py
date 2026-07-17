"""LoopAI Codex runner adapter for intelligent ingestion.

This uses LoopAI's shared ``codex-runner`` process with LoopAI data-lake ingest
instructions. Codex inspects the data file with its own shell tools and drives
the provided CLI to ingest it. The Codex kernel model comes from the model pool
(base url / key / model are passed to the shared runner).

Requirements at run time (checked, not assumed):
  * LoopAI's ``codex-runner`` directory and its Node/Yarn runtime
  * a model registered in the pool with a reachable endpoint + key

When the runtime is missing the caller can fall back to the offline ``builtin``
engine.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import tomlkit

from loopai.schema.model_pool import StarterModelPool, load_starter_system_config_sync
from .models import ModelPool, ModelSpec


class CodexError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# runtime detection
# ---------------------------------------------------------------------------

def _project_root() -> Path | None:
    for path in Path(__file__).resolve().parents:
        if (path / "codex-runner" / "package.json").exists():
            return path
    return None


def runner_dir() -> Path | None:
    root = _project_root()
    if not root:
        return None
    path = root / "codex-runner"
    return path if path.exists() else None


def codex_home() -> Path | None:
    if os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"])
    root = _project_root()
    if not root:
        return None
    path = root / "codex_home"
    return path if path.exists() else None


def corepack_path() -> str | None:
    explicit = os.environ.get("COREPACK_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("corepack")


def loopai_python_executable() -> str:
    explicit = os.environ.get("LOOPAI_PYTHON_EXECUTABLE")
    if explicit and os.access(explicit, os.X_OK):
        return explicit

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidate = Path(conda_prefix) / "bin" / "python"
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def runner_process_path(python_executable: str | None = None, base_path: str | None = None) -> str:
    entries: list[str] = []
    python_executable = python_executable or loopai_python_executable()
    python_bin = str(Path(python_executable).resolve().parent)
    node_bin_dir = os.environ.get("LOOPAI_NODE_BIN_DIR")
    python_path_candidate = "" if python_bin in {"/usr/bin", "/bin"} else python_bin
    for candidate in (node_bin_dir, python_path_candidate):
        if candidate and Path(candidate).exists() and candidate not in entries:
            entries.append(candidate)
    for item in (base_path or os.environ.get("PATH") or "").split(os.pathsep):
        if item and item not in entries:
            entries.append(item)
    return os.pathsep.join(entries)


@lru_cache(maxsize=1)
def sdk_available() -> bool:
    """True iff LoopAI's shared codex-runner can be launched."""
    runner = runner_dir()
    corepack = corepack_path()
    if not runner or not corepack:
        return False
    try:
        r = subprocess.run(
            [corepack, "yarn", "--version"],
            cwd=runner,
            env={**os.environ, "PATH": runner_process_path()},
            capture_output=True,
            timeout=20,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def runtime_status() -> dict:
    instructions = ingest_instructions_path()
    runner = runner_dir()
    home = codex_home()
    return {
        "corepack": corepack_path(),
        "runner": str(runner) if runner else None,
        "codex_home": str(home) if home else None,
        "codex_sdk": sdk_available(),
        "instructions": str(instructions) if instructions else None,
        "ready": bool(runner and sdk_available()),
        "install_hint": "ensure codex-runner dependencies are installed (`corepack yarn install` in codex-runner)",
    }


# ---------------------------------------------------------------------------
# instructions + provider mapping
# ---------------------------------------------------------------------------

def ingest_instructions_path() -> Path | None:
    cand = [os.environ.get("OBTAINERCLI_INGEST_INSTRUCTIONS_PATH"),
            Path(__file__).resolve().parents[3] / "skills" / "ObtainerCLI" / "datamixer_ingest_prompt.md"]
    for c in cand:
        if c and Path(c).exists():
            return Path(c)
    return None


def _base_url(api_url: str) -> str:
    for suf in ("/v1/chat/completions", "/v1/responses",
                "/chat/completions", "/responses"):
        if api_url.endswith(suf):
            cut = api_url[: -len(suf)]
            return (cut + "/v1") if suf.startswith("/v1") else cut.rstrip("/")
    return api_url.rstrip("/")


def provider_from_model(spec: ModelSpec) -> dict:
    """Map a model-pool entry onto the shared Codex runner environment."""
    system = load_starter_system_config_sync(_project_root(), prefer_db=True)
    model_value = system.get("model")
    has_explicit_pool = (
        isinstance(model_value, list)
        or (isinstance(model_value, dict) and isinstance(model_value.get("pool") or model_value.get("models"), list))
    )
    if has_explicit_pool:
        provider = StarterModelPool(system).resolve_proxy_provider(spec.name or spec.model)
        if provider is not None:
            return provider.as_provider()
    return {
        "base_url": _base_url(spec.api_url),
        "api_key": spec.resolved_key(),
        "model": spec.model,
        "wire_api": "responses" if spec.response_format == "response" else "chat",
    }


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_response_proxy_base_url(base_url: str) -> bool:
    return "/responseproxy/" in str(base_url or "").strip().lower()


def _requires_project_config(prov: dict) -> bool:
    if _to_bool(prov.get("use_project_config"), False):
        return True
    if "supports_websockets" in prov:
        return not _to_bool(prov.get("supports_websockets"), True)
    return _is_response_proxy_base_url(str(prov.get("base_url") or ""))


def _sync_runner_project_config(home: Path, prov: dict, cwd: str) -> None:
    """Write Codex provider config for transports that need CLI config fields.

    Passing only ``baseUrl`` to @openai/codex-sdk does not carry provider-level
    flags such as ``supports_websockets = false``. The Python response proxy is
    HTTP-only, so proxy-backed workers must use project config.
    """
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.toml"
    if config_path.exists():
        try:
            template = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        except Exception:
            template = tomlkit.document()
    else:
        template = tomlkit.document()

    provider_name = str(prov.get("model_provider") or "loopai_model_pool_proxy").strip()
    template["model_provider"] = provider_name

    model_providers = template.setdefault("model_providers", tomlkit.table())
    provider_config = model_providers.get(provider_name)
    if not isinstance(provider_config, dict):
        provider_config = tomlkit.table()
        model_providers[provider_name] = provider_config

    provider_config["name"] = str(prov.get("provider_name") or "LoopAI Model Pool Proxy")
    provider_config["base_url"] = str(prov.get("base_url") or "").rstrip("/")
    provider_config["env_key"] = str(prov.get("env_key") or "CODEX_API_KEY")
    provider_config["wire_api"] = str(prov.get("wire_api") or "responses")
    provider_config["supports_websockets"] = _to_bool(
        prov.get("supports_websockets"),
        default=not _is_response_proxy_base_url(str(prov.get("base_url") or "")),
    )

    workspace = str(Path(cwd).resolve())
    projects = template.setdefault("projects", tomlkit.table())
    project_config = projects.get(workspace)
    if not isinstance(project_config, dict):
        project_config = tomlkit.table()
        projects[workspace] = project_config
    project_config.setdefault("trust_level", "trusted")

    config_path.write_text(tomlkit.dumps(template), encoding="utf-8")


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------

def build_prompt(file_path: str, dataset: str, root: str) -> str:
    python_executable = loopai_python_executable()
    dm = f"{python_executable} -m loopai.agents.Obtainer.datamixer --root {root}"
    instructions = ingest_instructions_path()
    body = instructions.read_text() if instructions else ""
    return (
        "Ingest a data file into this LoopAI data-lake warehouse. Inspect the "
        "file yourself and drive the provided CLI; do not write the catalog or "
        "blobs directly.\n\n"
        f"FILE={file_path}\nDATASET={dataset}\nROOT={root}\nDM={dm}\n\n"
        "When done, return the structured JSON result (summary, format, stage, "
        "tags, records_ingested, dataset, dataset_card, derived_fields, "
        "validation).\n\n"
        + (f"--- ingest instructions ---\n{body}\n" if body else "")
    )


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def _run_loopai_codex_runner(
    env: dict,
    prompt: str,
    timeout: int,
    on_stdout_payload: Callable[[dict], None] | None = None,
) -> tuple[int, str, str]:
    runner = runner_dir()
    corepack = corepack_path()
    if not runner:
        raise CodexError("LoopAI codex-runner not found")
    if not corepack:
        raise CodexError("corepack not found; install Node.js with Corepack to use the Codex engine")
    cmd = [corepack, "yarn", "dev", prompt]
    merged_env = {**os.environ, **env}
    python_executable = loopai_python_executable()
    merged_env["LOOPAI_PYTHON_EXECUTABLE"] = python_executable
    merged_env["PATH"] = runner_process_path(python_executable, merged_env.get("PATH"))
    proc = subprocess.Popen(
        cmd,
        cwd=runner,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    selector = selectors.DefaultSelector()
    assert proc.stdout is not None
    assert proc.stderr is not None
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + max(timeout, 1)

    try:
        while selector.get_map():
            if time.monotonic() > deadline:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise subprocess.TimeoutExpired(cmd, timeout, output="".join(stdout_lines), stderr="".join(stderr_lines))

            for key, _ in selector.select(timeout=0.2):
                line = key.fileobj.readline()
                if line == "":
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    stdout_lines.append(line)
                    if on_stdout_payload:
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict):
                            on_stdout_payload(payload)
                else:
                    stderr_lines.append(line)
    finally:
        selector.close()

    return proc.wait(), "".join(stdout_lines), "".join(stderr_lines)


def _parse_json_object(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start:end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _parse_runner_output(stdout: str, stderr: str, exit_code: int) -> dict:
    completed: dict | None = None
    thread_id = ""
    for line in (stdout or "").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "event" and isinstance(payload.get("event"), dict):
            event = payload["event"]
            if event.get("type") == "thread.started" and event.get("thread_id"):
                thread_id = str(event["thread_id"])
        if payload.get("type") == "completed" and isinstance(payload.get("result"), dict):
            completed = payload["result"]
    if exit_code != 0:
        message = _runner_error_message(stdout=stdout, stderr=stderr, exit_code=exit_code)
        raise CodexError(message.strip())
    if completed is None:
        raise CodexError(f"could not parse codex-runner completion: {stderr or stdout}")

    final_response = str(completed.get("finalResponse") or "")
    structured = _parse_json_object(final_response) or {"summary": final_response}
    structured.setdefault("summary", final_response)
    if thread_id:
        structured["thread_id"] = thread_id
    structured["runner_result"] = completed
    return structured


def _runner_error_message(*, stdout: str, stderr: str, exit_code: int) -> str:
    messages: list[str] = []
    for line in (stderr or "").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                messages.append(line.strip())
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "runner.started":
            continue
        text = payload.get("message") or payload.get("error")
        if text:
            messages.append(str(text))
    if messages:
        return "\n".join(messages)
    return stdout or stderr or f"codex-runner exited with {exit_code}"


def run_via_sdk(prompt: str, prov: dict, cwd: str, timeout: int = 600,
                network: bool = True, thread_id: str | None = None,
                on_event: Callable[[dict], None] | None = None) -> dict:
    home = codex_home()
    use_project_config = _requires_project_config(prov)
    if home and use_project_config:
        _sync_runner_project_config(home, prov, cwd)
    env = {
        "CODEX_API_KEY": prov["api_key"] or "",
        "CODEX_BASE_URL": prov["base_url"],
        "CODEX_MODEL": prov["model"],
        "CODEX_WORKSPACE": cwd,
        "CODEX_RUN_TIMEOUT_MS": str(max(timeout, 1) * 1000),
        "CODEX_SANDBOX_MODE": "danger-full-access",
        "LOOPAI_PYTHON_EXECUTABLE": loopai_python_executable(),
    }
    if home:
        env["CODEX_HOME"] = str(home)
    if use_project_config:
        env["CODEX_USE_PROJECT_CONFIG"] = "1"
    env["CODEX_THREAD_ID"] = thread_id or ""
    code, out, err = _run_loopai_codex_runner(env, prompt, timeout, on_stdout_payload=on_event)
    return _parse_runner_output(out, err, code)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def codex_ingest(store, path, model: str, dataset: str | None = None,
                 timeout: int = 600, network: bool = True) -> dict:
    if not model:
        raise CodexError("the codex engine requires --model (a model-pool name)")
    spec = ModelPool(store.root).get(model)
    prov = provider_from_model(spec)
    abspath = str(Path(path).resolve())
    dataset = dataset or Path(path).stem or "agent_dataset"
    root = str(store.root)

    before = store.catalog.count()
    prompt = build_prompt(abspath, dataset, root)
    result = run_via_sdk(prompt, prov, cwd=root, timeout=timeout, network=network)
    after = store.catalog.count()         # Codex committed via the CLI subprocess

    ds_id = store.catalog.resolve_dataset(dataset)
    return {
        "engine": "codex", "model": model, "dataset": dataset,
        "dataset_id": ds_id,
        "ingested": max(after - before, result.get("records_ingested") or 0),
        "codex_result": result,
        "review": result.get("summary", ""),
        "format": result.get("format"),
    }
