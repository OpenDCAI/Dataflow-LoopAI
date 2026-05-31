from __future__ import annotations

import json
import logging
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from loopai.obtainercli.index import index_embeddings
from loopai.obtainercli.ingest import ingest_path
from loopai.obtainercli.lake_status import lake_status
from loopai.obtainercli.tags import list_tags
from loopai.obtainercli.lake_init import init_lake
from loopai.obtainercli.sample import sample_records
from loopai.obtainercli.tables import read_table


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_init_lake_uses_external_root_and_repo_pointer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    lake_root = tmp_path / "external" / "lake"
    link_path = repo / ".loopai" / "lake.yaml"

    result = init_lake(root=lake_root, link_path=link_path, if_not_exists=True)

    assert result["ok"] is True
    assert result["lake_root"] == str(lake_root)
    assert (lake_root / "lake.yaml").exists()
    assert link_path.exists()
    assert f"root: {lake_root}" in link_path.read_text(encoding="utf-8")
    for table in [
        "datasets",
        "assets",
        "records",
        "record_tags",
        "record_lineage",
        "embeddings",
        "quality_findings",
        "ingest_runs",
        "exports",
    ]:
        assert (lake_root / "warehouse" / "loopai.db" / table / "data.jsonl").exists()


def test_ingest_path_writes_records_tags_and_is_idempotent(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    input_path = tmp_path / "input" / "code.jsonl"
    _write_jsonl(
        input_path,
        [
            {"text": "def add(a, b): return a + b", "source_uri": "file://a.py"},
            {"text": "def sub(a, b): return a - b", "source_uri": "file://b.py"},
        ],
    )

    first = ingest_path(
        lake=link_path,
        input_path=input_path,
        dataset="code_seed",
        stage="bronze",
        domain="code",
        task_type="PT",
        processing_level="pretrain_ready",
        source_kind="local",
        tags=["lang=python", "quality=high"],
        idempotency_key="code-seed-1",
    )
    second = ingest_path(
        lake=link_path,
        input_path=input_path,
        dataset="code_seed",
        stage="bronze",
        domain="code",
        task_type="PT",
        processing_level="pretrain_ready",
        source_kind="local",
        tags=["lang=python", "quality=high"],
        idempotency_key="code-seed-1",
    )

    assert first["rows_written"] == 2
    assert second["rows_written"] == 0
    assert second["status"] == "success_with_warnings"
    records = read_table(lake_root, "records")
    tags = read_table(lake_root, "record_tags")
    ingest_runs = read_table(lake_root, "ingest_runs")
    assert len(records) == 2
    assert {r["domain"] for r in records} == {"code"}
    assert {r["processing_level"] for r in records} == {"pretrain_ready"}
    assert {r["source_kind"] for r in records} == {"local"}
    assert any(t["tag_name"] == "lang" and t["tag_value"] == "python" for t in tags)
    assert any(r["status"] == "skipped_duplicate_ingest" for r in ingest_runs)


def test_record_id_is_physical_and_dedup_key_is_semantic(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    input_path = tmp_path / "input" / "same.jsonl"
    _write_jsonl(input_path, [{"text": "same normalized content", "source_uri": "file://same.txt"}])

    for dataset in ["dataset_a", "dataset_b"]:
        ingest_path(
            lake=link_path,
            input_path=input_path,
            dataset=dataset,
            stage="bronze",
            domain="code",
            task_type="PT",
            processing_level="pretrain_ready",
            source_kind="local",
            tags=["quality=high"],
            idempotency_key=f"ingest-{dataset}",
        )

    records = read_table(lake_root, "records")
    assert len(records) == 2
    assert len({r["record_id"] for r in records}) == 2
    assert len({r["dedup_key"] for r in records}) == 1


def test_sample_intersects_core_filters_and_tags_with_allow_smaller_warning(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    code_path = tmp_path / "input" / "code.jsonl"
    math_path = tmp_path / "input" / "math.jsonl"
    _write_jsonl(code_path, [{"text": "python code sample", "source_uri": "file://code.txt"}])
    _write_jsonl(math_path, [{"text": "algebra sample", "source_uri": "file://math.txt"}])
    ingest_path(
        lake=link_path,
        input_path=code_path,
        dataset="code_seed",
        stage="silver",
        domain="code",
        task_type="PT",
        processing_level="postprocessed_high_quality",
        source_kind="local",
        tags=["lang=python", "quality=high"],
        idempotency_key="code",
    )
    ingest_path(
        lake=link_path,
        input_path=math_path,
        dataset="math_seed",
        stage="silver",
        domain="math",
        task_type="PT",
        processing_level="postprocessed_high_quality",
        source_kind="local",
        tags=["lang=python", "quality=high"],
        idempotency_key="math",
    )
    output_path = tmp_path / "exports" / "code.jsonl"

    result = sample_records(
        lake=link_path,
        output=output_path,
        domain="code",
        processing_level="postprocessed_high_quality",
        include_tags=["lang=python", "quality=high"],
        n=5,
        allow_smaller=True,
        seed=7,
    )

    exported = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert result["status"] == "success_with_warnings"
    assert result["warnings"][0]["code"] == "ALLOW_SMALLER_TRIGGERED"
    assert result["actual_size"] == 1
    assert len(exported) == 1
    assert exported[0]["domain"] == "code"
    assert exported[0]["text"] == "python code sample"



def test_ingest_synthetic_rows_can_write_quality_findings(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    input_path = tmp_path / "input" / "synthetic.jsonl"
    _write_jsonl(
        input_path,
        [
            {
                "text": "synthetic instruction response",
                "source_uri": "synthetic://run/1",
                "quality_findings": [
                    {
                        "finding_type": "low_diversity",
                        "severity": "warning",
                        "score": 0.42,
                        "metric_name": "distinct_3",
                        "metric_value": 0.18,
                        "detector": "diversity_check",
                        "detector_version": "v1",
                        "details": {"window": 128},
                    }
                ],
            }
        ],
    )

    ingest_path(
        lake=link_path,
        input_path=input_path,
        dataset="synthetic_seed",
        stage="silver",
        domain="code",
        task_type="SFT",
        processing_level="synthetic_validated",
        source_kind="synthetic",
        tags=["generator=qwen", "quality=medium"],
        idempotency_key="synthetic",
    )

    records = read_table(lake_root, "records")
    findings = read_table(lake_root, "quality_findings")
    assert records[0]["source_kind"] == "synthetic"
    assert records[0]["processing_level"] == "synthetic_validated"
    assert findings[0]["record_id"] == records[0]["record_id"]
    assert findings[0]["finding_type"] == "low_diversity"
    assert findings[0]["metric_name"] == "distinct_3"



def test_status_tags_and_embedding_index(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    input_path = tmp_path / "input" / "code.jsonl"
    _write_jsonl(
        input_path,
        [
            {"text": "alpha code", "source_uri": "file://alpha.txt"},
            {"text": "beta code", "source_uri": "file://beta.txt"},
        ],
    )
    ingest_path(
        lake=link_path,
        input_path=input_path,
        dataset="code_seed",
        stage="silver",
        domain="code",
        task_type="PT",
        processing_level="pretrain_ready",
        source_kind="local",
        tags=["lang=python", "quality=high"],
        idempotency_key="status-tags-index",
    )

    status = lake_status(lake=link_path)
    tag_summary = list_tags(lake=link_path)
    index_result = index_embeddings(
        lake=link_path,
        dataset="code_seed",
        model="local-hash-v1",
        backend="local-jsonl",
        text_field="text",
    )

    embeddings = read_table(lake_root, "embeddings")
    assert status["tables"]["records"] == 2
    assert status["tables"]["record_tags"] >= 8
    assert tag_summary["tags"]["lang"]["python"] == 2
    assert tag_summary["tags"]["quality"]["high"] == 2
    assert index_result["rows_indexed"] == 2
    assert len(embeddings) == 2
    assert {row["embedding_model"] for row in embeddings} == {"local-hash-v1"}
    assert all(row["index_backend"] == "local-jsonl" for row in embeddings)



def test_stratified_sample_balances_by_tag(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)
    a_path = tmp_path / "input" / "a.jsonl"
    b_path = tmp_path / "input" / "b.jsonl"
    _write_jsonl(
        a_path,
        [
            {"text": "a1", "source_uri": "file://a1.txt"},
            {"text": "a2", "source_uri": "file://a2.txt"},
        ],
    )
    _write_jsonl(b_path, [{"text": "b1", "source_uri": "file://b1.txt"}])
    ingest_path(
        lake=link_path,
        input_path=a_path,
        dataset="a_seed",
        stage="silver",
        domain="code",
        task_type="PT",
        processing_level="postprocessed_high_quality",
        source_kind="web",
        tags=["source_domain=a.example", "quality=high"],
        idempotency_key="a-seed",
    )
    ingest_path(
        lake=link_path,
        input_path=b_path,
        dataset="b_seed",
        stage="silver",
        domain="code",
        task_type="PT",
        processing_level="postprocessed_high_quality",
        source_kind="web",
        tags=["source_domain=b.example", "quality=high"],
        idempotency_key="b-seed",
    )
    output_path = tmp_path / "exports" / "balanced.jsonl"

    result = sample_records(
        lake=link_path,
        output=output_path,
        domain="code",
        processing_level="postprocessed_high_quality",
        include_tags=["quality=high"],
        n=2,
        strategy="stratified",
        balance_by="tag:source_domain",
        seed=11,
    )

    exported = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    tag_rows = read_table(lake_root, "record_tags")
    source_by_record = {
        row["record_id"]: row["tag_value"]
        for row in tag_rows
        if row["tag_name"] == "source_domain"
    }
    assert result["actual_size"] == 2
    assert {source_by_record[row["record_id"]] for row in exported} == {"a.example", "b.example"}



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
                {"object": "embedding", "index": index, "embedding": [float(index), float(len(text))]}
                for index, text in enumerate(inputs)
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


def test_cli_ingest_auto_embeds_with_openai_compatible_config(tmp_path: Path) -> None:
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
        input_path = tmp_path / "input" / "records.jsonl"
        _write_jsonl(
            input_path,
            [
                {"text": "first embedding record", "source_uri": "file://first.txt"},
                {"text": "second embedding record", "source_uri": "file://second.txt"},
            ],
        )

        from loopai.obtainercli.cli import run

        exit_code = run(
            [
                "ingest",
                "path",
                "--lake",
                str(link_path),
                "--input",
                str(input_path),
                "--dataset",
                "embed_seed",
                "--domain",
                "code",
                "--processing-level",
                "pretrain_ready",
                "--source-kind",
                "local",
                "--idempotency-key",
                "embed-seed",
                "--json",
            ]
        )

        embeddings = read_table(lake_root, "embeddings")
        assert exit_code == 0
        assert len(embeddings) == 2
        assert {row["embedding_model"] for row in embeddings} == {"test-embedding-model"}
        assert {row["index_backend"] for row in embeddings} == {"local-jsonl"}
        assert _EmbeddingHandler.calls[0]["model"] == "test-embedding-model"
        assert len(_EmbeddingHandler.calls[0]["input"]) == 2
    finally:
        server.shutdown()


def test_cli_ingest_auto_embeds_when_lake_root_is_passed(tmp_path: Path) -> None:
    server, base_url = _start_embedding_server()
    try:
        lake_root = tmp_path / "lake"
        init_lake(
            root=lake_root,
            link_path=None,
            if_not_exists=True,
            embedding_provider="openai-compatible",
            embedding_base_url=base_url,
            embedding_model="root-config-model",
        )
        input_path = tmp_path / "input" / "records.jsonl"
        _write_jsonl(input_path, [{"text": "root config embedding", "source_uri": "file://root.txt"}])

        from loopai.obtainercli.cli import run

        exit_code = run(
            [
                "ingest",
                "path",
                "--lake",
                str(lake_root),
                "--input",
                str(input_path),
                "--dataset",
                "root_embed_seed",
                "--domain",
                "code",
                "--processing-level",
                "pretrain_ready",
                "--source-kind",
                "local",
                "--idempotency-key",
                "root-embed-seed",
                "--json",
            ]
        )

        embeddings = read_table(lake_root, "embeddings")
        assert exit_code == 0
        assert len(embeddings) == 1
        assert embeddings[0]["embedding_model"] == "root-config-model"
        assert _EmbeddingHandler.calls[0]["model"] == "root-config-model"
    finally:
        server.shutdown()


def test_init_lake_writes_auto_embedding_defaults(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)

    text = link_path.read_text(encoding="utf-8")
    assert "auto_embed: true" in text
    assert "embedding_provider: openai-compatible" in text
    assert "embedding_model: BAAI/bge-small-zh-v1.5" in text
    assert "embedding_base_url: http://127.0.0.1:8000/v1" in text
