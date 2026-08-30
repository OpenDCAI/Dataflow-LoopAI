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
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import tomlkit

from loopai.schema.model_pool import StarterModelPool, load_starter_system_config_sync
from . import schema
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


def _direct_runner_command(runner: Path) -> list[str] | None:
    """Use the installed runner directly when Corepack itself is broken."""
    node = shutil.which("node")
    loader = runner / "node_modules" / "tsx" / "dist" / "loader.mjs"
    entrypoint = runner / "src" / "index.ts"
    sdk = runner / "node_modules" / "@openai" / "codex-sdk" / "package.json"
    if node and loader.is_file() and entrypoint.is_file() and sdk.is_file():
        return [node, "--import", str(loader), str(entrypoint)]
    return None


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


def runner_library_path(
    python_executable: str | None = None,
    base_path: str | None = None,
) -> str:
    """Prefer the active Python environment's native libraries in workers."""
    python_executable = python_executable or loopai_python_executable()
    env_lib = Path(python_executable).resolve().parent.parent / "lib"
    entries: list[str] = []
    if env_lib.is_dir():
        entries.append(str(env_lib))
    for item in (base_path or "").split(os.pathsep):
        if item and item not in entries:
            entries.append(item)
    return os.pathsep.join(entries)


@lru_cache(maxsize=1)
def sdk_available() -> bool:
    """True iff LoopAI's shared codex-runner can be launched."""
    runner = runner_dir()
    corepack = corepack_path()
    if not runner:
        return False
    if corepack:
        try:
            r = subprocess.run(
                [corepack, "yarn", "--version"],
                cwd=runner,
                env={**os.environ, "PATH": runner_process_path()},
                capture_output=True,
                timeout=20,
            )
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return _direct_runner_command(runner) is not None


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


ENV_MANIFEST_MARKER = "<!-- runtime_environment_manifest -->"


def _lake_pointer_summary() -> dict[str, str]:
    root = _project_root()
    link = Path(root) / ".datamixer" / "lake.yaml" if root else Path(".datamixer") / "lake.yaml"
    values: dict[str, str] = {}
    if link.is_file():
        for line in link.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            values[key.strip()] = value.strip()
    root_value = values.get("root", "")
    warehouse = values.get("warehouse", "")
    if not warehouse and root_value:
        warehouse = str((Path(root_value) / "warehouse").resolve())
    loaded = bool(warehouse and (Path(warehouse) / "datamixer.toml").is_file())
    return {
        "link": str(link),
        "warehouse": warehouse,
        "loaded": "yes" if loaded else "no",
    }


def environment_manifest_markdown(home: Path | None = None) -> str:
    """Runtime-detected environment manifest injected into every Codex home."""
    import loopai

    system = load_starter_system_config_sync(_project_root(), prefer_db=True)
    system = system if isinstance(system, dict) else {}
    api_port = os.environ.get("API_PORT") or str(system.get("api_port") or "8855")
    model_config = system.get("model")
    model_config = model_config if isinstance(model_config, dict) else {}
    proxy_base = str(model_config.get("proxy_base_url") or "")
    default_model = str(model_config.get("default_model") or "")
    home = home or codex_home()
    lake = _lake_pointer_summary()
    python_executable = loopai_python_executable()
    corepack = corepack_path() or "未检测到"
    runner = runner_dir()
    node = shutil.which("node") or "未检测到"
    db_path = Path(_project_root()) / "api" / "db" / "db.sqlite3"
    sdk_text = "yes" if sdk_available() else "no"
    return "\n".join([
        f"- **Python 解释器**：`{python_executable}`（loopai 包：`{loopai.__file__}`）",
        f"- **Node / corepack**：`{node}` / `{corepack}`",
        f"- **codex-runner**：`{runner}`（Codex SDK 就绪：{sdk_text}）",
        f"- **CODEX_HOME**：`{home}`",
        f"- **后端 API**：`http://127.0.0.1:{api_port}`（uvicorn `api.app.main:app`）",
        f"- **模型代理**：`{proxy_base}`（默认模型：`{default_model}`）",
        f"- **数据湖指针**：`{lake['link']}` → `{lake['warehouse']}`（loaded: {lake['loaded']}）",
        f"- **任务库**：`{db_path}`",
        f"- **标准命令前缀**：`{python_executable} -m loopai.skills.ObtainerCLI.cli dm --root {lake['warehouse']} <cmd> --json`",
    ])


def ensure_environment_manifest(home: Path) -> None:
    """Render the runtime environment manifest into a Codex home's AGENTS.md."""
    target = home / "AGENTS.md"
    manifest = environment_manifest_markdown(home)
    if target.is_file():
        text = target.read_text(encoding="utf-8")
        if ENV_MANIFEST_MARKER in text:
            target.write_text(text.replace(ENV_MANIFEST_MARKER, manifest), encoding="utf-8")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "## 运行环境清单\n\n"
        "以下运行环境信息由系统自动探测并注入，直接按清单使用：\n\n"
        f"{manifest}\n",
        encoding="utf-8",
    )


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

