"""Chunked streaming runner for DataFlow agent pipelines.

The DataFlow agent generates a standard FileStorage pipeline that reads a JSONL
input via the ``DATAFLOW_INPUT`` env var and writes step outputs under
``DATAFLOW_CACHE_DIR`` with the ``DATAFLOW_PREFIX`` file-name prefix. Full-scale
exports (e.g. a 4GB / 130k-row JSONL) cannot be loaded into one pandas
DataFrame, so this outer scaffold drives the pipeline chunk by chunk:

1. streams the fixed-format input JSONL and slices it into chunks of
   ``--chunk-size`` rows (default 10000);
2. for every chunk, launches the current pipeline in a subprocess with
   ``DATAFLOW_INPUT`` / ``DATAFLOW_CACHE_DIR`` / ``DATAFLOW_PREFIX`` pointing at
   that chunk (load chunk -> run pipeline -> next chunk);
3. merges every chunk output back in input order, preserving every original
   field/value byte-for-byte and overlaying only operator-added fields;
4. validates counts, sample_id uniqueness, and field preservation, then writes
   a JSON report.

This is the sanctioned way to run the DataFlow agent's full-scale L4
processing: the agent must never load the whole export into memory.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

CHUNK_FILE_TEMPLATE = "chunk_{index:05d}.jsonl"
PIPELINE_PREFIX = "l4_step"


class ChunkedRunnerError(RuntimeError):
    pass


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ChunkedRunnerError(
                    f"{path}:{line_number} is not valid JSON: {exc}"
                ) from None
            if not isinstance(record, dict):
                raise ChunkedRunnerError(
                    f"{path}:{line_number} must be a JSON object, got {type(record).__name__}"
                )
            if not str(record.get("sample_id") or "").strip():
                raise ChunkedRunnerError(
                    f"{path}:{line_number} is missing a non-empty sample_id"
                )
            yield record


def slice_input(input_path: Path, chunk_dir: Path, chunk_size: int) -> list[tuple[Path, int]]:
    """Stream the input into chunk files; returns [(chunk_path, input_rows)]."""
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, int]] = []
    current: Path | None = None
    handle = None
    written = 0
    try:
        for record in iter_jsonl(input_path):
            if current is None or written >= chunk_size:
                if handle is not None:
                    handle.close()
                current = chunk_dir / CHUNK_FILE_TEMPLATE.format(index=len(chunks))
                handle = current.open("w", encoding="utf-8")
                written = 0
                chunks.append((current, 0))
            assert handle is not None
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            chunks[-1] = (chunks[-1][0], chunks[-1][1] + 1)
        if handle is not None:
            handle.close()
    except Exception:
        if handle is not None:
            handle.close()
        raise
    if not chunks:
        raise ChunkedRunnerError(f"input is empty: {input_path}")
    return chunks


def _last_step_output(cache_dir: Path, prefix: str, cache_type: str = "jsonl") -> Path:
    pattern = f"{prefix}_step*.{cache_type}"
    matches = list(cache_dir.glob(pattern))
    if not matches:
        raise ChunkedRunnerError(
            f"pipeline wrote no output matching {pattern!r} in {cache_dir}"
        )
    step_of = lambda p: int(m.group(1)) if (m := re.search(r"_step(\d+)\.", p.name)) else 0
    return max(matches, key=step_of)


def run_pipeline_chunk(
    pipeline: Path,
    chunk_file: Path,
    cache_dir: Path,
    *,
    python: str,
    prefix: str = PIPELINE_PREFIX,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["DATAFLOW_INPUT"] = str(chunk_file)
    env["DATAFLOW_CACHE_DIR"] = str(cache_dir)
    env["DATAFLOW_PREFIX"] = prefix
    # DataFlow LLM operators route through the Starter model-pool default model
    # (response proxy), so the key/endpoint/model are injected here instead of
    # requiring an ad-hoc export before every full run.
    try:
        from .dataflow_agent import operator_llm_config_from_starter

        llm_cfg = operator_llm_config_from_starter()
        if llm_cfg.get("api_key"):
            env.setdefault("DF_API_KEY", llm_cfg["api_key"])
        if llm_cfg.get("api_url"):
            env.setdefault("DF_API_URL", llm_cfg["api_url"])
        if llm_cfg.get("model_name"):
            env.setdefault("DF_MODEL", llm_cfg["model_name"])
    except Exception:
        pass
    stdout_path = cache_dir / "pipeline.stdout.log"
    stderr_path = cache_dir / "pipeline.stderr.log"
    with stdout_path.open("w", encoding="utf-8") as out_fh, \
         stderr_path.open("w", encoding="utf-8") as err_fh:
        proc = subprocess.run(
            [python, str(pipeline)],
            env=env,
            cwd=str(chunk_file.parent),
            stdout=out_fh,
            stderr=err_fh,
            text=True,
        )
    if proc.returncode != 0:
        tail = _tail(stderr_path, 40) or _tail(stdout_path, 40)
        raise ChunkedRunnerError(
            f"pipeline failed on chunk {chunk_file.name} (exit {proc.returncode}):\n{tail}"
        )
    return _last_step_output(cache_dir, prefix)


def _tail(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def merge_chunk(
    input_path: Path,
    processed_path: Path,
    out_fh: Any,
    *,
    added_fields: set[str],
) -> int:
    """Merge one chunk's output back in input order.

    Returns the number of input rows dropped by the pipeline. Fails if the
    pipeline rewrote an original field or produced unknown/duplicate sample_ids.
    """
    output_by_sid: dict[str, dict[str, Any]] = {}
    for record in iter_jsonl(processed_path):
        sid = str(record["sample_id"])
        if sid in output_by_sid:
            raise ChunkedRunnerError(
                f"processed chunk {processed_path.name} has duplicate sample_id {sid}"
            )
        output_by_sid[sid] = record

    dropped = 0
    for source in iter_jsonl(input_path):
        sid = str(source["sample_id"])
        processed = output_by_sid.get(sid)
        if processed is None:
            dropped += 1
            continue
        changed = sorted(
            key for key, value in source.items()
            if key in processed and processed[key] != value
        )
        if changed:
            raise ChunkedRunnerError(
                f"pipeline rewrote original field(s) on sample_id {sid}: "
                + ", ".join(changed[:5])
            )
        merged = dict(source)
        for key, value in processed.items():
            if key not in source and value is not None and not (isinstance(value, float) and math.isnan(value)):
                merged[key] = value
                added_fields.add(key)
        out_fh.write(json.dumps(merged, ensure_ascii=False) + "\n")
    return dropped


def run_chunked(
    *,
    input_path: Path,
    pipeline: Path,
    output_path: Path,
    cache_root: Path,
    chunk_size: int = 10000,
    python: str | None = None,
    keep_cache: bool = False,
) -> dict[str, Any]:
    started = time.time()
    input_path = input_path.resolve()
    pipeline = pipeline.resolve()
    output_path = output_path.resolve()
    cache_root = cache_root.resolve()
    python = python or sys.executable
    if not input_path.is_file():
        raise ChunkedRunnerError(f"input not found: {input_path}")
    if not pipeline.is_file():
        raise ChunkedRunnerError(f"pipeline not found: {pipeline}")
    if chunk_size <= 0:
        raise ChunkedRunnerError("chunk_size must be positive")

    chunk_dir = cache_root / "chunks"
    if cache_root.exists():
        shutil.rmtree(cache_root)
    chunks = slice_input(input_path, chunk_dir, chunk_size)

    report: dict[str, Any] = {
        "ok": True,
        "input": str(input_path),
        "pipeline": str(pipeline),
        "output": str(output_path),
        "chunk_size": chunk_size,
        "total_input_rows": sum(count for _, count in chunks),
        "total_output_rows": 0,
        "total_dropped_rows": 0,
        "chunks": [],
        "added_fields": [],
        "errors": [],
    }
    added_fields: set[str] = set()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("w", encoding="utf-8") as out_fh:
            for index, (chunk_file, input_rows) in enumerate(chunks):
                cache_dir = cache_root / f"chunk_{index:05d}"
                try:
                    processed_path = run_pipeline_chunk(
                        pipeline, chunk_file, cache_dir, python=python
                    )
                    dropped = merge_chunk(
                        chunk_file, processed_path, out_fh, added_fields=added_fields
                    )
                except ChunkedRunnerError as exc:
                    report["ok"] = False
                    report["errors"].append(str(exc))
                    break
                output_rows = input_rows - dropped
                report["chunks"].append({
                    "index": index,
                    "input_rows": input_rows,
                    "output_rows": output_rows,
                    "dropped_rows": dropped,
                    "cache_dir": str(cache_dir),
                    "processed_file": str(processed_path),
                })
                report["total_output_rows"] += output_rows
                report["total_dropped_rows"] += dropped
        if report["ok"]:
            report["added_fields"] = sorted(added_fields)
    except ChunkedRunnerError as exc:
        report["ok"] = False
        report["errors"].append(str(exc))
    finally:
        report["duration_seconds"] = round(time.time() - started, 2)
        if report["ok"] and not keep_cache:
            shutil.rmtree(cache_root, ignore_errors=True)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataflow-chunked-runner",
        description=(
            "Chunked streaming runner for DataFlow agent pipelines: slice the "
            "fixed-format input into chunks, run the pipeline per chunk, merge "
            "in order, and validate preservation."
        ),
    )
    parser.add_argument("--input", required=True, help="fixed-format input JSONL (full export)")
    parser.add_argument("--pipeline", required=True, help="generated DataFlow pipeline .py")
    parser.add_argument("--output", required=True, help="merged full_processed JSONL to write")
    parser.add_argument("--chunk-size", type=int, default=10000,
                        help="rows per chunk (default 10000)")
    parser.add_argument("--cache-root", default=None,
                        help="scratch dir for chunk inputs/cache (default: <output dir>/cache_full)")
    parser.add_argument("--python", default=None,
                        help="python executable for the pipeline subprocess (default: current)")
    parser.add_argument("--report", default=None,
                        help="JSON report path (default: <output>.report.json)")
    parser.add_argument("--keep-cache", action="store_true",
                        help="keep chunk/cache scratch dir after success")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_path = Path(args.output)
    cache_root = Path(args.cache_root) if args.cache_root else output_path.parent / "cache_full"
    report_path = Path(args.report) if args.report else output_path.with_suffix(".report.json")
    try:
        report = run_chunked(
            input_path=Path(args.input),
            pipeline=Path(args.pipeline),
            output_path=output_path,
            cache_root=cache_root,
            chunk_size=args.chunk_size,
            python=args.python,
            keep_cache=args.keep_cache,
        )
    except ChunkedRunnerError as exc:
        report = {
            "ok": False,
            "input": str(Path(args.input).resolve()),
            "pipeline": str(Path(args.pipeline).resolve()),
            "output": str(output_path.resolve()),
            "chunk_size": args.chunk_size,
            "errors": [str(exc)],
            "chunks": [],
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report.get("ok"):
        print(
            f"chunked run ok: {report.get('total_input_rows')} in -> "
            f"{report.get('total_output_rows')} out ({report.get('total_dropped_rows')} dropped), "
            f"{len(report.get('chunks', []))} chunks, report={report_path}"
        )
        return 0
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
