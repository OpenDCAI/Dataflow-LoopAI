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
    mineru_url: str = "http://127.0.0.1:7986",
    mineru_python: str = "",
    mineru_model: str = "",
    mineru_gpu: str = "0",
    mineru_transport: str = "http",
    mineru_backend: str = "vllm",
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
                "# MinerU-HTML (Dripper) document parsing service",
                f"mineru_url: {mineru_url}",
                f"mineru_python: {mineru_python}",
                f"mineru_model: {mineru_model}",
                f"mineru_gpu: {mineru_gpu}",
                f"mineru_transport: {mineru_transport}",
                f"mineru_backend: {mineru_backend}",
                "",
                "# Persistent Obtainer acquisition context (no credentials)",
                *[f"{key}: {value}" for key, value in context.items()],
                "",
            ]
        ),
        encoding="utf-8",
    )


SERVICE_CONFIG_KEYS: tuple[str, ...] = (
    "embedding_provider",
    "embedding_base_url",
    "embedding_api_key",
    "embedding_model",
    "embedding_backend",
    "embedding_text_field",
    "mineru_url",
    "mineru_python",
    "mineru_model",
    "mineru_gpu",
    "mineru_transport",
    "mineru_backend",
)


def update_lake_service_config(
    path: str | Path,
    *,
    embedding: Mapping[str, Any] | None = None,
    mineru: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Patch the embedding/MinerU-HTML service keys in an existing lake pointer.

    Only the managed service keys are replaced; every other line (root,
    warehouse, obtainer context, comments) is preserved. Returns
    pointer_missing when the lake pointer does not exist so callers can treat
    the update as a no-op.
    """
    link = Path(path).expanduser().resolve()
    if not link.is_file():
        return {
            "ok": True,
            "status": "pointer_missing",
            "lake_config": str(link),
            "updated_keys": [],
        }
    embedding = dict(embedding or {})
    mineru = dict(mineru or {})
    values = read_lake_config(link)

    def pick(key: str, *, source: Dict[str, Any], fallback: str, legacy_key: str) -> str:
        if key in source and source[key] is not None:
            return str(source[key]).strip()
        if legacy_key in values and values.get(legacy_key) is not None:
            return str(values[legacy_key]).strip()
        return fallback

    updates: Dict[str, str] = {
        "embedding_provider": pick(
            "provider", source=embedding, fallback="openai-compatible", legacy_key="embedding_provider"
        ),
        "embedding_base_url": pick(
            "base_url", source=embedding, fallback="http://127.0.0.1:8000/v1", legacy_key="embedding_base_url"
        ),
        "embedding_api_key": pick(
            "api_key", source=embedding, fallback="", legacy_key="embedding_api_key"
        ),
        "embedding_model": pick(
            "model", source=embedding, fallback="BAAI/bge-small-zh-v1.5", legacy_key="embedding_model"
        ),
        "embedding_backend": pick(
            "backend", source=embedding, fallback="local-jsonl", legacy_key="embedding_backend"
        ),
        "embedding_text_field": pick(
            "text_field", source=embedding, fallback="text", legacy_key="embedding_text_field"
        ),
        "mineru_url": pick("url", source=mineru, fallback="http://127.0.0.1:7986", legacy_key="mineru_url"),
        "mineru_python": pick("python", source=mineru, fallback="", legacy_key="mineru_python"),
        "mineru_model": pick("model", source=mineru, fallback="", legacy_key="mineru_model"),
        "mineru_gpu": pick("gpu", source=mineru, fallback="0", legacy_key="mineru_gpu"),
        "mineru_transport": pick(
            "transport", source=mineru, fallback="http", legacy_key="mineru_transport"
        ),
        "mineru_backend": pick(
            "backend", source=mineru, fallback="vllm", legacy_key="mineru_backend"
        ),
    }

    lines = link.read_text(encoding="utf-8").splitlines()
    existing: Dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].strip()
        if key in SERVICE_CONFIG_KEYS:
            existing.setdefault(key, index)

    mineru_added = False
    for key in SERVICE_CONFIG_KEYS:
        value = updates[key]
        if key in existing:
            lines[existing[key]] = f"{key}: {value}"
        else:
            if key.startswith("mineru_") and not mineru_added:
                lines.append("")
                lines.append("# MinerU-HTML (Dripper) document parsing service")
                mineru_added = True
            lines.append(f"{key}: {value}")
    link.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "status": "updated",
        "lake_config": str(link),
        "updated_keys": list(updates.keys()),
        "values": updates,
    }


def resolve_lake_root(lake: str | Path) -> Path:
    path = Path(lake)
    if path.is_file():
        values = read_lake_config(path)
        root = values.get("root")
        if not root:
            raise ValueError(f"Lake config missing root: {path}")
        return Path(root).expanduser().resolve()
    return path.expanduser().resolve()
