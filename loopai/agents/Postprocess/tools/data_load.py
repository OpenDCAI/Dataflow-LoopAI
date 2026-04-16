"""
Load small samples from tabular / JSON-like data files inside a dataset directory.

Supports multiple text encodings, rejects likely-binary files, caps file size and
per-cell / total JSON size so tool output cannot blow the LLM context.
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Dict, Iterator, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from loopai.logger import get_logger

logger = get_logger()

DATA_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".parquet",
    ".arrow",
    ".txt",
}

# Encodings to try for line-based / pandas text files (order: BOM variants first, then common locales).
_TEXT_ENCODINGS: tuple[str, ...] = (
    "utf-8-sig",
    "utf-8",
    "gb18030",
    "gbk",
    "big5",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "latin-1",
    "cp1252",
    "iso-8859-1",
)

_MAX_FILE_BYTES = int(os.getenv("POSTPROCESS_DATA_LOAD_MAX_FILE_BYTES", str(80 * 1024 * 1024)))
_MAX_CELL_CHARS = int(os.getenv("POSTPROCESS_DATA_LOAD_MAX_CELL_CHARS", "2500"))
_MAX_JSON_CHARS = int(os.getenv("POSTPROCESS_DATA_LOAD_MAX_JSON_CHARS", str(100_000)))
_MAX_DICT_KEYS = int(os.getenv("POSTPROCESS_DATA_LOAD_MAX_DICT_KEYS", "48"))
_MAX_LIST_ITEMS = int(os.getenv("POSTPROCESS_DATA_LOAD_MAX_LIST_ITEMS", "48"))
_BINARY_SNIFF_BYTES = int(os.getenv("POSTPROCESS_DATA_LOAD_BINARY_SNIFF_BYTES", str(16_384)))


class DataLoadInput(BaseModel):
    file_path: str = Field(..., description="Path to the data file, relative to dataset root or absolute under dataset root")
    max_rows: int = Field(8, ge=1, le=50, description="Maximum sample rows to return")


def _dict_row(row: Any) -> Optional[Dict[str, Any]]:
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return None


def _is_likely_binary_file(path: str) -> bool:
    """Heuristic: NUL in head, or extreme control-byte ratio (ASCII-ish)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    # UTF-16 BOM — text
    if chunk.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    # If almost all bytes are ASCII graphic / whitespace, treat as text
    allowed = 0
    for b in chunk:
        if b in (9, 10, 13) or 32 <= b <= 126:
            allowed += 1
    if len(chunk) >= 512 and allowed / len(chunk) < 0.25:
        return True
    return False


def _shrink_value(val: Any, max_str: int, depth: int = 0) -> Any:
    if depth > 14:
        return "<nested_too_deep>"
    if val is None or isinstance(val, (bool, int, float)):
        return val
    if isinstance(val, bytes):
        return f"<bytes len={len(val)}>"
    if isinstance(val, str):
        if len(val) <= max_str:
            return val
        return val[:max_str] + f"...(truncated,len={len(val)})"
    if isinstance(val, dict):
        items = list(val.items())[:_MAX_DICT_KEYS]
        out = {str(k): _shrink_value(v, max_str, depth + 1) for k, v in items}
        if len(val) > _MAX_DICT_KEYS:
            out["__truncated_keys__"] = len(val) - _MAX_DICT_KEYS
        return out
    if isinstance(val, (list, tuple)):
        n = len(val)
        head = val[:_MAX_LIST_ITEMS]
        out = [_shrink_value(x, max_str, depth + 1) for x in head]
        if n > _MAX_LIST_ITEMS:
            out.append(f"...({n - _MAX_LIST_ITEMS} more items)")
        return out
    s = str(val)
    return s[:max_str] + (f"...(truncated,len={len(s)})" if len(s) > max_str else "")


def _sanitize_records(records: List[Dict[str, Any]], max_cell: int) -> List[Dict[str, Any]]:
    return [_shrink_value(r, max_cell, 0) for r in records]  # type: ignore[list-item]


