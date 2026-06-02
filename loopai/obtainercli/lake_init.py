from __future__ import annotations

from pathlib import Path

from .config import write_lake_config
from .models import utc_now
from .tables import ensure_tables


def init_lake(
    *,
    root: str | Path,
    link_path: str | Path | None = None,
    if_not_exists: bool = False,
    auto_embed: bool = True,
    embedding_provider: str = "openai-compatible",
    embedding_base_url: str = "http://127.0.0.1:8000/v1",
    embedding_api_key: str = "",
    embedding_model: str = "BAAI/bge-small-zh-v1.5",
    embedding_backend: str = "local-jsonl",
    embedding_text_field: str = "text",
) -> dict:
    lake_root = Path(root).expanduser().resolve()
    if lake_root.exists() and any(lake_root.iterdir()) and not if_not_exists:
        raise FileExistsError(f"Lake root already exists: {lake_root}")
    lake_root.mkdir(parents=True, exist_ok=True)
    config_kwargs = {
        "auto_embed": auto_embed,
        "embedding_provider": embedding_provider,
        "embedding_base_url": embedding_base_url,
        "embedding_api_key": embedding_api_key,
        "embedding_model": embedding_model,
        "embedding_backend": embedding_backend,
        "embedding_text_field": embedding_text_field,
    }
    write_lake_config(lake_root / "lake.yaml", root=lake_root, **config_kwargs)
    if link_path is not None:
        write_lake_config(Path(link_path), root=lake_root, **config_kwargs)
    ensure_tables(lake_root)
    return {
        "ok": True,
        "command": "lake init",
        "lake_root": str(lake_root),
        "lake_config": str(link_path) if link_path else str(lake_root / "lake.yaml"),
        "catalog": "local-parquet",
        "status": "success",
        "warnings": [],
        "auto_embed": auto_embed,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "created_at": utc_now(),
    }
