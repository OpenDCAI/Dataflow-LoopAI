import json
import logging
import sys
import threading
import types
import asyncio
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
from loopai.skills.ObtainerCLI.datamixer_adapter import auto_embed_pending_embeddings
from loopai.skills.ObtainerCLI.config import write_lake_config

from api.app.utils.obtainer.monitor import build_lake_monitor, probe_embedding_health
from api.app.controllers.obtainer import (
    DataMixerCliRequest,
    DataMixerLakeDeleteRequest,
    DataMixerLakeLoadRequest,
    _resolve_datamixer_root,
    delete_datamixer_lake,
    get_lake_monitor,
    get_datamixer_lake_current,
    get_datamixer_commands,
    load_datamixer_lake,
    run_datamixer_cli,
    scan_datamixer_lakes,
)
from loopai.skills.ObtainerCLI.monitor_state import read_monitor_state, rebuild_monitor_state


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


def test_auto_embed_commits_completed_batches_and_updates_monitor_delta(tmp_path: Path, monkeypatch) -> None:
    from loopai.skills.ObtainerCLI import datamixer_adapter

    server, base_url = _start_embedding_server()
    try:
        monkeypatch.setattr(
            datamixer_adapter,
            "start_background_auto_embed",
            lambda **kwargs: {"status": "queued"},
        )
        lake_root = tmp_path / "lake"
        warehouse = lake_root / "warehouse"
        link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
        input_path = tmp_path / "records.jsonl"
        _write_jsonl(
            input_path,
            [
                {"text": "alpha"},
                {"text": "beta"},
                {"text": "gamma"},
            ],
        )
        init_lake(
            root=lake_root,
            link_path=link_path,
            if_not_exists=True,
            embedding_provider="openai-compatible",
            embedding_base_url=base_url,
            embedding_model="test-embedding-model",
        )
        ingest_path(
            lake=link_path,
            input_path=input_path,
            dataset="auto_embed_seed",
            idempotency_key="auto-embed-seed",
        )

        result = auto_embed_pending_embeddings(lake=link_path, dataset="auto_embed_seed", batch_size=2)
        monitor = read_monitor_state(warehouse, lake=link_path)

        assert result["rows_indexed"] == 3
        assert monitor["summary"]["records"] == 3
        assert monitor["summary"]["embeddings"] == 3
        assert monitor["summary"]["embedding_coverage"] == 1.0
        assert monitor["embedding"]["indexed_records"] == 3
        assert monitor["embedding"]["pending_records"] == 0
        assert monitor["tables"]["embeddings"]["count"] == 3
        cluster_files = list((warehouse / "clusters").glob("*.json"))
        assert len(cluster_files) == 1
        cluster_state = json.loads(cluster_files[0].read_text(encoding="utf-8"))
        assert cluster_state["dataset_id"].startswith("ds_")
        assert len(cluster_state["assignments"]) == 3
        assert cluster_state["centroids"]
        assert [call["model"] for call in _EmbeddingHandler.calls[-2:]] == [
            "test-embedding-model",
            "test-embedding-model",
        ]
    finally:
        server.shutdown()


def test_lake_monitor_endpoint_reads_monitor_cache_without_rebuild(tmp_path: Path, monkeypatch) -> None:
    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    input_path = tmp_path / "input" / "records.jsonl"
    _write_jsonl(input_path, [{"text": "alpha"}, {"text": "beta"}])
    ingest_path(lake=link_path, input_path=input_path, dataset="cache_seed")
    rebuild_monitor_state(warehouse, lake=link_path)

    def fail_rebuild(*args, **kwargs):
        raise AssertionError("monitor endpoint must not call full rebuild")

    monkeypatch.setattr("api.app.utils.obtainer.monitor.build_lake_monitor", fail_rebuild)

    response = asyncio.run(get_lake_monitor(lake=str(link_path)))

    assert response["code"] == 200
    assert response["data"]["cache_status"] == "fresh"
    assert response["data"]["summary"]["records"] == 2


def test_lake_monitor_endpoint_returns_registered_benchmark_sets(tmp_path: Path) -> None:
    from loopai.agents.Obtainer.datamixer import contam

    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    contam.register(
        warehouse,
        "toybench",
        [
            "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu",
            "finance benchmark question with reference answer",
        ],
        ngram=3,
    )
    rebuild_monitor_state(warehouse, lake=link_path)

    response = asyncio.run(get_lake_monitor(lake=str(link_path)))

    assert response["code"] == 200
    assert response["data"]["summary"]["benchmarks"] == 1
    assert response["data"]["summary"]["benchmark_rows"] == 2
    assert response["data"]["benchmarks"]["sets"][0]["name"] == "toybench"
    assert response["data"]["benchmarks"]["sets"][0]["rows"] == 2