def _json_dumps_bounded(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) <= _MAX_JSON_CHARS:
        return raw
    # Shrink samples further
    samples = payload.get("sample_records") or []
    note = payload.get("note") or ""
    while len(samples) > 1 and len(raw) > _MAX_JSON_CHARS:
        samples = samples[:-1]
        payload = {
            **payload,
            "sample_records": _sanitize_records(samples, max(500, _MAX_CELL_CHARS // 4)),
            "note": (note + " output_truncated_row_count;").strip(),
        }
        raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) > _MAX_JSON_CHARS:
        payload["sample_records"] = []
        payload["note"] = (note + " output_truncated_empty_samples;").strip()
        payload["error"] = "serialized_payload_still_too_large_after_truncation"
        raw = json.dumps(payload, ensure_ascii=False, default=str)[: _MAX_JSON_CHARS + 1]
        if len(raw) > _MAX_JSON_CHARS:
            raw = raw[:_MAX_JSON_CHARS] + "..."
    return raw


def _iter_jsonl_decoded(path: str, max_rows: int) -> Iterator[Dict[str, Any]]:
    raw_lines: List[bytes] = []
    with open(path, "rb") as f:
        while len(raw_lines) < max_rows:
            line = f.readline()
            if not line:
                break
            if line.strip():
                raw_lines.append(line)

    last_err: Optional[Exception] = None
    for enc in _TEXT_ENCODINGS:
        try:
            for raw in raw_lines:
                s = raw.decode(enc).strip()
                if not s:
                    continue
                yield json.loads(s)
            return
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            last_err = e
            continue
    if last_err:
        raise ValueError(f"jsonl decode failed ({last_err})") from last_err


def _iter_csv_pandas(path: str, sep: str, max_rows: int) -> Iterator[Dict[str, Any]]:
    import pandas as pd

    cap = max_rows + 20
    for enc in _TEXT_ENCODINGS:
        try:
            n = 0
            _kw: Dict[str, Any] = dict(
                sep=sep,
                encoding=enc,
                chunksize=50,
                dtype=object,
                engine="python",
            )
            try:
                reader = pd.read_csv(path, on_bad_lines="skip", **_kw)
            except TypeError:
                reader = pd.read_csv(path, error_bad_lines=False, warn_bad_lines=False, **_kw)
            for chunk in reader:
                for _, row in chunk.iterrows():
                    d = _dict_row(row.to_dict())
                    if d is None:
                        continue
                    yield d
                    n += 1
                    if n >= cap:
                        return
            return
        except Exception as e:
            logger.debug(f"[data_load] csv try enc={enc} failed: {e}")
            continue
    raise ValueError("csv/tsv: could not read with supported encodings")


def _iter_json_array_decoded(path: str, max_rows: int) -> Iterator[Dict[str, Any]]:
    import pandas as pd

    with open(path, "rb") as bf:
        raw = bf.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise ValueError("json file larger than POSTPROCESS_DATA_LOAD_MAX_FILE_BYTES")

    for enc in _TEXT_ENCODINGS:
        try:
            head = raw.decode(enc)
            df = pd.read_json(io.StringIO(head))
            for _, row in df.head(max_rows).iterrows():
                d = _dict_row(row.to_dict())
                if d is not None:
                    yield d
            return
        except Exception as e:
            logger.debug(f"[data_load] json array try enc={enc} failed: {e}")
            continue
    raise ValueError("json: could not parse with supported encodings")


def _iter_file_rows(
    file_path: str,
    max_rows: int,
    hf_datasets_cache_dir: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    ext = os.path.splitext(file_path)[1].lower()

    # Structured binary: libraries handle; still cap rows when materializing.
    if ext in (".parquet", ".arrow"):
        try:
            import pandas as pd

            df = pd.read_parquet(file_path)
            for _, row in df.head(max_rows).iterrows():
                d = _dict_row(row.to_dict())
                if d is not None:
                    yield d
            return
        except Exception as e:
            logger.debug(f"[data_load] parquet failed: {e}")

    # Line-oriented JSONL: encoding-safe, row cap without full-file pandas.
    if ext == ".jsonl":
        yield from _iter_jsonl_decoded(file_path, max_rows)
        return

    if ext == ".json":
        yield from _iter_json_array_decoded(file_path, max_rows)
        return

    if ext == ".csv":
        yield from _iter_csv_pandas(file_path, ",", max_rows)
        return

    if ext == ".tsv":
        yield from _iter_csv_pandas(file_path, "\t", max_rows)
        return

    if ext == ".txt":
        for enc in _TEXT_ENCODINGS:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    text = f.read(min(_MAX_FILE_BYTES, 2_000_000))
                line = text.splitlines()[0] if text else ""
                yield {"text": line[: _MAX_CELL_CHARS * 2], "_encoding": enc, "_preview_chars": len(text)}
                return
            except UnicodeDecodeError:
                continue
        raise ValueError("txt: unsupported encoding")

    # Fallback: datasets + pandas (e.g. odd extensions symlinked)
    try:
        from datasets import DatasetDict, load_dataset

        builder_map = {
            ".json": "json",
            ".jsonl": "json",
            ".csv": "csv",
            ".tsv": "csv",
            ".parquet": "parquet",
            ".arrow": "arrow",
            ".txt": "text",
        }
        builder = builder_map.get(ext)
        if builder:
            kwargs: Dict[str, Any] = {"data_files": file_path}
            if ext == ".tsv":
                kwargs["delimiter"] = "\t"
            load_kw: Dict[str, Any] = {"trust_remote_code": True}
            if hf_datasets_cache_dir:
                load_kw["cache_dir"] = hf_datasets_cache_dir
            ds = load_dataset(builder, **kwargs, **load_kw)
            n = 0
            if isinstance(ds, DatasetDict):
                for split_name in ds:
                    for row in ds[split_name]:
                        d = _dict_row(row)
                        if d is not None:
                            yield d
                            n += 1
                            if n >= max_rows:
                                return
                return
            for row in ds:
                d = _dict_row(row)
                if d is not None:
                    yield d
                    n += 1
                    if n >= max_rows:
                        return
            return
    except Exception as e:
        logger.debug(f"[data_load] datasets load failed for {file_path}: {e}")


def create_data_load_tool(dataset_dir: str, hf_datasets_cache_dir: Optional[str] = None):
    abs_dataset = os.path.abspath(dataset_dir)

    @tool(args_schema=DataLoadInput)
    def data_load(file_path: str, max_rows: int = 8) -> str:
        """Load a small sample of rows from a data file (json/jsonl/csv/tsv/parquet/txt).
        Uses multiple text encodings, rejects likely-binary files, truncates large fields.
        Paths must stay inside the dataset directory."""

        raw = file_path
        if not os.path.isabs(raw):
            abs_path = os.path.abspath(os.path.join(abs_dataset, raw))
        else:
            abs_path = os.path.abspath(raw)

        if not abs_path.startswith(abs_dataset):
            return json.dumps({"error": "path outside dataset directory"}, ensure_ascii=False)

        if not os.path.isfile(abs_path):
            return json.dumps({"error": f"not a file: {raw}"}, ensure_ascii=False)

        try:
            size = os.path.getsize(abs_path)
        except OSError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if size > _MAX_FILE_BYTES:
            return json.dumps(
                {
                    "error": "file_too_large",
                    "hint": f"size={size} bytes exceeds POSTPROCESS_DATA_LOAD_MAX_FILE_BYTES={_MAX_FILE_BYTES}",
                    "file_path": raw,
                },
                ensure_ascii=False,
            )

        _, ext = os.path.splitext(abs_path.lower())
        if ext not in DATA_EXTENSIONS:
            return json.dumps({"error": f"unsupported data extension {ext}"}, ensure_ascii=False)

        # Parquet/arrow are binary by design; skip NUL heuristic for them.
        if ext not in (".parquet", ".arrow") and _is_likely_binary_file(abs_path):
            return json.dumps(
                {
                    "error": "likely_binary_file",
                    "hint": "File head looks binary (e.g. NUL bytes). Not loaded as text to avoid context blow-up.",
                    "file_path": raw,
                    "size_bytes": size,
                },
                ensure_ascii=False,
            )

        samples: List[Dict[str, Any]] = []
        columns: List[str] = []
        total_seen = 0
        err: Optional[str] = None
        try:
            for row in _iter_file_rows(abs_path, max_rows, hf_datasets_cache_dir):
                total_seen += 1
                if not columns and row:
                    columns = list(row.keys())
                if len(samples) < max_rows:
                    samples.append(row)
        except Exception as e:
            err = str(e)
            logger.info(f"[data_load] iterate failed {abs_path}: {e}")

        if not samples and err:
            return json.dumps(
                {
                    "error": err,
                    "file_path": raw,
                    "hint": "Try another file, or check encoding / whether file is binary.",
                },
                ensure_ascii=False,
            )

        sanitized = _sanitize_records(samples, _MAX_CELL_CHARS)
        payload: Dict[str, Any] = {
            "file_path": raw,
            "columns": columns,
            "sample_records": sanitized,
            "rows_sampled": len(sanitized),
            "rows_seen_hint": total_seen,
            "max_cell_chars": _MAX_CELL_CHARS,
        }
        if err and samples:
            payload["partial_error"] = err
        return _json_dumps_bounded(payload)

    return data_load