def build_prompt(
    file_path: str, dataset: str, root: str, quality_level: str
) -> str:
    quality_level = schema.validate_quality_level(quality_level)
    python_executable = loopai_python_executable()
    dm = f"{python_executable} -m loopai.agents.Obtainer.datamixer --root {root}"
    instructions = ingest_instructions_path()
    body = instructions.read_text() if instructions else ""
    return (
        "Ingest a data file into this LoopAI data-lake warehouse. Inspect the "
        "file yourself and drive the provided CLI; do not write the catalog or "
        "blobs directly.\n\n"
        f"FILE={file_path}\nDATASET={dataset}\nQUALITY_LEVEL={quality_level}\n"
        f"ROOT={root}\nDM={dm}\n\n"
        "Use QUALITY_LEVEL exactly as provided in the final ingest command; "
        "do not infer, replace, or omit it.\n\n"
        "When done, return the structured JSON result (summary, format, stage, "
        "tags, quality_level, records_ingested, dataset, dataset_card, derived_fields, "
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
    if not runner:
        raise CodexError("LoopAI codex-runner not found")
    # Prompt is delivered via a temp file (runner reads CODEX_PROMPT_FILE) to
    # avoid execve ARG_MAX limits when the prompt is large; stdin is unreliable
    # through corepack/yarn's nested process spawns.
    fd, prompt_path = tempfile.mkstemp(prefix="codex_prompt_", suffix=".txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(prompt)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    corepack = corepack_path()
    cmd: list[str] | None = None
    if corepack:
        try:
            check = subprocess.run(
                [corepack, "yarn", "--version"], cwd=runner,
                env={**os.environ, "PATH": runner_process_path()},
                capture_output=True, timeout=20,
            )
            if check.returncode == 0:
                cmd = [corepack, "yarn", "dev", "-"]
        except (OSError, subprocess.SubprocessError):
            pass
    if cmd is None:
        cmd = _direct_runner_command(runner)
    if cmd is None:
        raise CodexError(
            "codex-runner is unavailable: Corepack failed and the direct Node runner "
            "dependencies are missing"
        )
    merged_env = {**os.environ, **env}
    merged_env["CODEX_PROMPT_FILE"] = prompt_path
    python_executable = loopai_python_executable()
    merged_env["LOOPAI_PYTHON_EXECUTABLE"] = python_executable
    merged_env["PATH"] = runner_process_path(python_executable, merged_env.get("PATH"))
    library_path = runner_library_path(
        python_executable, merged_env.get("LD_LIBRARY_PATH")
    )
    if library_path:
        merged_env["LD_LIBRARY_PATH"] = library_path
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

    try:
        return proc.wait(), "".join(stdout_lines), "".join(stderr_lines)
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


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
                on_event: Callable[[dict], None] | None = None,
                codex_home_override: str | Path | None = None) -> dict:
    """Run the shared Codex runner with an optional isolated home.

    Managed Obtainer workers pass their role-specific home explicitly so an
    inherited interactive ``CODEX_HOME`` cannot redirect provider config or
    runtime state into a user's private Codex installation. Callers that do
    not pass an override retain the historical environment-based behavior.
    """
    home = Path(codex_home_override) if codex_home_override else codex_home()
    if home:
        ensure_environment_manifest(home)
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

def codex_ingest(store, path, model: str, *, quality_level: str,
                 dataset: str | None = None, timeout: int = 600,
                 network: bool = True) -> dict:
    quality_level = schema.validate_quality_level(quality_level)
    if not model:
        raise CodexError("the codex engine requires --model (a model-pool name)")
    spec = ModelPool(store.root).get(model)
    prov = provider_from_model(spec)
    abspath = str(Path(path).resolve())
    dataset = dataset or Path(path).stem or "agent_dataset"
    root = str(store.root)

    before = store.catalog.count()
    prompt = build_prompt(abspath, dataset, root, quality_level)
    result = run_via_sdk(prompt, prov, cwd=root, timeout=timeout, network=network)
    after = store.catalog.count()         # Codex committed via the CLI subprocess

    ds_id = store.catalog.resolve_dataset(dataset)
    return {
        "engine": "codex", "model": model, "dataset": dataset,
        "dataset_id": ds_id,
        "quality_level": quality_level,
        "ingested": max(after - before, result.get("records_ingested") or 0),
        "codex_result": result,
        "review": result.get("summary", ""),
        "format": result.get("format"),
    }