def test_lake_monitor_endpoint_prefers_root_warehouse_when_pointer_warehouse_is_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.agents.Obtainer.datamixer.store import DataStore

    lake_root = tmp_path / "lake"
    stale_warehouse = lake_root / "warehouse"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    DataStore.init(lake_root).close()
    DataStore.init(stale_warehouse).close()
    input_path = tmp_path / "input" / "records.jsonl"
    _write_jsonl(input_path, [{"text": "alpha"}])
    ingest_path(lake=lake_root, input_path=input_path, dataset="root_dataset")
    write_lake_config(link_path, root=lake_root, warehouse=stale_warehouse)
    rebuild_monitor_state(lake_root, lake=link_path)

    def fail_rebuild(*args, **kwargs):
        raise AssertionError("monitor endpoint must not call full rebuild")

    monkeypatch.setattr("api.app.utils.obtainer.monitor.build_lake_monitor", fail_rebuild)

    response = asyncio.run(get_lake_monitor(lake=str(link_path)))

    assert response["code"] == 200
    assert response["data"]["summary"]["records"] == 1
    assert response["data"]["warehouse"] == str(lake_root.resolve())
    assert response["data"]["config"]["warehouse"] == str(lake_root.resolve())


def test_datamixer_cli_endpoint_uses_warehouse_from_lake_pointer(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)

    assert _resolve_datamixer_root(lake=str(link_path)) == lake_root / "warehouse"

    response = asyncio.run(
        run_datamixer_cli(
            DataMixerCliRequest(
                lake=str(link_path),
                line="status",
            )
        )
    )

    assert response["code"] == 200
    assert response["data"]["exit"] == 0
    assert response["data"]["output"]["warehouse"] == str(lake_root / "warehouse")


def test_datamixer_cli_endpoint_accepts_ingest_validation_args(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    init_lake(root=lake_root, if_not_exists=True)
    input_path = tmp_path / "input.jsonl"
    card_path = tmp_path / "dataset.md"
    _write_jsonl(
        input_path,
        [
            {"id": 1, "question": "1+1?", "answer": "2", "reasoning": "addition"},
            {"id": 2, "question": "2+2?", "answer": "4", "reasoning": "addition"},
        ],
    )
    card_path.write_text("# frontend_ingest_check\n\nTest dataset card.\n", encoding="utf-8")

    response = asyncio.run(
        run_datamixer_cli(
            DataMixerCliRequest(
                root=str(warehouse),
                line=(
                    f"ingest frontend_ingest_check --file {input_path} "
                    f"--dataset-card {card_path} --source-row-count 2 --derived-field reasoning"
                ),
            )
        )
    )

    assert response["code"] == 200
    assert response["data"]["exit"] == 0
    assert response["data"]["output"]["ingested"] == 2
    assert response["data"]["output"]["validated_rows"] == 2
    assert response["data"]["output"]["derived_fields"] == ["reasoning"]
    assert (warehouse / "dataset_cards" / "frontend_ingest_check.md").exists()


def test_datamixer_command_payload_exposes_ingest_validation_args() -> None:
    response = asyncio.run(get_datamixer_commands())
    commands = "\n".join(
        command
        for group in response["data"]["groups"]
        for command in group.get("commands", [])
    )

    assert "--dataset-card" in commands
    assert "--source-row-count" in commands
    assert "--derived-field" in commands


def test_datamixer_lake_management_endpoints_load_and_unload_pointer(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, if_not_exists=True)

    loaded = asyncio.run(
        load_datamixer_lake(
            DataMixerLakeLoadRequest(
                warehouse=str(warehouse),
                link=str(link_path),
            )
        )
    )

    assert loaded["code"] == 200
    assert loaded["data"]["warehouse"] == str(warehouse)
    assert loaded["data"]["monitor"]["cache_status"] == "cache_missing"
    assert link_path.exists()

    current = asyncio.run(get_datamixer_lake_current(lake=str(link_path)))
    assert current["code"] == 200
    assert current["data"]["warehouse_exists"] is True

    deleted = asyncio.run(
        delete_datamixer_lake(
            DataMixerLakeDeleteRequest(
                link=str(link_path),
            )
        )
    )

    assert deleted["code"] == 200
    assert deleted["data"]["pointer_removed"] is True
    assert deleted["data"]["warehouse_deleted"] is False
    assert not link_path.exists()
    assert (warehouse / "datamixer.toml").exists()


def test_datamixer_lake_scan_endpoint_returns_active_candidate(tmp_path: Path, monkeypatch) -> None:
    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    monkeypatch.setattr("api.app.controllers.obtainer.REPO_ROOT", tmp_path)

    response = asyncio.run(scan_datamixer_lakes(lake=str(link_path)))

    assert response["code"] == 200
    assert response["data"]["count"] >= 1
    active = [lake for lake in response["data"]["lakes"] if lake["active"]]
    assert len(active) == 1
    assert active[0]["warehouse"] == str(warehouse)
