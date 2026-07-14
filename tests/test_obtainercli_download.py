from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_download_manifest_exports_huggingface_rows(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import download

    manifest = tmp_path / "searchagent_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "download_list": [
                    {
                        "source": "huggingface",
                        "dataset_id": "openai/gsm8k",
                        "download": {
                            "method": "huggingface",
                            "dataset_id": "openai/gsm8k",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_load_hf_dataset(dataset_id, *, split, streaming):
        assert dataset_id == "openai/gsm8k"
        assert split == "train"
        assert streaming is True
        return iter(
            [
                {
                    "question": "1+1?",
                    "answer": "2",
                },
                {
                    "question": "2+2?",
                    "answer": "4",
                },
            ]
        )

    monkeypatch.setattr(download, "_load_hf_dataset", fake_load_hf_dataset)

    result = download.download_manifest(
        manifest=manifest,
        output_root=tmp_path / "downloads",
        limit=1,
        max_rows=0,
    )

    records_path = Path(result["records_jsonl"][0])
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    assert result["ok"] is True
    assert result["completed"] == 1
    assert [row["source_uri"] for row in rows] == [
        "hf://datasets/openai/gsm8k/train#1",
        "hf://datasets/openai/gsm8k/train#2",
    ]
    assert rows[0]["output"] == "2"
    assert rows[1]["output"] == "4"


def test_download_huggingface_sets_hub_endpoint_from_existing_mirror(monkeypatch):
    from loopai.skills.ObtainerCLI import download

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setenv("HF_HUB_ENDPOINT", "https://hf.internal.example")
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)

    download._ensure_hf_mirror_env()

    assert download.os.environ["HF_ENDPOINT"] == "https://hf.internal.example"
    assert download.os.environ["HF_HUB_ENDPOINT"] == "https://hf.internal.example"
    assert download.os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_download_manifest_caps_zero_max_rows_per_dataset(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import download

    manifest = tmp_path / "searchagent_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "download_list": [
                    {
                        "source": "huggingface",
                        "dataset_id": "large/data",
                        "download": {
                            "method": "huggingface",
                            "dataset_id": "large/data",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_load_hf_dataset(dataset_id, *, split, streaming):
        assert dataset_id == "large/data"
        for index in range(download.MAX_ROWS_PER_DATASET + 1):
            yield {"text": f"row {index}"}

    monkeypatch.setattr(download, "_load_hf_dataset", fake_load_hf_dataset)

    result = download.download_manifest(
        manifest=manifest,
        output_root=tmp_path / "downloads",
        max_rows=0,
    )

    records_path = Path(result["records_jsonl"][0])
    assert sum(1 for _ in records_path.open(encoding="utf-8")) == download.MAX_ROWS_PER_DATASET
    assert result["max_rows_requested"] == 0
    assert result["max_rows_effective"] == download.MAX_ROWS_PER_DATASET
    assert result["results"][0]["row_cap_applied"] is True


def test_download_manifest_caps_oversized_max_rows_per_dataset(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import download

    manifest = tmp_path / "searchagent_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "download_list": [
                    {
                        "source": "huggingface",
                        "dataset_id": "large/data",
                        "download": {
                            "method": "huggingface",
                            "dataset_id": "large/data",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_load_hf_dataset(dataset_id, *, split, streaming):
        assert dataset_id == "large/data"
        for index in range(download.MAX_ROWS_PER_DATASET + 1):
            yield {"text": f"row {index}"}

    monkeypatch.setattr(download, "_load_hf_dataset", fake_load_hf_dataset)

    result = download.download_manifest(
        manifest=manifest,
        output_root=tmp_path / "downloads",
        max_rows=download.MAX_ROWS_PER_DATASET + 500,
    )

    records_path = Path(result["records_jsonl"][0])
    assert sum(1 for _ in records_path.open(encoding="utf-8")) == download.MAX_ROWS_PER_DATASET
    assert result["max_rows_requested"] == download.MAX_ROWS_PER_DATASET + 500
    assert result["max_rows_effective"] == download.MAX_ROWS_PER_DATASET
    assert result["results"][0]["row_cap_applied"] is True


def test_download_manifest_truncates_huggingface_rows_at_byte_cap(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import download

    manifest = tmp_path / "searchagent_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "download_list": [
                    {
                        "source": "huggingface",
                        "dataset_id": "huge/rows",
                        "download": {
                            "method": "huggingface",
                            "dataset_id": "huge/rows",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_load_hf_dataset(dataset_id, *, split, streaming):
        assert dataset_id == "huge/rows"
        for index in range(20):
            yield {"text": "x" * 80, "index": index}

    monkeypatch.setattr(download, "_load_hf_dataset", fake_load_hf_dataset)

    result = download.download_manifest(
        manifest=manifest,
        output_root=tmp_path / "downloads",
        max_rows=20,
        max_bytes_per_dataset=700,
    )

    item = result["results"][0]
    records_path = Path(result["records_jsonl"][0])
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]

    assert result["ok"] is True
    assert 0 < len(rows) < 20
    assert records_path.stat().st_size <= 700
    assert item["rows_written"] == len(rows)
    assert item["bytes_written"] == records_path.stat().st_size
    assert item["byte_cap_applied"] is True
    assert item["truncated"] is True
    assert item["truncated_reason"] == "max_bytes_per_dataset"


def test_cli_download_manifest_emits_json(tmp_path, monkeypatch, capsys):
    from loopai.skills.ObtainerCLI import cli

    def fake_download_manifest(**kwargs):
        assert kwargs["max_rows"] == 100_000
        assert kwargs["max_bytes_per_dataset"] == 2 * 1024 * 1024 * 1024
        return {
            "ok": True,
            "status": "completed",
            "schema_version": "obtainercli.download.v1",
            "records_jsonl": [str(tmp_path / "records.jsonl")],
        }

    monkeypatch.setattr(cli, "download_manifest", fake_download_manifest)

    exit_code = cli.run(
        [
            "download",
            "manifest",
            "--manifest",
            str(tmp_path / "searchagent_manifest.json"),
            "--output-root",
            str(tmp_path / "downloads"),
            "--limit",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["schema_version"] == "obtainercli.download.v1"
