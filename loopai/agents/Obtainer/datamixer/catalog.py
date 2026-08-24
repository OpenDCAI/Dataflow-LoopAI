"""Catalog & sample-metadata layer (L2), backed by SQLite.

Why SQLite for the MVP:

* It is *compact* (single file, tight row encoding, optional indexes only where
  we declare them) and ships with Python -- zero install.
* Recipe bucket filters are written as SQL predicates in the brief
  (``domain = 'code' AND quality_score > 0.7``); SQLite executes them directly.
* It scales comfortably to the tens-of-millions range that a single-node MVP
  targets, and the same query contract maps cleanly onto DuckDB/Lance later.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from . import schema, utils

def validate_filter(expr: str) -> str:
    """Validate a recipe/query predicate against the controlled grammar and
    return canonical SQL re-emitted from the parsed AST (whitelisted columns +
    operators only, so injection is impossible). See ``datamixer/filterc.py``."""
    from . import filterc
    return filterc.compile_filter(expr)


class Catalog:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        # Every webagent worker owns a separate Catalog connection. WAL keeps
        # reads concurrent while busy_timeout lets SQLite serialize the short
        # ingest transactions instead of failing immediately with "locked".
        self.conn = sqlite3.connect(self.path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._init_db()

    # -- schema ------------------------------------------------------------
    def _init_db(self) -> None:
        cols = ",\n  ".join(f"{f.name} {f.sql_type}" for f in schema.CORE_FIELDS)
        self.conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT
            );
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source TEXT,
                license TEXT,
                owner TEXT,
                description TEXT,
                created_at REAL,
                meta_json TEXT
            );
            CREATE TABLE IF NOT EXISTS domain_classes (
                name TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS samples (
                sample_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                cid TEXT NOT NULL,
                created_at REAL,
                version INTEGER DEFAULT 1,
                tags_json TEXT,
                {cols}
            );
            """
        )
        # additive-schema migration: a warehouse created with an older field set
        # gains any newly-declared core columns (the schema contract is additive).
        existing = {r["name"] for r in self.conn.execute(
            "PRAGMA table_info(samples)")}
        for f in schema.CORE_FIELDS:
            if f.name not in existing:
                self.conn.execute(
                    f"ALTER TABLE samples ADD COLUMN {f.name} {f.sql_type}")
        # indexes on declared-indexed core fields + foreign-keyish columns
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_samples_dataset ON samples(dataset_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_samples_cid ON samples(cid)"
        )
        for f in schema.CORE_FIELDS:
            if f.indexed:
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_samples_{f.name} "
                    f"ON samples({f.name})"
                )
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
            (schema.SCHEMA_VERSION,),
        )
        # These classes are intentionally lake-local rather than a global enum:
        # users can register capability-specific domains while every new lake
        # still has a useful baseline for LLM classification.
        self.register_domain_classes(schema.DEFAULT_DOMAIN_CLASSES, source="builtin")
        self.conn.commit()

    # -- domain taxonomy --------------------------------------------------
    @staticmethod
    def _normalise_domain(value: Any) -> str | None:
        if value is None:
            return None
        name = str(value).strip()
        return name or None

    def register_domain_classes(
        self, names: Iterable[Any], source: str = "user"
    ) -> list[str]:
        """Persist domain classes for this lake and return newly registered names.

        Registration is idempotent and stays in the caller's transaction.  That
        matters for ingestion: a failed batch cannot leave a partly committed
        taxonomy behind.  ``source`` is informational (``builtin``, ``user``,
        ``observed`` or ``classifier``) and never overwrites an earlier source.
        """
        created: list[str] = []
        seen: set[str] = set()
        now = time.time()
        for value in names:
            name = self._normalise_domain(value)
            if name is None or name in seen:
                continue
            seen.add(name)
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO domain_classes(name,source,created_at) "
                "VALUES (?,?,?)",
                (name, str(source or "user"), now),
            )
            if cur.rowcount:
                created.append(name)
        return created

    def sync_domain_classes(self) -> list[dict]:
        """Merge existing sample domains into the persisted lake taxonomy.

        Older warehouses may predate ``domain_classes``.  Calling this at the
        classifier boundary means their already-ingested domain values become
        selectable labels without a migration command or a hard-coded list.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT domain FROM samples "
            "WHERE domain IS NOT NULL AND TRIM(domain) != ''"
        ).fetchall()
        self.register_domain_classes((row["domain"] for row in rows), "observed")
        return self.list_domain_classes()

    def list_domain_classes(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name,source,created_at FROM domain_classes "
            "ORDER BY CASE source WHEN 'builtin' THEN 0 ELSE 1 END, name"
        ).fetchall()
        return [dict(row) for row in rows]

    # -- datasets ----------------------------------------------------------
    def add_dataset(
        self,
        name: str,
        source: str | None = None,
        license: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        meta: dict | None = None,
        dataset_id: str | None = None,
    ) -> str:
        ds_id = dataset_id or "ds-" + utils.fingerprint(
            {"name": name, "ts": time.time()}
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO datasets"
            "(id,name,source,license,owner,description,created_at,meta_json)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                ds_id,
                name,
                source,
                license,
                owner,
                description,
                time.time(),
                utils.canonical_json(meta or {}).decode(),
            ),
        )
        self.conn.commit()
        return ds_id

    def get_dataset(self, dataset_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM datasets WHERE id=?", (dataset_id,)
        ).fetchone()
        return dict(row) if row else None

    def resolve_dataset(self, name_or_id: str) -> str | None:
        if self.get_dataset(name_or_id):
            return name_or_id
        row = self.conn.execute(
            "SELECT id FROM datasets WHERE name=? ORDER BY created_at LIMIT 1",
            (name_or_id,),
        ).fetchone()
        return row["id"] if row else None

    def list_datasets(self) -> list[dict]:
        """List datasets with per-dataset record aggregates.

        ``quality_level`` is stored as a tag (``tags_json``), while domain/stage
        are real indexed columns; both are summarised so the management UI does
        not need a per-dataset query just to render the list.
        """
        rows = self.conn.execute(
            "SELECT d.*, "
            "(SELECT COUNT(*) FROM samples s WHERE s.dataset_id=d.id) AS n_samples, "
            "(SELECT MAX(s.created_at) FROM samples s WHERE s.dataset_id=d.id) AS last_ingested_at "
            "FROM datasets d ORDER BY created_at"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            ds_id = item["id"]
            item["quality_levels"] = {
                str(r["quality_level"]): int(r["n"])
                for r in self.conn.execute(
                    "SELECT quality_level, COUNT(*) AS n FROM samples WHERE dataset_id=? "
                    "AND quality_level IS NOT NULL AND quality_level != '' "
                    "GROUP BY quality_level ORDER BY n DESC",
                    (ds_id,),
                )
            }
            item["domains"] = {
                str(r["domain"]): int(r["n"])
                for r in self.conn.execute(
                    "SELECT domain, COUNT(*) AS n FROM samples WHERE dataset_id=? "
                    "AND domain IS NOT NULL AND domain != '' "
                    "GROUP BY domain ORDER BY n DESC",
                    (ds_id,),
                )
            }
            item["stages"] = {
                str(r["stage"]): int(r["n"])
                for r in self.conn.execute(
                    "SELECT stage, COUNT(*) AS n FROM samples WHERE dataset_id=? "
                    "AND stage IS NOT NULL AND stage != '' "
                    "GROUP BY stage ORDER BY n DESC",
                    (ds_id,),
                )
            }
            result.append(item)
        return result

    def update_dataset(
        self, dataset_id: str, **fields: Any
    ) -> dict | None:
        """Update editable dataset registry fields; returns the updated row."""
        allowed = {"name", "source", "license", "owner", "description"}
        updates = {
            key: value
            for key, value in fields.items()
            if key in allowed and value is not None
        }
        if not updates or not self.get_dataset(dataset_id):
            return self.get_dataset(dataset_id)
        assignments = ", ".join(f"{key}=?" for key in updates)
        self.conn.execute(
            f"UPDATE datasets SET {assignments} WHERE id=?",
            (*updates.values(), dataset_id),
        )
        self.conn.commit()
        return self.get_dataset(dataset_id)

    def delete_dataset_row(self, dataset_id: str) -> bool:
        """Remove the dataset registry row (samples handled by the caller)."""
        cur = self.conn.execute(
            "DELETE FROM datasets WHERE id=?", (dataset_id,)
        )
        self.conn.commit()
        return bool(cur.rowcount)

    # -- samples -----------------------------------------------------------
    def add_sample(
        self, dataset_id: str, cid: str, metadata: dict[str, Any],
        salt: Any = None,
    ) -> tuple[str, bool]:
        """Insert (or replace) a sample. Returns ``(sample_id, created)`` where
        ``created`` is False when an existing row with the same id was replaced
        (i.e. a within-dataset duplicate was merged).

        The id is ``smp-fingerprint(dataset, cid, core_metadata[, salt])``, so by
        default re-ingesting identical (dataset, content, tags) is idempotent.
        Pass a distinct ``salt`` per record (see ``--allow-duplicates``) to keep
        every occurrence as its own row and preserve multiplicity.
        """
        core, extra = schema.split_metadata(metadata)
        fp_input = {"d": dataset_id, "c": cid, "m": core}
        if salt is not None:
            fp_input["s"] = salt
        sample_id = "smp-" + utils.fingerprint(fp_input)
        existed = self.conn.execute(
            "SELECT 1 FROM samples WHERE sample_id=?", (sample_id,)
        ).fetchone() is not None
        col_names = list(core.keys())
        placeholders = ",".join(["?"] * (5 + len(col_names)))
        sql = (
            "INSERT OR REPLACE INTO samples"
            "(sample_id,dataset_id,cid,created_at,tags_json"
            + ("," + ",".join(col_names) if col_names else "")
            + f") VALUES ({placeholders})"
        )
        values = [
            sample_id,
            dataset_id,
            cid,
            time.time(),
            utils.canonical_json(extra).decode(),
        ] + [core[c] for c in col_names]
        self.conn.execute(sql, values)
        if core.get("domain"):
            self.register_domain_classes([core["domain"]], source="observed")
        return sample_id, (not existed)

    def commit(self) -> None:
        self.conn.commit()

    def get_sample(self, sample_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM samples WHERE sample_id=?", (sample_id,)
        ).fetchone()
        return self._row_to_sample(row) if row else None

    @staticmethod
    def _row_to_sample(row: sqlite3.Row) -> dict:
        d = dict(row)
        tags = d.pop("tags_json", None)
        d["tags"] = utils.loads(tags.encode()) if tags else {}
        return d

    def query(
        self,
        where: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
        columns: str = "*",
        order: str | None = None,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if dataset_id:
            clauses.append("dataset_id = ?")
            params.append(dataset_id)
        if where:
            clauses.append("(" + validate_filter(where) + ")")
        sql = f"SELECT {columns} FROM samples"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if order:
            sql += f" ORDER BY {order}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self.conn.execute(sql, params).fetchall()
        if columns == "*":
            return [self._row_to_sample(r) for r in rows]
        return [dict(r) for r in rows]

    def iter_query(self, where: str | None = None, dataset_id: str | None = None,
                   batch_size: int = 512):
        """Stream matching samples in batches of ``batch_size`` with constant
        memory. Uses keyset pagination on ``sample_id`` (a fresh query per batch,
        no long-lived cursor), so callers may write back between batches safely.
        Yields ``list[dict]`` (full sample rows)."""
        base = []
        params0: list[Any] = []
        if dataset_id:
            base.append("dataset_id = ?")
            params0.append(dataset_id)
        if where:
            base.append("(" + validate_filter(where) + ")")
        last = None
        while True:
            clauses = list(base)
            params = list(params0)
            if last is not None:
                clauses.append("sample_id > ?")
                params.append(last)
            sql = "SELECT * FROM samples"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += f" ORDER BY sample_id LIMIT {int(batch_size)}"
            rows = self.conn.execute(sql, params).fetchall()
            if not rows:
                return
            yield [self._row_to_sample(r) for r in rows]
            last = rows[-1]["sample_id"]
            if len(rows) < batch_size:
                return

    def count(self, where: str | None = None, dataset_id: str | None = None) -> int:
        clauses = []
        params: list[Any] = []
        if dataset_id:
            clauses.append("dataset_id = ?")
            params.append(dataset_id)
        if where:
            clauses.append("(" + validate_filter(where) + ")")
        sql = "SELECT COUNT(*) AS n FROM samples"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.conn.execute(sql, params).fetchone()["n"]

    def sum_tokens(self, where: str | None = None) -> int:
        sql = "SELECT COALESCE(SUM(n_tokens),0) AS t FROM samples"
        if where:
            sql += " WHERE (" + validate_filter(where) + ")"
        return self.conn.execute(sql).fetchone()["t"] or 0

    def delete_samples(self, where: str | None = None,
                       dataset_id: str | None = None) -> int:
        clauses = []
        params: list[Any] = []
        if dataset_id:
            clauses.append("dataset_id = ?")
            params.append(dataset_id)
        if where:
            clauses.append("(" + validate_filter(where) + ")")
        sql = "DELETE FROM samples"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount

    def delete_by_ids(self, sample_ids) -> int:
        ids = list(sample_ids)
        if not ids:
            return 0
        self.conn.executemany("DELETE FROM samples WHERE sample_id=?",
                             [(s,) for s in ids])
        self.conn.commit()
        return len(ids)

    def cid_referenced(self, cid: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM samples WHERE cid=? LIMIT 1", (cid,)).fetchone() is not None

    def update_cid(self, sample_id: str, new_cid: str) -> None:
        self.conn.execute("UPDATE samples SET cid=? WHERE sample_id=?",
                         (new_cid, sample_id))

    def update_fields(self, sample_id: str, fields: dict[str, Any]) -> None:
        core, extra = schema.split_metadata(fields)
        sets = []
        params: list[Any] = []
        for k, v in core.items():
            sets.append(f"{k}=?")
            params.append(v)
        if extra:
            # merge into tags_json
            cur = self.get_sample(sample_id)
            tags = (cur or {}).get("tags", {})
            tags.update(extra)
            sets.append("tags_json=?")
            params.append(utils.canonical_json(tags).decode())
        if not sets:
            return
        params.append(sample_id)
        self.conn.execute(
            f"UPDATE samples SET {','.join(sets)} WHERE sample_id=?", params
        )
        if core.get("domain"):
            self.register_domain_classes([core["domain"]], source="observed")

    def distribution(self, column: str, where: str | None = None) -> list[dict]:
        if column not in schema.CORE_FIELD_NAMES:
            raise ValueError(f"not a core column: {column}")
        sql = (
            f"SELECT {column} AS value, COUNT(*) AS n, "
            f"COALESCE(SUM(n_tokens),0) AS tokens FROM samples"
        )
        if where:
            sql += " WHERE (" + validate_filter(where) + ")"
        sql += f" GROUP BY {column} ORDER BY n DESC"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def histogram(self, column: str, bins: int = 10, where: str | None = None) -> dict:
        """Numeric histogram for quality-style columns."""
        rng = self.conn.execute(
            f"SELECT MIN({column}) AS lo, MAX({column}) AS hi FROM samples "
            f"WHERE {column} IS NOT NULL"
            + (f" AND ({validate_filter(where)})" if where else "")
        ).fetchone()
        lo, hi = rng["lo"], rng["hi"]
        if lo is None:
            return {"column": column, "bins": [], "min": None, "max": None}
        if hi == lo:
            hi = lo + 1.0
        width = (hi - lo) / bins
        counts = [0] * bins
        sql = (
            f"SELECT {column} AS v FROM samples WHERE {column} IS NOT NULL"
            + (f" AND ({validate_filter(where)})" if where else "")
        )
        for r in self.conn.execute(sql):
            idx = min(int((r["v"] - lo) / width), bins - 1)
            counts[idx] += 1
        return {
            "column": column,
            "min": lo,
            "max": hi,
            "bins": [
                {"lo": lo + i * width, "hi": lo + (i + 1) * width, "n": counts[i]}
                for i in range(bins)
            ],
        }

    def cid_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT cid, COUNT(*) AS n FROM samples GROUP BY cid"
        ).fetchall()
        return {r["cid"]: r["n"] for r in rows}

    def columns_overview(self, tag_scan: int = 500) -> dict:
        """Describe what is available to query/export: the core schema columns
        (with non-null counts) plus any custom tag keys discovered in the data.
        """
        total = self.count()
        # one query: non-null count per core column
        sel = ", ".join(f"COUNT({f.name}) AS {f.name}" for f in schema.CORE_FIELDS)
        row = self.conn.execute(f"SELECT {sel} FROM samples").fetchone()
        core = [
            {"name": f.name, "type": f.sql_type, "indexed": f.indexed,
             "dimension": f.dim, "non_null": (row[f.name] if row else 0)}
            for f in schema.CORE_FIELDS
        ]
        # discover custom tag keys from a sample of tags_json
        tags: dict[str, Any] = {}
        for r in self.conn.execute(
            "SELECT tags_json FROM samples WHERE tags_json IS NOT NULL "
            "AND tags_json != '{}' LIMIT ?", (tag_scan,)
        ):
            try:
                for k, v in utils.loads(r["tags_json"].encode()).items():
                    if k not in tags:
                        tags[k] = v
            except (ValueError, AttributeError):
                continue
        tag_list = [{"key": k, "example": v} for k, v in sorted(tags.items())]
        return {"total_samples": total, "core": core, "tags": tag_list,
                "note": "core fields are queryable columns; tag keys are "
                        "queryable via json_extract(tags_json,'$.<key>')"}

    def db_size(self) -> int:
        # fold the WAL back into the main file so the reported size is stable
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.path + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
