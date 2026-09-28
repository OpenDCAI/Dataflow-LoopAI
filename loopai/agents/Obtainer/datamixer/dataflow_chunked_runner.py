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
import fcntl
from functools import wraps
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
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
    seen_ids: set[str] = set()
    try:
        for record in iter_jsonl(input_path):
            sid = str(record["sample_id"])
            if sid in seen_ids:
                raise ChunkedRunnerError(f"input contains duplicate sample_id: {sid}")
            seen_ids.add(sid)
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
    lock_fd: int | None = None,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["DATAFLOW_INPUT"] = str(chunk_file)
    env["DATAFLOW_CACHE_DIR"] = str(cache_dir)
    env["DATAFLOW_PREFIX"] = prefix
    env["DATAFLOW_OUTPUT"] = str(cache_dir / "pipeline_output.jsonl")
    env["DATAFLOW_RUN_ID"] = "chunk-" + hashlib.sha256(str(cache_dir).encode()).hexdigest()[:20]
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
            env.setdefault("DF_MODEL_NAME", llm_cfg["model_name"])
    except Exception:
        pass
    stdout_path = cache_dir / "pipeline.stdout.log"
    stderr_path = cache_dir / "pipeline.stderr.log"
    with stdout_path.open("a", encoding="utf-8") as out_fh, \
         stderr_path.open("a", encoding="utf-8") as err_fh:
        proc = subprocess.run(
            [python, str(pipeline)],
            env=env,
            cwd=str(chunk_file.parent),
            stdout=out_fh,
            stderr=err_fh,
            text=True,
            pass_fds=(lock_fd,) if lock_fd is not None else (),
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

    input_ids = {str(source["sample_id"]) for source in iter_jsonl(input_path)}
    unknown_ids = sorted(set(output_by_sid) - input_ids)
    if unknown_ids:
        raise ChunkedRunnerError(
            f"processed chunk {processed_path.name} has sample_ids outside the input: "
            + ", ".join(unknown_ids[:5])
            + "; generated rows need an explicit append/lineage contract"
        )

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
            if key not in source and value is not None:
                merged[key] = value
                added_fields.add(key)
        out_fh.write(json.dumps(merged, ensure_ascii=False) + "\n")
    return dropped


def _exclusive_run(function):
    @wraps(function)
    def locked(*args, **kwargs):
        root = Path(kwargs["cache_root"]).resolve()
        root.parent.mkdir(parents=True, exist_ok=True)
        # Keep a stable inode outside the removable cache. Children inherit the
        # lock, preventing duplicate resumes even if the coordinator is killed.
        with (root.parent / (root.name + ".lock")).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ChunkedRunnerError("another runner or pipeline still owns this cache") from None
            return function(*args, **kwargs, _lock_fd=lock.fileno())
    return locked


@_exclusive_run
def run_chunked(
    *,
    input_path: Path,
    pipeline: Path,
    output_path: Path,
    cache_root: Path,
    chunk_size: int = 10000,
    python: str | None = None,
    keep_cache: bool = False,
    resume: bool = False,
    workers: int = 1,
    resource_manifest: Path | None = None,
    max_chunk_attempts: int = 1,
    _lock_fd: int | None = None,
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
    if workers <= 0:
        raise ChunkedRunnerError("workers must be positive")
    if max_chunk_attempts <= 0:
        raise ChunkedRunnerError("max_chunk_attempts must be positive")

    def digest(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def save(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    chunk_dir = cache_root / "chunks"
    contract = {"input": str(input_path), "input_sha256": digest(input_path),
                "pipeline": str(pipeline), "pipeline_sha256": digest(pipeline),
                "chunk_size": chunk_size, "python": python,
                "resource_manifest_sha256": digest(resource_manifest) if resource_manifest else None}
    manifest_path = cache_root / "run_manifest.json"
    if resume and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("contract") != contract:
            raise ChunkedRunnerError("resume input/pipeline/resource contract differs from cached run")
        chunks = []
        for item in manifest["chunks"]:
            path = Path(item["path"])
            if not path.is_file() or digest(path) != item["sha256"]:
                raise ChunkedRunnerError(f"resume chunk changed or missing: {path}")
            chunks.append((path, item["rows"]))
    else:
        if cache_root.exists() and any(cache_root.iterdir()):
            raise ChunkedRunnerError("cache already exists; use --resume with its original input and pipeline")
        chunks = slice_input(input_path, chunk_dir, chunk_size)
        save(manifest_path, {"contract": contract, "chunks": [
            {"path": str(path), "rows": count, "sha256": digest(path)} for path, count in chunks]})

    report: dict[str, Any] = {
        "ok": True,
        "state": "running",
        "input": str(input_path),
        "pipeline": str(pipeline),
        "output": str(output_path),
        "chunk_size": chunk_size,
        "workers": workers,
        "resume": resume,
        "max_chunk_attempts": max_chunk_attempts,
        "total_input_rows": sum(count for _, count in chunks),
        "total_output_rows": 0,
        "total_dropped_rows": 0,
        "chunks": [],
        "added_fields": [],
        "errors": [],
    }
    added_fields: set[str] = set()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def process_chunk(index: int) -> dict:
        chunk_file, input_rows = chunks[index]
        cache_dir = cache_root / f"chunk_{index:05d}"
        receipt_path = cache_dir / "completed.json"
        if resume and receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text())
            if (receipt.get("input_sha256") != digest(chunk_file)
                    or receipt.get("pipeline_sha256") != contract["pipeline_sha256"]
                    or receipt.get("index") != index or receipt.get("input_rows") != input_rows):
                raise ChunkedRunnerError(f"completed chunk contract changed: {cache_dir}")
            for file_key, hash_key in (("processed_file", "processed_sha256"), ("merged_file", "merged_sha256")):
                path = Path(receipt[file_key])
                if not path.is_file() or digest(path) != receipt[hash_key]:
                    raise ChunkedRunnerError(f"completed chunk artifact changed: {path}")
            return {**receipt, "reused_completed_chunk": True}
        for attempt in range(max_chunk_attempts):
            try:
                processed_path = run_pipeline_chunk(pipeline, chunk_file, cache_dir, python=python, lock_fd=_lock_fd)
                counts_path = cache_dir / "final_counts.json"
                if counts_path.exists():
                    counts = json.loads(counts_path.read_text())
                    faults = {key: value for key, value in counts.items()
                              if (key == "operational_fault_rows" or key.endswith("_operational_fault_rows"))
                              and value}
                    if faults:
                        raise ChunkedRunnerError(
                            f"pipeline reported operational faults {faults}; request caches preserved for retry")
                break
            except ChunkedRunnerError:
                if attempt + 1 == max_chunk_attempts:
                    raise
                time.sleep(min(30, 5 * (attempt + 1)))
        merged_path = cache_dir / "merged.jsonl"
        chunk_fields: set[str] = set()
        with merged_path.open("w", encoding="utf-8") as merged:
            dropped = merge_chunk(chunk_file, processed_path, merged, added_fields=chunk_fields)
        receipt = {"index": index, "input_rows": input_rows, "output_rows": input_rows - dropped,
                   "input_sha256": digest(chunk_file), "pipeline_sha256": contract["pipeline_sha256"],
                   "attempts_this_run": attempt + 1,
                   "dropped_rows": dropped, "cache_dir": str(cache_dir),
                   "processed_file": str(processed_path), "processed_sha256": digest(processed_path),
                   "merged_file": str(merged_path), "merged_sha256": digest(merged_path),
                   "added_fields": sorted(chunk_fields), "reused_completed_chunk": False}
        save(receipt_path, receipt)
        return receipt

    completed: dict[int, dict] = {}
    def progress() -> None:
        report["chunks"] = [completed[i] for i in sorted(completed)]
        report["completed_chunks"] = len(completed)
        report["total_output_rows"] = sum(c["output_rows"] for c in completed.values())
        report["total_dropped_rows"] = sum(c["dropped_rows"] for c in completed.values())
        report["duration_seconds"] = round(time.time() - started, 2)
        save(cache_root / "progress.json", report)

    try:
        progress()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            next_index = 0
            pending = {}
            while pending or (report["ok"] and next_index < len(chunks)):
                while report["ok"] and next_index < len(chunks) and len(pending) < workers:
                    pending[pool.submit(process_chunk, next_index)] = next_index
                    next_index += 1
                ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in ready:
                    index = pending.pop(future)
                    try:
                        completed[index] = future.result()
                    except Exception as exc:
                        report["ok"] = False
                        report["errors"].append(f"chunk {index}: {type(exc).__name__}: {exc}")
                progress()
        if report["ok"]:
            temporary_output = output_path.with_suffix(output_path.suffix + ".partial")
            with temporary_output.open("w", encoding="utf-8") as out_fh:
                for index in range(len(chunks)):
                    receipt = completed[index]
                    with Path(receipt["merged_file"]).open(encoding="utf-8") as merged:
                        shutil.copyfileobj(merged, out_fh)
                    added_fields.update(receipt["added_fields"])
            temporary_output.replace(output_path)
            report["added_fields"] = sorted(added_fields)
    except Exception as exc:
        report["ok"] = False
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        report["state"] = "completed" if report["ok"] else "failed"
        report["duration_seconds"] = round(time.time() - started, 2)
        progress()
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
    parser.add_argument("--resume", action="store_true", help="reuse verified completed chunks and preserve request caches")
    parser.add_argument("--workers", type=int, default=1, help="maximum concurrent isolated chunk subprocesses")
    parser.add_argument("--resource-manifest", type=Path, help="pin the database/resource manifest hash across resume")
    parser.add_argument("--max-chunk-attempts", type=int, default=1,
                        help="retry failed pipeline/operational-fault chunks while preserving their request caches")
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
            resume=args.resume,
            workers=args.workers,
            resource_manifest=args.resource_manifest,
            max_chunk_attempts=args.max_chunk_attempts,
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
