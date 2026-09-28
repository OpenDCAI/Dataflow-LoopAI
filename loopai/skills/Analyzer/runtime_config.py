from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from loopai.schema.model_pool import StarterModelPool

from .state_bridge import load_system_runtime_config

_DEFAULT_CHECKPOINT_PATH = "outputs/analyzer_checkpoints.sqlite"
_DEFAULT_THREAD_ID = "analyzer-default"


def get_version_checkpoint_path(
    output_dir: str,
    task_id: str,
    version_id: str,
) -> str:
    """Return the state checkpoint located beside this Analyzer run's artifacts."""
    return str(
        Path(output_dir)
        / str(task_id)
        / "analyzer"
        / str(version_id)
        / "state_checkpoint.sqlite"
    )


def find_latest_version_checkpoint(output_dir: str, task_id: str) -> Optional[tuple[str, str]]:
    """Find the newest version checkpoint for a task without reusing a finished run blindly."""
    analyzer_dir = Path(output_dir) / str(task_id) / "analyzer"
    candidates = []
    for version_dir in analyzer_dir.iterdir() if analyzer_dir.is_dir() else []:
        checkpoint = version_dir / "state_checkpoint.sqlite"
        if version_dir.is_dir() and checkpoint.exists():
            candidates.append((checkpoint.stat().st_mtime, version_dir.name, str(checkpoint)))
    if not candidates:
        return None
    _, version_id, checkpoint_path = max(candidates)
    return version_id, checkpoint_path


def cleanup_old_analyzer_checkpoints(
    output_dir: str,
    task_id: str,
    keep_version_id: str,
) -> None:
    """Keep only the active version's state checkpoint for a task.

    Historical reports and stream event files remain untouched.
    """
    analyzer_dir = Path(output_dir) / str(task_id) / "analyzer"
    if not analyzer_dir.is_dir():
        return
    for version_dir in analyzer_dir.iterdir():
        if not version_dir.is_dir() or version_dir.name == str(keep_version_id):
            continue
        checkpoint = version_dir / "state_checkpoint.sqlite"
        try:
            checkpoint.unlink(missing_ok=True)
            # Remove only now-empty version directories; reports/events remain.
            version_dir.rmdir()
        except OSError:
            continue


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _clean_version_id(value: Any) -> Any:
    if value in (None, "", "default"):
        return None
    return value


