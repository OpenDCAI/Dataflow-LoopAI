"""End-to-end test for the DataFlow chunked streaming runner.

Builds a fixed-format input (sample_id + original fields), slices it into
10k-row chunks via the runner, runs a real DataFlow FileStorage pipeline per
chunk, and asserts order/field-preservation/merge correctness.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PIPELINE_SRC = '''
import os
import pandas as pd
from dataflow.operators.core_text import PandasOperator
from dataflow.utils.storage import FileStorage

INPUT_FILE = os.environ.get("DATAFLOW_INPUT", "trial_input.jsonl")
CACHE_DIR = os.environ.get("DATAFLOW_CACHE_DIR", "./cache_l4")
PREFIX = os.environ.get("DATAFLOW_PREFIX", "l4_step")


class ChunkTestPipeline:
    def __init__(self):
        self.storage = FileStorage(
            first_entry_file_name=INPUT_FILE,
            cache_path=CACHE_DIR,
            file_name_prefix=PREFIX,
            cache_type="jsonl",
        )

        def _add_signals(df: pd.DataFrame) -> pd.DataFrame:
            df = df.copy()
            responses = df["response"].tolist()
            df["quality_score"] = [(len(str(r)) % 5) + 1 for r in responses]
            df["chunk_tag"] = ["l4"] * len(df)
            return df

        self.signals = PandasOperator(process_fn=[_add_signals])

    def forward(self):
        self.signals.run(storage=self.storage.step())


if __name__ == "__main__":
    ChunkTestPipeline().forward()
    print(f"Finished pipeline on input={INPUT_FILE} cache={CACHE_DIR} prefix={PREFIX}")
'''


def _write_fixed_input(path: Path, rows: int) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for index in range(rows):
            sid = f"row_{index:05d}"
            record = {
                "sample_id": sid,
                "content": f"code sample {sid}",
                "instruction": f"implement function f_{index}",
                "response": "def f_" + str(index) + "(x):\n    return x + " + str(index),
                "n_tokens": 100 + index,
                "source_uri": f"hf://chunk_test/{index}",
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_runner(
    tmp_path: Path,
    *,
    input_path: Path,
    pipeline_path: Path,
    output_path: Path,
    chunk_size: int = 10000,
    keep_cache: bool = False,
):
    cmd = [
        sys.executable,
        "-m",
        "loopai.agents.Obtainer.datamixer.dataflow_chunked_runner",
        "--input", str(input_path),
        "--pipeline", str(pipeline_path),
        "--output", str(output_path),
        "--chunk-size", str(chunk_size),
        "--report", str(tmp_path / "report.json"),
    ]
    if keep_cache:
        cmd.append("--keep-cache")
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=600)
    return proc


def test_chunked_runner_merges_10000_row_chunks_in_order(tmp_path: Path) -> None:
    total_rows = 25000
    input_path = tmp_path / "full_input.jsonl"
    pipeline_path = tmp_path / "test_pipeline.py"
    output_path = tmp_path / "full_processed.jsonl"

    _write_fixed_input(input_path, total_rows)
    pipeline_path.write_text(PIPELINE_SRC, encoding="utf-8")

    proc = _run_runner(
        tmp_path,
        input_path=input_path,
        pipeline_path=pipeline_path,
        output_path=output_path,
        chunk_size=10000,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert report["total_input_rows"] == total_rows
    assert report["total_output_rows"] == total_rows
    assert report["total_dropped_rows"] == 0
    assert [c["input_rows"] for c in report["chunks"]] == [10000, 10000, 5000]
    assert report["chunks"][0]["processed_file"].endswith("l4_step_step1.jsonl")
    assert "quality_score" in report["added_fields"]
    assert "chunk_tag" in report["added_fields"]

    # Order must be exactly the input order and every original field preserved.
    expected = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines()]
    merged = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [row["sample_id"] for row in merged] == [row["sample_id"] for row in expected]
    for src, out in zip(expected, merged):
        for key, value in src.items():
            assert out[key] == value, f"field {key!r} changed for {src['sample_id']}"
        assert out["quality_score"] == (len(str(src["response"])) % 5) + 1
        assert out["chunk_tag"] == "l4"

    # Default cleanup removes the scratch cache.
    assert not (tmp_path / "cache_full").exists()


def test_chunked_runner_rejects_pipeline_that_rewrites_original_fields(tmp_path: Path) -> None:
    total_rows = 12000  # two chunks
    input_path = tmp_path / "full_input.jsonl"
    pipeline_path = tmp_path / "rewriter.py"
    output_path = tmp_path / "full_processed.jsonl"
    _write_fixed_input(input_path, total_rows)
    rewrite_src = PIPELINE_SRC.replace(
        'df["chunk_tag"] = ["l4"] * len(df)',
        'df["chunk_tag"] = ["l4"] * len(df)\n            df["n_tokens"] = 0',
    )
    pipeline_path.write_text(rewrite_src, encoding="utf-8")

    proc = _run_runner(
        tmp_path,
        input_path=input_path,
        pipeline_path=pipeline_path,
        output_path=output_path,
        chunk_size=10000,
        keep_cache=True,
    )
    assert proc.returncode == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert any("rewrote original field" in err for err in report["errors"])
    assert "n_tokens" in report["errors"][0]
    # Failed chunk's cache is kept for debugging.
    assert (tmp_path / "cache_full" / "chunk_00000").exists()
