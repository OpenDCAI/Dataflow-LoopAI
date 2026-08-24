#!/usr/bin/env python
"""OpenAI-compatible embedding server for ObtainerCLI (supports multi-worker).

Usage:
  python scripts/obtainercli_embedding_server.py --workers 4 --port 8000

Multi-worker mode spawns ``--workers`` uvicorn processes; each worker lazily
loads its own model copy from the same model directory. Settings can be passed
as CLI flags or via OBTAINERCLI_EMBED_* environment variables.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loopai.skills.ObtainerCLI.embedding_server import TransformersEmbedder, create_app


DEFAULT_MODEL_DIR = "/mnt/paper2any/xbr/loopai0531/models/BAAI/bge-small-zh-v1.5"
DEFAULT_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start an OpenAI-compatible ObtainerCLI embedding server.")
    parser.add_argument("--model-dir", default=os.getenv("OBTAINERCLI_EMBED_MODEL_DIR", DEFAULT_MODEL_DIR))
    parser.add_argument("--model-name", default=os.getenv("OBTAINERCLI_EMBED_MODEL_NAME", DEFAULT_MODEL_NAME))
    parser.add_argument("--host", default=os.getenv("OBTAINERCLI_EMBED_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OBTAINERCLI_EMBED_PORT", "8000")))
    parser.add_argument("--device", default=os.getenv("OBTAINERCLI_EMBED_DEVICE", "auto"))
    parser.add_argument("--dtype", default=os.getenv("OBTAINERCLI_EMBED_DTYPE", "auto"))
    parser.add_argument("--max-length", type=int, default=int(os.getenv("OBTAINERCLI_EMBED_MAX_LENGTH", "512")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("OBTAINERCLI_EMBED_WORKERS", "1")))
    return parser


def _build_app() -> "FastAPI":
    """Build the FastAPI app from environment variables (model loads lazily).

    No model directory check here: the embedder only touches the filesystem on
    the first request, so the module can be imported by uvicorn workers (and
    by ``--help``) without requiring the model to exist yet.
    """
    from fastapi import FastAPI

    model_dir = Path(os.getenv("OBTAINERCLI_EMBED_MODEL_DIR", DEFAULT_MODEL_DIR)).expanduser().resolve()
    embedder = TransformersEmbedder(
        model_path=model_dir,
        device=os.getenv("OBTAINERCLI_EMBED_DEVICE", "auto"),
        dtype=os.getenv("OBTAINERCLI_EMBED_DTYPE", "auto"),
        max_length=int(os.getenv("OBTAINERCLI_EMBED_MAX_LENGTH", "512")),
    )
    return create_app(embedder=embedder, model_name=os.getenv("OBTAINERCLI_EMBED_MODEL_NAME", DEFAULT_MODEL_NAME))


# Module-level app so ``uvicorn scripts.obtainercli_embedding_server:app
# --workers N`` can import it; the embedder is lazy so this is cheap.
app = _build_app()


def main() -> None:
    args = build_parser().parse_args()
    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.exists():
        raise SystemExit(f"Model directory does not exist: {model_dir}")
    # Propagate CLI settings to workers spawned by uvicorn (CLI wins over env).
    os.environ["OBTAINERCLI_EMBED_MODEL_DIR"] = str(model_dir)
    os.environ["OBTAINERCLI_EMBED_MODEL_NAME"] = args.model_name
    os.environ["OBTAINERCLI_EMBED_DEVICE"] = args.device
    os.environ["OBTAINERCLI_EMBED_DTYPE"] = args.dtype
    os.environ["OBTAINERCLI_EMBED_MAX_LENGTH"] = str(args.max_length)
    if args.workers > 1:
        uvicorn.run(
            "scripts.obtainercli_embedding_server:app",
            host=args.host,
            port=args.port,
            workers=args.workers,
        )
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
