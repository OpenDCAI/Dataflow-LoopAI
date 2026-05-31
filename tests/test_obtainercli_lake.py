from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from loopai.obtainercli.ingest import ingest_path
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
