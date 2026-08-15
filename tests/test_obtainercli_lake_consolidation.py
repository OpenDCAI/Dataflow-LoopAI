"""Consolidation / bundle export-import / data-import tests.

These use a *real-shaped* lake fixture built through the actual DataMixer
pipeline (init -> ingest -> index -> recipe export), mirroring the scattered
layout of a production repository:
  .loopai/lake.yaml + lake/lake.yaml (duplicated pointers)
  lake/warehouse/ (catalog/blobs/index/exports/snapshots/audit/...)
  dataflow_work/ (root)
  outputs/obtainer_run_* , outputs/<task_id>/obtainercli/*.pkl
  codex_home_worker/ , codex_home_dataflow/
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import tarfile
import types
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from loopai.skills.ObtainerCLI.cli import run
from loopai.agents.Obtainer.datamixer.store import DataStore

CANONICAL_LAKE_DIR = ".datamixer"
CANONICAL_OBTAINER_DIR = "outputs/obtainer"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _last_json(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out
    return json.loads(out[-1])


def _dm(root: Path, args: list[str], capsys) -> dict:
    args = list(args)
    if args[:1] == ["lake"] and args[1:2] in (["consolidate"], ["export-bundle"], ["import-data"]):
        if "--project-root" not in args:
            args += ["--project-root", str(root)]
    exit_code = run(["dm", "--root", str(root), *args])
    payload = _last_json(capsys)
    assert exit_code == 0, payload
    return payload.get("result", payload)


def _records(prefix: str, n: int, domain: str) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append({
            "content": {"instruction": f"{prefix} q{i}", "output": f"{prefix} a{i}"},
            "bug_type": "syntax" if i % 2 else "logic",
            "quality_score": round(0.8 + (i % 20) / 100, 2),
            "source_uri": f"fixture://{prefix}/{i}",
        })
    return rows


def _build_real_like_lake(project: Path, capsys) -> dict[str, Path]:
    """Build the scattered production-shaped layout with real DataMixer data."""
    loopai = project / ".loopai"
    loopai.mkdir(parents=True, exist_ok=True)
    lake_root = project / "lake"
    warehouse = lake_root / "warehouse"
    outputs = project / "outputs"
    dataflow_work = project / "dataflow_work"
    (project / "codex_home_worker").mkdir(parents=True, exist_ok=True)
    (project / "codex_home_worker" / ".marker").write_text("worker", encoding="utf-8")
    (project / "codex_home_dataflow").mkdir(parents=True, exist_ok=True)
    (project / "codex_home_dataflow" / ".marker").write_text("dataflow", encoding="utf-8")

    # 1. real lake init (writes .loopai/lake.yaml pointer) + duplicate pointer at lake root
    run(["dm", "lake", "init", "--root", str(lake_root), "--link", str(loopai / "lake.yaml")])
    _last_json(capsys)
    dup = lake_root / "lake.yaml"
    dup.write_text((loopai / "lake.yaml").read_text(encoding="utf-8"), encoding="utf-8")

    # 2. ingest two datasets (L1 + L3) with metadata + dataset card
    for name, ql, domain, n in (
        ("web_l1_fixture", "L1", "web", 60),
        ("text2sql_l3_fixture", "L3", "code", 80),
    ):
        rec = project / "inputs" / f"{name}.jsonl"
        _write_jsonl(rec, _records(name, n, domain))
        card = project / "inputs" / f"{name}.md"
        card.write_text(f"# {name}\nsource: fixture\nlicense: unknown\n", encoding="utf-8")
        _dm(
            warehouse,
            [
                "ingest", name, "--file", str(rec),
                "--quality-level", ql, "--stage", "sft", "--domain", domain,
                "--lang", "zh", "--source", "fixture", "--license", "unknown",
                "--task-type", "SFT", "--processing-level", "normalized",
                "--source-kind", "fixture", "--loop-uuid", "fixture-1",
                "--version-id", "v1", "--dataset-card", str(card),
            ],
            capsys,
        )

    # 3. index build (creates index/ + monitor state)
    _dm(warehouse, ["index", "build"], capsys)

    # 4. recipe export (creates exports/ + snapshots/ + manifest + exports audit)
    recipe_path = project / "recipe.yaml"
    recipe_path.write_text(yaml.safe_dump({
        "recipe": {
            "name": "fixture_sft", "stage": "sft", "total_samples": 20,
            "sampling": {"strategy": "weighted_sample", "seed": 7},
            "export": {"format": "jsonl", "shard_size": "1MB", "schema": {
                "fields": {"instruction": {"sources": ["instruction"]},
                           "output": {"sources": ["output"]}},
                "include_dm": True,
            }},
            "buckets": [{"name": "code", "weight": 1.0,
                         "filter": "domain = 'code' AND task_type = 'SFT'",
                         "min_quality": 0.7}],
        }
    }, sort_keys=False), encoding="utf-8")
    _dm(warehouse, ["recipe", "export", str(recipe_path), "--out", str(lake_root / "exports" / "fixture"), "--snapshot"], capsys)

    # 5. obtainer run dir (orchestrator-style) with acquisition/ + recipe/ + final_report
    run_dir = outputs / "obtainer_run_fixture"
    (run_dir / "acquisition" / "manifest").mkdir(parents=True, exist_ok=True)
    (run_dir / "acquisition" / "downloads" / "records").mkdir(parents=True, exist_ok=True)
    (run_dir / "acquisition" / "dataset_cards").mkdir(parents=True, exist_ok=True)
    (run_dir / "recipe").mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "acquisition" / "thread.json").write_text(json.dumps({"state": "done", "warehouse": str(warehouse)}), encoding="utf-8")
    (run_dir / "acquisition" / "status.json").write_text(json.dumps({"state": "completed"}), encoding="utf-8")
    (run_dir / "acquisition" / "manifest" / "data_mix_plan.json").write_text(json.dumps({"objective": "x"}), encoding="utf-8")
    (run_dir / "acquisition" / "downloads" / "records" / "r.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (run_dir / "recipe" / "recipe_plan.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (run_dir / "final_report.json").write_text(json.dumps({"ok": True, "warehouse": str(warehouse), "datasets": ["text2sql_l3_fixture"]}), encoding="utf-8")
    (run_dir / "logs" / "worker_stdout.ndjson").write_text("", encoding="utf-8")

    # 6. dataflow_work (root) with real-ish status
    (dataflow_work).mkdir(parents=True, exist_ok=True)
    (dataflow_work / "status.json").write_text(json.dumps({"state": "completed", "phase": "completed", "output_rows": 80}), encoding="utf-8")
    (dataflow_work / "full_input.jsonl").write_text("", encoding="utf-8")

    # 7. obtainercli event pkl under outputs/<task_id>/obtainercli/<v>/
    task_id = "fixture-task-1"
    event_dir = outputs / task_id / "obtainercli" / "v1"
    event_dir.mkdir(parents=True, exist_ok=True)
    (event_dir / "obtainercli.pkl").write_bytes(b"obtainercli-events")

    return {
        "project": project, "loopai": loopai, "lake_root": lake_root,
        "warehouse": warehouse, "outputs": outputs, "run_dir": run_dir,
        "dataflow_work": dataflow_work, "task_id": task_id,
    }


def _lake_datasets(warehouse: Path) -> dict[str, int]:
    store = DataStore.open(warehouse)
    try:
        return {ds["name"]: int(ds["n_samples"]) for ds in store.catalog.list_datasets()}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# consolidation
# ---------------------------------------------------------------------------

def test_lake_consolidate_moves_everything_to_canonical_dirs(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    env = _build_real_like_lake(project, capsys)
    before = _lake_datasets(env["warehouse"])

    result = _dm(project, ["lake", "consolidate", "--project-root", str(project)], capsys)
    assert result.get("ok") is True

    # lake now lives under .datamixer/
    canonical_warehouse = project / CANONICAL_LAKE_DIR / "warehouse"
    assert (canonical_warehouse / "datamixer.toml").is_file()
    assert (canonical_warehouse / "catalog.db").is_file()
    # pointer lives under .datamixer/lake.yaml, old .loopai/lake.yaml gone
    assert (project / CANONICAL_LAKE_DIR / "lake.yaml").is_file()
    assert not (project / ".loopai" / "lake.yaml").exists()
    assert not (env["lake_root"] / "warehouse").exists()

    # datasets + counts preserved
    assert _lake_datasets(canonical_warehouse) == before

    # obtainer runs now under outputs/obtainer/runs/...
    runs = list((project / CANONICAL_OBTAINER_DIR / "runs").glob("obtainer_run_fixture"))
    assert runs, "run dir must move under outputs/obtainer/runs"
    assert (runs[0] / "final_report.json").is_file()

    # codex homes migrated under outputs/obtainer/.codex/
    assert (project / CANONICAL_OBTAINER_DIR / ".codex" / "worker" / ".marker").is_file()
    assert (project / CANONICAL_OBTAINER_DIR / ".codex" / "dataflow" / ".marker").is_file()
    assert not (project / "codex_home_worker").exists()
    assert not (project / "codex_home_dataflow").exists()

    # dataflow_work moved under outputs/obtainer/
    assert (project / CANONICAL_OBTAINER_DIR / "dataflow_work" / "status.json").is_file()
    assert not (project / "dataflow_work").exists()


# ---------------------------------------------------------------------------
# bundle export / import
# ---------------------------------------------------------------------------

def test_lake_export_bundle_contains_digital_assets_only(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    _build_real_like_lake(project, capsys)
    _dm(project, ["lake", "consolidate"], capsys)

    out = tmp_path / "bundle.tar.gz"
    result = _dm(project, ["lake", "export-bundle", "--out", str(out), "--project-root", str(project)], capsys)
    assert result.get("ok") is True
    assert out.is_file() and out.stat().st_size > 0

    names: list[str] = []
    with tarfile.open(out, "r:gz") as tf:
        members = tf.getmembers()
        names = [m.name for m in members]
        assert any(n.endswith("catalog.db") for n in names)
        assert any(n.endswith("datamixer.toml") for n in names)
        assert any(n.endswith("manifest.json") and "bundle" in n or n.endswith("bundle/manifest.json") for n in names)

    joined = "\n".join(names)
    # digital assets present
    assert ".datamixer/warehouse/blobs/" in joined
    assert "outputs/obtainer/runs/obtainer_run_fixture/final_report.json" in joined
    # runtime state excluded by default
    assert "codex_home" not in joined
    assert "thread.json" not in joined
    assert "status.json" not in joined
    assert "worker_stdout.ndjson" not in joined
    assert "monitor_state.json" not in joined


def test_lake_import_bundle_restores_lake_and_rewrites_paths(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    env = _build_real_like_lake(project, capsys)
    _dm(project, ["lake", "consolidate"], capsys)
    out = tmp_path / "bundle.tar.gz"
    _dm(project, ["lake", "export-bundle", "--out", str(out)], capsys)
    counts = _lake_datasets(project / CANONICAL_LAKE_DIR / "warehouse")

    target = tmp_path / "target"
    result = _dm(project, ["lake", "import-bundle", "--file", str(out), "--target", str(target)], capsys)
    assert result.get("ok") is True

    restored = target / CANONICAL_LAKE_DIR / "warehouse"
    assert (restored / "catalog.db").is_file()
    assert _lake_datasets(restored) == counts

    # pointer written at .datamixer/lake.yaml with NEW absolute root
    pointer = yaml.safe_load((target / CANONICAL_LAKE_DIR / "lake.yaml").read_text(encoding="utf-8"))
    assert pointer["warehouse"] == str(restored)
    assert pointer["root"] == str(target / CANONICAL_LAKE_DIR)


# ---------------------------------------------------------------------------
# data import (incremental / batch)
# ---------------------------------------------------------------------------

def test_lake_import_data_batch_into_empty_lake(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    _build_real_like_lake(project, capsys)
    _dm(project, ["lake", "consolidate"], capsys)
    warehouse = project / CANONICAL_LAKE_DIR / "warehouse"
    before = _lake_datasets(warehouse)

    # a new data package: one new dataset + one manifest
    pkg = tmp_path / "data_pkg"
    (pkg / "dataset_cards").mkdir(parents=True, exist_ok=True)
    _write_jsonl(pkg / "extra_l3.jsonl", _records("extra", 25, "education"))
    (pkg / "dataset_cards" / "extra_l3.md").write_text("# extra_l3\nlicense: unknown\n", encoding="utf-8")
    (pkg / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "datasets": [{"file": "extra_l3.jsonl", "name": "extra_l3_fixture",
                      "quality_level": "L3", "domain": "education",
                      "dataset_card": "dataset_cards/extra_l3.md"}],
    }), encoding="utf-8")

    result = _dm(project, ["lake", "import-data", "--package", str(pkg), "--project-root", str(project)], capsys)
    assert result.get("imported") == 25
    after = _lake_datasets(warehouse)
    assert after["extra_l3_fixture"] == 25
    assert before["text2sql_l3_fixture"] == after["text2sql_l3_fixture"]  # untouched


def test_lake_import_data_incremental_into_existing_lake(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    _build_real_like_lake(project, capsys)
    _dm(project, ["lake", "consolidate"], capsys)
    warehouse = project / CANONICAL_LAKE_DIR / "warehouse"
    before = _lake_datasets(warehouse)

    # append 10 new records to the existing text2sql dataset + re-import the same 10 (idempotent)
    pkg = tmp_path / "inc_pkg"
    (pkg / "dataset_cards").mkdir(parents=True, exist_ok=True)
    _write_jsonl(pkg / "inc.jsonl", _records("inc", 10, "finance"))
    (pkg / "dataset_cards" / "inc.md").write_text("# inc\n", encoding="utf-8")
    (pkg / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "datasets": [{"file": "inc.jsonl", "name": "text2sql_l3_fixture",
                      "quality_level": "L3", "domain": "finance",
                      "dataset_card": "dataset_cards/inc.md"}],
    }), encoding="utf-8")

    result = _dm(project, ["lake", "import-data", "--package", str(pkg), "--project-root", str(project)], capsys)
    assert result.get("imported") == 10
    assert _lake_datasets(warehouse)["text2sql_l3_fixture"] == before["text2sql_l3_fixture"] + 10

    # re-import same package -> idempotent (0 new)
    result2 = _dm(project, ["lake", "import-data", "--package", str(pkg), "--project-root", str(project)], capsys)
    assert result2.get("imported") == 0
    assert _lake_datasets(warehouse)["text2sql_l3_fixture"] == before["text2sql_l3_fixture"] + 10
