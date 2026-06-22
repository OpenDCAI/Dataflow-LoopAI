from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Mapping

import pyarrow as pa
import pyarrow.parquet as pq

from .config import read_lake_config_for_lake
from .models import sha256_text, utc_now


TABLES = [
    "datasets",
    "assets",
    "records",
    "record_tags",
    "record_lineage",
    "embeddings",
    "quality_findings",
    "ingest_runs",
    "exports",
]

JSON_COLUMNS = {
    "datasets": {"default_tags"},
    "assets": {"provenance"},
    "records": {"payload"},
    "quality_findings": {"details"},
    "ingest_runs": {"config_snapshot"},
    "exports": {"query"},
}

LIST_STRING_COLUMNS = {
    "records": {"tag_names", "parent_record_ids"},
}

LIST_JSON_COLUMNS = {
    "records": {"messages"},
}

LIST_FLOAT_COLUMNS = {
    "embeddings": {"vector"},
}

SCHEMAS = {
    "datasets": pa.schema(
        [
            ("dataset_id", pa.string()),
            ("name", pa.string()),
            ("stage", pa.string()),
            ("domain", pa.string()),
            ("task_type", pa.string()),
            ("description", pa.string()),
            ("owner", pa.string()),
            ("source_kind", pa.string()),
            ("created_at", pa.string()),
            ("updated_at", pa.string()),
            ("schema_version", pa.string()),
            ("default_tags", pa.string()),
        ]
    ),
    "assets": pa.schema(
        [
            ("asset_id", pa.string()),
            ("dataset_id", pa.string()),
            ("source_uri", pa.string()),
            ("source_kind", pa.string()),
            ("local_uri", pa.string()),
            ("content_sha256", pa.string()),
            ("size_bytes", pa.int64()),
            ("mime_type", pa.string()),
            ("license", pa.string()),
            ("provenance", pa.string()),
            ("ingest_run_id", pa.string()),
            ("created_at", pa.string()),
        ]
    ),
    "records": pa.schema(
        [
            ("record_id", pa.string()),
            ("dataset_id", pa.string()),
            ("asset_id", pa.string()),
            ("stage", pa.string()),
            ("domain", pa.string()),
            ("processing_level", pa.string()),
            ("source_kind", pa.string()),
            ("source_domain", pa.string()),
            ("source_uri", pa.string()),
            ("task_type", pa.string()),
            ("payload", pa.string()),
            ("text", pa.string()),
            ("instruction", pa.string()),
            ("input", pa.string()),
            ("output", pa.string()),
            ("messages", pa.list_(pa.string())),
            ("tag_names", pa.list_(pa.string())),
            ("quality_score", pa.float64()),
            ("dedup_key", pa.string()),
            ("parent_record_ids", pa.list_(pa.string())),
            ("pipeline_run_id", pa.string()),
            ("split", pa.string()),
            ("created_at", pa.string()),
            ("schema_version", pa.string()),
        ]
    ),
    "record_tags": pa.schema(
        [
            ("record_id", pa.string()),
            ("dataset_id", pa.string()),
            ("tag_name", pa.string()),
            ("tag_value", pa.string()),
            ("tag_source", pa.string()),
            ("confidence", pa.float64()),
            ("created_at", pa.string()),
        ]
    ),
    "record_lineage": pa.schema(
        [
            ("record_id", pa.string()),
            ("parent_record_id", pa.string()),
            ("lineage_type", pa.string()),
            ("pipeline_run_id", pa.string()),
            ("created_at", pa.string()),
        ]
    ),
    "embeddings": pa.schema(
        [
            ("record_id", pa.string()),
            ("dataset_id", pa.string()),
            ("source_snapshot_id", pa.string()),
            ("text_field", pa.string()),
            ("chunk_id", pa.string()),
            ("chunk_text_sha256", pa.string()),
            ("embedding_model", pa.string()),
            ("embedding_dim", pa.int64()),
            ("vector", pa.list_(pa.float64())),
            ("vector_uri", pa.string()),
            ("index_backend", pa.string()),
            ("index_status", pa.string()),
            ("created_at", pa.string()),
        ]
    ),
    "quality_findings": pa.schema(
        [
            ("finding_id", pa.string()),
            ("record_id", pa.string()),
            ("dataset_id", pa.string()),
            ("processing_level", pa.string()),
            ("source_kind", pa.string()),
            ("finding_type", pa.string()),
            ("severity", pa.string()),
            ("score", pa.float64()),
            ("metric_name", pa.string()),
            ("metric_value", pa.float64()),
            ("detector", pa.string()),
            ("detector_version", pa.string()),
            ("details", pa.string()),
            ("pipeline_run_id", pa.string()),
            ("created_at", pa.string()),
        ]
    ),
    "ingest_runs": pa.schema(
        [
            ("ingest_run_id", pa.string()),
            ("idempotency_key", pa.string()),
            ("command", pa.string()),
            ("input_uri", pa.string()),
            ("dataset_id", pa.string()),
            ("status", pa.string()),
            ("started_at", pa.string()),
            ("finished_at", pa.string()),
            ("rows_seen", pa.int64()),
            ("rows_written", pa.int64()),
            ("rows_quarantined", pa.int64()),
            ("error_summary", pa.string()),
            ("config_snapshot", pa.string()),
        ]
    ),
    "exports": pa.schema(
        [
            ("export_id", pa.string()),
            ("query", pa.string()),
            ("strategy", pa.string()),
            ("seed", pa.int64()),
            ("requested_size", pa.int64()),
            ("actual_size", pa.int64()),
            ("output_uri", pa.string()),
            ("format", pa.string()),
            ("record_ids_sha256", pa.string()),
            ("created_at", pa.string()),
        ]
    ),
}


