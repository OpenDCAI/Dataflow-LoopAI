"""Outer orchestration for query expansion + persistent webagent workers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from .. import llm
from ..models import ModelPool, system_default_model_name
from ..store import DataStore
from .base import create
from .webcrawler_dm import WebCrawlerDMConfig, mask_proxy_url


@dataclass
class ExpandedQuery:
    query: str
    goal: str = ""
    domain: str = ""
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CampaignConfig:
    webagent: str = "domain_data_acquisition"
    model: str = ""
    expand_model: str = ""
    subquery_count: int = 24
    workers: int = 4
    batch_size: int = 0
    task_retries: int = 1
    dataset: str = "domain_data_acquisition_l1"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    webagent_config: dict[str, Any] = field(default_factory=dict)
    auto_pipeline: str = ""
    focus_keywords: list[str] = field(default_factory=list)
    pipeline_model: str = ""
    l2_dataset: str = ""
    l3_dataset: str = ""
    pipeline_batch_size: int = 8
    pipeline_extractor: str = "pipeline"
    pipeline_mineru_gpu: str = "0"
    pipeline_mineru_url: str = ""
    pipeline_mineru_python: str = ""
    pipeline_mineru_model: str = ""
    pipeline_mineru_transport: str = ""

    def __post_init__(self) -> None:
        self.subquery_count = max(1, min(64, int(self.subquery_count)))
        self.workers = max(1, min(32, int(self.workers)))
        self.batch_size = max(0, int(self.batch_size))
        self.task_retries = max(0, int(self.task_retries))
        if not self.expand_model:
            self.expand_model = self.model
        if not self.pipeline_model:
            self.pipeline_model = self.model
        self.pipeline_batch_size = max(1, int(self.pipeline_batch_size))
        if self.pipeline_extractor not in {"pipeline", "auto", "mineru", "legacy"}:
            raise ValueError(
                "pipeline_extractor must be pipeline, auto, mineru, or legacy"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_EXPAND_SYSTEM = """You are a search-campaign planner for a web data collector.

Split one broad user request into diverse, non-overlapping, search-ready small
goals. Each goal will be handled independently by a web agent whose job is to
find all authoritative relevant resource URLs. Cover distinct aspects, resource types,
subtopics, audiences, jurisdictions, methods, and primary-source institutions.

Write each query in the terminology and language used by its target search
ecosystem, rather than mechanically copying the root query. For global
technical or code repositories, produce concise English discovery queries.
Every query must preserve all non-negotiable concepts in the root request.
The query field is only for search-ready terms; put scope and rationale in goal.
The domain field is a concise semantic subject label for the content, never a
hostname, search provider, or URL; leave it empty when a subject label is not
clear.

