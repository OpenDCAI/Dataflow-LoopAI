from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from loopai.skills.ObtainerCLI.cli import run
from loopai.skills.ObtainerCLI.config import write_lake_config


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

    def fake_run_via_sdk(prompt, prov, cwd, timeout=600, network=True):
        captured["prompt"] = prompt
        captured["base_url"] = prov["base_url"]
        sample_line = next(line for line in prompt.splitlines() if line.startswith("- Sample JSONL file:"))
        sample_path = Path(sample_line.split(":", 1)[1].strip())
        rows = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()]
        processed = work_dir / "processed.jsonl"
        processed.parent.mkdir(parents=True, exist_ok=True)
        with processed.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        {
                            "sample_id": row["sample_id"],
                            "raw_content": row["raw_content"],
                            "math_answer_quality": 0.95,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        pipeline = work_dir / "pipeline.py"
        pipeline.write_text("# generated by fake codex\n", encoding="utf-8")
        return {
            "ok": True,
            "mode": "trial_run",
            "operator_decision": {
                "ops": ["FormatStrPromptedGenerator", "GeneralFilter"],
                "field_flow": "raw_content -> math_answer_quality",
                "reason": "score answer quality then keep rows",
            },
            "pipeline_path": str(pipeline),
            "processed_jsonl": str(processed),
            "trial_rows_in": len(rows),
            "trial_rows_out": len(rows),
            "stdout_tail": "",
            "errors": [],
            "summary": "trial ok",
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

    assert result["trial_rows_exported"] == 2
    assert result["applied"] is True
    assert result["merge"]["updated"] == 2
    assert "generating-dataflow-pipeline" in captured["prompt"]
    assert "sub-agents" in captured["prompt"]
    assert Path(result["trial_jsonl"]).exists()

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


def test_sft_export_agent_start_dry_run_writes_isolated_worker_prompt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "sft_export_run"
    report = tmp_path / "analysis_report.md"
    report.write_text("Need Alpaca SFT math data.", encoding="utf-8")
    monkeypatch.setenv("CODEX_BASE_URL", "http://127.0.0.1:15721/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-chat")
    monkeypatch.setenv("CODEX_API_KEY", "dummy")

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
    state = json.loads((run_dir / "thread.json").read_text(encoding="utf-8"))
    assert state["target_records"] == 100000
    assert state["format"] == "alpaca"

    exit_code = run(["dm", "--root", str(warehouse), "sft-export-agent", "status", "--run", str(run_dir)])
    status_payload = _last_json(capsys)
    assert exit_code == 0
    assert status_payload["status"]["state"] == "dry_run"


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
                "provider": {"source": "env", "model": "deepseek-chat"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_BASE_URL", "http://127.0.0.1:15721/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-chat")
    monkeypatch.setenv("CODEX_API_KEY", "dummy")
    captured: dict[str, object] = {}

    def fake_run_via_sdk(prompt, prov, cwd, timeout=600, network=True, thread_id=None):
        captured["prompt"] = prompt
        captured["thread_id"] = thread_id
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
    assert captured["thread_id"] == "thread-123"
    assert "remove text fallback and retry" in str(captured["prompt"])
    assert payload["thread_id"] == "thread-123"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["worker_ok"] is False


def test_sft_export_agent_start_defaults_to_background(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from loopai.skills.ObtainerCLI import sft_export_agent

    warehouse = tmp_path / "warehouse"
    run_dir = tmp_path / "sft_export_run"
    report = tmp_path / "analysis_report.md"
    report.write_text("Need Alpaca SFT math data.", encoding="utf-8")
    monkeypatch.setenv("CODEX_BASE_URL", "http://127.0.0.1:15721/v1")
    monkeypatch.setenv("CODEX_MODEL", "deepseek-chat")
    monkeypatch.setenv("CODEX_API_KEY", "dummy")
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
