from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    encoding_format: str = "float"


class TransformersEmbedder:
    def __init__(
        self,
        *,
        model_path: str | Path,
        device: str = "auto",
        dtype: str = "auto",
        max_length: int = 512,
        normalize: bool = True,
    ) -> None:
        self.model_path = str(Path(model_path).expanduser().resolve())
        self.device_name = device
        self.dtype_name = dtype
        self.max_length = max_length
        self.normalize = normalize
        self._loaded = False
        self._tokenizer = None
        self._model = None
        self._torch = None

    def _load(self) -> None:
        if self._loaded:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        device = self.device_name
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float32
        if self.dtype_name == "auto":
            dtype = torch.float16 if device.startswith("cuda") else torch.float32
        elif self.dtype_name in {"float16", "fp16"}:
            dtype = torch.float16
        elif self.dtype_name in {"bfloat16", "bf16"}:
            dtype = torch.bfloat16
        elif self.dtype_name in {"float32", "fp32"}:
            dtype = torch.float32
        else:
            raise ValueError(f"Unsupported dtype: {self.dtype_name}")

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_path, local_files_only=True)
        model = AutoModel.from_pretrained(self.model_path, local_files_only=True)
        if device != "cpu":
            model = model.to(dtype=dtype)
        self._model = model.to(device).eval()
        self.device_name = device
        self._torch = torch
        self._loaded = True

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._load()
        assert self._tokenizer is not None
        assert self._model is not None
        assert self._torch is not None
        torch = self._torch
        batch = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        batch = {key: value.to(self.device_name) for key, value in batch.items()}
        with torch.no_grad():
            output = self._model(**batch)
            vectors = output.last_hidden_state[:, 0]
            if self.normalize:
                vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
        return vectors.float().cpu().tolist()


def _normalize_inputs(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def create_app(*, embedder: Embedder, model_name: str) -> FastAPI:
    app = FastAPI(title="ObtainerCLI Embedding Server")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "model": model_name}

    @app.get("/v1/models")
    def list_models() -> dict:
        return {
            "object": "list",
            "data": [
                {
                    "id": model_name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "loopai",
                }
            ],
        }

    @app.post("/v1/embeddings")
    def embeddings(request: EmbeddingRequest) -> dict:
        if request.model != model_name:
            raise HTTPException(status_code=404, detail=f"Unknown embedding model: {request.model}")
        texts = _normalize_inputs(request.input)
        started = time.time()
        vectors = embedder.embed(texts)
        if len(vectors) != len(texts):
            raise HTTPException(
                status_code=500,
                detail=f"Embedder returned {len(vectors)} vectors for {len(texts)} inputs",
            )
        prompt_tokens = sum(len(text.split()) for text in texts)
        return {
            "object": "list",
            "data": [
                {
                    "object": "embedding",
                    "index": index,
                    "embedding": vector,
                }
                for index, vector in enumerate(vectors)
            ],
            "model": model_name,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "total_tokens": prompt_tokens,
            },
            "metadata": {
                "latency_ms": round((time.time() - started) * 1000, 3),
            },
        }

    return app
