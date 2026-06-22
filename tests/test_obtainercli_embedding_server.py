from __future__ import annotations

import logging
import subprocess
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from loopai.obtainercli.embedding_server import create_app


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(index), float(len(text))] for index, text in enumerate(texts)]


def test_embedding_server_exposes_openai_compatible_embeddings() -> None:
    client = TestClient(create_app(embedder=_FakeEmbedder(), model_name="test-embed"))

    response = client.post("/v1/embeddings", json={"model": "test-embed", "input": ["alpha", "beta"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert payload["model"] == "test-embed"
    assert payload["data"] == [
        {"object": "embedding", "index": 0, "embedding": [0.0, 5.0]},
        {"object": "embedding", "index": 1, "embedding": [1.0, 4.0]},
    ]


def test_embedding_server_accepts_single_string_input() -> None:
    client = TestClient(create_app(embedder=_FakeEmbedder(), model_name="test-embed"))

    response = client.post("/v1/embeddings", json={"model": "test-embed", "input": "solo"})

    assert response.status_code == 200
    assert response.json()["data"] == [{"object": "embedding", "index": 0, "embedding": [0.0, 4.0]}]


def test_embedding_server_rejects_unknown_model_name() -> None:
    client = TestClient(create_app(embedder=_FakeEmbedder(), model_name="test-embed"))

    response = client.post("/v1/embeddings", json={"model": "other", "input": "solo"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown embedding model: other"


def test_embedding_server_lists_served_model() -> None:
    client = TestClient(create_app(embedder=_FakeEmbedder(), model_name="test-embed"))

    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "test-embed"


def test_embedding_server_script_help_runs_from_repo_root() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/obtainercli_embedding_server.py", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--model-dir" in result.stdout