Do not answer the request. Do not include URLs. Do not create vague variants
that differ only by wording. Return JSON only:
{"subqueries":[{"query":"...","goal":"...","domain":"...","priority":0}]}
"""


class LLMQueryExpander:
    """Expand one root query into a deduplicated set of focused subgoals."""

    def __init__(self, root: str | Path, model: str, max_rounds: int = 3):
        if not model:
            raise ValueError("query expansion requires a DataMixer model-pool name")
        self.model_name = model
        self.model_spec = ModelPool(root).get(model)
        self.max_rounds = max(1, int(max_rounds))

    def expand(
        self,
        root_query: str,
        count: int,
    ) -> tuple[list[ExpandedQuery], list[dict[str, Any]]]:
        root_query = str(root_query or "").strip()
        count = max(1, min(64, int(count)))
        if not root_query:
            raise ValueError("campaign root query must not be empty")
        if count == 1:
            return [ExpandedQuery(query=root_query, goal=root_query)], []

        collected: list[ExpandedQuery] = []
        seen: set[str] = set()
        trace = []
        # Reasoning models spend part of max_output_tokens on chain-of-thought,
        # so keep a generous floor to avoid truncating the JSON mid-generation.
        budget = max(4096, min(8192, 300 + count * 150))
        spec = replace(
            self.model_spec,
            max_tokens=max(self.model_spec.max_tokens, budget),
        )
        for round_index in range(1, self.max_rounds + 1):
            remaining = count - len(collected)
            if remaining <= 0:
                break
            existing = [item.query for item in collected]
            user = {
                "root_query": root_query,
                "required_new_subqueries": remaining,
                "target_total": count,
                "already_generated": existing,
                "requirements": [
                    "Each query must be independently searchable.",
                    "Prefer concrete resource-finding goals over broad questions.",
                    "Use the target search ecosystem's terminology and language.",
                    "For global technical or code repositories, use concise English queries.",
                    "Keep query search-ready; put explanations and scope in goal.",
                    "Preserve every non-negotiable concept from the root query in each subquery.",
                    "Set domain to the semantic content subject, never a hostname or search provider.",
                    "Do not include URLs.",
                    "Return exactly the requested number of new unique entries.",
                ],
            }
            text = llm.complete(
                spec,
                [
                    {"role": "system", "content": _EXPAND_SYSTEM},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                json_mode=True,
                max_retries=3,
            )
            payload = llm.parse_json(text)
            rows = payload.get("subqueries") or payload.get("queries") or []
            added = 0
            for row in rows:
                item = self._parse_item(row)
                key = self._key(item.query)
                if not key or key in seen:
                    continue
                seen.add(key)
                item.priority = len(collected)
                collected.append(item)
                added += 1
                if len(collected) >= count:
                    break
            trace.append({
                "round": round_index,
                "requested": remaining,
                "returned": len(rows) if isinstance(rows, list) else 0,
                "added": added,
                "total": len(collected),
            })
            if added == 0:
                break
        if len(collected) < count:
            raise RuntimeError(
                f"LLM query expansion produced only {len(collected)}/{count} "
                f"unique subqueries after {self.max_rounds} rounds"
            )
        return collected[:count], trace

    @staticmethod
    def _parse_item(row: Any) -> ExpandedQuery:
        if isinstance(row, str):
            query = row.strip()
            return ExpandedQuery(query=query, goal=query)
        if not isinstance(row, dict):
            return ExpandedQuery(query="")
        query = str(row.get("query") or row.get("search_query") or "").strip()
        known = {"query", "search_query", "goal", "domain", "priority"}
        return ExpandedQuery(
            query=query,
            goal=str(row.get("goal") or query).strip(),
            domain=str(row.get("domain") or "").strip(),
            metadata={key: value for key, value in row.items() if key not in known},
        )

    @staticmethod
    def _key(query: str) -> str:
        return re.sub(r"\s+", " ", str(query or "").strip().lower())


class CampaignQueue:
    """SQLite-backed queue that survives process restarts."""

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
            CREATE TABLE IF NOT EXISTS campaigns (
                run_id TEXT PRIMARY KEY,
                webagent TEXT NOT NULL,
                root_query TEXT NOT NULL,
                dataset TEXT NOT NULL,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL,
                expansion_json TEXT,
                pipeline_json TEXT,
                error TEXT,
                executor_pid INTEGER,
                executor_started_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                query TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                goal TEXT,
                domain TEXT,
                metadata_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT,
                result_json TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                UNIQUE(run_id, query_hash)
            );
            CREATE INDEX IF NOT EXISTS ix_webagent_tasks_run_status
              ON tasks(run_id, status, position);
            """
        )
        existing = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(campaigns)")
        }
        if "pipeline_json" not in existing:
            self.conn.execute("ALTER TABLE campaigns ADD COLUMN pipeline_json TEXT")
        if "executor_pid" not in existing:
            self.conn.execute("ALTER TABLE campaigns ADD COLUMN executor_pid INTEGER")
        if "executor_started_at" not in existing:
            self.conn.execute("ALTER TABLE campaigns ADD COLUMN executor_started_at REAL")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def create_campaign(
        self,
        run_id: str,
        root_query: str,
        config: CampaignConfig,
        expansion_trace: list[dict[str, Any]],
    ) -> None:
        now = time.time()
        with self.lock:
            self.conn.execute(
                "INSERT INTO campaigns"
                "(run_id,webagent,root_query,dataset,status,config_json,"
                " expansion_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    config.webagent,
                    root_query,
                    config.dataset,
                    "queued",
                    self._json(config.to_dict()),
                    self._json(expansion_trace),
                    now,
                    now,
                ),
            )
            self.conn.commit()

    def add_tasks(self, run_id: str, tasks: list[ExpandedQuery]) -> int:
        now = time.time()
        written = 0
        with self.lock:
            for position, item in enumerate(tasks):
                query_hash = hashlib.sha256(
                    LLMQueryExpander._key(item.query).encode("utf-8")
                ).hexdigest()
                cursor = self.conn.execute(
                    "INSERT OR IGNORE INTO tasks"
                    "(run_id,position,query,query_hash,goal,domain,metadata_json,"
                    " status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        position,
                        item.query,
                        query_hash,
                        item.goal,
                        item.domain,
                        self._json(item.metadata),
                        "pending",
                        now,
                    ),
                )
                written += int(cursor.rowcount > 0)
            self.conn.commit()
        return written

    def get_campaign(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM campaigns WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._campaign_from_row(row)

    @staticmethod
    def _campaign_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """Decode one campaign row for both detailed and list views."""
        if row is None:
            return None
        data = dict(row)
        data["config"] = json.loads(data.pop("config_json") or "{}")
        data["expansion"] = json.loads(data.pop("expansion_json") or "[]")
        data["pipeline"] = json.loads(data.pop("pipeline_json") or "null")
        return data

    def list_campaigns(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return most-recent campaigns, prioritising currently actionable ones.

        The dashboard needs a non-mutating way to find the current campaign
        without knowing a run id ahead of time.  Prefer queued/running/paused
        rows, then fall back to newest terminal rows for historical context.
        """
        limit = max(1, min(int(limit), 100))
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM campaigns ORDER BY "
                "CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 "
                "WHEN 'paused' THEN 2 ELSE 3 END, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [item for row in rows if (item := self._campaign_from_row(row))]

    def recent_tasks(self, run_id: str, limit: int = 8) -> list[dict[str, Any]]:
        """Return recently touched queue tasks for operational status surfaces."""
        limit = max(1, min(int(limit), 50))
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY "
                "COALESCE(finished_at, started_at, created_at) DESC, task_id DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            item["result"] = json.loads(item.pop("result_json") or "null")
            out.append(item)
        return out

    def mark_campaign(self, run_id: str, status: str, error: str = "") -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE campaigns SET status=?,error=?,updated_at=?,"
                "executor_pid=CASE WHEN ?='running' THEN executor_pid ELSE NULL END "
                "WHERE run_id=?",
                (status, error[:2000], time.time(), status, run_id),
            )
            self.conn.commit()

    def set_executor(self, run_id: str, pid: int) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE campaigns SET executor_pid=?,executor_started_at=?,updated_at=? "
                "WHERE run_id=?",
                (int(pid), time.time(), time.time(), run_id),
            )
            self.conn.commit()

    def reconcile_dead_executor(self, run_id: str) -> bool:
        """Convert orphaned running work into a terminal, queryable failure."""
        with self.lock:
            row = self.conn.execute(
                "SELECT status,executor_pid FROM campaigns WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None or row["status"] != "running" or not row["executor_pid"]:
                return False
            try:
                os.kill(int(row["executor_pid"]), 0)
                return False
            except (OSError, TypeError, ValueError):
                pass
            now = time.time()
            error = (
                f"campaign executor pid {row['executor_pid']} exited while tasks were running"
            )
            self.conn.execute(
                "UPDATE tasks SET status='failed',worker_id=NULL,error=?,finished_at=? "
                "WHERE run_id=? AND status='running'",
                (error, now, run_id),
            )
            self.conn.execute(
                "UPDATE campaigns SET status='completed_with_errors',error=?,"
                "executor_pid=NULL,updated_at=? WHERE run_id=?",
                (error, now, run_id),
            )
            self.conn.commit()
            return True

    def set_pipeline_report(self, run_id: str, report: dict[str, Any]) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE campaigns SET pipeline_json=?,updated_at=? WHERE run_id=?",
                (self._json(report), time.time(), run_id),
            )
            self.conn.commit()

    def reset_running(self, run_id: str) -> int:
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE tasks SET status='pending',worker_id=NULL,started_at=NULL "
                "WHERE run_id=? AND status='running'",
                (run_id,),
            )
            self.conn.commit()
            return cursor.rowcount

    def reset_failed(self, run_id: str) -> int:
        """Explicitly requeue terminal failures after config/network changes."""

        with self.lock:
            cursor = self.conn.execute(
                "UPDATE tasks SET status='pending',attempts=0,worker_id=NULL,"
                "started_at=NULL,finished_at=NULL,error=NULL "
                "WHERE run_id=? AND status='failed'",
                (run_id,),
            )
            self.conn.commit()
            return cursor.rowcount

    def claim_next(self, run_id: str, worker_id: str) -> dict[str, Any] | None:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    "SELECT * FROM tasks WHERE run_id=? AND status='pending' "
                    "ORDER BY position,task_id LIMIT 1",
                    (run_id,),
                ).fetchone()
                if row is None:
                    self.conn.commit()
                    return None
                now = time.time()
                self.conn.execute(
                    "UPDATE tasks SET status='running',attempts=attempts+1,"
                    "worker_id=?,started_at=?,error=NULL WHERE task_id=?",
                    (worker_id, now, row["task_id"]),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        task = dict(row)
        task["attempts"] = int(task["attempts"]) + 1
        task["metadata"] = json.loads(task.pop("metadata_json") or "{}")
        return task

    def complete_task(self, task_id: int, result: dict[str, Any]) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE tasks SET status='succeeded',result_json=?,error=NULL,"
                "finished_at=? WHERE task_id=?",
                (self._json(result), time.time(), task_id),
            )
            self.conn.commit()

    def fail_task(self, task_id: int, error: str, max_retries: int) -> str:
        with self.lock:
            row = self.conn.execute(
                "SELECT attempts FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            attempts = int(row["attempts"]) if row else max_retries + 1
            retry = attempts <= max_retries
            status = "pending" if retry else "failed"
            self.conn.execute(
                "UPDATE tasks SET status=?,worker_id=NULL,error=?,"
                "finished_at=? WHERE task_id=?",
                (status, error[:4000], None if retry else time.time(), task_id),
            )
            self.conn.commit()
            return status

    def summary(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT status,COUNT(*) AS n FROM tasks WHERE run_id=? GROUP BY status",
                (run_id,),
            ).fetchall()
            attempts = self.conn.execute(
                "SELECT COALESCE(SUM(attempts),0) AS n FROM tasks WHERE run_id=?",
                (run_id,),
            ).fetchone()["n"]
        counts = {row["status"]: int(row["n"]) for row in rows}
        total = sum(counts.values())
        return {
            "total": total,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "attempts": int(attempts or 0),
        }

    def list_tasks(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks WHERE run_id=?"
        params: list[Any] = [run_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY position,task_id LIMIT ?"
        params.append(max(1, int(limit)))
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            item["result"] = json.loads(item.pop("result_json") or "null")
            out.append(item)
        return out


TaskExecutor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class WebAgentCampaignRunner:
    """Expand, enqueue, and drain tasks with a bounded worker pool."""

    def __init__(
        self,
        root: str | Path,
        *,
        queue_path: str | Path | None = None,
        task_executor: TaskExecutor | None = None,
        expander_factory: Callable[[str | Path, str], Any] | None = None,
    ):
        self.root = Path(root)
        self.queue = CampaignQueue(
            queue_path or self.root / "webagent_queue.sqlite"
        )
        self.task_executor = task_executor
        self.expander_factory = expander_factory or LLMQueryExpander

    def close(self) -> None:
        self.queue.close()

    def start(
        self,
        root_query: str,
        config: CampaignConfig,
        *,
        enqueue_only: bool = False,
    ) -> dict[str, Any]:
        self._ensure_dataset(config)
        expander = self.expander_factory(self.root, config.expand_model)
        subqueries, expansion_trace = expander.expand(
            root_query, config.subquery_count
        )
        run_id = "webcampaign-" + uuid.uuid4().hex[:16]
        self.queue.create_campaign(run_id, root_query, config, expansion_trace)
        queued = self.queue.add_tasks(run_id, subqueries)
        self._write_expansion(run_id, root_query, config, subqueries, expansion_trace)
        if queued != len(subqueries):
            self.queue.mark_campaign(run_id, "failed", "query dedup changed queue size")
            raise RuntimeError(
                f"queued {queued}/{len(subqueries)} expanded subqueries"
            )
        if enqueue_only:
            return self.status(run_id, include_tasks=True)
        return self._execute(
            run_id,
            workers=config.workers,
            max_tasks=config.batch_size or None,
        )

    def resume(
        self,
        run_id: str,
        *,
        workers: int | None = None,
        max_tasks: int | None = None,
        retry_failed: bool = False,
    ) -> dict[str, Any]:
        campaign = self.queue.get_campaign(run_id)
        if campaign is None:
            raise KeyError(f"webagent campaign not found: {run_id}")
        self.queue.reset_running(run_id)
        requeued_failed = 0
        if retry_failed:
            requeued_failed = self.queue.reset_failed(run_id)
        configured = int(campaign["config"].get("workers") or 4)
        configured_batch = int(campaign["config"].get("batch_size") or 0)
        previous_pipeline = campaign.get("pipeline") or {}
        return self._execute(
            run_id,
            workers=workers or configured,
            max_tasks=max_tasks if max_tasks is not None else (configured_batch or None),
            force_pipeline=(
                requeued_failed > 0
                or (bool(previous_pipeline) and not previous_pipeline.get("ok", False))
            ),
        )

    def status(
        self,
        run_id: str,
        *,
        include_tasks: bool = False,
        task_status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self.queue.reconcile_dead_executor(run_id)
        campaign = self.queue.get_campaign(run_id)
        if campaign is None:
            raise KeyError(f"webagent campaign not found: {run_id}")
        summary = self.queue.summary(run_id)
        out = {
            "run_id": run_id,
            "status": campaign["status"],
            "root_query": campaign["root_query"],
            "webagent": campaign["webagent"],
            "dataset": campaign["dataset"],
            "queue_path": str(self.queue.path),
            "created_at": campaign["created_at"],
            "updated_at": campaign["updated_at"],
            "error": campaign.get("error") or "",
            "config": self._public_config(campaign["config"]),
            "expansion": campaign["expansion"],
            "pipeline": campaign.get("pipeline"),
            "queue": summary,
        }
        if include_tasks:
            out["tasks"] = self.queue.list_tasks(
                run_id, status=task_status, limit=limit
            )
        return out

    @staticmethod
    def _public_config(config: dict[str, Any]) -> dict[str, Any]:
        data = json.loads(json.dumps(config))
        web_config = data.get("webagent_config") or {}
        if web_config.get("tavily_api_key"):
            web_config["tavily_api_key"] = "***"
        if web_config.get("proxy"):
            web_config["proxy"] = mask_proxy_url(web_config["proxy"])
        return data

    def _execute(
        self,
        run_id: str,
        *,
        workers: int,
        max_tasks: int | None = None,
        force_pipeline: bool = False,
    ) -> dict[str, Any]:
        campaign = self.queue.get_campaign(run_id)
        if campaign is None:
            raise KeyError(f"webagent campaign not found: {run_id}")
        workers = max(1, min(32, int(workers)))
        max_tasks = None if not max_tasks else max(1, int(max_tasks))
        budget = {"remaining": max_tasks}
        budget_lock = threading.Lock()

        def claim(worker_id: str):
            with budget_lock:
                if budget["remaining"] is not None and budget["remaining"] <= 0:
                    return None
                task = self.queue.claim_next(run_id, worker_id)
                if task is not None and budget["remaining"] is not None:
                    budget["remaining"] -= 1
                return task

        self.queue.set_executor(run_id, os.getpid())
        self.queue.mark_campaign(run_id, "running")
        started = time.time()
        pipeline_path = str(campaign["config"].get("auto_pipeline") or "")
        pipeline_runner = None
        pipeline_meta: dict[str, Any] = {}
        pipeline_error = ""

        # Start every downstream consumer before the web producer.  Each L1
        # sample is discovered by the persistent feeder and can reach L2/L3
        # while the crawler is still adding pages.
        if pipeline_path:
            try:
                from ..operators import StreamingPipelineRunner

                pipeline_meta = self._prepare_campaign_pipeline(run_id, campaign)

                def persist_stream_progress(progress: dict[str, Any]) -> None:
                    report = self._stream_pipeline_report(
                        pipeline_meta,
                        progress,
                        status=str(progress.get("status") or "running"),
                    )
                    self.queue.set_pipeline_report(run_id, report)

                pipeline_runner = StreamingPipelineRunner(
                    self.root,
                    pipeline_meta["spec"],
                    run_id=run_id,
                    batch_size=int(
                        campaign["config"].get("pipeline_batch_size") or 8
                    ),
                    retry_failed=force_pipeline,
                    progress_callback=persist_stream_progress,
                )
                pipeline_runner.start()
                pipeline_runner.wait_ready()
                persist_stream_progress(pipeline_runner.report(force=True))
            except Exception as exc:  # noqa: BLE001 - persist startup failure
                pipeline_error = f"{type(exc).__name__}: {exc}"[:4000]
                if pipeline_runner is not None:
                    pipeline_runner.finish_input(final=False)
                    try:
                        pipeline_runner.wait()
                    except Exception:
                        pass
                    pipeline_runner.close()
                    pipeline_runner = None
                self.queue.set_pipeline_report(run_id, {
                    "ok": False,
                    "status": "failed",
                    "mode": "streaming",
                    "pipeline_path": pipeline_path,
                    "error": pipeline_error,
                })

        worker_errors: list[str] = []
        if not pipeline_error:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix=f"{run_id}-worker",
            ) as pool:
                futures = [
                    pool.submit(self._worker_loop, run_id, index, campaign, claim)
                    for index in range(workers)
                ]
                worker_errors = [
                    error for error in (future.result() for future in futures) if error
                ]
        summary = self.queue.summary(run_id)
        if pipeline_error:
            status = "completed_with_errors"
        elif summary["pending"] or summary["running"]:
            status = "paused"
        elif summary["failed"]:
            status = "completed_with_errors"
        else:
            status = "completed"

        if pipeline_runner is not None:
            try:
                source_final = not summary["pending"] and not summary["running"]
                pipeline_runner.finish_input(final=source_final)
                stream_progress = pipeline_runner.wait()
                pipeline_report = self._stream_pipeline_report(
                    pipeline_meta,
                    stream_progress,
                    status=str(stream_progress.get("status") or "completed"),
                )
                pipeline_report["levels"] = self._pipeline_level_counts(
                    run_id, campaign
                )
                missing_levels = [
                    level for level, detail in pipeline_report["levels"].items()
                    if source_final and not detail["count"]
                ]
                stage_failures = sum(
                    int(stage.get("failed") or 0)
                    for stage in pipeline_report.get("stages", [])
                )
                pipeline_report["ok"] = bool(
                    source_final and not missing_levels and not stage_failures
                )
                if missing_levels:
                    pipeline_report["error"] = (
                        "no records materialized for levels: "
                        + ", ".join(missing_levels)
                    )
                elif stage_failures:
                    pipeline_report["error"] = (
                        f"{stage_failures} pipeline jobs failed"
                    )
                if source_final and not pipeline_report.get("ok", False):
                    pipeline_error = str(
                        pipeline_report.get("error")
                        or "streaming pipeline did not materialize every requested level"
                    )[:4000]
                    status = "completed_with_errors"
            except Exception as exc:  # noqa: BLE001 - persist pipeline failure
                pipeline_error = f"{type(exc).__name__}: {exc}"[:4000]
                pipeline_report = {
                    "ok": False,
                    "status": "failed",
                    "mode": "streaming",
                    "error": pipeline_error,
                }
                status = "completed_with_errors"
            finally:
                pipeline_runner.close()
            self.queue.set_pipeline_report(run_id, pipeline_report)
        all_errors = [*worker_errors, *([pipeline_error] if pipeline_error else [])]
        self.queue.mark_campaign(run_id, status, "; ".join(all_errors))
        report = self.status(run_id, include_tasks=True, limit=10000)
        report["workers"] = workers
        report["batch_size"] = max_tasks or 0
        report["elapsed_s"] = round(time.time() - started, 3)
        report["selected_urls"] = []
        for task in report.get("tasks", []):
            result = task.get("result")
            if task.get("status") != "succeeded" or not isinstance(result, dict):
                continue
            urls = result.get("selected_urls")
            if not isinstance(urls, list):
                urls = [result.get("selected_url")]
            for url in urls:
                if url and url not in report["selected_urls"]:
                    report["selected_urls"].append(url)
        self._write_final_report(run_id, report)
        return report

    def _prepare_campaign_pipeline(
        self, run_id: str, campaign: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve campaign-specific datasets/models before streaming starts."""
        from ..operators import load_pipeline

        settings = campaign["config"]
        path = Path(str(settings.get("auto_pipeline") or "")).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"auto pipeline not found: {path}")
        spec = load_pipeline(str(path))
        spec["name"] = f"{spec.get('name', 'web_pipeline')}__{run_id}"
        source = spec.setdefault("source", {})
        source["dataset"] = settings["dataset"]
        campaign_filter = (
            "json_extract(tags_json, '$.campaign_id') = " f"'{run_id}'"
        )
        source["filter"] = f"quality_level = 'L1' AND {campaign_filter}"
        l2_dataset = str(
            settings.get("l2_dataset") or f"{settings['dataset']}_l2_pt"
        )
        l3_dataset = str(
            settings.get("l3_dataset") or f"{settings['dataset']}_l3_sft"
        )
        model = str(settings.get("pipeline_model") or "")
        if not model:
            # LLM operators use the model-pool default; URL discovery keeps the
            # Codex default (the campaign model).
            model = system_default_model_name() or str(settings.get("model") or "")
        extractor = str(settings.get("pipeline_extractor") or "pipeline")
        mineru_gpu = str(settings.get("pipeline_mineru_gpu") or "0")
        mineru_url = str(settings.get("pipeline_mineru_url") or "").strip()
        mineru_python = str(settings.get("pipeline_mineru_python") or "").strip()
        mineru_model = str(settings.get("pipeline_mineru_model") or "").strip()
        mineru_transport = str(settings.get("pipeline_mineru_transport") or "").strip()
        focus_keywords = [
            str(item).strip()
            for item in (settings.get("focus_keywords") or [])
            if str(item).strip()
        ]
        for operator in spec.get("operators", []):
            name = operator.get("name")
            args = operator.setdefault("args", {})
            if name == "webpage_to_pt":
                if extractor != "pipeline":
                    args["engine"] = extractor
                args["mineru_gpu"] = mineru_gpu
                if mineru_url:
                    args["mineru_url"] = mineru_url
                if mineru_python:
                    args["mineru_python"] = mineru_python
                if mineru_model:
                    args["mineru_model"] = mineru_model
                if mineru_transport:
                    args["mineru_transport"] = mineru_transport
            elif name in {
                "domain_classify", "pt_to_sft_qa", "pt_to_sft_code",
                "pt_to_sft_text2sql",
            }:
                if model:
                    args["model"] = model
                if name == "domain_classify" and focus_keywords:
                    args["focus_keywords"] = [
                        *(args.get("focus_keywords") or []),
                        *focus_keywords,
                    ]
            output = operator.get("output") or operator.get("materialize")
            if isinstance(output, dict):
                if output.get("quality_level") == "L2":
                    output["dataset"] = l2_dataset
                elif output.get("quality_level") == "L3":
                    output["dataset"] = l3_dataset
        return {
            "spec": spec,
            "pipeline_path": str(path),
            "focus_keywords": focus_keywords,
            "model": model,
            "extractor": extractor,
            "l2_dataset": l2_dataset,
            "l3_dataset": l3_dataset,
        }

    @staticmethod
    def _stream_pipeline_report(
        meta: dict[str, Any], progress: dict[str, Any], *, status: str
    ) -> dict[str, Any]:
        stages = []
        active = []
        for raw in progress.get("stages", []):
            item = dict(raw)
            item["state"] = item.get("status") or "waiting"
            if item["state"] == "running":
                active.append(item.get("name") or "")
            stages.append(item)
        return {
            "ok": False,
            "status": status,
            "mode": "streaming",
            "current_stage": ",".join(name for name in active if name),
            "active_stages": [name for name in active if name],
            "pipeline_path": meta.get("pipeline_path") or "",
            "model": meta.get("model") or "",
            "extractor": meta.get("extractor") or "",
            "selected": int(progress.get("selected") or 0),
            "source_done": bool(progress.get("source_done")),
            "stages": stages,
            "error": str(progress.get("error") or ""),
            "lineage_path": str(progress.get("lineage_path") or ""),
            "quality_report": progress.get("quality_report"),
            "quality_report_path": str(progress.get("quality_report_path") or ""),
        }

    def _pipeline_level_counts(
        self, run_id: str, campaign: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        settings = campaign["config"]
        campaign_filter = (
            "json_extract(tags_json, '$.campaign_id') = " f"'{run_id}'"
        )
        datasets = (
            ("L1", settings["dataset"]),
            (
                "L2",
                str(settings.get("l2_dataset") or f"{settings['dataset']}_l2_pt"),
            ),
            (
                "L3",
                str(settings.get("l3_dataset") or f"{settings['dataset']}_l3_sft"),
            ),
        )
        store = DataStore.open(self.root)
        try:
            counts = {}
            for level, dataset in datasets:
                dataset_id = store.catalog.resolve_dataset(dataset)
                counts[level] = {
                    "dataset": dataset,
                    "dataset_id": dataset_id,
                    "count": (
                        store.catalog.count(
                            where=campaign_filter, dataset_id=dataset_id
                        )
                        if dataset_id else 0
                    ),
                }
            return counts
        finally:
            store.close()

    def _pipeline_has_new_l1(
        self,
        run_id: str,
        campaign: dict[str, Any],
    ) -> bool:
        previous = campaign.get("pipeline") or {}
        if not previous:
            return False
        previous_count = int(
            (((previous.get("levels") or {}).get("L1") or {}).get("count") or 0)
        )
        dataset = str(campaign["config"].get("dataset") or campaign.get("dataset") or "")
        store = DataStore.open(self.root)
        try:
            dataset_id = store.catalog.resolve_dataset(dataset)
            if dataset_id is None:
                return False
            current_count = store.catalog.count(
                where=(
                    "json_extract(tags_json, '$.campaign_id') = "
                    f"'{run_id}'"
                ),
                dataset_id=dataset_id,
            )
        finally:
            store.close()
        return current_count > previous_count

    def _run_auto_pipeline(
        self,
        run_id: str,
        campaign: dict[str, Any],
        *,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        from ..operators import load_pipeline, run_pipeline

        settings = campaign["config"]
        path = Path(str(settings.get("auto_pipeline") or "")).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"auto pipeline not found: {path}")
        spec = load_pipeline(str(path))
        spec["name"] = f"{spec.get('name', 'web_pipeline')}__{run_id}"
        source = spec.setdefault("source", {})
        source["dataset"] = settings["dataset"]
        campaign_filter = (
            "json_extract(tags_json, '$.campaign_id') = "
            f"'{run_id}'"
        )
        source["filter"] = f"quality_level = 'L1' AND {campaign_filter}"
        l2_dataset = str(settings.get("l2_dataset") or f"{settings['dataset']}_l2_pt")
        l3_dataset = str(settings.get("l3_dataset") or f"{settings['dataset']}_l3_sft")
        model = str(settings.get("pipeline_model") or "")
        if not model:
            # LLM operators use the model-pool default; URL discovery keeps the
            # Codex default (the campaign model).
            model = system_default_model_name() or str(settings.get("model") or "")
        extractor = str(settings.get("pipeline_extractor") or "pipeline")
        mineru_gpu = str(settings.get("pipeline_mineru_gpu") or "0")
        mineru_url = str(settings.get("pipeline_mineru_url") or "").strip()
        mineru_python = str(settings.get("pipeline_mineru_python") or "").strip()
        mineru_model = str(settings.get("pipeline_mineru_model") or "").strip()
        mineru_transport = str(settings.get("pipeline_mineru_transport") or "").strip()
        for operator in spec.get("operators", []):
            name = operator.get("name")
            args = operator.setdefault("args", {})
            if name == "webpage_to_pt":
                if extractor != "pipeline":
                    args["engine"] = extractor
                args["mineru_gpu"] = mineru_gpu
                if mineru_url:
                    args["mineru_url"] = mineru_url
                if mineru_python:
                    args["mineru_python"] = mineru_python
                if mineru_model:
                    args["mineru_model"] = mineru_model
                if mineru_transport:
                    args["mineru_transport"] = mineru_transport
            elif name in {"domain_classify", "pt_to_sft_qa"} and model:
                args["model"] = model
            output = operator.get("output") or operator.get("materialize")
            if isinstance(output, dict):
                if output.get("quality_level") == "L2":
                    output["dataset"] = l2_dataset
                elif output.get("quality_level") == "L3":
                    output["dataset"] = l3_dataset
        store = DataStore.open(self.root)
        try:
            replaced_existing = {}
            if replace_existing:
                for dataset in (l3_dataset, l2_dataset):
                    dataset_id = store.catalog.resolve_dataset(dataset)
                    if dataset_id is None:
                        replaced_existing[dataset] = 0
                        continue
                    erased = store.erase(
                        where=campaign_filter,
                        dataset_id=dataset_id,
                        reason=f"rebuild auto pipeline outputs for {run_id}",
                    )
                    replaced_existing[dataset] = int(erased.get("erased") or 0)

            progress_report: dict[str, Any] = {
                "ok": False,
                "status": "running",
                "current_stage": "",
                "pipeline_path": str(path),
                "model": model,
                "extractor": extractor,
                "replaced_existing": replaced_existing,
                "levels": {},
                "stages": [],
            }

            def persist_progress(progress: dict[str, Any]) -> None:
                progress_report.update({
                    "status": progress.get("status") or "running",
                    "current_stage": progress.get("current_stage") or "",
                    "stages": progress.get("stages") or [],
                    "selected": progress.get("selected") or 0,
                })
                self.queue.set_pipeline_report(run_id, progress_report)

            result = run_pipeline(
                store,
                spec,
                batch_size=int(settings.get("pipeline_batch_size") or 8),
                progress_callback=persist_progress,
            )
            counts = {}
            for level, dataset in (
                ("L1", settings["dataset"]),
                ("L2", l2_dataset),
                ("L3", l3_dataset),
            ):
                dataset_id = store.catalog.resolve_dataset(dataset)
                counts[level] = {
                    "dataset": dataset,
                    "dataset_id": dataset_id,
                    "count": (
                        store.catalog.count(
                            where=campaign_filter,
                            dataset_id=dataset_id,
                        )
                        if dataset_id else 0
                    ),
                }
        finally:
            store.close()
        missing_levels = [
            level for level, detail in counts.items() if not detail["count"]
        ]
        ok = not missing_levels
        return {
            "ok": ok,
            "status": "completed" if ok else "failed",
            "current_stage": "",
            "error": (
                "no records materialized for levels: " + ", ".join(missing_levels)
                if missing_levels else ""
            ),
            "pipeline_path": str(path),
            "model": model,
            "extractor": extractor,
            "replaced_existing": replaced_existing,
            "result": result.to_dict(),
            "stages": (progress_report.get("stages") if "progress_report" in locals() else []),
            "levels": counts,
        }

    def _worker_loop(
        self,
        run_id: str,
        worker_index: int,
        campaign: dict[str, Any],
        claim_task: Callable[[str], dict[str, Any] | None],
    ) -> str | None:
        worker_id = f"worker-{worker_index + 1}"
        settings = campaign["config"]
        max_retries = int(settings.get("task_retries") or 0)
        store = None
        agent = None
        try:
            if self.task_executor is None:
                web_config = dict(settings.get("webagent_config") or {})
                env_name = str(settings.get("tavily_api_key_env") or "")
                web_config["tavily_api_key"] = os.environ.get(env_name, "") if env_name else ""
                config = WebCrawlerDMConfig(**web_config)
                store = DataStore.open(self.root)
                agent = create(settings["webagent"], config=config)
            while True:
                task = claim_task(worker_id)
                if task is None:
                    return None
                try:
                    context = {
                        "run_id": run_id,
                        "worker_id": worker_id,
                        "campaign": campaign,
                        "settings": settings,
                    }
                    if self.task_executor is not None:
                        result = self.task_executor(task, context)
                    else:
                        result_obj = agent.run(
                            task["query"],
                            store=store,
                            dataset=settings["dataset"],
                            campaign_id=run_id,
                            parent_query=campaign["root_query"],
                            subgoal_index=task["position"],
                            subgoal_metadata={
                                "goal": task.get("goal") or "",
                                "domain": task.get("domain") or "",
                                **(task.get("metadata") or {}),
                            },
                        )
                        result = result_obj.to_dict(include_trace=False)
                    self.queue.complete_task(task["task_id"], result)
                except Exception as exc:  # noqa: BLE001 - isolate queue tasks
                    self.queue.fail_task(
                        task["task_id"],
                        f"{type(exc).__name__}: {exc}",
                        max_retries=max_retries,
                    )
        except Exception as exc:  # worker setup / connection failure
            return f"{worker_id}: {type(exc).__name__}: {exc}"[:2000]
        finally:
            if agent is not None:
                agent.close()
            if store is not None:
                store.close()

    def _ensure_dataset(self, config: CampaignConfig) -> None:
        store = DataStore.open(self.root)
        try:
            if store.catalog.resolve_dataset(config.dataset) is None:
                web_cfg = config.webagent_config
                store.catalog.add_dataset(
                    name=config.dataset,
                    source=config.webagent,
                    license=str(web_cfg.get("license") or "unknown"),
                    description=(
                        "Raw HTML generated by an expanded-query webagent campaign."
                    ),
                    meta={
                        "quality_level": "L1",
                        "webagent": config.webagent,
                        "workers": config.workers,
                        "subquery_count": config.subquery_count,
                    },
                )
        finally:
            store.close()

    def _write_expansion(
        self,
        run_id: str,
        root_query: str,
        config: CampaignConfig,
        subqueries: list[ExpandedQuery],
        expansion_trace: list[dict[str, Any]],
    ) -> None:
        run_dir = self.root / "webagent_campaigns" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "expanded_queries.jsonl").open("w", encoding="utf-8") as handle:
            for index, item in enumerate(subqueries):
                handle.write(json.dumps({
                    "index": index,
                    "parent_query": root_query,
                    **item.to_dict(),
                }, ensure_ascii=False) + "\n")
        (run_dir / "campaign.json").write_text(
            json.dumps({
                "run_id": run_id,
                "root_query": root_query,
                "config": config.to_dict(),
                "expansion": expansion_trace,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_final_report(self, run_id: str, report: dict[str, Any]) -> None:
        run_dir = self.root / "webagent_campaigns" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        final_path = run_dir / "final_report.json"
        final_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        lineage = self.root / "lineage" / f"{run_id}.json"
        lineage.parent.mkdir(exist_ok=True)
        lineage.write_text(
            json.dumps(
                {
                    **report,
                    "kind": "webagent_campaign",
                    "final_report": str(final_path),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
