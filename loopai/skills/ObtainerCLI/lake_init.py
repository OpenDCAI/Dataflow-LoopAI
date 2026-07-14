from __future__ import annotations

from pathlib import Path

from .datamixer_adapter import init_datamixer_lake


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
    return init_datamixer_lake(
        root=root,
        link_path=link_path,
        if_not_exists=if_not_exists,
        auto_embed=auto_embed,
        embedding_provider=embedding_provider,
        embedding_base_url=embedding_base_url,
        embedding_api_key=embedding_api_key,
        embedding_model=embedding_model,
        embedding_backend=embedding_backend,
        embedding_text_field=embedding_text_field,
    )