def _validate_table(table: str) -> None:
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table}")


def _catalog_name(lake_root: str | Path) -> str:
    config = read_lake_config_for_lake(lake_root)
    return config.get("catalog", "local-parquet")


def get_catalog(lake_root: str | Path) -> "BaseCatalog":
    catalog = _catalog_name(lake_root)
    if catalog == "local-jsonl":
        return JsonlCatalog(Path(lake_root))
    if catalog == "local-parquet":
        return ParquetCatalog(Path(lake_root))
    raise ValueError(f"Unsupported catalog: {catalog}")


class BaseCatalog:
    def __init__(self, lake_root: Path) -> None:
        self.lake_root = lake_root

    def table_path(self, table: str) -> Path:
        raise NotImplementedError

    def ensure_tables(self) -> None:
        raise NotImplementedError

    def append_rows(self, table: str, rows: Iterable[Mapping]) -> None:
        raise NotImplementedError

    def read_table(self, table: str) -> List[dict]:
        raise NotImplementedError


class JsonlCatalog(BaseCatalog):
    def table_path(self, table: str) -> Path:
        _validate_table(table)
        return self.lake_root / "warehouse" / "loopai.db" / table / "data.jsonl"

    def ensure_tables(self) -> None:
        for table in TABLES:
            path = self.table_path(table)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        _ensure_aux_dirs(self.lake_root)

    def append_rows(self, table: str, rows: Iterable[Mapping]) -> None:
        path = self.table_path(table)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")

    def read_table(self, table: str) -> List[dict]:
        path = self.table_path(table)
        if not path.exists():
            return []
        result: List[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    result.append(json.loads(line))
        return result


class ParquetCatalog(BaseCatalog):
    def table_path(self, table: str) -> Path:
        _validate_table(table)
        return self.lake_root / "warehouse" / "loopai.db" / table

    def data_dir(self, table: str) -> Path:
        return self.table_path(table) / "data"

    def ensure_tables(self) -> None:
        for table in TABLES:
            table_root = self.table_path(table)
            (table_root / "data").mkdir(parents=True, exist_ok=True)
            (table_root / "_schema.json").write_text(
                json.dumps(_schema_manifest(table), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        _ensure_aux_dirs(self.lake_root)

    def append_rows(self, table: str, rows: Iterable[Mapping]) -> None:
        normalized = [_normalize_for_parquet(table, dict(row)) for row in rows]
        if not normalized:
            return
        self.ensure_tables()
        schema = SCHEMAS[table]
        columns = {field.name: [row.get(field.name) for row in normalized] for field in schema}
        arrow_table = pa.table(columns, schema=schema)
        part_name = "part-" + sha256_text(f"{table}:{utc_now()}:{len(normalized)}")[:24] + ".parquet"
        pq.write_table(arrow_table, self.data_dir(table) / part_name, compression="zstd")

    def read_table(self, table: str) -> List[dict]:
        data_dir = self.data_dir(table)
        if not data_dir.exists():
            return []
        rows: List[dict] = []
        for path in sorted(data_dir.glob("*.parquet")):
            parquet_table = pq.read_table(path, schema=SCHEMAS[table])
            rows.extend(_restore_from_parquet(table, row) for row in parquet_table.to_pylist())
        return rows


def _ensure_aux_dirs(lake_root: Path) -> None:
    for dirname in ["staging", "quarantine", "reports", "locks"]:
        (lake_root / dirname).mkdir(parents=True, exist_ok=True)


def _schema_manifest(table: str) -> dict:
    schema = SCHEMAS[table]
    return {
        "table": table,
        "format": "parquet",
        "schema": [{"name": field.name, "type": str(field.type)} for field in schema],
    }


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: object, default: object) -> object:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        return value
    return json.loads(value)


def _normalize_for_parquet(table: str, row: dict) -> dict:
    schema = SCHEMAS[table]
    normalized = {}
    for field in schema:
        value = row.get(field.name)
        if field.name in JSON_COLUMNS.get(table, set()):
            value = _json_dump(value if value is not None else {})
        elif field.name in LIST_JSON_COLUMNS.get(table, set()):
            value = [_json_dump(item) for item in (value or [])]
        elif field.name in LIST_STRING_COLUMNS.get(table, set()):
            value = [str(item) for item in (value or [])]
        elif field.name in LIST_FLOAT_COLUMNS.get(table, set()):
            value = [float(item) for item in (value or [])]
        elif pa.types.is_string(field.type):
            value = "" if value is None else str(value)
        elif pa.types.is_integer(field.type):
            value = 0 if value is None else int(value)
        elif pa.types.is_floating(field.type):
            value = None if value is None else float(value)
        normalized[field.name] = value
    return normalized


def _restore_from_parquet(table: str, row: dict) -> dict:
    restored = {}
    for key, value in row.items():
        if key in JSON_COLUMNS.get(table, set()):
            restored[key] = _json_load(value, {})
        elif key in LIST_JSON_COLUMNS.get(table, set()):
            restored[key] = [_json_load(item, {}) for item in (value or [])]
        else:
            restored[key] = value
    return restored
