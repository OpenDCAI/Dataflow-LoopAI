#!/usr/bin/env python
"""MinerU-HTML (Dripper-compatible) HTTP service.

Serves the same /health and /extract endpoints the DataMixer webpage_to_pt
operator expects for ``mineru_transport: http``. Run with the dedicated
``mineruhtml`` Conda environment:

  /data/xuebinrui/miniconda3/envs/mineruhtml/bin/python scripts/mineruhtml_server.py \
      --model /data/xuebinrui/model/MinerU-HTML-v1.1-hunyuan0.5B-compact --port 7986 --gpu 1
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

import uvicorn
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_MODEL = "/data/xuebinrui/model/MinerU-HTML-v1.1-hunyuan0.5B-compact"
DEFAULT_PORT = 7986

_extractor = None
_extractor_lock = threading.Lock()
_args: argparse.Namespace | None = None


def _build_extractor(model: str, *, gpu: str, gpu_memory_utilization: float,
                     max_context_window: int, max_tokens: int,
                     fallback: str, output_format: str):
    from mineru_html import MinerUHTMLConfig, MinerUHTMLGeneric
    from mineru_html.inference.factory import create_vllm_backend

    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu)
    os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
    config = MinerUHTMLConfig(
        use_fall_back=fallback,
        early_load=True,
        prompt_version="short_compact",
        response_format="compact",
        output_format=output_format,
    )
    backend = create_vllm_backend(
        model_path=model,
        response_format="compact",
        max_context_window=max_context_window,
        model_init_kwargs={
            "tensor_parallel_size": 1,
            "gpu_memory_utilization": gpu_memory_utilization,
            "enforce_eager": True,
        },
        model_gen_kwargs={
            "max_tokens": max_tokens,
            "temperature": 0,
        },
    )
    return MinerUHTMLGeneric(backend, config)


def get_extractor():
    global _extractor
    if _extractor is None:
        with _extractor_lock:
            if _extractor is None:
                _extractor = _build_extractor(
                    _args.model,
                    gpu=_args.gpu,
                    gpu_memory_utilization=_args.gpu_memory_utilization,
                    max_context_window=_args.max_context_window,
                    max_tokens=_args.max_tokens,
                    fallback=_args.fallback,
                    output_format=_args.output_format,
                )
    return _extractor


class ExtractRequest(BaseModel):
    html: str


def create_app() -> "FastAPI":
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="MinerU-HTML Dripper")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/extract")
    def extract(payload: ExtractRequest) -> dict:
        try:
            extractor = get_extractor()
            with _extractor_lock:
                cases = extractor.process([payload.html])
            case = cases[0]
            output = getattr(case, "output_data", None)
            error = str(getattr(case, "error", "") or "").strip() or None
            if error:
                raise HTTPException(status_code=500, detail=error)
            main_content = getattr(output, "main_content", None)
            return {
                "main_html": getattr(case, "main_html", None),
                "main_content": str(main_content) if main_content is not None else None,
                "error": None,
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - surface to the caller
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the MinerU-HTML (Dripper-compatible) HTTP service.")
    parser.add_argument("--model", default=os.getenv("MINERU_HTML_MODEL", DEFAULT_MODEL))
    parser.add_argument("--host", default=os.getenv("MINERU_HTML_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MINERU_HTML_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--gpu", default=os.getenv("MINERU_HTML_GPU", "0"))
    parser.add_argument("--gpu-memory-utilization", type=float,
                        default=float(os.getenv("MINERU_HTML_GPU_MEMORY_UTILIZATION", "0.3")))
    parser.add_argument("--max-context-window", type=int,
                        default=int(os.getenv("MINERU_HTML_MAX_CONTEXT_WINDOW", "8192")))
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("MINERU_HTML_MAX_TOKENS", "2048")))
    parser.add_argument("--fallback", default=os.getenv("MINERU_HTML_FALLBACK", "trafilatura"),
                        choices=("trafilatura", "bypass", "empty"))
    parser.add_argument("--output-format", default=os.getenv("MINERU_HTML_OUTPUT_FORMAT", "mm_md"))
    return parser


def main() -> None:
    global _args
    _args = build_parser().parse_args()
    model = Path(_args.model).expanduser().resolve()
    if not model.exists():
        raise SystemExit(f"Model directory does not exist: {model}")
    # Preload the model so the first /extract is not slow.
    get_extractor()
    uvicorn.run(create_app(), host=_args.host, port=_args.port)


if __name__ == "__main__":
    main()
