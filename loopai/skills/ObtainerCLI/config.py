from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping


# These values are intentionally credential-free. A lake pointer is shared by
# the UI, ObtainerCLI and isolated workers, so it must retain operational
# defaults without copying API keys into project state.
# Active run bindings tied to a concrete task/run. These are cleared by
# `dm lake unbind` so a stale task_id never leaks into a new task's run.
OBTAINER_ACTIVE_BINDING_KEYS: tuple[str, ...] = (
    "obtainer_active_acquisition_run",
    "obtainer_active_campaign_id",
    "obtainer_active_l1_dataset",
    "obtainer_active_task_id",
)


OBTAINER_CONTEXT_DEFAULTS: Dict[str, str] = {
    "obtainer_webagent": "domain_data_acquisition",
    "obtainer_webagent_model": "",
    "obtainer_resolved_model": "",
    "obtainer_model_source": "",
    "obtainer_webagent_workers": "4",
    "obtainer_webagent_subquery_count": "24",
    "obtainer_webagent_auto_process": "false",
    "obtainer_active_acquisition_run": "",
    "obtainer_active_campaign_id": "",
    "obtainer_active_l1_dataset": "",
    "obtainer_active_task_id": "",
}


def _parse_simple_yaml(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def read_lake_config(path: str | Path) -> Dict[str, str]:
    return _parse_simple_yaml(Path(path))


def read_lake_config_for_lake(lake: str | Path) -> Dict[str, str]:
    path = Path(lake)
    if path.is_file():
        return read_lake_config(path)
    config_path = path / "lake.yaml"
    if config_path.is_file():
        return read_lake_config(config_path)
    return {}


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def obtainer_context(values: Mapping[str, Any] | None = None) -> Dict[str, str]:
    """Return the persisted, credential-free Obtainer runtime context."""
    source = values or {}
    context = dict(OBTAINER_CONTEXT_DEFAULTS)
    for key in context:
        value = source.get(key)
        if value is not None:
            context[key] = str(value).strip()
    return context


def write_lake_config(
    path: Path,
    *,
    root: Path,
    warehouse: Path | None = None,
    catalog: str = "datamixer",
    backend: str = "datamixer",
    auto_embed: bool = True,
    embedding_provider: str = "openai-compatible",
    embedding_base_url: str = "http://127.0.0.1:8000/v1",
    embedding_api_key: str = "",
    embedding_model: str = "BAAI/bge-small-zh-v1.5",
    embedding_backend: str = "local-jsonl",
    embedding_text_field: str = "text",
    auto_embed_async: bool = True,
    auto_embed_batch_size: int = 512,
    obtainer_context_values: Mapping[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    warehouse = warehouse or root / "warehouse"
    context = obtainer_context(obtainer_context_values)
    path.write_text(
        "\n".join(
            [
                "# LoopAI ObtainerCLI lake pointer",
                f"root: {root}",
                f"warehouse: {warehouse}",
                f"catalog: {catalog}",
                f"backend: {backend}",
                "namespace: loopai",
                f"auto_embed: {_bool_text(auto_embed)}",
                f"embedding_provider: {embedding_provider}",
                f"embedding_base_url: {embedding_base_url}",
                f"embedding_api_key: {embedding_api_key}",
                f"embedding_model: {embedding_model}",
                f"embedding_backend: {embedding_backend}",
                f"embedding_text_field: {embedding_text_field}",
                f"auto_embed_async: {_bool_text(auto_embed_async)}",
                f"auto_embed_batch_size: {int(auto_embed_batch_size)}",
                "",
                "# Persistent Obtainer acquisition context (no credentials)",
                *[f"{key}: {value}" for key, value in context.items()],
                "",
            ]
        ),
        encoding="utf-8",
    )


def resolve_lake_root(lake: str | Path) -> Path:
    path = Path(lake)
    if path.is_file():
        values = read_lake_config(path)
        root = values.get("root")
        if not root:
            raise ValueError(f"Lake config missing root: {path}")
        return Path(root).expanduser().resolve()
    return path.expanduser().resolve()
