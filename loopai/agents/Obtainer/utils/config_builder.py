from typing import Tuple, Dict, Any

from omegaconf import OmegaConf


def _get(ds: Any, key: str):
    try:
        if isinstance(ds, dict):
            return ds.get(key)
        return getattr(ds, key) if hasattr(ds, key) else ds.get(key)
    except Exception:
        return None


def _has(ds: Any, key: str) -> bool:
    try:
        return key in ds or hasattr(ds, key)
    except Exception:
        return False


def build_obtainer_rag_config(
    cfg,
    *,
    api_key: str,
    tavily_api_key: str = "",
    rag_api_key: str = "",
    kaggle_username: str = "",
    kaggle_key: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Assemble obtainer & RAG related config from OmegaConf cfg.

    Returns:
        obtainer_config, rag_config (dicts)
    """
    ds = getattr(cfg, "default_states", {}) or {}
    obtainer_config: Dict[str, Any] = {}

    # Required credentials/model/base
    if _has(ds, "obtainer_model_path") and _get(ds, "obtainer_model_path"):
        obtainer_config["obtainer_model_path"] = _get(ds, "obtainer_model_path")
    if _has(ds, "obtainer_base_url") and _get(ds, "obtainer_base_url"):
        obtainer_config["obtainer_base_url"] = _get(ds, "obtainer_base_url")
    if _has(ds, "obtainer_api_key") and _get(ds, "obtainer_api_key"):
        obtainer_config["obtainer_api_key"] = _get(ds, "obtainer_api_key")
    else:
        # fallback to starter api_key
        obtainer_config["obtainer_api_key"] = api_key

    # Optional obtainer params
    for key in [
        "obtainer_temperature",
        "obtainer_search_engine",
        "obtainer_max_urls",
        "obtainer_max_download_subtasks",
        "obtainer_debug",
    ]:
        if _has(ds, key):
            obtainer_config[key] = _get(ds, key)
    if _has(ds, "obtainer_category") and _get(ds, "obtainer_category"):
        obtainer_config["obtainer_category"] = str(_get(ds, "obtainer_category")).upper()

    # External keys
    obtainer_config["obtainer_tavily_api_key"] = tavily_api_key or ""
    obtainer_config["obtainer_kaggle_username"] = kaggle_username or ""
    obtainer_config["obtainer_kaggle_key"] = kaggle_key or ""

    # RAG config
    rag_config: Dict[str, Any] = {}
    rag_cfg = getattr(cfg, "rag", None)
    if rag_cfg:
        if hasattr(rag_cfg, "reset"):
            rag_config["obtainer_reset_rag"] = rag_cfg.reset
        if hasattr(rag_cfg, "embed_model"):
            embed_model = rag_cfg.embed_model
            if embed_model:
                rag_config["obtainer_rag_embed_model"] = embed_model
        if hasattr(rag_cfg, "collection_name"):
            rag_config["obtainer_rag_collection_name"] = rag_cfg.collection_name
        if hasattr(rag_cfg, "api_base_url") and rag_cfg.api_base_url:
            rag_config["obtainer_rag_api_base_url"] = rag_cfg.api_base_url
        if rag_api_key:
            rag_config["obtainer_rag_api_key"] = rag_api_key

    return obtainer_config, rag_config



