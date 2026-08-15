"""Shared multi-level HuggingFace endpoint fallback.

The hub endpoint is resolved at runtime instead of being hard-wired, so a dead
mirror no longer sinks every download.  ``datasets`` reads its endpoint from
``datasets.config.HF_ENDPOINT`` at call time (``HfApi(endpoint=config.HF_ENDPOINT)``)
and ``huggingface_hub`` reads ``constants.ENDPOINT``, so both are reassigned
together with the environment variables when switching mirrors.
"""
from __future__ import annotations

import os

DEFAULT_HF_ENDPOINTS = (
    "https://huggingface.co",
    "https://hf-mirror.com",
)


def hf_endpoint_chain() -> list[str]:
    """Multi-level HuggingFace endpoint chain for fallback downloads.

    Precedence: an explicitly configured ``HF_ENDPOINT``/``HF_HUB_ENDPOINT``
    (if any), then the official hub, then the hf-mirror, then any extra mirrors
    listed in ``HF_ENDPOINT_FALLBACKS`` (comma separated).  Duplicates are
    dropped so one broken endpoint never stalls the whole download twice.
    """
    chain: list[str] = []
    explicit = (
        os.environ.get("HF_ENDPOINT")
        or os.environ.get("HF_HUB_ENDPOINT")
        or ""
    ).strip().rstrip("/")
    if explicit:
        chain.append(explicit)
    for endpoint in DEFAULT_HF_ENDPOINTS:
        if endpoint not in chain:
            chain.append(endpoint)
    for raw in (os.environ.get("HF_ENDPOINT_FALLBACKS") or "").split(","):
        endpoint = raw.strip().rstrip("/")
        if endpoint and endpoint not in chain:
            chain.append(endpoint)
    return chain


def endpoint_reachable(endpoint: str, timeout: float = 3.0) -> bool:
    """Quick liveness probe so a dead endpoint is skipped within seconds.

    A non-5xx answer (including 401/403/404) means the service is up; 5xx and
    network failures mean the endpoint is unusable and should be skipped.  The
    probe never retries, so one broken mirror cannot stall the fallback chain.
    """
    try:
        import requests

        response = requests.get(
            f"{endpoint}/api/models?limit=1",
            timeout=timeout,
            allow_redirects=True,
        )
        return response.status_code < 500
    except Exception:
        return False


def apply_hf_endpoint(endpoint: str) -> None:
    """Point the huggingface runtime at ``endpoint`` for this process."""
    os.environ["HF_ENDPOINT"] = endpoint
    os.environ["HF_HUB_ENDPOINT"] = endpoint
    try:
        import datasets.config as datasets_config

        datasets_config.HF_ENDPOINT = endpoint
    except Exception:
        pass
    try:
        import huggingface_hub.constants as hub_constants

        hub_constants.ENDPOINT = endpoint
    except Exception:
        pass
