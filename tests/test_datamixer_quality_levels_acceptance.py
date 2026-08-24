from __future__ import annotations

import inspect
import json
import logging
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.modules.setdefault("colorlog", types.SimpleNamespace(ColoredFormatter=logging.Formatter))

from api.app.utils.obtainer.monitor import build_lake_monitor  # noqa: E402
from loopai.agents.Obtainer.datamixer.store import DataStore  # noqa: E402
from loopai.skills.ObtainerCLI.cli import run  # noqa: E402
from loopai.skills.ObtainerCLI.ingest import ingest_path  # noqa: E402
from loopai.skills.ObtainerCLI.lake_init import init_lake  # noqa: E402
from loopai.skills.ObtainerCLI.monitor_state import read_monitor_state  # noqa: E402


QUALITY_LEVELS = ("L1", "L2", "L3", "L4")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _json_payload(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _invoke_dm(root: Path, args: list[str], capsys) -> tuple[int, dict | None, str]:
    """Run the public ObtainerCLI surface, including argparse rejections."""
    try:
        exit_code = run(["dm", "--root", str(root), *args])
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    captured = capsys.readouterr()
    return exit_code, _json_payload(captured.out), captured.err


def _dm(root: Path, args: list[str], capsys) -> dict:
    exit_code, payload, stderr = _invoke_dm(root, args, capsys)
    assert exit_code == 0, payload or stderr
    assert payload is not None
    assert payload["command"] == "dm"
    return payload["result"]


def _warehouse_counts(root: Path, capsys) -> tuple[int, int]:
    status = _dm(root, ["status"], capsys)
    return int(status["datasets"]), int(status["samples"])


def _command_window(text: str, marker: str, *, width: int = 1800) -> str:
    start = text.index(marker)
    return text[start : start + width]


def test_ingest_requires_quality_level_before_any_lake_mutation(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "records.jsonl"
    _write_jsonl(records, [{"content": {"text": "raw page"}}])
    _dm(warehouse, ["init"], capsys)

    exit_code, payload, stderr = _invoke_dm(
        warehouse,
        ["ingest", "missing_level", "--file", str(records)],
        capsys,
    )

    assert exit_code != 0, payload
    error_text = json.dumps(payload, ensure_ascii=False) if payload else stderr
    assert "quality" in error_text.lower() and "level" in error_text.lower()
    assert _warehouse_counts(warehouse, capsys) == (0, 0)


def test_ingest_rejects_unknown_quality_level_before_any_lake_mutation(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "records.jsonl"
    _write_jsonl(records, [{"content": {"text": "unknown level"}}])
    _dm(warehouse, ["init"], capsys)

    exit_code, payload, stderr = _invoke_dm(
        warehouse,
        [
            "ingest",
            "invalid_level",
            "--file",
            str(records),
            "--quality-level",
            "L5",
        ],
        capsys,
    )

    assert exit_code != 0, payload
    error_text = json.dumps(payload, ensure_ascii=False) if payload else stderr
    assert "L5" in error_text
    assert _warehouse_counts(warehouse, capsys) == (0, 0)


@pytest.mark.parametrize(
    ("defaults", "expected_error"),
    [
        ({}, "quality_level"),
        ({"quality_level": "L5"}, "L5"),
    ],
    ids=["missing", "invalid"],
)
def test_store_enforces_quality_level_as_a_lake_invariant(
    tmp_path: Path,
    defaults: dict,
    expected_error: str,
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    try:
        dataset_id = store.catalog.add_dataset("store_invariant")
        with pytest.raises(ValueError, match=expected_error):
            store.ingest_records(
                dataset_id,
                [{"content": {"text": "must not be written"}}],
                defaults=defaults,
            )
        assert store.catalog.count(dataset_id=dataset_id) == 0
    finally:
        store.close()


def test_store_rejects_a_late_conflicting_level_before_any_blob_or_sample_write(
    tmp_path: Path,
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    try:
        dataset_id = store.catalog.add_dataset("atomic_quality_batch")
        with pytest.raises(ValueError, match="L2"):
            store.ingest_records(
                dataset_id,
                [
                    {"content": {"text": "valid first row"}},
                    {
                        "content": {"text": "conflicting late row"},
                        "quality_level": "L2",
                    },
                ],
                defaults={"quality_level": "L3"},
            )
        assert store.catalog.count(dataset_id=dataset_id) == 0
        assert store.cas.stats().num_blobs == 0
    finally:
        store.close()


def test_cli_rejects_row_and_batch_level_conflicts_without_creating_a_dataset(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "records.jsonl"
    _write_jsonl(
        records,
        [
            {
                "content": {"text": "row says L2"},
                "quality_level": "L2",
            }
        ],
    )
    _dm(warehouse, ["init"], capsys)

    exit_code, payload, stderr = _invoke_dm(
        warehouse,
        [
            "ingest",
            "conflicting_level",
            "--file",
            str(records),
            "--quality-level",
            "L3",
        ],
        capsys,
    )

    assert exit_code != 0, payload
    error_text = json.dumps(payload, ensure_ascii=False) if payload else stderr
    assert "L2" in error_text and "L3" in error_text
    assert _warehouse_counts(warehouse, capsys) == (0, 0)


def test_all_quality_levels_are_persisted_queryable_and_written_to_lineage(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    _dm(warehouse, ["init"], capsys)

    for quality_level in QUALITY_LEVELS:
        records = tmp_path / f"{quality_level}.jsonl"
        dataset = f"dataset_{quality_level.lower()}"
        _write_jsonl(
            records,
            [
                {
                    "content": {"text": f"record classified as {quality_level}"},
                    "source_uri": f"fixture://{quality_level.lower()}",
                }
            ],
        )
        result = _dm(
            warehouse,
            [
                "ingest",
                dataset,
                "--file",
                str(records),
                "--quality-level",
                quality_level,
            ],
            capsys,
        )
        assert result["written"] == 1
        lineage = json.loads(Path(result["lineage"]).read_text(encoding="utf-8"))
        assert lineage["defaults"]["quality_level"] == quality_level

    query = _dm(
        warehouse,
        [
            "query",
            "--columns",
            "sample_id,dataset_id,quality_level",
            "--limit",
            "20",
        ],
        capsys,
    )
    assert query["total"] == 4
    assert {row["quality_level"] for row in query["rows"]} == set(QUALITY_LEVELS)

    l3 = _dm(
        warehouse,
        [
            "query",
            "--filter",
            "quality_level = 'L3'",
            "--columns",
            "dataset_id,quality_level",
        ],
        capsys,
    )
    assert l3["total"] == 1
    assert l3["rows"][0]["quality_level"] == "L3"


def test_schema_and_columns_publish_the_quality_level_contract(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    _dm(warehouse, ["init"], capsys)

    schema = _dm(warehouse, ["schema"], capsys)
    fields = {field["name"]: field for field in schema["fields"]}
    assert "quality_level" in fields
    quality_field = fields["quality_level"]
    allowed_values = quality_field.get("allowed_values") or schema.get("quality_levels")
    assert allowed_values == list(QUALITY_LEVELS)
    assert quality_field["indexed"] is True

    columns = _dm(warehouse, ["columns"], capsys)
    core_columns = {column["name"] for column in columns["core"]}
    assert "quality_level" in core_columns


def test_agent_ingest_requires_quality_level_before_any_lake_mutation(
    tmp_path: Path,
    capsys,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "agent-input.jsonl"
    _write_jsonl(records, [{"text": "extracted webpage body"}])
    _dm(warehouse, ["init"], capsys)

    exit_code, payload, stderr = _invoke_dm(
        warehouse,
        [
            "agent-ingest",
            str(records),
            "--engine",
            "builtin",
            "--dataset",
            "agent_missing_level",
        ],
        capsys,
    )

    assert exit_code != 0, payload
    error_text = json.dumps(payload, ensure_ascii=False) if payload else stderr
    assert "quality" in error_text.lower() and "level" in error_text.lower()
    assert _warehouse_counts(warehouse, capsys) == (0, 0)


@pytest.mark.parametrize("quality_level", ["L2", "L4"])
def test_builtin_agent_ingest_persists_level_without_an_l4_confirmation_flag(
    tmp_path: Path,
    capsys,
    quality_level: str,
) -> None:
    warehouse = tmp_path / "warehouse"
    records = tmp_path / "agent-input.jsonl"
    _write_jsonl(records, [{"text": f"agent record for {quality_level}"}])
    _dm(warehouse, ["init"], capsys)

    result = _dm(
        warehouse,
        [
            "agent-ingest",
            str(records),
            "--engine",
            "builtin",
            "--dataset",
            f"agent_{quality_level.lower()}",
            "--quality-level",
            quality_level,
        ],
        capsys,
    )
    assert result["ingested"] == 1

    query = _dm(
        warehouse,
        [
            "query",
            "--dataset",
            f"agent_{quality_level.lower()}",
            "--columns",
            "sample_id,quality_level",
        ],
        capsys,
    )
    assert query["total"] == 1
    assert query["rows"][0]["quality_level"] == quality_level


def test_python_ingest_wrapper_requires_an_explicit_quality_level() -> None:
    parameters = inspect.signature(ingest_path).parameters
    assert "quality_level" in parameters
    parameter = parameters["quality_level"]
    assert parameter.default is inspect.Parameter.empty


def test_python_ingest_wrapper_rejects_invalid_level_before_lake_creation(
    tmp_path: Path,
) -> None:
    lake = tmp_path / "not-created-lake"
    input_path = tmp_path / "records.jsonl"
    _write_jsonl(input_path, [{"text": "invalid wrapper level"}])

    with pytest.raises(ValueError, match="L5"):
        ingest_path(
            lake=lake,
            input_path=input_path,
            dataset="invalid_wrapper_level",
            quality_level="L5",
        )

    assert not lake.exists()


def test_monitor_exposes_quality_level_composition_and_latest_records(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    link_path = tmp_path / "repo" / ".loopai" / "lake.yaml"
    init_lake(root=lake_root, link_path=link_path, if_not_exists=True)

    for quality_level in QUALITY_LEVELS:
        input_path = tmp_path / "input" / f"{quality_level}.jsonl"
        _write_jsonl(input_path, [{"text": f"monitor row {quality_level}"}])
        result = ingest_path(
            lake=link_path,
            input_path=input_path,
            dataset=f"monitor_{quality_level.lower()}",
            quality_level=quality_level,
            idempotency_key=f"monitor-{quality_level.lower()}",
        )
        assert result["rows_written"] == 1

    monitor = build_lake_monitor(lake=link_path)
    cached_monitor = read_monitor_state(lake_root / "warehouse", lake=link_path)

    assert monitor["charts"]["composition"]["quality_level"] == {
        "L1": 1,
        "L2": 1,
        "L3": 1,
        "L4": 1,
    }
    assert cached_monitor["charts"]["composition"]["quality_level"] == {
        "L1": 1,
        "L2": 1,
        "L3": 1,
        "L4": 1,
    }
    assert {
        row["quality_level"] for row in monitor["latest"]["records"]
    } == set(QUALITY_LEVELS)
    assert {
        row["quality_level"] for row in cached_monitor["latest"]["records"]
    } == set(QUALITY_LEVELS)


def test_obtainer_skill_and_agent_ingest_demos_require_quality_level() -> None:
    skill = (PROJECT_ROOT / "skills" / "obtainer" / "SKILL.md").read_text(encoding="utf-8")
    usage = (PROJECT_ROOT / "docs" / "OBTAINERCLI_USAGE.md").read_text(encoding="utf-8")
    worker = (
        PROJECT_ROOT / "loopai" / "skills" / "ObtainerCLI" / "dataset_acquisition_agent.py"
    ).read_text(encoding="utf-8")
    codex_prompt = (
        PROJECT_ROOT / "loopai" / "skills" / "ObtainerCLI" / "datamixer_ingest_prompt.md"
    ).read_text(encoding="utf-8")

    assert "--quality-level L3" in _command_window(
        skill,
        "ingest code_repair_mix",
    )
    assert "--quality-level L3" in _command_window(
        skill,
        "agent-ingest ./downloads/raw_file",
    )
    assert "--quality-level L3" in _command_window(
        usage,
        "ingest code_repair_mix",
    )
    assert "--quality-level L3" in _command_window(
        usage,
        "agent-ingest ./outputs/downloads/raw_file",
    )
    assert "--quality-level" in _command_window(
        worker,
        "ingest <dataset_name>",
    )
    assert "quality_level" in _command_window(
        worker,
        "Ingest every accepted dataset",
    )
    assert "--quality-level" in _command_window(
        codex_prompt,
        '$DM ingest "$DATASET"',
    )


def test_monitor_dashboard_uses_quality_level_as_the_data_grade() -> None:
    dashboard = (
        PROJECT_ROOT / "ui" / "src" / "views" / "manage" / "obtainerLake" / "index.vue"
    ).read_text(encoding="utf-8")

    assert "compositionMode: 'quality_level'" in dashboard
    assert "{ key: 'quality_level', label: this.local('Quality Level') }" in dashboard
    assert "{ key: 'quality_level', label: this.local('Quality Level') }" in _command_window(
        dashboard,
        "records: [",
    )
