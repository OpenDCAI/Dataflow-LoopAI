import json
import logging
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.modules.setdefault(
    "colorlog",
    types.SimpleNamespace(ColoredFormatter=logging.Formatter),
)

from loopai.skills.ObtainerCLI.index import index_embeddings
from loopai.skills.ObtainerCLI.ingest import ingest_path
from loopai.skills.ObtainerCLI.lake_init import init_lake
from loopai.skills.ObtainerCLI.tables import append_rows

from api.app.utils.obtainer.monitor import build_lake_monitor, probe_embedding_health


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


class _EmbeddingHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.calls.append(payload)
        inputs = payload["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        response = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": index, "embedding": [1.0, float(index)]}
                for index, _ in enumerate(inputs)
            ],
            "model": payload["model"],
        }
        data = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def _start_embedding_server() -> tuple[HTTPServer, str]:
    _EmbeddingHandler.calls = []
    server = HTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


def test_build_lake_monitor_returns_chart_ready_payload(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    input_path = tmp_path / "input" / "records.jsonl"
    _write_jsonl(
        input_path,
        [
            {
                "text": "alpha code",
                "source_uri": "file://alpha.txt",
                "quality_findings": [
                    {
                        "finding_type": "duplicate",
                        "severity": "warning",
                        "score": 0.42,
                    }
                ],
            },
            {"text": "beta code", "source_uri": "file://beta.txt"},
            {"text": "gamma code", "source_uri": "file://gamma.txt"},
        ],
    )
    ingest_path(
        lake=link_path,
        input_path=input_path,
        dataset="monitor_seed",
        stage="silver",
        domain="code",
        task_type="PT",
        processing_level="pretrain_ready",
        source_kind="local",
        tags=["lang=python", "quality=high"],
        idempotency_key="monitor-seed",
    )
    index_embeddings(
        lake=link_path,
        dataset="monitor_seed",
        model="local-hash-v1",
        backend="local-jsonl",
        text_field="text",
    )
    append_rows(
        lake_root,
        "exports",
        [
            {
                "export_id": "export_1",
                "query": {"domain": "code"},
                "strategy": "random",
                "seed": 7,
                "requested_size": 2,
                "actual_size": 2,
                "output_uri": str(tmp_path / "exports" / "sample.jsonl"),
                "format": "jsonl",
                "record_ids_sha256": "abc",
                "created_at": "2026-05-31T00:00:00+00:00",
            }
        ],
    )

    monitor = build_lake_monitor(lake=link_path)

    assert monitor["ok"] is True
    assert monitor["lake_root"] == str(lake_root)
    assert monitor["summary"]["records"] == 3
    assert monitor["summary"]["embeddings"] == 3
    assert monitor["summary"]["embedding_coverage"] == 1.0
    assert monitor["summary"]["quality_findings"] == 1
    assert monitor["summary"]["exports"] == 1
    assert monitor["tables"]["records"]["count"] == 3
    assert monitor["tables"]["records"]["exists"] is True
    assert monitor["charts"]["ingest_trend"][0]["rows_written"] == 3
    assert monitor["charts"]["composition"]["processing_level"]["pretrain_ready"] == 3
    assert monitor["charts"]["top_tags"][0]["count"] >= 3
    assert monitor["charts"]["quality_findings"][0]["finding_type"] == "duplicate"
    assert monitor["latest"]["records"][0]["text"]
    assert monitor["latest"]["ingest_runs"][0]["status"] == "succeeded"
    assert monitor["latest"]["exports"][0]["export_id"] == "export_1"


def test_probe_embedding_health_checks_openai_compatible_endpoint(tmp_path: Path) -> None:
    server, base_url = _start_embedding_server()
    try:
        lake_root = tmp_path / "lake"
        link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
        init_lake(
            root=lake_root,
            link_path=link_path,
            if_not_exists=True,
            embedding_provider="openai-compatible",
            embedding_base_url=base_url,
            embedding_model="test-embedding-model",
        )

        health = probe_embedding_health(lake=link_path, timeout_seconds=2)

        assert health["status"] == "online"
        assert health["embedding_model"] == "test-embedding-model"
        assert health["embedding_dim"] == 2
        assert _EmbeddingHandler.calls[0]["model"] == "test-embedding-model"
    finally:
        server.shutdown()
