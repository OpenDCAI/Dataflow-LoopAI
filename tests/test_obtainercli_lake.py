from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from loopai.skills.ObtainerCLI.cli import run
from loopai.skills.ObtainerCLI.config import read_lake_config, write_lake_config
from loopai.skills.ObtainerCLI.errors import ObtainerCliError
from loopai.skills.ObtainerCLI.lake_manager import (
    current_lake_pointer,
    load_lake_pointer,
    update_lake_obtainer_context,
)
from loopai.skills.ObtainerCLI.monitor_state import monitor_state_path, read_monitor_state
from loopai.agents.Obtainer.datamixer.clusters import update_dataset_clusters
from loopai.agents.Obtainer.datamixer.store import DataStore

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
    exit_code = run(["dm", "--root", str(root), *args])
    payload = _last_json(capsys)
    assert exit_code == 0, payload
    assert payload["command"] == "dm"
    return payload["result"]

def _set_starter_model_pool(tmp_path: Path, monkeypatch) -> Path:
    starter_config = tmp_path / "starter.yaml"
    starter_config.write_text(
        yaml.safe_dump(
            {
                "system": {
                    "api_port": 8855,
                    "codex_model_pool_name": "codex",
                    "model": {
                        "default_tier": "low",
                        "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
                        "pool": [
                            {
                                "tier": "low",
                                "name": "starter",
                                "model_name": "gpt-4o-mini",
                                "base_url": "http://low.example/v1",
                                "api_key": "low-key",
                            },
                            {
                                "tier": "medium",
                                "name": "codex",
                                "model_name": "deepseek-chat",
                                "base_url": "http://deepseek.example/v1",
                                "api_key": "deepseek-key",
                            },
                        ],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STARTER_CONFIG", str(starter_config))
    return starter_config

def _write_successful_acquisition_artifacts(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "thread.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state.update({
        "resolved_model": "deepseek-chat",
        "webagent_model": "codex",
        "model_source": "codex_default",
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")
    target_datasets = int(state.get("target_datasets") or 1)
    manifest_dir = run_dir / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "data_mix_plan.json").write_text(
        json.dumps({
            "objective": "finance SFT data",
            "target_datasets": target_datasets,
            "buckets": [{
                "name": "finance",
                "weight": 1.0,
                "target_datasets": target_datasets,
                "search_objectives": ["finance instruction and QA datasets"],
                "quality_gates": {"task_type": "SFT", "domain": "finance"},
                "rationale": "The current task is entirely finance-focused.",
            }],
        }),
        encoding="utf-8",
    )
    search_dir = run_dir / "manifest" / "searchagent"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "searchagent_manifest.json").write_text(
        json.dumps({"ok": True, "status": "completed", "candidates": []}),
        encoding="utf-8",
    )
    (run_dir / "manifest" / "webagent_start.json").write_text(
        json.dumps({
            "run_id": "campaign-1",
            "dataset": "finance_web_l1",
            "status": "completed",
            "queue": {"succeeded": 1},
        }),
        encoding="utf-8",
    )
    (run_dir / "manifest" / "webagent_campaign_status.json").write_text(
        json.dumps({
            "run_id": "campaign-1",
            "dataset": "finance_web_l1",
            "status": "completed",
            "queue": {"succeeded": 1},
        }),
        encoding="utf-8",
    )

def test_lake_context_persists_with_the_loaded_warehouse(tmp_path: Path) -> None:
    lake_root = tmp_path / "code_lake"
    warehouse = lake_root / "warehouse"
    DataStore.init(warehouse).close()
    pointer = tmp_path / "repo" / ".loopai" / "lake.yaml"
    write_lake_config(pointer, root=lake_root, warehouse=warehouse)

    update_lake_obtainer_context(
        link_path=pointer,
        updates={
            "obtainer_webagent_model": "deepseek-proxy",
            "obtainer_active_acquisition_run": str(warehouse / "obtainer_runs" / "acquisition_01"),
        },
    )
    load_lake_pointer(warehouse=warehouse, link_path=pointer, lake_root=lake_root)
    current = current_lake_pointer(link_path=pointer)
    canonical = read_lake_config(lake_root / "lake.yaml")

    assert current["obtainer_context"]["obtainer_webagent"] == "domain_data_acquisition"
    assert current["obtainer_context"]["obtainer_webagent_model"] == "deepseek-proxy"
    assert current["obtainer_context"]["obtainer_active_acquisition_run"].endswith("acquisition_01")
    assert canonical["obtainer_webagent_model"] == "deepseek-proxy"

def test_dm_lake_unbind_clears_active_bindings_but_keeps_webagent_defaults(
    tmp_path: Path,
    capsys,
) -> None:
    lake_root = tmp_path / "code_lake"
    warehouse = lake_root / "warehouse"
    DataStore.init(warehouse).close()
    pointer = tmp_path / "repo" / ".loopai" / "lake.yaml"
    write_lake_config(pointer, root=lake_root, warehouse=warehouse)
    update_lake_obtainer_context(
        link_path=pointer,
        updates={
            "obtainer_webagent": "domain_data_acquisition",
            "obtainer_webagent_model": "deepseek-chat",
            "obtainer_webagent_workers": "4",
            "obtainer_active_task_id": "fdda07da-dead-beef-task",
            "obtainer_active_acquisition_run": str(warehouse / "obtainer_runs" / "acquisition_stale"),
            "obtainer_active_campaign_id": "campaign-stale",
            "obtainer_active_l1_dataset": "finance_web_l1",
        },
    )

    exit_code = run(["dm", "lake", "unbind", "--link", str(pointer)])
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["command"] == "dm lake context"
    context = payload["obtainer_context"]
    assert context["obtainer_active_task_id"] == ""
    assert context["obtainer_active_acquisition_run"] == ""
    assert context["obtainer_active_campaign_id"] == ""
    assert context["obtainer_active_l1_dataset"] == ""
    assert context["obtainer_webagent"] == "domain_data_acquisition"
    assert context["obtainer_webagent_model"] == "deepseek-chat"
    assert context["obtainer_webagent_workers"] == "4"
    current = current_lake_pointer(link_path=pointer)
    assert current["obtainer_context"]["obtainer_active_task_id"] == ""


def test_dm_lake_unbind_requires_a_loaded_pointer(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "repo" / ".loopai" / "lake.yaml"
    exit_code = run(["dm", "lake", "unbind", "--link", str(missing)])
    payload = _last_json(capsys)
    assert exit_code == 2
    assert payload["error_code"] == "LAKE_POINTER_MISSING"


def test_dataset_acquisition_agent_uses_lake_warehouse_and_persists_default_run(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    DataStore.init(warehouse).close()
    pointer = tmp_path / "repo" / ".loopai" / "lake.yaml"
    write_lake_config(pointer, root=lake_root, warehouse=warehouse)
    _set_starter_model_pool(tmp_path, monkeypatch)

    def fake_spawn_background(**kwargs):
        return {
            "ok": True,
            "status": "background_started",
            "run_dir": str(kwargs["run_dir"]),
            "pid": 5252,
        }

    monkeypatch.setattr(dataset_acquisition_agent, "_spawn_background", fake_spawn_background)
    exit_code = run(
        [
            "dm",
            "--lake",
            str(pointer),
            "dataset-acquisition-agent",
            "start",
        ]
    )
    payload = _last_json(capsys)
    assert exit_code == 2, payload
    assert payload["error_code"] == "DATASET_ACQUISITION_AGENT_RUN_REQUIRED"

    run_dir = warehouse / "obtainer_runs" / "acquisition_01"
    exit_code = run(
        [
            "dm", "--lake", str(pointer), "dataset-acquisition-agent", "start",
            "--run", str(run_dir), "--objective", "collect code data",
        ]
    )
    payload = _last_json(capsys)
    assert exit_code == 0, payload
    assert Path(payload["run_dir"]) == run_dir
    current = current_lake_pointer(link_path=pointer)
    assert current["obtainer_context"]["obtainer_active_acquisition_run"] == str(run_dir)

def test_dataset_acquisition_agent_rejects_a_sqlite_file_as_warehouse(
    tmp_path: Path,
    capsys,
) -> None:
    db_file = tmp_path / "app.sqlite3"
    db_file.write_bytes(b"SQLite format 3\x00")
    exit_code = run(
        [
            "dm",
            "--root",
            str(db_file),
            "dataset-acquisition-agent",
            "start",
            "--run",
            str(tmp_path / "run"),
            "--dry-run",
        ]
    )
    payload = _last_json(capsys)
    assert exit_code == 2
    assert payload["error_code"] == "DATASET_ACQUISITION_AGENT_WAREHOUSE_INVALID"

def test_dataset_acquisition_agent_start_requires_loaded_lake(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"  # never initialized
    run_dir = tmp_path / "acquisition_run"
    _set_starter_model_pool(tmp_path, monkeypatch)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "ingest math datasets",
            "--target-datasets",
            "1",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 2
    assert payload["error_code"] == "LAKE_NOT_LOADED"
    assert "dm lake init" in payload["hint"] or "dm lake load" in payload["hint"]
    assert not (run_dir / "thread.json").exists()


def test_dataset_acquisition_agent_dry_run_skips_lake_loaded_check(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"  # never initialized; dry-run is planning only
    run_dir = tmp_path / "acquisition_run"
    _set_starter_model_pool(tmp_path, monkeypatch)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "ingest math datasets",
            "--dry-run",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"] == "dry_run"


def test_obtainercli_dm_init_ingest_query_index_and_recipe_export(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "code_repair.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    export_dir = tmp_path / "exports" / "code_sft"
    _write_jsonl(
        records,
        [
            {
                "content": {"instruction": "Fix the syntax error", "output": "def add(a, b): return a + b"},
                "bug_type": "syntax",
                "quality_score": 0.95,
                "source_uri": "hf://syntax/0",
            },
            {
                "content": {"instruction": "Fix the logic bug", "output": "def max2(a, b): return a if a > b else b"},
                "bug_type": "logic",
                "quality_score": 0.91,
                "source_uri": "hf://logic/0",
            },
        ],
    )
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "code_failure_repair_sft_test",
                    "stage": "sft",
                    "total_samples": 2,
                    "dedup_across_buckets": True,
                    "sampling": {"strategy": "weighted_sample", "seed": 7},
                    "export": {
                        "format": "jsonl",
                        "shard_size": "1MB",
                        "schema": {
                            "fields": {
                                "instruction": {"sources": ["instruction"]},
                                "input": {
                                    "sources": ["input"],
                                    "required": False,
                                    "default": "",
                                },
                                "output": {"sources": ["output"]},
                            },
                            "keep": ["source_uri"],
                            "include_dm": True,
                        },
                    },
                    "buckets": [
                        {
                            "name": "syntax_repair",
                            "weight": 0.5,
                            "filter": "domain = 'code' AND task_type = 'SFT' "
                            "AND json_extract(tags_json, '$.\"bug_type\"') = 'syntax'",
                            "min_quality": 0.7,
                        },
                        {
                            "name": "logic_repair",
                            "weight": 0.5,
                            "filter": "domain = 'code' AND task_type = 'SFT' "
                            "AND json_extract(tags_json, '$.\"bug_type\"') = 'logic'",
                            "min_quality": 0.7,
                        },
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    init = _dm(warehouse, ["init"], capsys)
    assert Path(init["initialized"]) == warehouse
    assert (warehouse / "datamixer.toml").exists()
    exit_code = run(["dm", "lake", "monitor", "rebuild", "--warehouse", str(warehouse)])
    monitor_rebuild = _last_json(capsys)
    assert exit_code == 0
    assert monitor_rebuild["status"] == "queued"
    assert monitor_state_path(warehouse).exists()
    status = _dm(warehouse, ["status"], capsys)
    assert status["warehouse"] == str(warehouse)
    assert status["samples"] == 0

    ingest = _dm(
        warehouse,
        [
            "ingest",
            "code_repair_mix",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "code",
            "--lang",
            "python",
            "--source",
            "huggingface",
            "--license",
            "unknown",
            "--task-type",
            "SFT",
            "--processing-level",
            "normalized",
            "--source-kind",
            "huggingface",
            "--loop-uuid",
            "loop-001",
            "--version-id",
            "version-001",
            "--tag",
            "source_dataset=code-repair-fixture",
        ],
        capsys,
    )
    assert ingest["written"] == 2
    assert Path(ingest["lineage"]).exists()

    query = _dm(
        warehouse,
        [
            "query",
            "--filter",
            "json_extract(tags_json, '$.\"bug_type\"') = 'syntax'",
            "--columns",
            "sample_id,domain,task_type,tags_json",
            "--limit",
            "5",
        ],
        capsys,
    )
    assert query["total"] == 1
    assert json.loads(query["rows"][0]["tags_json"])["processing_level"] == "normalized"

    index = _dm(warehouse, ["index", "build"], capsys)
    assert index["indexed"] == 2
    recall = _dm(warehouse, ["recall", "--match", "syntax", "--limit", "5"], capsys)
    assert recall["mode"] == "keyword"
    assert recall["results"]

    plan = _dm(warehouse, ["recipe", "plan", str(recipe_path)], capsys)
    assert plan["total_samples"] == 2
    assert not plan["warnings"]

    exported = _dm(
        warehouse,
        ["recipe", "export", str(recipe_path), "--out", str(export_dir), "--snapshot"],
        capsys,
    )
    assert exported["selected_samples"] == 2
    assert exported["recipe_fingerprint"]
    assert exported["snapshot_id"]
    assert Path(exported["manifest_path"]).exists()
    assert (export_dir / "manifest.json").exists()
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["recipe_fingerprint"] == exported["recipe_fingerprint"]
    assert manifest["snapshot_id"] == exported["snapshot_id"]
    assert manifest["export_schema"]["enabled"] is True
    rows = [
        json.loads(line)
        for line in (export_dir / "part-00000.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {tuple(sorted(row)) for row in rows} == {
        ("_dm", "input", "instruction", "output", "source_uri")
    }

    # The final 出湖 artifact path must land in the shared lake monitor state
    # and the exports audit, so the Obtainer task card can surface it.
    monitor = read_monitor_state(warehouse)
    assert monitor["summary"]["exports"] >= 1
    assert monitor["latest"]["exports"]
    latest_export = monitor["latest"]["exports"][0]
    assert latest_export["output_uri"] == str(export_dir.resolve())
    assert latest_export["strategy"] == "recipe_export"
    assert latest_export["export_id"] == exported["export_id"]
    assert Path(latest_export["manifest_path"]).exists()
    audit_rows = [
        json.loads(line)
        for line in (warehouse / "obtainercli_audit" / "exports.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert audit_rows and audit_rows[-1]["output_uri"] == str(export_dir.resolve())

def test_obtainercli_dm_export_jsonl_records_latest_export(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "records.jsonl"
    out_jsonl = tmp_path / "exports" / "flat.jsonl"
    _write_jsonl(
        records,
        [
            {"content": {"text": "hello world"}, "source_uri": "fixture://export-jsonl"},
        ],
    )

    init = _dm(warehouse, ["init"], capsys)
    assert Path(init["initialized"]) == warehouse
    _dm(
        warehouse,
        [
            "ingest",
            "flat_mix",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "general",
            "--lang",
            "zh",
            "--source",
            "fixture",
            "--license",
            "unknown",
            "--task-type",
            "SFT",
            "--processing-level",
            "normalized",
            "--source-kind",
            "fixture",
            "--loop-uuid",
            "loop-export-jsonl",
            "--version-id",
            "v1",
        ],
        capsys,
    )

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    exported = _dm(
        warehouse,
        ["export-jsonl", "--dataset", "flat_mix", "--out", str(out_jsonl), "--field", "text"],
        capsys,
    )
    assert exported["exported"] == 1
    assert Path(exported["out"]).exists()

    monitor = read_monitor_state(warehouse)
    assert monitor["summary"]["exports"] >= 1
    assert monitor["latest"]["exports"]
    latest_export = monitor["latest"]["exports"][0]
    assert latest_export["output_uri"] == str(out_jsonl.resolve())
    assert latest_export["strategy"] == "export_jsonl"
    assert latest_export["actual_size"] == 1
    audit_rows = [
        json.loads(line)
        for line in (warehouse / "obtainercli_audit" / "exports.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert audit_rows and audit_rows[-1]["output_uri"] == str(out_jsonl.resolve())


def test_obtainercli_dataset_management_list_update_delete(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "records.jsonl"
    _write_jsonl(
        records,
        [
            {"content": {"instruction": "q1", "output": "a1"}, "bug_type": "code", "quality_score": 0.9},
            {"content": {"instruction": "q2", "output": "a2"}, "bug_type": "math", "quality_score": 0.8},
        ],
    )

    init = _dm(warehouse, ["init"], capsys)
    assert Path(init["initialized"]) == warehouse
    _dm(
        warehouse,
        [
            "ingest",
            "manage_ds",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "code",
            "--lang",
            "zh",
            "--source",
            "fixture",
            "--license",
            "unknown",
            "--task-type",
            "SFT",
            "--processing-level",
            "normalized",
            "--source-kind",
            "fixture",
            "--loop-uuid",
            "loop-manage-ds",
            "--version-id",
            "v1",
        ],
        capsys,
    )

    # list carries per-dataset aggregates for the management UI
    listed = _dm(warehouse, ["dataset", "list"], capsys)["datasets"]
    assert len(listed) == 1
    row = listed[0]
    assert row["n_samples"] == 2
    assert row["quality_levels"] == {"L3": 2}
    assert row["domains"] == {"code": 2}
    assert row["stages"] == {"sft": 2}
    assert row["last_ingested_at"]

    # update metadata + rename
    updated = _dm(
        warehouse,
        ["dataset", "update", "manage_ds", "--description", "managed via UI", "--owner", "xbr"],
        capsys,
    )
    assert updated["description"] == "managed via UI"
    assert updated["owner"] == "xbr"
    renamed = _dm(warehouse, ["dataset", "update", "manage_ds", "--name", "manage_ds_v2"], capsys)
    assert renamed["name"] == "manage_ds_v2"

    # delete without --yes only asks for confirmation
    confirm = _dm(warehouse, ["dataset", "delete", "manage_ds_v2"], capsys)
    assert confirm.get("confirm_required") is True
    assert _dm(warehouse, ["dataset", "list"], capsys)["datasets"]

    # delete with --yes removes registry + samples
    deleted = _dm(
        warehouse,
        ["dataset", "delete", "manage_ds_v2", "--yes", "--reason", "ui test"],
        capsys,
    )
    assert deleted["erased"] == 2
    assert deleted["dataset_name"] == "manage_ds_v2"
    assert _dm(warehouse, ["dataset", "list"], capsys)["datasets"] == []


def test_datamixer_ingest_skips_registered_benchmark_contamination(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    bench_file = tmp_path / "bench.txt"
    records = tmp_path / "input" / "records.jsonl"
    bench_text = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa "
        "lambda mu nu xi omicron"
    )
    bench_file.write_text(bench_text + "\n", encoding="utf-8")
    _write_jsonl(
        records,
        [
            {"content": {"text": bench_text}, "source_uri": "fixture://bench-overlap"},
            {
                "content": {
                    "text": "unique training sample about portfolio risk and cash flow analysis"
                },
                "source_uri": "fixture://clean",
            },
        ],
    )

    _dm(warehouse, ["init"], capsys)
    _dm(warehouse, ["contam", "add", "--name", "toybench", "--file", str(bench_file)], capsys)
    ingest = _dm(
        warehouse,
        [
            "ingest",
            "finance_training",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--domain",
            "general",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    assert ingest["ingested"] == 2
    assert ingest["written"] == 1
    assert ingest["contaminated"] == 1
    assert ingest["contam_sources"] == {"toybench": 1}

    query = _dm(warehouse, ["query", "--limit", "10"], capsys)
    assert query["total"] == 1
    store = DataStore.open(warehouse)
    try:
        content = store.get_content(query["rows"][0]["cid"])
    finally:
        store.close()
    serialized = json.dumps(content, ensure_ascii=False)
    assert "unique training sample" in serialized
    assert bench_text not in serialized

def test_finance_ingest_batch_writes_all_rows_without_record_gate(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "records.jsonl"
    _write_jsonl(
        records,
        [
            {
                "content": {
                    "instruction": "Explain revenue in this financial statement",
                    "output": "Use net income from the cash flow statement.",
                },
                "source_uri": "https://www.sec.gov/edgar/one",
                "source_dataset_id": "sec-filings",
            },
            {
                "content": {
                    "instruction": "Review this movie",
                    "output": "Good acting.",
                },
                "source_uri": "https://example.invalid/movie",
                "source_dataset_id": "Finance_Alpaca_v2",
            },
        ],
    )
    _dm(warehouse, ["init"], capsys)

    result = _dm(
        warehouse,
        [
            "ingest", "finance_batch", "--file", str(records),
            "--quality-level", "L3", "--domain", "finance",
        ],
        capsys,
    )

    assert result["ingested"] == 2
    assert result["quality_level"] == "L3"
    assert "finance_quality_report" not in result
    assert "finance_accepted" not in result
    query = _dm(warehouse, ["query", "--dataset", "finance_batch", "--limit", "10"], capsys)
    assert query["total"] == 2

def test_finance_ingest_preserves_caller_classifier_metadata_as_tags(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "mixed.jsonl"
    common_evidence = {
        "domain_labels": ["finance"],
        "domain_confidence": 0.96,
        "domain_classifier": "domain_classify@1.3",
        "domain_classifier_model": "codex",
        "finance_semantic_signals": [
            {"type": "accounting_metric", "evidence": "net income", "confidence": 0.95},
            {"type": "financial_statement", "evidence": "cash flow statement", "confidence": 0.93},
        ],
    }
    _write_jsonl(
        records,
        [
            {
                "content": {
                    "instruction": "Explain revenue and net income in this financial statement",
                    "output": "Read the cash flow statement with the SEC filing.",
                },
                "source_uri": "https://www.sec.gov/edgar/accepted",
                "source_dataset_id": "sec-filings",
                **common_evidence,
            },
            {
                "content": {
                    "instruction": "Explain revenue and net income in this financial statement",
                    "output": "Read the cash flow statement from this blog.",
                },
                "source_uri": "https://finance-blog.example/rejected",
                "source_dataset_id": "untrusted-blog",
                **common_evidence,
            },
            {
                "content": {
                    "instruction": "Review this movie and its acting",
                    "output": "The travel scenes and pets were entertaining.",
                },
                "source_uri": "https://www.sec.gov/edgar/not-finance",
                "source_dataset_id": "sec-filings",
                **common_evidence,
            },
        ],
    )
    _dm(warehouse, ["init"], capsys)

    result = _dm(
        warehouse,
        [
            "ingest", "mixed_finance", "--file", str(records),
            "--quality-level", "L3", "--domain", "finance",
        ],
        capsys,
    )

    assert result["ingested"] == 3
    store = DataStore.open(warehouse)
    try:
        rows = store.catalog.query(
            dataset_id=store.catalog.resolve_dataset("mixed_finance")
        )
    finally:
        store.close()
    assert len(rows) == 3
    assert all(
        row["tags"]["domain_classifier"] == "domain_classify@1.3"
        for row in rows
    )
    assert all(
        len(row["tags"]["finance_semantic_signals"]) == 2
        for row in rows
    )

def test_finance_ingest_never_invokes_per_row_llm_classification(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.agents.Obtainer.datamixer import llm

    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "unverified.jsonl"
    _write_jsonl(records, [{
        "content": {
            "instruction": "Explain revenue from the income statement",
            "output": "Compare revenue with net income.",
        },
        "source_uri": "https://www.sec.gov/edgar/auto",
        "source_dataset_id": "sec-filings",
    }])

    def fail_if_called(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("batch ingest must not run per-row LLM classification")

    monkeypatch.setattr(llm, "complete", fail_if_called)
    _dm(warehouse, ["init"], capsys)

    result = _dm(
        warehouse,
        [
            "ingest", "auto_finance", "--file", str(records),
            "--quality-level", "L3", "--domain", "finance",
        ],
        capsys,
    )

    assert result["ingested"] == 1
    store = DataStore.open(warehouse)
    try:
        rows = store.catalog.query(
            dataset_id=store.catalog.resolve_dataset("auto_finance")
        )
    finally:
        store.close()
    assert len(rows) == 1
    assert rows[0]["tags"]["source_dataset_id"] == "sec-filings"

def test_finance_export_requires_policy_and_writes_source_report(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "records.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    export_dir = tmp_path / "export"
    _write_jsonl(
        records,
        [{
            "content": {
                "instruction": "Explain revenue in this SEC financial statement",
                "output": "Use net income from the filing.",
            },
            "source_uri": "https://www.sec.gov/edgar/one",
            "source_dataset_id": "sec-filings",
            "domain_labels": ["finance"],
            "domain_confidence": 0.96,
            "domain_classifier": "domain_classify@1.3",
            "domain_classifier_model": "codex",
            "finance_semantic_signals": [
                {"type": "accounting_metric", "evidence": "revenue", "confidence": 0.97},
                {"type": "financial_statement", "evidence": "financial statement", "confidence": 0.95},
            ],
        }],
    )
    recipe_path.write_text(
        yaml.safe_dump({
            "recipe": {
                "name": "finance_gate_missing",
                "stage": "sft",
                "total_samples": 1,
                "buckets": [{"name": "finance", "weight": 1, "filter": "domain='finance'"}],
                "export": {
                    "format": "jsonl",
                    "schema": {"fields": {
                        "instruction": {"sources": ["instruction"]},
                        "input": {"sources": ["input"], "required": False, "default": ""},
                        "output": {"sources": ["output"]},
                    }},
                },
            }
        }),
        encoding="utf-8",
    )
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest", "Finance_Alpaca_v2", "--file", str(records),
            "--quality-level", "L3", "--domain", "finance", "--stage", "sft",
        ],
        capsys,
    )

    exit_code = run([
        "dm", "--root", str(warehouse), "recipe", "export", str(recipe_path),
        "--out", str(export_dir),
    ])
    payload = _last_json(capsys)

    assert exit_code == 1
    result = payload["details"]
    assert result["error_code"] == "finance_quality_policy_missing"
    assert result["finance_quality"]["finance_samples"] == 1
    assert (export_dir / "finance_quality_report.json").exists()
    assert not list(export_dir.glob("part-*.jsonl"))

    rejected_dir = tmp_path / "rejected_export"
    recipe_doc = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    recipe_doc["recipe"]["quality_gates"] = {
        "finance": {
            "min_field_valid_rate": 1.0,
            "min_classifier_confidence": 0.99,
            "min_classifier_pass_rate": 1.0,
            "sample_size": 1,
            "manual_review": {"required": False},
        }
    }
    recipe_path.write_text(yaml.safe_dump(recipe_doc), encoding="utf-8")
    exit_code = run([
        "dm", "--root", str(warehouse), "recipe", "export", str(recipe_path),
        "--out", str(rejected_dir),
    ])
    payload = _last_json(capsys)
    rejected = payload["details"]["finance_quality"]

    assert exit_code == 1
    assert rejected["code"] == "finance_quality_rejected"
    assert rejected["sources"][0]["rejection_reasons"] == [
        "classifier_pass_rate_below_threshold",
    ]
    assert len(rejected["sources"][0]["manual_review_sample"]) == 1
    assert not list(rejected_dir.glob("part-*.jsonl"))

def test_finance_export_enforces_llm_and_field_gates_across_sources(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "records.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    export_dir = tmp_path / "export"
    _write_jsonl(
        records,
        [
            {
                "content": {
                    "instruction": "Explain revenue in the financial statement",
                    "output": "Use net income from the SEC filing.",
                },
                "source_uri": "https://www.sec.gov/edgar/one",
                "source_dataset_id": "sec-filings",
                "domain_labels": ["finance"],
                "domain_confidence": 0.96,
                "domain_classifier": "domain_classify@1.3",
                "domain_classifier_model": "codex",
                "finance_semantic_signals": [
                    {"type": "accounting_metric", "evidence": "revenue", "confidence": 0.97},
                    {"type": "financial_statement", "evidence": "financial statement", "confidence": 0.94},
                ],
            },
            {
                "content": {
                    "instruction": "Explain revenue and cash flow in this financial statement for a corporate bond",
                    "output": "Read the cash flow statement.",
                },
                "source_uri": "https://finance-blog.example/two",
                "source_dataset_id": "independent-finance-blog",
                "domain_labels": ["finance"],
                "domain_confidence": 0.92,
                "domain_classifier": "domain_classify@1.3",
                "domain_classifier_model": "codex",
                "finance_semantic_signals": [
                    {"type": "accounting_metric", "evidence": "cash flow", "confidence": 0.95},
                    {"type": "financial_instrument", "evidence": "corporate bond", "confidence": 0.93},
                ],
            },
        ],
    )
    recipe = {
        "recipe": {
            "name": "finance_gate",
            "stage": "sft",
            "total_samples": 2,
            "buckets": [{"name": "finance", "weight": 1, "filter": "domain='finance'"}],
            "quality_gates": {
                "finance": {
                    "min_field_valid_rate": 1.0,
                    "min_classifier_confidence": 0.8,
                    "min_classifier_pass_rate": 1.0,
                    "min_semantic_signals": 2,
                    "min_semantic_signal_pass_rate": 1.0,
                    "sample_size": 2,
                    "manual_review": {"required": False},
                }
            },
            "export": {
                "format": "jsonl",
                "schema": {"fields": {
                    "instruction": {"sources": ["instruction"]},
                    "input": {"sources": ["input"], "required": False, "default": ""},
                    "output": {"sources": ["output"]},
                }},
            },
        }
    }
    recipe_path.write_text(yaml.safe_dump(recipe), encoding="utf-8")
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest", "sec_finance", "--file", str(records), "--quality-level", "L3",
            "--domain", "finance", "--stage", "sft",
        ],
        capsys,
    )

    exported = _dm(
        warehouse,
        ["recipe", "export", str(recipe_path), "--out", str(export_dir)],
        capsys,
    )
    report = json.loads((export_dir / "finance_quality_report.json").read_text(encoding="utf-8"))

    assert exported["selected_samples"] == 2
    assert report["ok"] is True
    assert report["source_count"] == 2
    assert all(source["field_valid_rate"] == 1.0 for source in report["sources"])
    assert all(source["classifier_pass_rate"] == 1.0 for source in report["sources"])
    assert all(source["semantic_signal_pass_rate"] == 1.0 for source in report["sources"])
    assert sum(len(source["manual_review_sample"]) for source in report["sources"]) == 2
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["finance_quality"]["code"] == "finance_quality_passed"

def test_contam_add_removes_existing_benchmark_contamination(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    bench_file = tmp_path / "bench.txt"
    records = tmp_path / "input" / "records.jsonl"
    bench_text = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa "
        "lambda mu nu xi omicron"
    )
    bench_file.write_text(bench_text + "\n", encoding="utf-8")
    _write_jsonl(
        records,
        [
            {"content": {"text": bench_text}, "source_uri": "fixture://bench-overlap"},
            {
                "content": {
                    "text": "unique training sample about portfolio risk and cash flow analysis"
                },
                "source_uri": "fixture://clean",
            },
        ],
    )

    _dm(warehouse, ["init"], capsys)
    ingest = _dm(
        warehouse,
        [
            "ingest",
            "finance_training",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--domain",
            "general",
            "--task-type",
            "SFT",
        ],
        capsys,
    )
    assert ingest["written"] == 2

    registered = _dm(
        warehouse,
        ["contam", "add", "--name", "toybench", "--file", str(bench_file)],
        capsys,
    )
    audit = registered["auto_decontamination"]
    assert audit["scanned"] == 2
    assert audit["contaminated"] == 1
    assert audit["removed"] == 1
    assert audit["applied"] is True

    query = _dm(warehouse, ["query", "--limit", "10"], capsys)
    assert query["total"] == 1
    store = DataStore.open(warehouse)
    try:
        content = store.get_content(query["rows"][0]["cid"])
    finally:
        store.close()
    serialized = json.dumps(content, ensure_ascii=False)
    assert "unique training sample" in serialized
    assert bench_text not in serialized

def test_dm_ingest_registers_dataset_card_and_validates_derived_fields(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "alphamath_normalized.jsonl"
    card = tmp_path / "manifest" / "dataset_cards" / "alphamath_sft.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        "# AlphaMath SFT\n\nOriginal fields are preserved. train_output is derived from reasoning + answer.\n",
        encoding="utf-8",
    )
    _write_jsonl(
        records,
        [
            {
                "content": {
                    "instruction": "<question>1+1?</question>",
                    "output": '[{"step":"<step>1+1=2</step>","P":1,"Q":1,"depth":1}]',
                    "answer": "2",
                    "train_output": "<think>1+1=2</think>\n2",
                },
                "source_uri": "hf://alpha/0",
            },
            {
                "content": {
                    "instruction": "<question>2+2?</question>",
                    "output": '[{"step":"<step>2+2=4</step>","P":1,"Q":1,"depth":1}]',
                    "answer": "4",
                    "train_output": "<think>2+2=4</think>\n4",
                },
                "source_uri": "hf://alpha/1",
            },
        ],
    )
    _dm(warehouse, ["init"], capsys)

    ingest = _dm(
        warehouse,
        [
            "ingest",
            "alphamath_trainset_sft_v1",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--content-key",
            "content",
            "--dataset-card",
            str(card),
            "--derived-field",
            "train_output",
            "--source-row-count",
            "2",
            "--stage",
            "sft",
            "--domain",
            "math",
            "--source",
            "huggingface",
            "--license",
            "MIT",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    assert ingest["written"] == 2
    assert ingest["derived_fields"] == ["train_output"]
    registered_card = Path(ingest["dataset_card"])
    assert registered_card == warehouse / "dataset_cards" / "alphamath_trainset_sft_v1.md"
    assert registered_card.read_text(encoding="utf-8").startswith("# AlphaMath SFT")
    lineage = json.loads(Path(ingest["lineage"]).read_text(encoding="utf-8"))
    assert lineage["dataset_card"] == str(registered_card)
    assert lineage["validation"]["rows"] == 2

    store = DataStore.open(warehouse)
    ds_id = store.catalog.resolve_dataset("alphamath_trainset_sft_v1")
    row = store.catalog.query(dataset_id=ds_id, columns="sample_id,cid", limit=1)[0]
    content = store.get_content(row["cid"])
    store.close()
    assert content["output"].startswith("[")
    assert content["answer"] == "2"
    assert content["train_output"].endswith("\n2")

def test_dm_ingest_rejects_empty_declared_derived_field(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "bad.jsonl"
    card = tmp_path / "card.md"
    card.write_text("# Bad\n\nMissing derived field.\n", encoding="utf-8")
    _write_jsonl(
        records,
        [
            {"content": {"instruction": "q", "answer": "a", "train_output": "a"}},
            {"content": {"instruction": "q2", "answer": "b", "train_output": ""}},
        ],
    )
    _dm(warehouse, ["init"], capsys)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "ingest",
            "bad_sft",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--content-key",
            "content",
            "--dataset-card",
            str(card),
            "--derived-field",
            "train_output",
            "--source-row-count",
            "2",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code != 0
    assert "derived fields must be non-empty" in json.dumps(payload)
    assert not (warehouse / "dataset_cards" / "bad_sft.md").exists()

def test_recipe_export_defaults_to_cluster_similarity_sampling_with_random_fallback(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "mixed.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    export_dir = tmp_path / "export"
    _write_jsonl(
        records,
        [
            {"content": {"instruction": "i0", "output": "o0"}, "source_uri": "hf://mix/0"},
            {"content": {"instruction": "i1", "output": "o1"}, "source_uri": "hf://mix/1"},
            {"content": {"instruction": "i2", "output": "o2"}, "source_uri": "hf://mix/2"},
            {"content": {"instruction": "i3", "output": "o3"}, "source_uri": "hf://mix/3"},
        ],
    )
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "clustered_mix",
                    "stage": "sft",
                    "total_samples": 4,
                    "sampling": {"strategy": "weighted_sample", "seed": 11},
                    "export": {
                        "format": "jsonl",
                        "schema": {
                            "fields": {
                                "instruction": {"sources": ["instruction"]},
                                "input": {"sources": ["input"], "required": False, "default": ""},
                                "output": {"sources": ["output"]},
                            },
                            "include_dm": True,
                        },
                    },
                    "buckets": [
                        {
                            "name": "all_sft",
                            "weight": 1,
                            "filter": "domain = 'general' AND task_type = 'SFT'",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest",
            "mixed_sft",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "general",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    store = DataStore.open(warehouse)
    samples = store.catalog.query(order="sample_id")
    dataset_id = samples[0]["dataset_id"]
    update_dataset_clusters(
        warehouse,
        dataset_id=dataset_id,
        embeddings=[
            {
                "record_id": samples[0]["sample_id"],
                "dataset_id": dataset_id,
                "embedding_model": "test-embed",
                "embedding_dim": 2,
                "vector": [1.0, 0.0],
            },
            {
                "record_id": samples[1]["sample_id"],
                "dataset_id": dataset_id,
                "embedding_model": "test-embed",
                "embedding_dim": 2,
                "vector": [0.99, 0.01],
            },
        ],
        max_clusters=1,
    )
    store.close()

    result = _dm(warehouse, ["recipe", "export", str(recipe_path), "--out", str(export_dir)], capsys)
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    sampling = manifest["summary"]["buckets"][0]["sampling"]
    assert result["selected_samples"] == 4
    assert sampling["strategy"] == "cluster_similarity"
    assert sampling["clustered_candidates"] == 2
    assert sampling["random_candidates"] == 2

    rows = [
        json.loads(line)
        for line in (export_dir / "part-00000.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    clustered_rows = [row for row in rows if row["_dm"].get("cluster_id")]
    random_rows = [row for row in rows if not row["_dm"].get("cluster_id")]
    assert len(clustered_rows) == 2
    assert len(random_rows) == 2
    assert all(row["_dm"]["cluster_similarity"] is not None for row in clustered_rows)

def test_sft_recipe_export_requires_explicit_schema_mapping(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "math.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    _write_jsonl(
        records,
        [
            {"question": "1+1?", "answer": "2", "source_uri": "hf://math/0"},
        ],
    )
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "missing_sft_mapping",
                    "stage": "sft",
                    "total_samples": 1,
                    "sampling": {"strategy": "weighted_sample"},
                    "export": {"format": "jsonl"},
                    "buckets": [
                        {
                            "name": "math",
                            "weight": 1,
                            "filter": "domain = 'math' AND task_type = 'SFT'",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest",
            "math_sft",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "math",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    exit_code = run(["dm", "--root", str(warehouse), "recipe", "validate", str(recipe_path)])
    payload = _last_json(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["result"]["valid"] is False
    assert payload["result"]["export_schema"]["code"] == "missing_export_schema_mapping"
    assert "suggested_yaml" in payload["result"]["export_schema"]

    exit_code = run(["dm", "--root", str(warehouse), "recipe", "export", str(recipe_path)])
    payload = _last_json(capsys)
    assert exit_code != 0
    assert payload["error_code"] == "DATAMIXER_COMMAND_FAILED"
    assert payload["details"]["blocked"] is True
    assert payload["details"]["error_code"] == "missing_export_schema_mapping"
    assert payload["details"]["export_schema"]["code"] == "missing_export_schema_mapping"

def test_sft_recipe_export_mapping_normalizes_heterogeneous_keys(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "math.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    export_dir = tmp_path / "export"
    _write_jsonl(
        records,
        [
            {"question": "1+1?", "answer": "2", "source_uri": "hf://math/0"},
            {"INSTRUCTION": "2+2?", "RESPONSE": "4", "source_uri": "hf://math/1"},
        ],
    )
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "mapped_sft",
                    "stage": "sft",
                    "total_samples": 2,
                    "sampling": {"strategy": "weighted_sample", "seed": 11},
                    "export": {
                        "format": "jsonl",
                        "schema": {
                            "fields": {
                                "instruction": {
                                    "sources": ["instruction", "INSTRUCTION", "question"],
                                },
                                "input": {
                                    "sources": ["input"],
                                    "required": False,
                                    "default": "",
                                },
                                "output": {"sources": ["output", "RESPONSE", "answer"]},
                            },
                            "keep": ["source_uri"],
                            "include_dm": True,
                        },
                    },
                    "buckets": [
                        {
                            "name": "math",
                            "weight": 1,
                            "filter": "domain = 'math' AND task_type = 'SFT'",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest",
            "math_sft",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "math",
            "--task-type",
            "SFT",
        ],
        capsys,
    )
    result = _dm(warehouse, ["recipe", "export", str(recipe_path), "--out", str(export_dir)], capsys)
    assert result["selected_samples"] == 2
    rows = [
        json.loads(line)
        for line in (export_dir / "part-00000.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {tuple(sorted(row)) for row in rows} == {
        ("_dm", "input", "instruction", "output", "source_uri")
    }
    assert {row["instruction"] for row in rows} == {"1+1?", "2+2?"}
    assert {row["output"] for row in rows} == {"2", "4"}

def test_sft_recipe_export_supports_bucket_schema_templates(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    math_records = tmp_path / "input" / "math.jsonl"
    sql_records = tmp_path / "input" / "sql.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    export_dir = tmp_path / "export"
    _write_jsonl(
        math_records,
        [
            {
                "instruction": "What is 2+2?",
                "reasoning": "2 plus 2 combines two pairs.",
                "answer": "4",
                "output": "bad fallback",
            },
        ],
    )
    _write_jsonl(
        sql_records,
        [
            {
                "question": "List active user ids.",
                "evidence": "Only active users should be returned.",
                "sql_schema": "users(id INT, active BOOL)",
                "sql_block": "Use the users table.",
                "sql": "SELECT id FROM users WHERE active = TRUE;",
            },
        ],
    )
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "bucket_templates",
                    "stage": "sft",
                    "total_samples": 2,
                    "sampling": {"strategy": "weighted_sample", "seed": 7},
                    "export": {"format": "jsonl"},
                    "buckets": [
                        {
                            "name": "math",
                            "weight": 0.5,
                            "filter": "domain = 'math' AND task_type = 'SFT'",
                            "schema": {
                                "fields": {
                                    "instruction": {"sources": ["instruction"]},
                                    "input": {
                                        "sources": ["input"],
                                        "required": False,
                                        "default": "",
                                    },
                                    "output": {
                                        "template": "<think>{reasoning}</think>{answer}",
                                    },
                                },
                                "include_dm": False,
                            },
                        },
                        {
                            "name": "text2sql",
                            "weight": 0.5,
                            "filter": "domain = 'sql' AND task_type = 'SFT'",
                            "schema": {
                                "fields": {
                                    "instruction": {"template": "{question}"},
                                    "input": {
                                        "template": (
                                            "Evidence:\n{evidence}\n"
                                            "Schema:\n{sql_schema}\n"
                                            "SQL block:\n{sql_block}"
                                        ),
                                    },
                                    "output": {"sources": ["sql"]},
                                },
                                "include_dm": False,
                            },
                        },
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest",
            "math_sft",
            "--file",
            str(math_records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "math",
            "--task-type",
            "SFT",
        ],
        capsys,
    )
    _dm(
        warehouse,
        [
            "ingest",
            "sql_sft",
            "--file",
            str(sql_records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "sql",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    result = _dm(warehouse, ["recipe", "export", str(recipe_path), "--out", str(export_dir)], capsys)
    assert result["selected_samples"] == 2
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["export_schema"]["bucket_fields"] == {
        "math": ["instruction", "input", "output"],
        "text2sql": ["instruction", "input", "output"],
    }
    rows = [
        json.loads(line)
        for line in (export_dir / "part-00000.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {tuple(sorted(row)) for row in rows} == {("input", "instruction", "output")}
    by_instruction = {row["instruction"]: row for row in rows}
    assert by_instruction["What is 2+2?"]["output"] == (
        "<think>2 plus 2 combines two pairs.</think>4"
    )
    assert by_instruction["List active user ids."]["input"] == (
        "Evidence:\nOnly active users should be returned.\n"
        "Schema:\nusers(id INT, active BOOL)\n"
        "SQL block:\nUse the users table."
    )
    assert by_instruction["List active user ids."]["output"] == (
        "SELECT id FROM users WHERE active = TRUE;"
    )

def test_sft_recipe_export_reports_mapping_failures_for_agent_repair(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "mixed.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    export_dir = tmp_path / "export"
    _write_jsonl(
        records,
        [
            {"question": "1+1?", "answer": "2", "source_uri": "hf://math/0"},
            {"question": "2+2?", "final": "4", "source_uri": "hf://math/1"},
        ],
    )
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "needs_mapping_repair",
                    "stage": "sft",
                    "total_samples": 2,
                    "sampling": {"strategy": "weighted_sample", "seed": 11},
                    "export": {
                        "format": "jsonl",
                        "schema": {
                            "fields": {
                                "instruction": {"sources": ["question"]},
                                "input": {"sources": ["input"], "required": False, "default": ""},
                                "output": {"sources": ["answer"]},
                            },
                            "keep": ["source_uri"],
                            "include_dm": True,
                        },
                    },
                    "buckets": [
                        {
                            "name": "math",
                            "weight": 1,
                            "filter": "domain = 'math' AND task_type = 'SFT'",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest",
            "math_sft",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "math",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    exit_code = run([
        "dm",
        "--root",
        str(warehouse),
        "recipe",
        "export",
        str(recipe_path),
        "--out",
        str(export_dir),
    ])
    payload = _last_json(capsys)
    assert exit_code != 0
    details = payload["details"]
    assert details["blocked"] is True
    assert details["error_code"] == "export_schema_mapping_failed"
    assert details["export_schema"]["failures"][0]["missing"] == ["output"]
    assert "final" in details["export_schema"]["failures"][0]["available_source_keys"]
    assert not (export_dir / "part-00000.jsonl").exists()

def test_datamixer_dataflow_agent_run_exports_trials_and_applies_results(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.agents.Obtainer.datamixer import dataflow_agent

    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "math_sft.jsonl"
    work_dir = tmp_path / "dataflow_agent"
    _write_jsonl(
        records,
        [
            {
                "content": {"question": "What is 1+1?", "answer": "2"},
                "source_uri": "hf://math/0",
            },
            {
                "content": {"question": "What is 2+2?", "answer": "4"},
                "source_uri": "hf://math/1",
            },
        ],
    )

    captured: dict[str, str] = {}

    def fake_run_via_sdk(prompt, prov, cwd, timeout=600, network=True, **kwargs):
        captured["prompt"] = prompt
        captured["base_url"] = prov["base_url"]
        sample_line = next(line for line in prompt.splitlines() if line.startswith("- Sample JSONL file:"))
        sample_path = Path(sample_line.split(":", 1)[1].strip())
        rows = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()]
        full_line = next(
            line for line in prompt.splitlines()
            if line.startswith("- Full input JSONL")
        )
        full_path = Path(full_line.split(":", 1)[1].strip())
        full_rows = [json.loads(line) for line in full_path.read_text(encoding="utf-8").splitlines()]

        def write_processed(src_rows, name):
            processed = work_dir / name
            processed.parent.mkdir(parents=True, exist_ok=True)
            with processed.open("w", encoding="utf-8") as handle:
                for row in src_rows:
                    handle.write(
                        json.dumps(
                            {
                                **row,
                                "math_answer_quality": 0.95,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            return processed

        processed = write_processed(rows, "processed.jsonl")
        full_processed = write_processed(full_rows, "full_processed.jsonl")
        pipeline = work_dir / "pipeline.py"
        pipeline.write_text("# generated by fake codex\n", encoding="utf-8")
        return {
            "ok": True,
            "mode": "full_run",
            "operator_decision": {
                "ops": ["FormatStrPromptedGenerator", "GeneralFilter"],
                "field_flow": "raw_content -> math_answer_quality",
                "reason": "score answer quality then keep rows",
            },
            "pipeline_path": str(pipeline),
            "processed_jsonl": str(processed),
            "trial_rows_in": len(rows),
            "trial_rows_out": len(rows),
            "full_processed_jsonl": str(full_processed),
            "full_rows_in": len(full_rows),
            "full_rows_out": len(full_rows),
            "stdout_tail": "",
            "errors": [],
            "summary": "trial and full run ok",
        }

    monkeypatch.setattr(dataflow_agent, "run_via_sdk", fake_run_via_sdk)

    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "model",
            "add",
            "--name",
            "codex-test",
            "--api-url",
            "http://127.0.0.1:15721/v1/chat/completions",
            "--key",
            "dummy",
            "--model",
            "deepseek-chat",
        ],
        capsys,
    )
    _dm(
        warehouse,
        [
            "ingest",
            "math_sft",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "math",
            "--lang",
            "en",
            "--source",
            "huggingface",
            "--license",
            "mit",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    result = _dm(
        warehouse,
        [
            "dataflow",
            "agent-run",
            "--target",
            "score GSM8K answer-focused SFT rows and keep high-quality rows",
            "--model",
            "codex-test",
            "--dataset",
            "math_sft",
            "--trial-rows",
            "2",
            "--work-dir",
            str(work_dir),
            "--expected-outputs",
            "math_answer_quality",
            "--apply",
        ],
        capsys,
    )

    assert result["mode"] == "full_run"
    assert result["trial_rows_exported"] == 2
    assert result["full_rows_exported"] == 2
    assert result["full_rows_out"] == 2
    assert result["applied"] is True
    assert result["merge"]["updated"] == 2
    assert result["agent_result"]["full_output_audit"] == {
        "input_rows": 2,
        "output_rows": 2,
        "retained_input_rows": 2,
        "dropped_input_rows": 0,
        "unique_output_sample_ids": 2,
        "original_fields_preserved": True,
        "added_fields": ["math_answer_quality"],
    }
    assert result["agent_result"]["output_audit"] == {
        "input_rows": 2,
        "output_rows": 2,
        "retained_input_rows": 2,
        "dropped_input_rows": 0,
        "unique_output_sample_ids": 2,
        "original_fields_preserved": True,
        "added_fields": ["math_answer_quality"],
    }
    assert "generating-dataflow-pipeline" in captured["prompt"]
    assert "launch the full processing" in captured["prompt"]
    assert "sub-agents" in captured["prompt"]
    assert "after removing complete `<think>`" in captured["prompt"]
    assert Path(result["trial_jsonl"]).exists()
    assert Path(result["full_jsonl"]).exists()

    query = _dm(
        warehouse,
        [
            "query",
            "--dataset",
            "math_sft",
            "--columns",
            "sample_id,tags_json",
            "--limit",
            "2",
        ],
        capsys,
    )
    tags = [json.loads(row["tags_json"]) for row in query["rows"]]
    assert [tag["math_answer_quality"] for tag in tags] == [0.95, 0.95]

def test_dataflow_operator_llm_uses_default_qwen_not_codex_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.agents.Obtainer.datamixer import dataflow_agent

    (tmp_path / "starter.yaml").write_text(
        yaml.safe_dump({
            "system": {
                "model": {
                    "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
                    "proxy_api_key": "proxy-key",
                    "default_model": "qwen3-14b-fp8",
                    "codex_model": "deepseek-chat",
                    "default_tier": "medium",
                    "pool": [
                        {
                            "tier": "medium",
                            "name": "qwen3-14b-fp8",
                            "model_name": "qwen3-14b-fp8",
                            "base_url": "http://127.0.0.1:8000/v1",
                            "api_key": "local-key",
                        },
                        {
                            "tier": "high",
                            "name": "deepseek-chat",
                            "model_name": "deepseek-chat",
                            "base_url": "https://api.deepseek.com/v1",
                            "api_key": "deepseek-key",
                        },
                    ],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(dataflow_agent, "_project_root", lambda: tmp_path)

    config = dataflow_agent.operator_llm_config_from_starter()

    assert config == {
        "api_url": "http://127.0.0.1:8855/responseProxy/v1/chat/completions",
        "model_name": "qwen3-14b-fp8",
        "api_key_env": "DF_API_KEY",
        "api_key": "proxy-key",
    }


def test_dataflow_operator_llm_supports_process_local_override(monkeypatch) -> None:
    from loopai.agents.Obtainer.datamixer.dataflow_agent import (
        operator_llm_config_from_starter,
    )

    monkeypatch.setenv("DF_API_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setenv("DF_MODEL_NAME", "Qwen3.6-27B")
    monkeypatch.setenv("DF_API_KEY", "local-vllm")

    assert operator_llm_config_from_starter() == {
        "api_url": "http://127.0.0.1:8001/v1/chat/completions",
        "model_name": "Qwen3.6-27B",
        "api_key_env": "DF_API_KEY",
        "api_key": "local-vllm",
    }

def test_dataflow_agent_rejects_incomplete_intermediate_response(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.agents.Obtainer.datamixer import dataflow_agent

    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input.jsonl"
    _write_jsonl(records, [{"content": {"text": "finance quality sample"}}])

    monkeypatch.setattr(
        dataflow_agent,
        "run_via_sdk",
        lambda *args, **kwargs: {"summary": "Let me inspect more files."},
    )

    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        ["model", "add", "--name", "codex-test", "--api-url", "mock://agent"],
        capsys,
    )
    _dm(
        warehouse,
        [
            "ingest", "finance_l2", "--file", str(records),
            "--quality-level", "L2", "--domain", "general",
        ],
        capsys,
    )

    exit_code = run([
        "dm", "--root", str(warehouse), "dataflow", "agent-run",
        "--target", "audit quality", "--model", "codex-test",
        "--dataset", "finance_l2", "--trial-rows", "1", "--json",
    ])
    payload = _last_json(capsys)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "DATAMIXER_COMMAND_FAILED"
    assert "incomplete final result" in payload["details"]["error"]

def test_dataflow_agent_continues_incomplete_thread_until_valid(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.agents.Obtainer.datamixer import dataflow_agent

    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input.jsonl"
    work_dir = tmp_path / "agent"
    _write_jsonl(records, [{"content": {"text": "finance quality sample"}}])
    calls: list[tuple[str, str | None]] = []

    def fake_run(prompt, prov, cwd, timeout=600, thread_id=None, **kwargs):  # noqa: ARG001
        calls.append((prompt, thread_id))
        if len(calls) == 1:
            return {"summary": "Inspecting operators.", "thread_id": "thread-1"}
        trial_path = work_dir / "trial_input.jsonl"
        full_path = work_dir / "full_input.jsonl"
        processed = work_dir / "processed.jsonl"
        full_processed = work_dir / "full_processed.jsonl"
        pipeline = work_dir / "pipeline.py"
        processed.write_text(trial_path.read_text(encoding="utf-8"), encoding="utf-8")
        full_processed.write_text(full_path.read_text(encoding="utf-8"), encoding="utf-8")
        pipeline.write_text("# generated pipeline\n", encoding="utf-8")
        full_rows = [json.loads(line) for line in full_path.read_text(encoding="utf-8").splitlines()]
        return {
            "ok": True,
            "mode": "full_run",
            "operator_decision": {
                "ops": ["HashDeduplicateFilter"],
                "field_flow": "raw_content -> exact_dedup_label",
                "reason": "remove exact duplicates",
            },
            "pipeline_path": str(pipeline),
            "processed_jsonl": str(processed),
            "trial_rows_in": 1,
            "trial_rows_out": 1,
            "full_processed_jsonl": str(full_processed),
            "full_rows_in": len(full_rows),
            "full_rows_out": len(full_rows),
            "stdout_tail": "",
            "errors": [],
            "summary": "trial and full complete",
        }

    monkeypatch.setattr(dataflow_agent, "run_via_sdk", fake_run)
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        ["model", "add", "--name", "codex-test", "--api-url", "mock://agent"],
        capsys,
    )
    _dm(
        warehouse,
        [
            "ingest", "finance_l2", "--file", str(records),
            "--quality-level", "L2", "--domain", "general",
        ],
        capsys,
    )

    result = _dm(
        warehouse,
        [
            "dataflow", "agent-run", "--target", "audit quality",
            "--model", "codex-test", "--dataset", "finance_l2",
            "--trial-rows", "1", "--work-dir", str(work_dir),
        ],
        capsys,
    )

    assert result["agent_result"]["mode"] == "full_run"
    assert len(calls) == 2
    assert calls[1][1] == "thread-1"
    assert "Finish the operator decision" in calls[1][0]

def test_dataflow_agent_rejects_dropped_original_metadata(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.agents.Obtainer.datamixer import dataflow_agent

    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input.jsonl"
    work_dir = tmp_path / "agent"
    _write_jsonl(
        records,
        [{
            "content": {"text": "finance quality sample"},
            "retrieved_at": "2026-08-01T04:02:05.341781+00:00",
        }],
    )

    def fake_run(prompt, prov, cwd, timeout=600, **kwargs):  # noqa: ARG001
        trial_path = work_dir / "trial_input.jsonl"
        full_path = work_dir / "full_input.jsonl"
        row = json.loads(trial_path.read_text(encoding="utf-8"))
        bad_row = json.loads(full_path.read_text(encoding="utf-8"))
        del bad_row["retrieved_at"]  # dropping an original field is fatal
        processed = work_dir / "processed.jsonl"
        full_processed = work_dir / "full_processed.jsonl"
        pipeline = work_dir / "pipeline.py"
        processed.write_text(json.dumps(row) + "\n", encoding="utf-8")
        full_processed.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")
        pipeline.write_text("# generated pipeline\n", encoding="utf-8")
        return {
            "ok": True,
            "mode": "full_run",
            "operator_decision": {
                "ops": ["PandasOperator"],
                "field_flow": "raw_content -> quality_score",
                "reason": "score quality",
            },
            "pipeline_path": str(pipeline),
            "processed_jsonl": str(processed),
            "trial_rows_in": 1,
            "trial_rows_out": 1,
            "full_processed_jsonl": str(full_processed),
            "full_rows_in": 1,
            "full_rows_out": 1,
            "stdout_tail": "",
            "errors": [],
            "summary": "trial and full complete",
        }

    monkeypatch.setattr(dataflow_agent, "run_via_sdk", fake_run)
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        ["model", "add", "--name", "codex-test", "--api-url", "mock://agent"],
        capsys,
    )
    _dm(
        warehouse,
        [
            "ingest", "finance_l2", "--file", str(records),
            "--quality-level", "L2", "--domain", "general",
        ],
        capsys,
    )

    exit_code = run([
        "dm", "--root", str(warehouse), "dataflow", "agent-run",
        "--target", "audit quality", "--model", "codex-test",
        "--dataset", "finance_l2", "--trial-rows", "1",
        "--work-dir", str(work_dir), "--json",
    ])
    payload = _last_json(capsys)

    assert exit_code == 1
    assert payload["ok"] is False
    assert "dropped original input fields" in payload["details"]["error"]
    assert "retrieved_at" in payload["details"]["error"]


def test_dataflow_agent_delivers_trial_pipeline_for_upstream_full_run(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.agents.Obtainer.datamixer import dataflow_agent

    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input.jsonl"
    work_dir = tmp_path / "agent"
    _write_jsonl(records, [{"content": {"text": "finance quality sample"}}])

    def fake_run(prompt, prov, cwd, timeout=600, **kwargs):  # noqa: ARG001
        trial_path = work_dir / "trial_input.jsonl"
        processed = work_dir / "processed.jsonl"
        pipeline = work_dir / "pipeline.py"
        processed.write_text(trial_path.read_text(encoding="utf-8"), encoding="utf-8")
        pipeline.write_text("# generated pipeline\n", encoding="utf-8")
        return {
            "ok": True,
            "mode": "trial_run",
            "operator_decision": {
                "ops": ["HashDeduplicateFilter"],
                "field_flow": "raw_content -> exact_dedup_label",
                "reason": "remove exact duplicates",
            },
            "pipeline_path": str(pipeline),
            "processed_jsonl": str(processed),
            "trial_rows_in": 1,
            "trial_rows_out": 1,
            "full_processed_jsonl": None,
            "full_rows_in": None,
            "full_rows_out": None,
            "stdout_tail": "",
            "errors": [],
            "summary": "trial only",
        }

    monkeypatch.setattr(dataflow_agent, "run_via_sdk", fake_run)
    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        ["model", "add", "--name", "codex-test", "--api-url", "mock://agent"],
        capsys,
    )
    _dm(
        warehouse,
        [
            "ingest", "finance_l2", "--file", str(records),
            "--quality-level", "L2", "--domain", "general",
        ],
        capsys,
    )

    exit_code = run([
        "dm", "--root", str(warehouse), "dataflow", "agent-run",
        "--target", "audit quality", "--model", "codex-test",
        "--dataset", "finance_l2", "--trial-rows", "1",
        "--work-dir", str(work_dir), "--json",
    ])
    payload = _last_json(capsys)

    # The DataFlow agent only delivers the trial-verified pipeline; the
    # full run is executed upstream via the chunked runner.
    assert exit_code == 0
    assert payload["ok"] is True
    details = payload["result"]
    assert details["mode"] == "trial_run"
    upstream = details["upstream"]
    assert upstream["delivered_pipeline"] is True
    assert "dataflow_chunked_runner" in upstream["chunked_run_command"]
    assert "--chunk-size 10000" in upstream["chunked_run_command"]
    assert "apply-jsonl" in upstream["apply_command"]
    assert details["applied"] is False
    assert details["merge"] is None


def test_parse_llm_scalar_score_ignores_reasoning_numbers_and_fails_closed() -> None:
    from loopai.agents.Obtainer.datamixer.dataflow_agent import parse_llm_scalar_score

    assert parse_llm_scalar_score(
        "<think>Check dimensions 1, 2, 3, 4, and 5. Maybe score 2.</think>\n4"
    ) == 4
    assert parse_llm_scalar_score("<answer>5</answer>") == 5

    with pytest.raises(ValueError, match="one integer"):
        parse_llm_scalar_score("<think>score 4")
    with pytest.raises(ValueError, match="allowed range"):
        parse_llm_scalar_score("0")
    with pytest.raises(ValueError, match="multiple"):
        parse_llm_scalar_score("<answer>2</answer><answer>4</answer>")

def test_sft_recipe_defaults_to_100k_records_when_budget_is_missing(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "input" / "syntax.jsonl"
    recipe_path = tmp_path / "recipe.yaml"
    _write_jsonl(
        records,
        [
            {
                "content": {"instruction": "Fix syntax", "output": "print('ok')"},
                "bug_type": "syntax",
                "quality_score": 0.9,
            }
        ],
    )
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "syntax_default_budget",
                    "stage": "sft",
                    "sampling": {"strategy": "weighted_sample"},
                    "buckets": [
                        {
                            "name": "syntax_repair",
                            "weight": 1,
                            "filter": "domain = 'code' AND task_type = 'SFT' "
                            "AND json_extract(tags_json, '$.\"bug_type\"') = 'syntax'",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    _dm(warehouse, ["init"], capsys)
    _dm(
        warehouse,
        [
            "ingest",
            "syntax_mix",
            "--file",
            str(records),
            "--quality-level",
            "L3",
            "--stage",
            "sft",
            "--domain",
            "code",
            "--task-type",
            "SFT",
        ],
        capsys,
    )

    plan = _dm(warehouse, ["recipe", "plan", str(recipe_path)], capsys)
    assert plan["budget_kind"] == "sample"
    assert plan["total_samples"] == 100000
    assert "short on samples" in plan["warnings"][0]

def test_failure_taxonomy_recipe_rejects_broad_proxy_filters(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "warehouse"
    recipe_path = tmp_path / "bad_recipe.yaml"
    recipe_path.write_text(
        yaml.safe_dump(
            {
                "recipe": {
                    "name": "bad_failure_taxonomy",
                    "stage": "sft",
                    "total_samples": 100000,
                    "sampling": {"strategy": "weighted_sample"},
                    "buckets": [
                        {
                            "name": "syntax_repair",
                            "weight": 1,
                            "filter": "domain = 'code' AND lang = 'python'",
                        }
                    ],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _dm(warehouse, ["init"], capsys)

    exit_code = run(["dm", "--root", str(warehouse), "recipe", "plan", str(recipe_path)])
    payload = _last_json(capsys)
    assert exit_code != 0
    assert payload["error_code"] == "DATAMIXER_COMMAND_FAILED"
    assert "semantic failure tag" in payload["hint"]

def test_dm_lake_pointer_resolves_to_datamixer_warehouse(tmp_path: Path, capsys) -> None:
    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    link = tmp_path / "repo" / ".loopai" / "lake.yaml"

    _dm(warehouse, ["init"], capsys)
    write_lake_config(link, root=lake_root, warehouse=warehouse)

    result = run(["dm", "--lake", str(link), "stats"])
    payload = _last_json(capsys)
    assert result == 0
    assert payload["result"]["samples"] == 0

def test_dm_lake_pointer_prefers_root_when_root_is_datamixer_warehouse(tmp_path: Path, capsys) -> None:
    lake_root = tmp_path / "lake"
    stale_warehouse = lake_root / "warehouse"
    link = tmp_path / "repo" / ".loopai" / "lake.yaml"

    _dm(lake_root, ["init"], capsys)
    _dm(stale_warehouse, ["init"], capsys)
    input_path = tmp_path / "records.jsonl"
    _write_jsonl(input_path, [{"text": "root record"}])
    _dm(
        lake_root,
        [
            "ingest",
            "root_dataset",
            "--file",
            str(input_path),
            "--quality-level",
            "L1",
        ],
        capsys,
    )
    write_lake_config(link, root=lake_root, warehouse=stale_warehouse)

    result = run(["dm", "--lake", str(link), "status"])
    payload = _last_json(capsys)

    assert result == 0
    assert payload["result"]["warehouse"] == str(lake_root.resolve())
    assert payload["result"]["samples"] == 1

def test_dm_lake_load_reuses_existing_datamixer_warehouse(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "shared" / "warehouse"
    link = tmp_path / "repo" / ".loopai" / "lake.yaml"

    _dm(warehouse, ["init"], capsys)

    exit_code = run(["dm", "lake", "load", "--warehouse", str(warehouse), "--link", str(link)])
    payload = _last_json(capsys)
    assert exit_code == 0
    assert payload["command"] == "dm lake load"
    assert payload["warehouse"] == str(warehouse.resolve())
    assert payload["lake_config"] == str(link.resolve())
    assert link.exists()

    result = run(["dm", "--lake", str(link), "status"])
    status = _last_json(capsys)
    assert result == 0
    assert status["result"]["warehouse"] == str(warehouse.resolve())

def test_dm_lake_delete_unloads_pointer_and_preserves_warehouse(tmp_path: Path, capsys) -> None:
    warehouse = tmp_path / "shared" / "warehouse"
    link = tmp_path / "repo" / ".loopai" / "lake.yaml"

    _dm(warehouse, ["init"], capsys)
    run(["dm", "lake", "load", "--warehouse", str(warehouse), "--link", str(link)])
    _last_json(capsys)

    exit_code = run(["dm", "lake", "delete", "--link", str(link)])
    payload = _last_json(capsys)
    assert exit_code == 0
    assert payload["command"] == "dm lake delete"
    assert payload["pointer_removed"] is True
    assert payload["warehouse_deleted"] is False
    assert not link.exists()
    assert (warehouse / "datamixer.toml").exists()

def test_dm_lake_scan_discovers_project_lakes_and_marks_active(tmp_path: Path, capsys) -> None:
    lake_root = tmp_path / "lake"
    warehouse = lake_root / "warehouse"
    link = tmp_path / "repo" / ".loopai" / "lake.yaml"

    _dm(warehouse, ["init"], capsys)
    write_lake_config(link, root=lake_root, warehouse=warehouse)

    exit_code = run(
        [
            "dm",
            "lake",
            "scan",
            "--project-root",
            str(tmp_path),
            "--link",
            str(link),
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["command"] == "dm lake scan"
    assert payload["count"] >= 1
    active = [lake for lake in payload["lakes"] if lake["active"]]
    assert len(active) == 1
    assert active[0]["warehouse"] == str(warehouse.resolve())
    assert active[0]["warehouse_exists"] is True

def test_sft_export_agent_start_dry_run_writes_isolated_worker_prompt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "sft_export_run"
    report = tmp_path / "analysis_report.md"
    report.write_text("Need Alpaca SFT math data.", encoding="utf-8")
    _set_starter_model_pool(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "sft-export-agent",
            "start",
            "--run",
            str(run_dir),
            "--analysis-report",
            str(report),
            "--format",
            "alpaca",
            "--target-records",
            "10",
            "--dry-run",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"] == "dry_run"
    assert payload["command"] == "dm.sft-export-agent"
    prompt = (run_dir / "worker_prompt.md").read_text(encoding="utf-8")
    assert "Final training data must be produced by DataMixer `recipe export`" in prompt
    assert "output.sources` must not include text, raw_content, content" in prompt
    assert "instruction != output" in prompt
    assert f"{sys.executable} -m loopai.skills.ObtainerCLI.cli" in prompt
    state = json.loads((run_dir / "thread.json").read_text(encoding="utf-8"))
    assert state["target_records"] == 10
    assert state["format"] == "alpaca"
    assert state["provider"]["model_pool_name"] == "codex"
    assert state["provider"]["source"] == "starter_yaml:system.model"

    exit_code = run(["dm", "--root", str(warehouse), "sft-export-agent", "status", "--run", str(run_dir)])
    status_payload = _last_json(capsys)
    assert exit_code == 0
    assert status_payload["status"]["state"] == "dry_run"

def test_sft_export_agent_start_requires_loaded_lake(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"  # never initialized
    run_dir = tmp_path / "sft_export_run"
    report = tmp_path / "analysis_report.md"
    report.write_text("Need SFT math data.", encoding="utf-8")
    _set_starter_model_pool(tmp_path, monkeypatch)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "sft-export-agent",
            "start",
            "--run",
            str(run_dir),
            "--analysis-report",
            str(report),
            "--target-records",
            "10",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 2
    assert payload["error_code"] == "LAKE_NOT_LOADED"
    assert "dm lake init" in payload["hint"] or "dm lake load" in payload["hint"]


def test_sft_export_agent_resume_reuses_saved_thread_id(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import sft_export_agent

    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "sft_export_run"
    run_dir.mkdir()
    (run_dir / "thread.json").write_text(
        json.dumps(
            {
                "warehouse": str(warehouse),
                "thread_id": "thread-123",
                "provider": {"source": "starter_yaml:system.model", "model_pool_name": "codex", "model": "codex"},
            }
        ),
        encoding="utf-8",
    )
    _set_starter_model_pool(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")
    captured: dict[str, object] = {"prompts": [], "thread_ids": []}

    def fake_run_via_sdk(prompt, prov, cwd, timeout=600, network=True, thread_id=None, **kwargs):
        captured["prompts"].append(prompt)
        captured["thread_ids"].append(thread_id)
        captured["prov"] = prov
        (run_dir / "final_report.json").write_text(
            json.dumps({"ok": False, "blockers": ["needs repair"]}),
            encoding="utf-8",
        )
        return {"summary": "resumed", "thread_id": thread_id}

    monkeypatch.setattr(sft_export_agent.codex, "run_via_sdk", fake_run_via_sdk)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "sft-export-agent",
            "resume",
            "--run",
            str(run_dir),
            "--message",
            "remove text fallback and retry",
            "--foreground",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert captured["thread_ids"]
    assert all(thread_id == "thread-123" for thread_id in captured["thread_ids"])
    assert "remove text fallback and retry" in str(captured["prompts"][0])
    assert "outer SFT export acceptance gate rejected" in str(captured["prompts"][-1])
    assert payload["ok"] is False
    assert payload["thread_id"] == "thread-123"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["worker_ok"] is False
    assert status["acceptance_ok"] is False

def test_sft_export_agent_acceptance_feedback_retries_until_valid(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import sft_export_agent

    warehouse = tmp_path / "warehouse"
    DataStore.init(warehouse).close()
    run_dir = tmp_path / "sft_export_run"
    report = tmp_path / "analysis_report.md"
    report.write_text("Need two Alpaca records.", encoding="utf-8")
    _set_starter_model_pool(tmp_path, monkeypatch)
    calls: list[dict[str, object]] = []

    def write_export(*, invalid: bool) -> None:
        export_dir = run_dir / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        recipe_dir = run_dir / "recipe"
        recipe_dir.mkdir(parents=True, exist_ok=True)
        source = "text" if invalid else "answer"
        (recipe_dir / "recipe_plan.json").write_text(
            json.dumps({
                "budget_kind": "sample",
                "total_samples": 2,
                "buckets": [{
                    "name": "bucket_a",
                    "weight": 1.0,
                    "target_samples": 2,
                    "available_samples": 2,
                }],
            }),
            encoding="utf-8",
        )
        (recipe_dir / "mix_plan.json").write_text(
            json.dumps({
                "budget_kind": "sample",
                "target_records": 2,
                "buckets": [{
                    "name": "bucket_a",
                    "weight": 1.0,
                    "target_records": 2,
                    "rationale": "The Analyzer requested this capability only.",
                }],
            }),
            encoding="utf-8",
        )
        (export_dir / "part-00000.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"instruction": "q1", "input": "", "output": "a1"}),
                    json.dumps({"instruction": "q2", "input": "", "output": "a2"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (export_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "snapshot_id": "snap-1",
                    "dataset_digest": "digest-1",
                    "recipe_fingerprint": "fp-1",
                    "export_schema": {"fields": ["instruction", "input", "output"]},
                    "recipe": {
                        "export": {
                            "schema": {
                                "fields": {
                                    "output": {"sources": [source]},
                                }
                            }
                        },
                        "buckets": [
                            {
                                "name": "bucket_a",
                                "filter": "dataset_id='ds-a'",
                                "export": {
                                    "schema": {
                                        "fields": {
                                            "output": {"sources": [source]},
                                        }
                                    }
                                },
                            }
                        ],
                    },
                    "summary": {
                        "selected_samples": 2,
                        "buckets": [{
                            "bucket": "bucket_a",
                            "weight": 1.0,
                            "target": 2,
                            "realized_samples": 2,
                        }],
                    },
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "final_report.json").write_text(
            json.dumps({
                "ok": True,
                "export_path": str(export_dir),
                "planned_mix": {"bucket_a": 2},
                "actual_mix": {"bucket_a": 2},
            }),
            encoding="utf-8",
        )

    def fake_run_via_sdk(prompt, prov, cwd, timeout=600, network=True, thread_id=None, **kwargs):
        calls.append({"prompt": prompt, "thread_id": thread_id})
        write_export(invalid=len(calls) == 1)
        return {"summary": "ok", "thread_id": "thread-accept"}

    monkeypatch.setattr(sft_export_agent.codex, "run_via_sdk", fake_run_via_sdk)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "sft-export-agent",
            "start",
            "--run",
            str(run_dir),
            "--analysis-report",
            str(report),
            "--target-records",
            "2",
            "--foreground",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["ok"] is True
    assert len(calls) == 2
    assert calls[0]["thread_id"] is None
    assert calls[1]["thread_id"] == "thread-accept"
    assert "BUCKET_OUTPUT_SOURCE_FORBIDDEN" in str(calls[1]["prompt"])
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["acceptance_ok"] is True

def test_sft_export_agent_start_defaults_to_background(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import sft_export_agent

    warehouse = tmp_path / "warehouse"
    DataStore.init(warehouse).close()
    run_dir = tmp_path / "sft_export_run"
    report = tmp_path / "analysis_report.md"
    report.write_text("Need Alpaca SFT math data.", encoding="utf-8")
    _set_starter_model_pool(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")
    captured: dict[str, object] = {}

    def fake_spawn_background(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "background_started",
            "run_dir": str(kwargs["run_dir"]),
            "pid": 4242,
            "prompt_path": str(kwargs["prompt_path"]),
            "thread_id": kwargs.get("thread_id") or None,
        }

    monkeypatch.setattr(sft_export_agent, "_spawn_background", fake_spawn_background)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "sft-export-agent",
            "start",
            "--run",
            str(run_dir),
            "--analysis-report",
            str(report),
            "--format",
            "alpaca",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"] == "background_started"
    assert payload["pid"] == 4242
    assert captured["warehouse"] == warehouse.resolve()
    assert Path(captured["prompt_path"]).exists()
    assert (run_dir / "thread.json").exists()

def test_dataset_acquisition_agent_start_dry_run_writes_worker_prompt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "acquisition_run"
    report = tmp_path / "analysis_report.md"
    report.write_text("Need general domain datasets.", encoding="utf-8")
    _set_starter_model_pool(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "start",
            "--run",
            str(run_dir),
            "--analysis-report",
            str(report),
            "--objective",
            "ingest general domain datasets",
            "--target-datasets",
            "30",
            "--max-rows-per-dataset",
            "200000",
            "--max-bytes-per-dataset",
            str(5 * 1024 * 1024 * 1024),
            "--dry-run",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"] == "dry_run"
    assert payload["command"] == "dm.dataset-acquisition-agent"
    prompt = (run_dir / "worker_prompt.md").read_text(encoding="utf-8")
    assert "compare the candidate list against the original user" in prompt
    assert "Each single dataset is capped at 100000 rows" in prompt
    assert "2147483648 output bytes" in prompt
    assert "DataMixer `ingest` or `agent-ingest`" in prompt
    assert "--quality-level <L1|L2|L3|L4>" in prompt
    assert "selected quality_level and selection rationale" in prompt
    assert "register it during ingest with `--dataset-card <path>`" in prompt
    assert "Pass `--derived-field <name>` for each derived field" in prompt
    assert "dataset_cards/*.md" in prompt
    assert "SearchAgent task JSON:" in prompt
    assert "manifest/tasks.json" in prompt
    assert "manifest/searchagent/searchagent_manifest.json" in prompt
    assert "use its non-empty `webagent_model` exactly" in prompt
    assert "launch SearchAgent and WebAgent in" in prompt
    assert "webagent campaign start domain_data_acquisition" in prompt
    assert "SEARCHAGENT_PID" in prompt
    assert "WEBAGENT_PID" in prompt
    assert "webagent_campaign_status.json" in prompt
    assert "webagent_model_missing" in prompt
    assert "--output-root" in prompt
    assert "--no-deepsearch" in prompt
    assert "Do not inspect" in prompt
    assert "manifest/tasks/" in prompt
    assert "download manifest" in prompt
    assert "Do not hand-write this file with `echo`" in prompt
    assert "Keep an oversampled candidate pool" in prompt
    assert "--limit 150" in prompt
    assert "--max-rows 100000" in prompt
    assert "--max-bytes-per-dataset 2147483648" in prompt
    assert "download_results.json" in prompt
    assert "records_jsonl" in prompt
    assert "Wait for the download command to exit" in prompt
    assert "zero-byte JSONL file is" in prompt
    assert "ingest <dataset_name>" in prompt
    assert "--file <records_jsonl>" in prompt
    assert "`<dataset_name>` is required" in prompt
    assert "Do not omit `<dataset_name>`" in prompt
    assert "Top-level" in prompt
    assert "cli ingest" in prompt
    assert "--tag source_dataset_id=<source_dataset_id>" in prompt
    assert "--source-dataset-id" in prompt
    assert "--source-url" in prompt
    assert f"{sys.executable} -m loopai.skills.ObtainerCLI.cli" in prompt
    state = json.loads((run_dir / "thread.json").read_text(encoding="utf-8"))
    assert state["target_datasets"] == 30
    assert state["max_rows_per_dataset"] == 100000
    assert state["max_bytes_per_dataset"] == 2147483648
    assert state["provider"]["model_pool_name"] == "codex"
    assert state["provider"]["model"] == "codex"

def test_dataset_acquisition_agent_model_requires_starter_model_pool(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "acquisition_run"
    starter_config = tmp_path / "starter.yaml"
    starter_config.write_text(
        yaml.safe_dump(
            {
                "system": {
                    "starter_model_name": "yaml-model",
                    "starter_model_path": "yaml-model",
                    "starter_base_url": "http://yaml.example/v1",
                    "starter_api_key": "dummy",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "yaml-model")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("STARTER_CONFIG", str(starter_config))

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "ingest reasoning datasets",
            "--model",
            "yaml-model",
            "--dry-run",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 2
    assert payload["error_code"] == "OBTAINERCLI_MODEL_NOT_FOUND"
    assert "Starter model pool" in payload["message"]
    assert not (run_dir / "thread.json").exists()

def test_obtainercli_provider_prefers_codex_model_from_starter_pool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI.sft_export_agent import _resolve_provider

    starter_config = tmp_path / "starter.yaml"
    starter_config.write_text(
        yaml.safe_dump(
            {
                "system": {
                    "api_port": 8855,
                    "codex_model": "deepseek-chat",
                    "model": {
                        "default_tier": "low",
                        "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
                        "pool": [
                            {
                                "tier": "low",
                                "name": "starter",
                                "model_name": "gpt-4o-mini",
                                "base_url": "http://low.example/v1",
                                "api_key": "low-key",
                            },
                            {
                                "tier": "medium",
                                "name": "codex",
                                "model_name": "deepseek-chat",
                                "base_url": "http://deepseek.example/v1",
                                "api_key": "deepseek-key",
                            },
                        ],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STARTER_CONFIG", str(starter_config))
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")

    prov, meta = _resolve_provider(tmp_path, None)

    assert prov["base_url"] == "http://127.0.0.1:8855/responseProxy/v1"
    assert prov["model"] == "deepseek-chat"
    assert meta["model_pool_name"] == "codex"
    assert meta["tier"] == "medium"
    assert meta["upstream_model_name"] == "deepseek-chat"
    assert meta["requested_model"] == "deepseek-chat"

def test_obtainercli_provider_uses_nested_codex_default_not_default_tier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI.sft_export_agent import _resolve_provider

    starter_config = tmp_path / "starter.yaml"
    starter_config.write_text(
        yaml.safe_dump({
            "system": {
                "model": {
                    "default_model": "local-qwen",
                    "default_tier": "medium",
                    "codex_model": "deepseek-chat",
                    "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
                    "pool": [
                        {
                            "tier": "medium",
                            "name": "local-qwen",
                            "model_name": "qwen3-14b-fp8",
                            "base_url": "http://127.0.0.1:8000/v1",
                        },
                        {
                            "tier": "high",
                            "name": "deepseek-chat",
                            "model_name": "deepseek-chat",
                            "base_url": "https://api.deepseek.com/v1",
                            "api_key": "deepseek-key",
                        },
                    ],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("STARTER_CONFIG", str(starter_config))

    prov, meta = _resolve_provider(tmp_path, None)

    assert prov["model"] == "deepseek-chat"
    assert meta["model_pool_name"] == "deepseek-chat"
    assert meta["upstream_model_name"] == "deepseek-chat"
    assert meta["tier"] == "high"
    assert meta["requested_model"] == "deepseek-chat"

def test_obtainercli_provider_does_not_silently_fallback_unknown_pool_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI.sft_export_agent import _resolve_provider

    starter_config = tmp_path / "starter.yaml"
    starter_config.write_text(
        yaml.safe_dump(
            {
                "system": {
                    "model": {
                        "default_tier": "low",
                        "pool": [
                            {
                                "tier": "low",
                                "name": "starter",
                                "model_name": "gpt-4o-mini",
                                "base_url": "http://low.example/v1",
                                "api_key": "low-key",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STARTER_CONFIG", str(starter_config))
    monkeypatch.delenv("CODEX_BASE_URL", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)

    with pytest.raises(ObtainerCliError) as exc:
        _resolve_provider(tmp_path, "deepseek-codex")

    assert exc.value.error_code == "OBTAINERCLI_MODEL_NOT_FOUND"

def test_dataset_acquisition_agent_start_defaults_to_background(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    warehouse = tmp_path / "warehouse"
    DataStore.init(warehouse).close()
    run_dir = tmp_path / "acquisition_run"
    _set_starter_model_pool(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")
    captured: dict[str, object] = {}

    def fake_spawn_background(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "background_started",
            "run_dir": str(kwargs["run_dir"]),
            "pid": 5252,
            "prompt_path": str(kwargs["prompt_path"]),
            "thread_id": kwargs.get("thread_id") or None,
        }

    monkeypatch.setattr(dataset_acquisition_agent, "_spawn_background", fake_spawn_background)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "ingest 3 math datasets",
            "--target-datasets",
            "3",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"] == "background_started"
    assert payload["pid"] == 5252
    assert captured["warehouse"] == warehouse.resolve()
    assert Path(captured["prompt_path"]).exists()
    assert (run_dir / "thread.json").exists()
    state = json.loads((run_dir / "thread.json").read_text(encoding="utf-8"))
    assert state["provider"]["model_pool_name"] == "codex"
    assert state["provider"]["model"] == "codex"
    assert state["resolved_model"] == "deepseek-chat"
    assert state["webagent_model"] == "codex"
    assert state["model_source"] == "codex_default"
    registered = json.loads((warehouse / "models.json").read_text(encoding="utf-8"))
    assert registered["default_model"] == "codex"
    assert registered["models"]["codex"]["model"] == "deepseek-chat"
    assert registered["models"]["codex"]["response_format"] == "response"

def test_dataset_acquisition_agent_large_start_uses_scaled_default_timeout(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    warehouse = tmp_path / "warehouse"
    DataStore.init(warehouse).close()
    run_dir = tmp_path / "acquisition_run"
    _set_starter_model_pool(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def fake_spawn_background(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "background_started",
            "run_dir": str(kwargs["run_dir"]),
            "pid": 5252,
            "prompt_path": str(kwargs["prompt_path"]),
        }

    monkeypatch.setattr(dataset_acquisition_agent, "_spawn_background", fake_spawn_background)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "start",
            "--run",
            str(run_dir),
            "--objective",
            "build a 100k finance SFT dataset",
            "--target-datasets",
            "40",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"] == "background_started"
    assert captured["timeout"] > 3600

def test_dataset_acquisition_agent_resume_reuses_saved_thread_id(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "acquisition_run"
    run_dir.mkdir()
    (run_dir / "thread.json").write_text(
        json.dumps(
            {
                "warehouse": str(warehouse),
                "thread_id": "thread-acq-123",
                "provider": {"source": "starter_yaml:system.model", "model_pool_name": "codex", "model": "codex"},
            }
        ),
        encoding="utf-8",
    )
    _set_starter_model_pool(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")
    captured: dict[str, object] = {}

    def fake_run_via_sdk(prompt, prov, cwd, timeout=600, network=True, thread_id=None, **kwargs):
        captured["prompt"] = prompt
        captured["thread_id"] = thread_id
        _write_successful_acquisition_artifacts(run_dir)
        (run_dir / "final_report.json").write_text(
            json.dumps({"ok": True, "datasets_ingested": 1}),
            encoding="utf-8",
        )
        return {"summary": "resumed", "thread_id": thread_id}

    monkeypatch.setattr(dataset_acquisition_agent.codex, "run_via_sdk", fake_run_via_sdk, raising=False)

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "resume",
            "--run",
            str(run_dir),
            "--message",
            "drop unrelated datasets and continue",
            "--foreground",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert captured["thread_id"] == "thread-acq-123"
    assert "drop unrelated datasets and continue" in str(captured["prompt"])
    assert payload["thread_id"] == "thread-acq-123"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["worker_ok"] is True

def test_dataset_acquisition_worker_env_isolates_outer_task_context(
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent
    from loopai.agents.Obtainer.datamixer import codex

    monkeypatch.setenv("TASK_ID", "outer-task")
    monkeypatch.setenv("task_id", "outer-task-lower")
    monkeypatch.setenv("DB_PATH", "/tmp/outer.db")
    monkeypatch.setenv("CODEX_THREAD_ID", "outer-thread")
    monkeypatch.setenv("CODEX_USE_PROJECT_CONFIG", "1")

    env = dataset_acquisition_agent._worker_env(
        prov={"model": "deepseek-v4-pro", "base_url": "http://proxy/v1", "api_key": "dummy"}
    )

    assert "TASK_ID" not in env
    assert "task_id" not in env
    assert "DB_PATH" not in env
    assert "CODEX_THREAD_ID" not in env
    assert "CODEX_USE_PROJECT_CONFIG" not in env
    assert env["CODEX_HOME"].endswith("outputs/obtainer/.codex/worker")
    assert env["LOOPAI_WORKER_KIND"] == "dataset-acquisition-agent"
    python_executable = codex.loopai_python_executable()
    assert env["LOOPAI_PYTHON_EXECUTABLE"] == python_executable
    assert str(Path(python_executable).resolve().parent) in env["PATH"].split(":")
    assert env["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert env["HF_HUB_ENDPOINT"] == "https://hf-mirror.com"
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert "CODEX_API_KEY" not in env
    assert "CODEX_BASE_URL" not in env
    assert "CODEX_MODEL" not in env
    assert "DEEPSEEK_API_KEY" not in env
    assert "DEEPSEEK_BASE_URL" not in env
    assert "DEEPSEEK_MODEL" not in env
    assert "OBTAINER_MODEL" not in env
    assert "OBTAINER_BASE_URL" not in env
    assert "OBTAINER_API_KEY" not in env

def test_dataset_acquisition_worker_preserves_custom_hf_endpoint(
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    monkeypatch.setenv("HF_ENDPOINT", "https://hf.internal.example")

    env = dataset_acquisition_agent._worker_env()

    assert env["HF_ENDPOINT"] == "https://hf.internal.example"
    assert env["HF_HUB_ENDPOINT"] == "https://hf.internal.example"

def test_datamixer_codex_prompt_uses_current_python_executable(
    monkeypatch,
) -> None:
    from loopai.agents.Obtainer.datamixer import codex

    monkeypatch.delenv("LOOPAI_PYTHON_EXECUTABLE", raising=False)

    prompt = codex.build_prompt(
        "/tmp/data.jsonl", "demo", "/tmp/warehouse", "L2"
    )

    assert f"DM={codex.loopai_python_executable()} -m loopai.agents.Obtainer.datamixer" in prompt
    assert "QUALITY_LEVEL=L2" in prompt
    assert '--quality-level "$QUALITY_LEVEL"' in prompt

def test_codex_runner_path_uses_configured_node_bin_dir(tmp_path: Path, monkeypatch) -> None:
    from loopai.agents.Obtainer.datamixer import codex

    env_bin = tmp_path / "env" / "bin"
    node_bin = tmp_path / "node" / "bin"
    env_bin.mkdir(parents=True)
    node_bin.mkdir(parents=True)
    python_executable = env_bin / "python"
    python_executable.write_text("", encoding="utf-8")
    python_executable.chmod(0o755)
    monkeypatch.setenv("LOOPAI_NODE_BIN_DIR", str(node_bin))

    path = codex.runner_process_path(str(python_executable), "/usr/bin:/bin")

    assert path.split(":")[:2] == [str(node_bin), str(env_bin)]

def test_codex_runner_path_does_not_prepend_system_python_bin(monkeypatch) -> None:
    from loopai.agents.Obtainer.datamixer import codex

    monkeypatch.delenv("LOOPAI_NODE_BIN_DIR", raising=False)

    path = codex.runner_process_path("/usr/bin/python3", "/opt/node/bin:/usr/bin:/bin")

    assert path.split(":")[:3] == ["/opt/node/bin", "/usr/bin", "/bin"]

def test_codex_runner_library_path_prefers_python_environment(
    tmp_path: Path,
) -> None:
    from loopai.agents.Obtainer.datamixer import codex

    python_executable = tmp_path / "env" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    (tmp_path / "env" / "lib").mkdir()
    python_executable.write_text("", encoding="utf-8")

    path = codex.runner_library_path(
        str(python_executable), "/existing/lib:/another/lib"
    )

    assert path.split(":") == [
        str(tmp_path / "env" / "lib"), "/existing/lib", "/another/lib",
    ]

def test_codex_response_proxy_uses_project_config_without_websockets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import tomlkit

    from loopai.agents.Obtainer.datamixer import codex

    captured: dict = {}

    def fake_runner(env, prompt, timeout, on_stdout_payload=None):
        captured["env"] = env
        return 0, json.dumps({
            "type": "completed",
            "result": {"finalResponse": "{}"},
        }) + "\n", ""

    codex_home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex, "_run_loopai_codex_runner", fake_runner)

    codex.run_via_sdk(
        "ping",
        {
            "base_url": "http://127.0.0.1:8855/responseProxy/v1",
            "api_key": "loopai-local-proxy",
            "model": "medium",
        },
        cwd=str(tmp_path),
        timeout=1,
    )

    env = captured["env"]
    assert env["CODEX_USE_PROJECT_CONFIG"] == "1"
    assert env["CODEX_HOME"] == str(codex_home)
    parsed = tomlkit.parse((codex_home / "config.toml").read_text(encoding="utf-8"))
    assert parsed["model_provider"] == "loopai_model_pool_proxy"
    provider = parsed["model_providers"]["loopai_model_pool_proxy"]
    assert provider["base_url"] == "http://127.0.0.1:8855/responseProxy/v1"
    assert provider["wire_api"] == "responses"
    assert provider["supports_websockets"] is False
    assert parsed["projects"][str(tmp_path.resolve())]["trust_level"] == "trusted"

def test_dataset_acquisition_spawn_uses_explicit_python_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    captured: dict = {}

    class FakePopen:
        pid = 12345

        def __init__(self, cmd, cwd, env, stdout, stderr, start_new_session):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["cwd"] = cwd

    prompt_path = tmp_path / "worker_prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    python_executable = str(tmp_path / "env" / "bin" / "python")
    Path(python_executable).parent.mkdir(parents=True)
    Path(python_executable).write_text("", encoding="utf-8")
    Path(python_executable).chmod(0o755)
    monkeypatch.setattr(dataset_acquisition_agent.subprocess, "Popen", FakePopen)

    dataset_acquisition_agent._spawn_background(
        run_dir=tmp_path / "run",
        warehouse=tmp_path / "warehouse",
        prompt_path=prompt_path,
        timeout=1,
        model="",
        python_executable=python_executable,
    )

    assert captured["cmd"][0] == python_executable
    assert captured["env"]["LOOPAI_PYTHON_EXECUTABLE"] == python_executable
    assert captured["env"]["PATH"].split(":")[0] == str(Path(python_executable).parent)

def test_dataset_acquisition_worker_preserves_custom_hf_hub_endpoint(
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setenv("HF_HUB_ENDPOINT", "https://hf.internal.example")

    env = dataset_acquisition_agent._worker_env()

    assert env["HF_ENDPOINT"] == "https://hf.internal.example"
    assert env["HF_HUB_ENDPOINT"] == "https://hf.internal.example"

def test_dataset_acquisition_worker_records_thread_started_event(
    tmp_path: Path,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    run_dir = tmp_path / "acquisition_run"
    run_dir.mkdir()
    (run_dir / "thread.json").write_text(json.dumps({"warehouse": "/tmp/warehouse"}), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({"state": "running"}), encoding="utf-8")

    dataset_acquisition_agent._record_thread_started(
        run_dir,
        {"type": "event", "event": {"type": "thread.started", "thread_id": "thread-acq-new"}},
    )

    state = json.loads((run_dir / "thread.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert state["thread_id"] == "thread-acq-new"
    assert status["thread_id"] == "thread-acq-new"
    assert status["state"] == "running"

def test_dataset_acquisition_agent_resume_refuses_active_run(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "acquisition_run"
    run_dir.mkdir()
    (run_dir / "thread.json").write_text(
        json.dumps({"warehouse": str(warehouse), "thread_id": "thread-acq-123"}),
        encoding="utf-8",
    )
    (run_dir / "status.json").write_text(
        json.dumps({"state": "running", "pid": 12345}),
        encoding="utf-8",
    )
    monkeypatch.setattr("loopai.skills.ObtainerCLI.dataset_acquisition_agent._pid_alive", lambda pid: True)
    _set_starter_model_pool(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_BASE_URL", "http://bad-env.example/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-codex")
    monkeypatch.setenv("CODEX_API_KEY", "bad-env-key")

    exit_code = run(
        [
            "dm",
            "--root",
            str(warehouse),
            "dataset-acquisition-agent",
            "resume",
            "--run",
            str(run_dir),
            "--message",
            "continue",
            "--foreground",
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 2
    assert payload["error_code"] == "DATASET_ACQUISITION_AGENT_RUN_ACTIVE"

def test_dataset_acquisition_worker_marks_status_interrupted_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    run_dir = tmp_path / "acquisition_run"

    def raise_keyboard_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(dataset_acquisition_agent.codex, "run_via_sdk", raise_keyboard_interrupt, raising=False)

    try:
        dataset_acquisition_agent._run_worker(
            run_dir=run_dir,
            prompt="test prompt",
            prov={"base_url": "http://127.0.0.1:15721/v1", "api_key": "dummy", "model": "deepseek-chat"},
            provider_meta={"source": "test", "model": "deepseek-chat"},
            timeout=1,
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected KeyboardInterrupt")

    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "interrupted"
    assert status["error"] == "KeyboardInterrupt"
    assert status["prompt_path"].endswith("worker_prompt.md")

def test_dataset_acquisition_worker_fails_without_a_successful_final_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    run_dir = tmp_path / "acquisition_run"
    monkeypatch.setattr(
        dataset_acquisition_agent.codex,
        "run_via_sdk",
        lambda *args, **kwargs: {"summary": "worker exited"},
        raising=False,
    )

    with pytest.raises(ObtainerCliError) as exc:
        dataset_acquisition_agent._run_worker(
            run_dir=run_dir,
            prompt="test prompt",
            prov={"base_url": "http://127.0.0.1:8855/responseProxy/v1", "api_key": "dummy", "model": "deepseek-chat"},
            provider_meta={"source": "test", "model": "deepseek-chat"},
            timeout=1,
        )

    assert exc.value.error_code == "DATASET_ACQUISITION_AGENT_FAILED"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["worker_ok"] is False

def test_dataset_acquisition_worker_rejects_success_without_webagent_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    run_dir = tmp_path / "acquisition_run"

    def fake_run(*args, **kwargs):
        (run_dir / "final_report.json").write_text(
            json.dumps({"ok": True, "datasets_ingested": 1}),
            encoding="utf-8",
        )
        return {"summary": "claimed success"}

    monkeypatch.setattr(dataset_acquisition_agent.codex, "run_via_sdk", fake_run, raising=False)

    with pytest.raises(ObtainerCliError) as exc:
        dataset_acquisition_agent._run_worker(
            run_dir=run_dir,
            prompt="test prompt",
            prov={"base_url": "http://proxy/v1", "api_key": "dummy", "model": "deepseek-chat"},
            provider_meta={
                "resolved_model": "deepseek-chat",
                "webagent_model": "codex",
                "model_source": "codex_default",
            },
            timeout=1,
        )

    assert exc.value.error_code == "DATASET_ACQUISITION_AGENT_ACCEPTANCE_FAILED"
    acceptance = json.loads((run_dir / "acceptance_report.json").read_text(encoding="utf-8"))
    assert {item["code"] for item in acceptance["issues"]} >= {
        "searchagent_manifest_missing",
        "webagent_campaign_id_missing",
        "webagent_l1_evidence_missing",
    }

def test_dataset_acquisition_worker_preserves_successful_final_report_on_runner_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loopai.skills.ObtainerCLI import dataset_acquisition_agent

    run_dir = tmp_path / "acquisition_run"

    def raise_after_report(*args, **kwargs):
        _write_successful_acquisition_artifacts(run_dir)
        (run_dir / "final_report.json").write_text(
            json.dumps({"ok": True, "datasets_ingested": ["finance_sft_100k"]}),
            encoding="utf-8",
        )
        raise TimeoutError("runner timed out after final report")

    monkeypatch.setattr(dataset_acquisition_agent.codex, "run_via_sdk", raise_after_report, raising=False)

    result = dataset_acquisition_agent._run_worker(
        run_dir=run_dir,
        prompt="test prompt",
        prov={"base_url": "http://127.0.0.1:15721/v1", "api_key": "dummy", "model": "deepseek-chat"},
        provider_meta={"source": "test", "model": "deepseek-chat"},
        timeout=1,
    )

    assert result["status"] == "completed"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["worker_ok"] is True
    assert status["runner_warning"] == "Codex runner timed out after final report"

def test_dataset_acquisition_status_reconciles_successful_final_report(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "acquisition_run"
    run_dir.mkdir()
    (run_dir / "thread.json").write_text(
        json.dumps({"warehouse": str(tmp_path / "warehouse"), "thread_id": "thread-1"}),
        encoding="utf-8",
    )
    (run_dir / "status.json").write_text(
        json.dumps({"state": "failed", "error": "Command '['corepack', 'yarn', 'dev', '<very long prompt>']' timed out after 3600 seconds"}),
        encoding="utf-8",
    )
    (run_dir / "final_report.json").write_text(
        json.dumps({"ok": True, "datasets_ingested": ["finance_sft_100k"]}),
        encoding="utf-8",
    )
    _write_successful_acquisition_artifacts(run_dir)

    exit_code = run(
        [
            "dm",
            "--root",
            str(tmp_path / "warehouse"),
            "dataset-acquisition-agent",
            "status",
            "--run",
            str(run_dir),
        ]
    )
    payload = _last_json(capsys)

    assert exit_code == 0
    assert payload["status"]["state"] == "completed"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["worker_ok"] is True
    assert status["runner_warning"] == "Codex runner timed out after 3600 seconds"


def test_update_lake_service_config_patches_embedding_and_mineru(tmp_path) -> None:
    from loopai.skills.ObtainerCLI.config import update_lake_service_config

    link = tmp_path / ".loopai" / "lake.yaml"
    link.parent.mkdir(parents=True)
    link.write_text(
        "\n".join(
            [
                "# LoopAI ObtainerCLI lake pointer",
                "root: /tmp/lake",
                "warehouse: /tmp/lake/warehouse",
                "catalog: datamixer",
                "backend: datamixer",
                "namespace: loopai",
                "auto_embed: false",
                "embedding_provider: openai-compatible",
                "embedding_base_url: http://127.0.0.1:8000/v1",
                "embedding_api_key:",
                "embedding_model: BAAI/bge-small-zh-v1.5",
                "embedding_backend: local-jsonl",
                "embedding_text_field: text",
                "auto_embed_async: true",
                "auto_embed_batch_size: 512",
                "",
                "# Persistent Obtainer acquisition context (no credentials)",
                "obtainer_webagent: domain_data_acquisition",
                "obtainer_webagent_workers: 4",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = update_lake_service_config(
        link,
        embedding={"base_url": "http://127.0.0.1:9000/v1", "model": "bge-m3"},
        mineru={"url": "http://10.0.0.5:7986", "gpu": "1"},
    )
    assert result["status"] == "updated"
    values = read_lake_config(link)
    assert values["embedding_base_url"] == "http://127.0.0.1:9000/v1"
    assert values["embedding_model"] == "bge-m3"
    assert values["mineru_url"] == "http://10.0.0.5:7986"
    assert values["mineru_gpu"] == "1"
    assert values["mineru_transport"] == "http"
    # Unrelated lines and the obtainer context are preserved.
    assert values["root"] == "/tmp/lake"
    assert values["obtainer_webagent"] == "domain_data_acquisition"
    assert values["obtainer_webagent_workers"] == "4"
    text = link.read_text(encoding="utf-8")
    assert "# LoopAI ObtainerCLI lake pointer" in text

    # Missing pointer is a no-op.
    missing = update_lake_service_config(tmp_path / "missing.yaml", embedding={"model": "x"})
    assert missing["status"] == "pointer_missing"


def test_update_lake_service_config_preserves_existing_mineru_keys(tmp_path) -> None:
    from loopai.skills.ObtainerCLI.config import update_lake_service_config

    link = tmp_path / ".loopai" / "lake.yaml"
    link.parent.mkdir(parents=True)
    link.write_text(
        "\n".join(
            [
                "root: /tmp/lake",
                "warehouse: /tmp/lake/warehouse",
                "catalog: datamixer",
                "backend: datamixer",
                "namespace: loopai",
                "auto_embed: false",
                "embedding_provider: openai-compatible",
                "embedding_base_url: http://127.0.0.1:8000/v1",
                "embedding_api_key:",
                "embedding_model: BAAI/bge-small-zh-v1.5",
                "embedding_backend: local-jsonl",
                "embedding_text_field: text",
                "auto_embed_async: true",
                "auto_embed_batch_size: 512",
                "mineru_url: http://127.0.0.1:7986",
                "mineru_python: /opt/mineru/bin/python",
                "mineru_model: /models/mineru-html",
                "mineru_gpu: 0",
                "mineru_transport: http",
                "mineru_backend: vllm",
                "obtainer_webagent: domain_data_acquisition",
                "",
            ]
        ),
        encoding="utf-8",
    )
    update_lake_service_config(link, mineru={"python": "/opt/new-mineru/bin/python"})
    values = read_lake_config(link)
    assert values["mineru_python"] == "/opt/new-mineru/bin/python"
    assert values["mineru_model"] == "/models/mineru-html"
    assert values["mineru_url"] == "http://127.0.0.1:7986"
    # No duplicate mineru section comment should be introduced.
    assert link.read_text(encoding="utf-8").count("MinerU-HTML") == 0