def _analyzer(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    analyzer = state.setdefault("analyzer", {})
    if not isinstance(analyzer, dict):
        state["analyzer"] = {}
        return state["analyzer"]
    return analyzer


def _system_runtime(state: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _first_non_empty(
        kwargs.get("thread_id"),
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state.get("task_id") if isinstance(state, dict) else None,
    )
    runtime = load_system_runtime_config(task_id)
    if runtime:
        return runtime
    # Standalone CLI/worker runs may not have DB_PATH/TASK_ID.  The Starter
    # YAML is still the authoritative model-pool source in that mode.
    try:
        from loopai.schema.model_pool import load_starter_system_config_sync
        return load_starter_system_config_sync(prefer_db=True) or {}
    except Exception:
        return {}


def _needs_llm(analyzer: Dict[str, Any], kwargs: Dict[str, Any]) -> bool:
    explicit = kwargs.get("require_api_key")
    if explicit is not None:
        return bool(explicit)
    return bool(
        _first_non_empty(
            kwargs.get("analyzer_model"),
            kwargs.get("model"),
            os.getenv("ANALYZER_MODEL"),
            analyzer.get("analyze_model_path"),
            analyzer.get("model"),
        )
        or _first_non_empty(
            kwargs.get("analyzer_base_url"),
            kwargs.get("base_url"),
            os.getenv("ANALYZER_BASE_URL"),
            analyzer.get("analyze_base_url"),
            analyzer.get("base_url"),
        )
    )


def resolve_analyzer_runtime_config(
    state: Optional[Dict[str, Any]],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Resolve Analyzer runtime values from the shared Starter model pool.

    Codex is used for orchestration and medium for concrete analysis calls;
    credentials never come from Analyzer task state.
    """
    analyzer = _analyzer(state)
    system_runtime = _system_runtime(state, kwargs)

    pool = StarterModelPool(system_runtime)
    # Analyzer has two distinct model roles: Codex for orchestration/SDK
    # workers and medium for concrete scoring/labeling.  Both are resolved by
    # the shared StarterModelPool; medium falls back to the default entry.
    codex_provider = pool.resolve_role_provider("codex")
    requested_mid = _first_non_empty(
        kwargs.get("analyzer_mid_model"),
        kwargs.get("mid_model"),
        kwargs.get("analyzer_model"),
        kwargs.get("model"),
        system_runtime.get("analyzer_mid_model"),
        system_runtime.get("mid_model"),
        analyzer.get("analyzer_mid_model"),
    )
    mid_entry = pool.get_entry_by_name(requested_mid) if requested_mid else None
    if mid_entry is None or mid_entry.tier != "medium":
        requested_mid = None
    mid_provider = pool.resolve_role_provider(
        "medium",
        requested=requested_mid,
    )
    pooled_model = mid_provider.model if mid_provider else ""
    pooled_base_url = mid_provider.base_url if mid_provider else ""
    pooled_api_key = mid_provider.api_key if mid_provider else ""
    codex_model = codex_provider.model if codex_provider else ""
    codex_base_url = codex_provider.base_url if codex_provider else ""

    api_key = pooled_api_key

    model = _first_non_empty(
        # Explicit model aliases are retained only when they resolve to a
        # registered medium entry; otherwise the role resolver is canonical.
        pooled_model,
    )
    base_url = _first_non_empty(
        pooled_base_url,
    )
    task_id = _first_non_empty(
        kwargs.get("thread_id"),
        kwargs.get("task_id"),
        os.getenv("TASK_ID"),
        state.get("task_id") if isinstance(state, dict) else None,
        _DEFAULT_THREAD_ID,
    )
    db_path = _first_non_empty(
        kwargs.get("db_path"),
        os.getenv("DB_PATH"),
        analyzer.get("db_path"),
    )
    checkpoint_path = _first_non_empty(
        kwargs.get("checkpoint_path"),
        os.getenv("ANALYZER_CHECKPOINT_PATH"),
        analyzer.get("checkpoint_path"),
        _DEFAULT_CHECKPOINT_PATH,
    )
    version_id = _first_non_empty(
        _clean_version_id(kwargs.get("version_id")),
        _clean_version_id(kwargs.get("run_id")),
        _clean_version_id(os.getenv("ANALYZER_VERSION_ID")),
        _clean_version_id(os.getenv("VERSION_ID")),
        _clean_version_id(state.get("version_id") if isinstance(state, dict) else None),
        _clean_version_id(analyzer.get("version_id")),
        _clean_version_id(analyzer.get("run_id")),
    )
    output_dir = _first_non_empty(
        kwargs.get("output_dir"),
        analyzer.get("output_dir"),
        state.get("output_dir") if isinstance(state, dict) else None,
        "./outputs",
    )
    request_timeout_seconds = float(_first_non_empty(
        kwargs.get("analyze_request_timeout_seconds"),
        os.getenv("ANALYZER_REQUEST_TIMEOUT_SECONDS"),
        analyzer.get("analyze_request_timeout_seconds"),
        300,
    ))
    if request_timeout_seconds <= 0:
        raise ValueError("analyze_request_timeout_seconds must be greater than 0")

    require_api_key = kwargs.get("require_api_key")
    needs_llm = bool(require_api_key) if require_api_key is not None else bool(model or base_url or _needs_llm(analyzer, kwargs))
    if needs_llm and mid_provider is None and codex_provider is None:
        raise RuntimeError("Analyzer requires a configured Starter model pool (medium role)")
    if needs_llm and not api_key:
        raise RuntimeError("missing API key for the configured Analyzer model-pool provider")

    if isinstance(state, dict):
        if task_id and not state.get("task_id"):
            state["task_id"] = task_id
        if output_dir and not state.get("output_dir"):
            state["output_dir"] = output_dir
        if db_path:
            state["DB_PATH"] = db_path

    if model:
        analyzer["analyze_model_path"] = model
    if base_url:
        analyzer["analyze_base_url"] = base_url
    analyzer.pop("analyze_api_key", None)
    analyzer.pop("api_key", None)
    if api_key:
        os.environ["_LOOPAI_ANALYZER_RUNTIME_API_KEY"] = str(api_key)
    if checkpoint_path:
        analyzer["checkpoint_path"] = checkpoint_path
    if version_id:
        if isinstance(state, dict):
            state["version_id"] = str(version_id)
        analyzer["version_id"] = str(version_id)
    if db_path:
        analyzer["db_path"] = db_path
    if output_dir:
        analyzer["output_dir"] = output_dir
    analyzer["analyze_request_timeout_seconds"] = request_timeout_seconds
    analyzer["codex_model"] = codex_model
    analyzer["codex_base_url"] = codex_base_url
    analyzer["mid_model"] = model or pooled_model
    analyzer["model_resolution"] = {
        "codex": {
            "model": codex_model,
            "base_url": codex_base_url,
            "resolved": bool(codex_provider),
        },
        "medium": {
            "model": pooled_model,
            "base_url": pooled_base_url,
            "resolved": bool(mid_provider),
            "fallback": bool(mid_provider.fallback) if mid_provider else False,
        },
    }
    if task_id and version_id and output_dir:
        analyzer["runtime_output_dir"] = str(
            Path(str(output_dir))
            / str(task_id)
            / "analyzer"
            / str(version_id)
        )
    elif analyzer.get("runtime_output_dir", "").endswith("/default"):
        analyzer.pop("runtime_output_dir", None)

    return {
        "thread_id": str(task_id or _DEFAULT_THREAD_ID),
        "version_id": str(version_id) if version_id else "",
        "checkpoint_path": str(checkpoint_path or _DEFAULT_CHECKPOINT_PATH),
        "output_dir": str(output_dir or "./outputs"),
        "db_path": db_path,
        "analyzer_model": model,
        "analyzer_base_url": base_url,
        "analyzer_codex_model": codex_model,
        "analyzer_codex_base_url": codex_base_url,
        "analyzer_mid_model": model or pooled_model,
        "analyzer_mid_base_url": pooled_base_url,
        "analyzer_model_resolution": analyzer.get("model_resolution", {}),
        "analyze_request_timeout_seconds": request_timeout_seconds,
        "has_analyzer_api_key": bool(api_key),
        "api_key_source": (
            "starter_model_pool_proxy" if api_key else None
        ),
    }
