#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loopai.obtainercli.embedding_server import TransformersEmbedder, create_app


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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.exists():
        raise SystemExit(f"Model directory does not exist: {model_dir}")
    embedder = TransformersEmbedder(
        model_path=model_dir,
        device=args.device,
        dtype=args.dtype,
        max_length=args.max_length,
    )
    app = create_app(embedder=embedder, model_name=args.model_name)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
