"""Persistent, stage-by-stage streaming execution for DataMixer pipelines.

The batch runner in :mod:`pipeline` is useful for finite datasets.  A web
campaign is an unbounded producer while it is crawling, so it needs a durable
queue between every operator.  This module stores only row metadata and CAS
references in SQLite; large HTML/PT bodies stay in the content store.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .. import schema, utils
from ..catalog import validate_filter
from ..store import DataStore
from . import base
from .pipeline import _materialize_rows, _prepare_output, _row_metadata


_INTERNAL_ROOT = "_stream_pipeline_root_sample_id"
_RESERVED = {
    "content", "sample_id", "dataset_id", "cid", "created_at", "version",
    "tags", "embedding", _INTERNAL_ROOT,
}


class PersistentPipelineQueue:
    """SQLite queue with one logical queue per ``(run_id, stage_index)``."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.path), timeout=30.0, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                pipeline_name TEXT NOT NULL,
                spec_fingerprint TEXT NOT NULL,
                spec_json TEXT,
                status TEXT NOT NULL,
                source_done INTEGER NOT NULL DEFAULT 0,
                cursor_created_at REAL NOT NULL DEFAULT 0,
                cursor_sample_id TEXT NOT NULL DEFAULT '',
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pipeline_stages (
                run_id TEXT NOT NULL,
                stage_index INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                version TEXT NOT NULL,
                output_dataset TEXT,
                status TEXT NOT NULL DEFAULT 'waiting',
                error TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY(run_id, stage_index)
            );
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stage_index INTEGER NOT NULL,
                root_sample_id TEXT NOT NULL,
                sample_id TEXT NOT NULL,
                content_cid TEXT NOT NULL,
                row_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                UNIQUE(run_id, stage_index, root_sample_id)
            );
            CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_claim
              ON pipeline_jobs(run_id, stage_index, status, job_id);
            """
        )
        run_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(pipeline_runs)")
        }
        if "spec_json" not in run_columns:
            self.conn.execute("ALTER TABLE pipeline_runs ADD COLUMN spec_json TEXT")
        self.conn.commit()

    @staticmethod
    def _json(value: Any) -> str:
        return utils.canonical_json(value).decode("utf-8")

    def migration_plan(
        self,
        run_id: str,
        spec: dict[str, Any],
        stages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Describe stale derived outputs affected by a changed pipeline spec."""
        fingerprint = utils.fingerprint(spec)
        with self.lock:
            row = self.conn.execute(
                "SELECT spec_fingerprint,spec_json FROM pipeline_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None or row["spec_fingerprint"] == fingerprint:
                return None
            first_changed = self._first_changed_stage(
                run_id, row["spec_json"], spec, stages
            )
            old_outputs = [
                str(item["output_dataset"])
                for item in self.conn.execute(
                    "SELECT output_dataset FROM pipeline_stages WHERE run_id=? "
                    "AND stage_index>=? AND output_dataset IS NOT NULL",
                    (run_id, first_changed),
                )
                if item["output_dataset"]
            ]
        new_outputs = [
            str(item["output_dataset"])
            for item in stages[first_changed:]
            if item.get("output_dataset")
        ]
        return {
            "first_changed_stage": first_changed,
            "output_datasets": list(dict.fromkeys([*old_outputs, *new_outputs])),
        }

    def stage_content_cids(self, run_id: str, stage_index: int) -> set[str]:
        with self.lock:
            return {
                str(row["content_cid"])
                for row in self.conn.execute(
                    "SELECT DISTINCT content_cid FROM pipeline_jobs WHERE run_id=? "
                    "AND stage_index=?",
                    (run_id, stage_index),
                )
                if row["content_cid"]
            }

    def rewind_from_source(self, run_id: str) -> int:
        """Rebuild stage zero from original source CIDs after lost intermediates."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT job_id,row_json FROM pipeline_jobs WHERE run_id=? "
                "AND stage_index=0",
                (run_id,),
            ).fetchall()
            restored = []
            for row in rows:
                payload = json.loads(row["row_json"] or "{}")
                source_cid = str(payload.get("cid") or "")
                if not source_cid:
                    raise RuntimeError(
                        f"stage-zero job {row['job_id']} has no original source cid"
                    )
                restored.append((source_cid, int(row["job_id"])))
            self.conn.execute(
                "DELETE FROM pipeline_jobs WHERE run_id=? AND stage_index>0",
                (run_id,),
            )
            self.conn.executemany(
                "UPDATE pipeline_jobs SET content_cid=?,status='pending',attempts=0,"
                "worker_id=NULL,error=NULL,started_at=NULL,finished_at=NULL "
                "WHERE job_id=?",
                restored,
            )
            self.conn.execute(
                "UPDATE pipeline_stages SET status='waiting',error=NULL,updated_at=? "
                "WHERE run_id=?",
                (time.time(), run_id),
            )
            self.conn.execute(
                "UPDATE pipeline_runs SET status='running',source_done=0,error=NULL,"
                "updated_at=? WHERE run_id=?",
                (time.time(), run_id),
            )
            self.conn.commit()
        return len(restored)

    def prepare_run(
        self,
        run_id: str,
        pipeline_name: str,
        spec: dict[str, Any],
        stages: list[dict[str, Any]],
        *,
        retry_failed: bool = False,
    ) -> None:
        fingerprint = utils.fingerprint(spec)
        now = time.time()
        with self.lock:
            row = self.conn.execute(
                "SELECT spec_fingerprint,spec_json FROM pipeline_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is not None and row["spec_fingerprint"] != fingerprint:
                if not retry_failed:
                    raise RuntimeError(
                        "streaming pipeline spec changed for an existing campaign; "
                        "resume with failed-job retry to migrate durable queues"
                    )
                first_changed = self._first_changed_stage(
                    run_id, row["spec_json"], spec, stages
                )
                self.conn.execute(
                    "DELETE FROM pipeline_jobs WHERE run_id=? AND stage_index>?",
                    (run_id, first_changed),
                )
                self.conn.execute(
                    "UPDATE pipeline_jobs SET status='pending',attempts=0,"
                    "worker_id=NULL,error=NULL,started_at=NULL,finished_at=NULL "
                    "WHERE run_id=? AND stage_index=?",
                    (run_id, first_changed),
                )
                self.conn.execute(
                    "DELETE FROM pipeline_stages WHERE run_id=? AND stage_index>=?",
                    (run_id, len(stages)),
                )
            self.conn.execute(
                "INSERT OR IGNORE INTO pipeline_runs"
                "(run_id,pipeline_name,spec_fingerprint,spec_json,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    run_id, pipeline_name, fingerprint, self._json(spec),
                    "running", now, now,
                ),
            )
            self.conn.execute(
                "UPDATE pipeline_runs SET pipeline_name=?,spec_fingerprint=?,"
                "spec_json=?,status='running',error=NULL,updated_at=? "
                "WHERE run_id=?",
                (pipeline_name, fingerprint, self._json(spec), now, run_id),
            )
            for item in stages:
                self.conn.execute(
                    "INSERT OR IGNORE INTO pipeline_stages"
                    "(run_id,stage_index,name,kind,version,output_dataset,status,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        run_id, item["index"], item["name"], item["kind"],
                        item["version"], item.get("output_dataset"), "waiting", now,
                    ),
                )
                self.conn.execute(
                    "UPDATE pipeline_stages SET name=?,kind=?,version=?,"
                    "output_dataset=?,status='waiting',error=NULL,updated_at=? "
                    "WHERE run_id=? AND stage_index=?",
                    (
                        item["name"], item["kind"], item["version"],
                        item.get("output_dataset"), now, run_id, item["index"],
                    ),
                )
            self.conn.execute(
                "UPDATE pipeline_jobs SET status='pending',worker_id=NULL,"
                "started_at=NULL WHERE run_id=? AND status='running'",
                (run_id,),
            )
            if retry_failed:
                self.conn.execute(
                    "UPDATE pipeline_jobs SET status='pending',attempts=0,"
                    "worker_id=NULL,error=NULL,started_at=NULL,finished_at=NULL "
                    "WHERE run_id=? AND status='failed'",
                    (run_id,),
                )
            self.conn.commit()

    def _first_changed_stage(
        self,
        run_id: str,
        old_spec_json: str | None,
        new_spec: dict[str, Any],
        new_stages: list[dict[str, Any]],
    ) -> int:
        """Return the first queue boundary that must be replayed."""
        if old_spec_json:
            try:
                old_spec = json.loads(old_spec_json)
                old_source = old_spec.get("source", {}) or {}
                new_source = new_spec.get("source", {}) or {}
                if old_source != new_source:
                    return 0
                old_ops = old_spec.get("operators", [])
                new_ops = new_spec.get("operators", [])
                for index in range(max(len(old_ops), len(new_ops))):
                    if index >= len(old_ops) or index >= len(new_ops):
                        return min(index, max(0, len(new_ops) - 1))
                    if old_ops[index] != new_ops[index]:
                        return index
            except (TypeError, ValueError):
                pass
        old_stages = self.conn.execute(
            "SELECT stage_index,name,output_dataset FROM pipeline_stages "
            "WHERE run_id=? ORDER BY stage_index",
            (run_id,),
        ).fetchall()
        for index in range(max(len(old_stages), len(new_stages))):
            if index >= len(old_stages) or index >= len(new_stages):
                return min(index, max(0, len(new_stages) - 1))
            old = old_stages[index]
            new = new_stages[index]
            if (
                old["name"] != new["name"]
                or (old["output_dataset"] or None)
                != (new.get("output_dataset") or None)
            ):
                return index
        return 0

    def cursor(self, run_id: str) -> tuple[float, str]:
        with self.lock:
            row = self.conn.execute(
                "SELECT cursor_created_at,cursor_sample_id FROM pipeline_runs "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return (float(row[0]), str(row[1])) if row else (0.0, "")

    def enqueue_sources(
        self,
        run_id: str,
        rows: list[dict[str, Any]],
        cursor: tuple[float, str],
    ) -> int:
        now = time.time()
        written = 0
        with self.lock:
            for row in rows:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO pipeline_jobs"
                    "(run_id,stage_index,root_sample_id,sample_id,content_cid,"
                    "row_json,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        run_id, 0, row["sample_id"], row["sample_id"], row["cid"],
                        self._json(_payload_metadata(row)), "pending", now,
                    ),
                )
                written += int(cur.rowcount > 0)
            self.conn.execute(
                "UPDATE pipeline_runs SET cursor_created_at=?,cursor_sample_id=?,"
                "updated_at=? WHERE run_id=?",
                (cursor[0], cursor[1], now, run_id),
            )
            self.conn.commit()
        return written

    def claim_batch(
        self, run_id: str, stage_index: int, worker_id: str, limit: int
    ) -> list[dict[str, Any]]:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self.conn.execute(
                    "SELECT * FROM pipeline_jobs WHERE run_id=? AND stage_index=? "
                    "AND status='pending' ORDER BY job_id LIMIT ?",
                    (run_id, stage_index, max(1, int(limit))),
                ).fetchall()
                if rows:
                    now = time.time()
                    self.conn.executemany(
                        "UPDATE pipeline_jobs SET status='running',attempts=attempts+1,"
                        "worker_id=?,started_at=?,error=NULL WHERE job_id=?",
                        [(worker_id, now, row["job_id"]) for row in rows],
                    )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        out = []
        for raw in rows:
            item = dict(raw)
            item["attempts"] = int(item["attempts"]) + 1
            item["row"] = json.loads(item.pop("row_json"))
            out.append(item)
        return out

    def finish_batch(
        self,
        run_id: str,
        stage_index: int,
        jobs: list[dict[str, Any]],
        outputs: dict[str, dict[str, Any]],
        *,
        next_stage: int | None,
        rejections: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        now = time.time()
        with self.lock:
            for job in jobs:
                root = job["root_sample_id"]
                output = outputs.get(root)
                status = "succeeded" if output is not None else "dropped"
                rejected = (rejections or {}).get(root)
                persisted = output or rejected
                if persisted is None:
                    self.conn.execute(
                        "UPDATE pipeline_jobs SET status=?,worker_id=NULL,error=NULL,"
                        "finished_at=? WHERE job_id=?",
                        (status, now, job["job_id"]),
                    )
                else:
                    self.conn.execute(
                        "UPDATE pipeline_jobs SET status=?,content_cid=?,row_json=?,"
                        "worker_id=NULL,error=NULL,finished_at=? WHERE job_id=?",
                        (
                            status, persisted["content_cid"],
                            self._json(persisted["row"]), now, job["job_id"],
                        ),
                    )
                if output is not None and next_stage is not None:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO pipeline_jobs"
                        "(run_id,stage_index,root_sample_id,sample_id,content_cid,"
                        "row_json,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            run_id, next_stage, root, output["sample_id"],
                            output["content_cid"], self._json(output["row"]),
                            "pending", now,
                        ),
                    )
            self.conn.commit()

    def fail_batch(
        self, jobs: list[dict[str, Any]], error: str, max_retries: int
    ) -> None:
        now = time.time()
        with self.lock:
            for job in jobs:
                retry = int(job["attempts"]) <= max_retries
                self.conn.execute(
                    "UPDATE pipeline_jobs SET status=?,worker_id=NULL,error=?,"
                    "finished_at=? WHERE job_id=?",
                    (
                        "pending" if retry else "failed", error[:4000],
                        None if retry else now, job["job_id"],
                    ),
                )
            self.conn.commit()

    def set_stage(self, run_id: str, index: int, status: str, error: str = "") -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE pipeline_stages SET status=?,error=?,updated_at=? "
                "WHERE run_id=? AND stage_index=?",
                (status, error[:2000], time.time(), run_id, index),
            )
            self.conn.commit()

    def set_run(
        self, run_id: str, status: str, *, source_done: bool | None = None,
        error: str = "",
    ) -> None:
        fields = ["status=?", "error=?", "updated_at=?"]
        values: list[Any] = [status, error[:4000], time.time()]
        if source_done is not None:
            fields.append("source_done=?")
            values.append(int(source_done))
        values.append(run_id)
        with self.lock:
            self.conn.execute(
                f"UPDATE pipeline_runs SET {','.join(fields)} WHERE run_id=?", values
            )
            self.conn.commit()

    def stage_has_work(self, run_id: str, index: int) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM pipeline_jobs WHERE run_id=? AND stage_index=? "
                "AND status IN ('pending','running') LIMIT 1",
                (run_id, index),
            ).fetchone()
        return row is not None

    def summary(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            run = self.conn.execute(
                "SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            stages = self.conn.execute(
                "SELECT * FROM pipeline_stages WHERE run_id=? ORDER BY stage_index",
                (run_id,),
            ).fetchall()
            counts = self.conn.execute(
                "SELECT stage_index,status,COUNT(*) AS n FROM pipeline_jobs "
                "WHERE run_id=? GROUP BY stage_index,status",
                (run_id,),
            ).fetchall()
        by_stage: dict[int, dict[str, int]] = {}
        for row in counts:
            by_stage.setdefault(int(row["stage_index"]), {})[row["status"]] = int(row["n"])
        stage_rows = []
        for raw in stages:
            item = dict(raw)
            count = by_stage.get(int(item["stage_index"]), {})
            total = sum(count.values())
            item.update({
                "queued": count.get("pending", 0),
                "running": count.get("running", 0),
                "succeeded": count.get("succeeded", 0),
                "dropped": count.get("dropped", 0),
                "failed": count.get("failed", 0),
                "rows_in": total,
                "rows_out": count.get("succeeded", 0),
                "materialized": (
                    count.get("succeeded", 0) if item.get("output_dataset") else 0
                ),
            })
            stage_rows.append(item)
        return {
            "status": run["status"] if run else "missing",
            "source_done": bool(run["source_done"]) if run else False,
            "error": (run["error"] or "") if run else "",
            "stages": stage_rows,
            "selected": stage_rows[0]["rows_in"] if stage_rows else 0,
        }

    def quality_report(self, run_id: str) -> dict[str, Any] | None:
        """Collect the LLM admission decisions persisted by the filter stage."""
        with self.lock:
            stage = self.conn.execute(
                "SELECT stage_index FROM pipeline_stages WHERE run_id=? "
                "AND name = 'topic_quality_filter' "
                "LIMIT 1",
                (run_id,),
            ).fetchone()
            if stage is None:
                return None
            rows = self.conn.execute(
                "SELECT j.status,j.row_json,n.row_json AS next_row_json "
                "FROM pipeline_jobs j LEFT JOIN pipeline_jobs n ON "
                "n.run_id=j.run_id AND n.stage_index=j.stage_index+1 "
                "AND n.root_sample_id=j.root_sample_id WHERE j.run_id=? "
                "AND j.stage_index=? ORDER BY j.job_id",
                (run_id, int(stage["stage_index"])),
            ).fetchall()
        decisions = []
        reasons: Counter[str] = Counter()
        for row in rows:
            payloads = [json.loads(row["row_json"] or "{}")]
            if row["next_row_json"]:
                payloads.insert(0, json.loads(row["next_row_json"]))
            decision = None
            for payload in payloads:
                tags = (
                    payload.get("tags")
                    if isinstance(payload.get("tags"), dict) else {}
                )
                decision = (
                    payload.get("finance_quality_report")
                    or tags.get("finance_quality_report")
                )
                if isinstance(decision, dict):
                    break
            if not isinstance(decision, dict):
                continue
            item = dict(decision)
            item["queue_status"] = row["status"]
            decisions.append(item)
            for reason in item.get("rejection_reasons") or []:
                reasons[str(reason)] += 1
        return {
            "ok": bool(decisions) and not reasons,
            "input_records": len(decisions),
            "accepted_records": sum(
                1 for item in decisions if item.get("accepted")
            ),
            "rejected_records": sum(
                1 for item in decisions if not item.get("accepted")
            ),
            "rejection_reasons": dict(reasons),
            "records": decisions[:5000],
            "records_truncated": len(decisions) > 5000,
        }

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class StreamingPipelineRunner:
    """Run all pipeline stages concurrently with durable hand-off queues."""

    def __init__(
        self,
        root: str | Path,
        spec: dict[str, Any],
        *,
        run_id: str,
        batch_size: int = 8,
        queue_path: str | Path | None = None,
        max_retries: int = 2,
        retry_failed: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        poll_interval: float = 0.1,
    ):
        self.root = Path(root)
        self.spec = spec
        self.run_id = run_id
        self.name = str(spec.get("name") or "streaming_pipeline")
        self.batch_size = max(1, int(batch_size))
        self.max_retries = max(0, int(max_retries))
        self.retry_failed = retry_failed
        self.progress_callback = progress_callback
        self.poll_interval = max(0.01, float(poll_interval))
        self.queue = PersistentPipelineQueue(
            queue_path or self.root / "pipeline_queue.sqlite"
        )
        self._stop_input = threading.Event()
        self._input_final = False
        self._feeder_done = threading.Event()
        self._stop_all = threading.Event()
        self._ready = threading.Event()
        self._ready_lock = threading.Lock()
        self._ready_count = 0
        self._setup_error = ""
        self._threads: list[threading.Thread] = []
        self._stage_done: list[threading.Event] = []
        self._last_progress = 0.0
        self._progress_lock = threading.Lock()
        self._started = False
        self._migration: dict[str, Any] | None = None
        self._prepare()

    def _prepare(self) -> None:
        src = self.spec.get("source", {}) or {}
        self.source_dataset = str(src.get("dataset") or "")
        self.source_where = str(src.get("filter") or "")
        store = DataStore.open(self.root)
        try:
            self.source_dataset_id = store.catalog.resolve_dataset(self.source_dataset)
            if self.source_dataset and self.source_dataset_id is None:
                raise ValueError(f"source dataset not found: {self.source_dataset}")
            self._operators = []
            stage_specs = []
            for index, op_spec in enumerate(self.spec.get("operators", [])):
                args = op_spec.get("args", {}) or {}
                op = base.create(op_spec["name"], **args)
                if op.spec.stateful:
                    raise ValueError(
                        f"stateful operator {op.spec.name!r} is not supported by "
                        "the per-record streaming scheduler"
                    )
                output = _prepare_output(store, self.spec, op_spec, self.name)
                ctx = base.OperatorContext(
                    run_id=self.run_id,
                    seed=0,
                    device=op_spec.get("device", "cpu"),
                    root=str(self.root),
                    extra=args,
                )
                self._operators.append((op, ctx, output))
                stage_specs.append({
                    "index": index,
                    "name": op.spec.name,
                    "kind": op.spec.kind,
                    "version": op.spec.version,
                    "output_dataset": (output or {}).get("dataset_name"),
                })
            migration = self.queue.migration_plan(
                self.run_id, self.spec, stage_specs
            )
            if migration is not None and self.retry_failed:
                escaped_run_id = self.run_id.replace("'", "''")
                preserved_cids = self.queue.stage_content_cids(
                    self.run_id, migration["first_changed_stage"]
                )
                erased_outputs = {}
                preserved_blobs = 0
                for dataset_name in migration["output_datasets"]:
                    dataset_id = store.catalog.resolve_dataset(dataset_name)
                    if dataset_id is None:
                        erased_outputs[dataset_name] = 0
                        continue
                    result = store.erase(
                        where=(
                            "json_extract(tags_json, '$.pipeline_run_id') = "
                            f"'{escaped_run_id}'"
                        ),
                        dataset_id=dataset_id,
                        reason=(
                            "rebuild changed streaming pipeline outputs for "
                            f"{self.run_id} from stage "
                            f"{migration['first_changed_stage']}"
                        ),
                        preserve_cids=preserved_cids,
                    )
                    erased_outputs[dataset_name] = int(result.get("erased") or 0)
                    preserved_blobs += int(result.get("blobs_preserved") or 0)
                self._migration = {
                    **migration,
                    "erased_outputs": erased_outputs,
                    "preserved_queue_cids": len(preserved_cids),
                    "preserved_blobs": preserved_blobs,
                }
        finally:
            store.close()
        if not self._operators:
            raise ValueError("streaming pipeline has no operators")
        self.queue.prepare_run(
            self.run_id, self.name, self.spec, stage_specs,
            retry_failed=self.retry_failed,
        )
        self._stage_done = [threading.Event() for _ in self._operators]

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        feeder = threading.Thread(
            target=self._feeder_loop,
            name=f"{self.run_id}-pipeline-feeder",
            daemon=True,
        )
        self._threads.append(feeder)
        feeder.start()
        for index in range(len(self._operators)):
            thread = threading.Thread(
                target=self._stage_loop,
                args=(index,),
                name=f"{self.run_id}-pipeline-stage-{index}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def wait_ready(self, timeout: float = 30.0) -> None:
        if not self._ready.wait(timeout):
            raise TimeoutError("streaming pipeline operators did not become ready")
        if self._setup_error:
            raise RuntimeError(self._setup_error)

    def finish_input(self, *, final: bool) -> None:
        self._input_final = bool(final)
        self._stop_input.set()

    def wait(self) -> dict[str, Any]:
        for thread in self._threads:
            thread.join()
        summary = self.queue.summary(self.run_id)
        if self._migration is not None:
            summary["migration"] = self._migration
        stages = summary["stages"]
        failures = sum(int(item["failed"]) for item in stages)
        if self._setup_error:
            status = "failed"
            error = self._setup_error
        elif not self._input_final:
            status = "paused"
            error = ""
        elif failures:
            status = "completed_with_errors"
            error = f"{failures} pipeline jobs failed"
        else:
            status = "completed"
            error = ""
        self.queue.set_run(
            self.run_id, status, source_done=self._input_final, error=error
        )
        report = self.report(force=True)
        if self._input_final:
            lineage_dir = self.root / "lineage"
            lineage_dir.mkdir(exist_ok=True)
            lineage_path = lineage_dir / f"stream-{self.run_id}.json"
            lineage_path.write_text(
                utils.canonical_json({
                    "kind": "streaming_pipeline_run",
                    "run_id": self.run_id,
                    "pipeline": self.name,
                    "timestamp": time.time(),
                    "spec_fingerprint": utils.fingerprint(self.spec),
                    "result": report,
                }).decode("utf-8"),
                encoding="utf-8",
            )
            report["lineage_path"] = str(lineage_path)
            quality = self.queue.quality_report(self.run_id)
            if quality is not None:
                report["quality_report"] = quality
                quality_dir = self.root / "quality_reports"
                quality_dir.mkdir(exist_ok=True)
                quality_path = quality_dir / f"{self.run_id}.finance_quality.json"
                quality_path.write_text(
                    utils.canonical_json(quality).decode("utf-8"),
                    encoding="utf-8",
                )
                report["quality_report_path"] = str(quality_path)
        return report

    def report(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        with self._progress_lock:
            if not force and now - self._last_progress < 0.25:
                return self.queue.summary(self.run_id)
            self._last_progress = now
        summary = self.queue.summary(self.run_id)
        if self._migration is not None:
            summary["migration"] = self._migration
        if self.progress_callback is not None:
            try:
                self.progress_callback(summary)
            except Exception:
                pass
        return summary

    def close(self) -> None:
        self.queue.close()

    def _mark_ready(self, error: str = "") -> None:
        with self._ready_lock:
            if error and not self._setup_error:
                self._setup_error = error
                self._stop_all.set()
            self._ready_count += 1
            if self._setup_error or self._ready_count == len(self._operators):
                self._ready.set()

    def _feeder_loop(self) -> None:
        store = DataStore.open(self.root)
        try:
            while not self._stop_all.is_set():
                count = self._feed_once(store)
                if count:
                    self.report()
                    continue
                if self._stop_input.is_set():
                    break
                self._stop_input.wait(self.poll_interval)
        except Exception as exc:  # noqa: BLE001
            self._setup_error = f"pipeline feeder failed: {type(exc).__name__}: {exc}"
            self._stop_all.set()
            self._ready.set()
        finally:
            store.close()
            self._feeder_done.set()

    def _feed_once(self, store: DataStore) -> int:
        cursor_created, cursor_sample = self.queue.cursor(self.run_id)
        clauses = ["dataset_id = ?"]
        params: list[Any] = [self.source_dataset_id]
        if self.source_where:
            clauses.append(f"({validate_filter(self.source_where)})")
        clauses.append("(created_at > ? OR (created_at = ? AND sample_id > ?))")
        params.extend([cursor_created, cursor_created, cursor_sample])
        rows = store.catalog.conn.execute(
            "SELECT * FROM samples WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at,sample_id LIMIT 256",
            params,
        ).fetchall()
        if not rows:
            return 0
        decoded = [store.catalog._row_to_sample(row) for row in rows]
        last = decoded[-1]
        return self.queue.enqueue_sources(
            self.run_id,
            decoded,
            (float(last["created_at"]), str(last["sample_id"])),
        )

    def _stage_loop(self, index: int) -> None:
        op, ctx, output = self._operators[index]
        store = DataStore.open(self.root)
        worker_id = f"stage-{index + 1}"
        ready_marked = False
        try:
            op.setup(ctx)
            self.queue.set_stage(self.run_id, index, "running")
            self._mark_ready()
            ready_marked = True
            while not self._stop_all.is_set():
                jobs = self.queue.claim_batch(
                    self.run_id, index, worker_id, self.batch_size
                )
                if jobs:
                    self._process_jobs(store, index, op, ctx, output, jobs)
                    self.report()
                    continue
                upstream_done = (
                    self._feeder_done.is_set()
                    if index == 0 else self._stage_done[index - 1].is_set()
                )
                if upstream_done and not self.queue.stage_has_work(self.run_id, index):
                    break
                self._stop_all.wait(self.poll_interval)
            stage = self.queue.summary(self.run_id)["stages"][index]
            state = "completed_with_errors" if stage["failed"] else "completed"
            if self._stop_all.is_set() and self._setup_error:
                state = "failed"
            self.queue.set_stage(self.run_id, index, state, self._setup_error)
        except Exception as exc:  # noqa: BLE001
            error = f"{op.spec.name} setup/worker failed: {type(exc).__name__}: {exc}"
            self.queue.set_stage(self.run_id, index, "failed", error)
            self._stop_all.set()
            if not ready_marked:
                self._mark_ready(error)
                ready_marked = True
            elif not self._setup_error:
                self._setup_error = error
        finally:
            if not ready_marked:
                self._mark_ready("pipeline stage exited before setup")
            try:
                op.teardown(ctx)
            except Exception:
                pass
            store.close()
            self._stage_done[index].set()

    def _process_jobs(self, store, index, op, ctx, output, jobs) -> None:
        rows = []
        input_by_root = {}
        for job in jobs:
            row = dict(job["row"])
            row["content"] = store.get_content(job["content_cid"])
            row[_INTERNAL_ROOT] = job["root_sample_id"]
            rows.append(row)
            input_by_root[job["root_sample_id"]] = row
        try:
            processed = op.process(rows, ctx)
            by_root: dict[str, dict[str, Any]] = {}
            for row in processed:
                root = str(row.get(_INTERNAL_ROOT) or "")
                if root:
                    by_root[root] = row
            clean_rows = []
            clean_roots = []
            for root, row in by_root.items():
                row.pop(_INTERNAL_ROOT, None)
                clean_roots.append(root)
                clean_rows.append(row)
            if output and clean_rows:
                clean_rows = _materialize_rows(
                    store, clean_rows, output, self.name, self.run_id, op.spec.name
                )
                by_root = {
                    root: row for root, row in zip(clean_roots, clean_rows)
                }
            else:
                by_root = {
                    root: row for root, row in zip(clean_roots, clean_rows)
                }
            if index == len(self._operators) - 1 and clean_rows:
                _writeback_rows(store, clean_rows)
            payloads = {
                root: _encode_payload(store, row) for root, row in by_root.items()
            }
            rejections = {}
            for root, row in input_by_root.items():
                if root in payloads:
                    continue
                row.pop(_INTERNAL_ROOT, None)
                rejections[root] = _encode_payload(store, row)
            self.queue.finish_batch(
                self.run_id,
                index,
                jobs,
                payloads,
                next_stage=(index + 1 if index + 1 < len(self._operators) else None),
                rejections=rejections,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            # Strict LLM operators can reject one semantically incomplete item
            # after the rest of a claimed queue batch has produced valid output.
            # Keep the cheap whole-batch retries first (valid chunk responses are
            # cached), then bisect only semantic-output failures so one bad item
            # cannot terminally fail every unrelated job in the claim.  Transport
            # failures deliberately stay batch-scoped to avoid multiplying calls
            # against an unavailable model service.
            exhausted = all(
                int(job["attempts"]) > self.max_retries for job in jobs
            )
            if (
                len(jobs) > 1
                and exhausted
                and "received invalid model output" in error
            ):
                middle = len(jobs) // 2
                self._process_jobs(
                    store, index, op, ctx, output, jobs[:middle]
                )
                self._process_jobs(
                    store, index, op, ctx, output, jobs[middle:]
                )
                return
            self.queue.fail_batch(jobs, error, self.max_retries)


def _payload_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"content", _INTERNAL_ROOT}}


def _encode_payload(store: DataStore, row: dict[str, Any]) -> dict[str, Any]:
    content_cid, _ = store.cas.put_json(row.get("content"))
    return {
        "sample_id": str(row.get("sample_id") or ""),
        "content_cid": content_cid,
        "row": _payload_metadata(row),
    }


def _writeback_rows(store: DataStore, rows: list[dict[str, Any]]) -> None:
    from array import array

    for row in rows:
        sample_id = row.get("sample_id")
        if not sample_id:
            continue
        if row.get("embedding") is not None:
            store.index.vectors.add(sample_id, array("f", row["embedding"]))
        updates = {key: value for key, value in row.items() if key not in _RESERVED}
        if updates:
            store.catalog.update_fields(sample_id, updates)
    store.catalog.commit()
