from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from loopai.agents.Obtainer.datamixer import llm
from loopai.agents.Obtainer.datamixer.cli import build_parser
from loopai.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from loopai.agents.Obtainer.datamixer.store import DataStore
from loopai.agents.Obtainer.datamixer.operators import (
    PersistentPipelineQueue,
    StreamingPipelineRunner,
)
from loopai.agents.Obtainer.datamixer.operators import base as operator_base
from loopai.agents.Obtainer.datamixer.webagents.campaign import (
    CampaignConfig,
    CampaignQueue,
    ExpandedQuery,
    LLMQueryExpander,
    WebAgentCampaignRunner,
)


def test_llm_query_expander_deduplicates_across_rounds(monkeypatch, tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    store.close()
    payloads = iter([
        {
            "subqueries": [
                {"query": "Python asyncio official task docs", "goal": "official docs", "domain": "code"},
                {"query": "Python asyncio official task docs", "goal": "duplicate", "domain": "code"},
                {"query": "Python asyncio cancellation guide", "goal": "cancellation", "domain": "code"},
            ]
        },
        {
            "subqueries": [
                {"query": "Python asyncio structured concurrency examples", "goal": "examples", "domain": "code"}
            ]
        },
    ])

    prompts = []

    def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        prompts.append(messages)
        return json.dumps(next(payloads))

    monkeypatch.setattr(llm, "complete", fake_complete)
    expander = LLMQueryExpander(store.root, "fake")
    rows, trace = expander.expand("Collect asyncio resources", 3)
    assert [row.query for row in rows] == [
        "Python asyncio official task docs",
        "Python asyncio cancellation guide",
        "Python asyncio structured concurrency examples",
    ]
    assert trace[-1]["total"] == 3
    assert len({row.query.lower() for row in rows}) == 3
    system_prompt = " ".join(prompts[0][0]["content"].split())
    request = json.loads(prompts[0][1]["content"])
    assert "global technical or code repositories" in system_prompt
    assert "Every query must preserve all non-negotiable concepts" in system_prompt
    assert "never a hostname, search provider, or URL" in system_prompt
    assert "Use the target search ecosystem's terminology and language." in request["requirements"]


def test_campaign_queue_persists_and_resets_abandoned_tasks(tmp_path) -> None:
    path = tmp_path / "queue.sqlite"
    queue = CampaignQueue(path)
    config = CampaignConfig(model="fake", subquery_count=2)
    queue.create_campaign("run-1", "root", config, [])
    queue.add_tasks("run-1", [ExpandedQuery("q1"), ExpandedQuery("q2")])
    task = queue.claim_next("run-1", "worker-1")
    assert task["query"] == "q1"
    assert queue.summary("run-1")["running"] == 1
    queue.close()

    reopened = CampaignQueue(path)
    assert reopened.reset_running("run-1") == 1
    claimed = reopened.claim_next("run-1", "worker-2")
    assert claimed["query"] == "q1"
    assert claimed["attempts"] == 2
    reopened.close()


def test_campaign_queue_can_explicitly_requeue_failed_tasks(tmp_path) -> None:
    queue = CampaignQueue(tmp_path / "queue.sqlite")
    config = CampaignConfig(model="fake", subquery_count=1, task_retries=0)
    queue.create_campaign("run-failed", "root", config, [])
    queue.add_tasks("run-failed", [ExpandedQuery("q1")])
    task = queue.claim_next("run-failed", "worker-1")
    assert queue.fail_task(task["task_id"], "network", max_retries=0) == "failed"
    assert queue.summary("run-failed")["failed"] == 1
    assert queue.reset_failed("run-failed") == 1
    retried = queue.claim_next("run-failed", "worker-2")
    assert retried["attempts"] == 1
    queue.close()


def test_pipeline_queue_replays_from_first_changed_stage(tmp_path) -> None:
    queue = PersistentPipelineQueue(tmp_path / "pipeline.sqlite")
    old_spec = {
        "name": "p",
        "source": {"dataset": "l1"},
        "operators": [
            {"name": "extract"},
            {"name": "classify", "output": {"dataset": "l2"}},
            {"name": "qa"},
        ],
    }
    old_stages = [
        {"index": 0, "name": "extract", "kind": "map", "version": "1", "output_dataset": None},
        {"index": 1, "name": "classify", "kind": "score", "version": "1", "output_dataset": "l2"},
        {"index": 2, "name": "qa", "kind": "map", "version": "1", "output_dataset": None},
    ]
    queue.prepare_run("run", "p", old_spec, old_stages)
    queue.enqueue_sources(
        "run",
        [{"sample_id": "source", "cid": "cid", "created_at": 1.0}],
        (1.0, "source"),
    )
    job0 = queue.claim_batch("run", 0, "w0", 1)
    payload = {"sample_id": "source", "content_cid": "cid", "row": {"sample_id": "source", "cid": "cid"}}
    queue.finish_batch("run", 0, job0, {"source": payload}, next_stage=1)
    job1 = queue.claim_batch("run", 1, "w1", 1)
    queue.finish_batch("run", 1, job1, {"source": payload}, next_stage=2)
    job2 = queue.claim_batch("run", 2, "w2", 1)
    queue.finish_batch("run", 2, job2, {"source": payload}, next_stage=None)

    new_spec = {
        "name": "p",
        "source": {"dataset": "l1"},
        "operators": [
            {"name": "extract"},
            {"name": "classify"},
            {"name": "quality", "output": {"dataset": "l2"}},
            {"name": "qa"},
        ],
    }
    new_stages = [
        {"index": 0, "name": "extract", "kind": "map", "version": "1", "output_dataset": None},
        {"index": 1, "name": "classify", "kind": "score", "version": "1", "output_dataset": None},
        {"index": 2, "name": "quality", "kind": "filter", "version": "1", "output_dataset": "l2"},
        {"index": 3, "name": "qa", "kind": "map", "version": "1", "output_dataset": None},
    ]
    queue.prepare_run(
        "run", "p", new_spec, new_stages, retry_failed=True
    )

    with queue.lock:
        jobs = queue.conn.execute(
            "SELECT stage_index,status FROM pipeline_jobs WHERE run_id='run' "
            "ORDER BY stage_index"
        ).fetchall()
        stages = queue.conn.execute(
            "SELECT stage_index,name FROM pipeline_stages WHERE run_id='run' "
            "ORDER BY stage_index"
        ).fetchall()
    assert [(row[0], row[1]) for row in jobs] == [
        (0, "succeeded"),
        (1, "pending"),
    ]
    assert [(row[0], row[1]) for row in stages] == [
        (0, "extract"), (1, "classify"), (2, "quality"), (3, "qa")
    ]
    queue.close()


def test_streaming_pipeline_spec_migration_removes_stale_derived_rows(
    tmp_path,
) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    source_id = store.catalog.add_dataset(name="source")
    store.ingest_records(
        source_id,
        [{
            "content": {
                "html": "<html><body>one two three four five</body></html>"
            },
            "quality_level": "L1",
        }],
        decontaminate=False,
    )
    store.close()
    old_spec = {
        "name": "migrate_outputs",
        "source": {"dataset": "source"},
        "operators": [
            {
                "name": "webpage_to_pt",
                "args": {"engine": "legacy", "min_chars": 5},
            },
            {"name": "token_count"},
            {
                "name": "token_count",
                "output": {"dataset": "l2", "quality_level": "L2"},
            },
            {
                "name": "token_count",
                "output": {"dataset": "l3", "quality_level": "L3"},
            },
        ],
    }
    first = StreamingPipelineRunner(root, old_spec, run_id="run-migrate")
    try:
        first.start()
        first.wait_ready()
        deadline = time.time() + 5
        while first.report()["selected"] < 1 and time.time() < deadline:
            time.sleep(0.01)
        first.finish_input(final=True)
        first.wait()
    finally:
        first.close()

    store = DataStore.open(root)
    try:
        assert store.catalog.count(dataset_id=store.catalog.resolve_dataset("l2")) == 1
        assert store.catalog.count(dataset_id=store.catalog.resolve_dataset("l3")) == 1
    finally:
        store.close()

    new_spec = json.loads(json.dumps(old_spec))
    new_spec["operators"][1]["note"] = "changed contract"
    replay = StreamingPipelineRunner(
        root,
        new_spec,
        run_id="run-migrate",
        retry_failed=True,
    )
    try:
        store = DataStore.open(root)
        try:
            assert store.catalog.count(
                dataset_id=store.catalog.resolve_dataset("l2")
            ) == 0
            assert store.catalog.count(
                dataset_id=store.catalog.resolve_dataset("l3")
            ) == 0
        finally:
            store.close()
        assert replay.report()["migration"] == {
            "first_changed_stage": 1,
            "output_datasets": ["l2", "l3"],
            "erased_outputs": {"l2": 1, "l3": 1},
            "preserved_queue_cids": 1,
            "preserved_blobs": 1,
        }
        replay.start()
        replay.wait_ready()
        replay.finish_input(final=True)
        report = replay.wait()
    finally:
        replay.close()

    assert report["status"] == "completed"
    store = DataStore.open(root)
    try:
        assert store.catalog.count(dataset_id=store.catalog.resolve_dataset("l2")) == 1
        assert store.catalog.count(dataset_id=store.catalog.resolve_dataset("l3")) == 1
    finally:
        store.close()


def test_streaming_pipeline_isolates_semantically_invalid_batch_item(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    dataset_id = store.catalog.add_dataset(name="source")
    store.ingest_records(
        dataset_id,
        [
            {
                "content": {"text": text},
                "quality_level": "L1",
                "source_uri": f"https://example.test/{index}",
            }
            for index, text in enumerate(("good one", "bad", "good two"))
        ],
        decontaminate=False,
    )
    store.close()

    class SemanticBatchOperator(operator_base.Operator):
        spec = operator_base.OperatorSpec("semantic_batch", "1.0", "map")

        def process(self, batch, ctx):  # noqa: ARG002
            if any(row["content"]["text"] == "bad" for row in batch):
                raise RuntimeError(
                    "semantic_batch received invalid model output: missing item"
                )
            return batch

    monkeypatch.setattr(
        operator_base,
        "create",
        lambda name, **kwargs: SemanticBatchOperator(),
    )
    runner = StreamingPipelineRunner(
        root,
        {
            "name": "isolate_semantic_item",
            "source": {"dataset": "source"},
            "operators": [{"name": "semantic_batch"}],
        },
        run_id="run-isolate",
        batch_size=3,
        max_retries=0,
    )
    try:
        runner.start()
        runner.wait_ready()
        deadline = time.time() + 5
        while runner.report()["selected"] < 3 and time.time() < deadline:
            time.sleep(0.01)
        runner.finish_input(final=True)
        report = runner.wait()
    finally:
        runner.close()

    stage = report["stages"][0]
    assert stage["succeeded"] == 2
    assert stage["failed"] == 1


def test_campaign_status_reconciles_a_dead_executor_as_terminal_failure(tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    store.close()
    runner = WebAgentCampaignRunner(tmp_path / "warehouse")
    try:
        config = CampaignConfig(model="fake", subquery_count=1)
        runner.queue.create_campaign("run-orphaned", "root", config, [])
        runner.queue.add_tasks("run-orphaned", [ExpandedQuery("q1")])
        assert runner.queue.claim_next("run-orphaned", "worker-1") is not None
        runner.queue.set_executor("run-orphaned", 99_999_999)
        runner.queue.mark_campaign("run-orphaned", "running")

        status = runner.status("run-orphaned", include_tasks=True)

        assert status["status"] == "completed_with_errors"
        assert status["queue"]["running"] == 0
        assert status["queue"]["failed"] == 1
        assert "executor pid" in status["error"]
        assert "executor pid" in status["tasks"][0]["error"]
    finally:
        runner.close()


def test_campaign_runs_with_four_concurrent_workers_and_persists_status(tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    store.close()

    class FakeExpander:
        def __init__(self, root, model):  # noqa: ARG002
            pass

        def expand(self, root_query, count):
            return [
                ExpandedQuery(
                    query=f"{root_query} subgoal {index}",
                    goal=f"goal {index}",
                    priority=index,
                )
                for index in range(count)
            ], [{"round": 1, "added": count, "total": count}]

    lock = threading.Lock()
    barrier = threading.Barrier(4)
    active = 0
    max_active = 0

    def execute(task, context):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        if task["position"] < 4:
            barrier.wait(timeout=3)
        time.sleep(0.02)
        with lock:
            active -= 1
        result = {
            "selected_url": f"https://example.test/{task['position']}",
            "worker_id": context["worker_id"],
        }
        if task["position"] == 0:
            result["selected_urls"] = [
                "https://example.test/0",
                "https://example.test/0/second-root",
            ]
        return result

    runner = WebAgentCampaignRunner(
        store.root,
        task_executor=execute,
        expander_factory=FakeExpander,
    )
    report = runner.start(
        "broad query",
        CampaignConfig(
            model="fake",
            subquery_count=8,
            workers=4,
            dataset="campaign_l1",
        ),
    )
    run_id = report["run_id"]
    assert report["status"] == "completed"
    assert report["queue"]["succeeded"] == 8
    assert report["queue"]["attempts"] == 8
    assert len(report["selected_urls"]) == 9
    assert "https://example.test/0/second-root" in report["selected_urls"]
    assert max_active == 4
    runner.close()

    reopened = WebAgentCampaignRunner(store.root)
    status = reopened.status(run_id, include_tasks=True)
    assert status["queue"]["succeeded"] == 8
    assert len(status["tasks"]) == 8
    reopened.close()


def test_campaign_retries_one_task_without_stopping_other_workers(tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    store.close()

    class FakeExpander:
        def __init__(self, root, model):  # noqa: ARG002
            pass

        def expand(self, root_query, count):  # noqa: ARG002
            return [ExpandedQuery(f"q{index}") for index in range(count)], []

    def flaky(task, context):  # noqa: ARG001
        if task["position"] == 1 and task["attempts"] == 1:
            raise RuntimeError("transient")
        return {"selected_url": f"https://example.test/{task['position']}"}

    runner = WebAgentCampaignRunner(
        store.root,
        task_executor=flaky,
        expander_factory=FakeExpander,
    )
    report = runner.start(
        "root",
        CampaignConfig(
            model="fake",
            subquery_count=3,
            workers=2,
            task_retries=1,
        ),
    )
    assert report["status"] == "completed"
    assert report["queue"]["succeeded"] == 3
    assert report["queue"]["failed"] == 0
    assert report["queue"]["attempts"] == 4
    runner.close()


def test_campaign_can_process_persistent_queue_in_batches(tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    store.close()

    class FakeExpander:
        def __init__(self, root, model):  # noqa: ARG002
            pass

        def expand(self, root_query, count):  # noqa: ARG002
            return [ExpandedQuery(f"batch-q{index}") for index in range(count)], []

    def execute(task, context):  # noqa: ARG001
        return {"selected_url": f"https://example.test/{task['position']}"}

    runner = WebAgentCampaignRunner(
        store.root,
        task_executor=execute,
        expander_factory=FakeExpander,
    )
    first = runner.start(
        "root",
        CampaignConfig(
            model="fake",
            subquery_count=10,
            workers=4,
            batch_size=4,
        ),
    )
    assert first["status"] == "paused"
    assert first["queue"]["succeeded"] == 4
    assert first["queue"]["pending"] == 6
    second = runner.resume(first["run_id"], workers=4, max_tasks=4)
    assert second["status"] == "paused"
    assert second["queue"]["succeeded"] == 8
    final = runner.resume(first["run_id"], workers=4, max_tasks=4)
    assert final["status"] == "completed"
    assert final["queue"]["succeeded"] == 10
    runner.close()


def test_completed_campaign_auto_materializes_l1_l2_l3(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    ModelPool(root).add(ModelSpec(name="fake", api_url="mock://llm"))
    l1_id = store.catalog.add_dataset(
        name="math_code_l1",
        source="test",
        license="unknown",
        meta={"quality_level": "L1"},
    )
    store.ingest_records(
        l1_id,
        [{
            "content": {
                "html": (
                    "<html><body>Historical page from a different campaign "
                    "with enough words to pass preprocessing if isolation fails.</body></html>"
                ),
                "url": "https://example.test/historical",
            },
            "quality_level": "L1",
            "source_uri": "https://example.test/historical",
            "campaign_id": "webcampaign-historical",
        }],
        decontaminate=False,
    )
    store.close()
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        """pipeline:
  name: campaign_auto_levels
  source:
    dataset: replaced_by_campaign
    filter: quality_level = 'L1'
  operators:
    - name: webpage_to_pt
      args:
        engine: legacy
        min_chars: 20
      output:
        dataset: replaced_l2
        quality_level: L2
        stage: pretrain
        modality: text
    - name: pt_to_sft_qa
      args:
        model: fake
        chunk_size: 2
        max_concurrency: 1
    - name: sft_validate
      args:
        mode: filter
      output:
        dataset: replaced_l3
        quality_level: L3
        stage: sft
        modality: text
        task_type: grounded_qa
""",
        encoding="utf-8",
    )

    class FakeExpander:
        def __init__(self, root, model):  # noqa: ARG002
            pass

        def expand(self, root_query, count):  # noqa: ARG002
            return [ExpandedQuery(f"math code goal {index}") for index in range(count)], []

    def collect(task, context):  # noqa: ARG001
        local = DataStore.open(root)
        try:
            dataset_id = local.catalog.resolve_dataset("math_code_l1")
            local.ingest_records(
                dataset_id,
                [{
                    "content": {
                        "html": (
                            "<html><title>Math code</title><body><main>"
                            f"Algorithm {task['position']} explains numerical linear algebra "
                            "with equations, implementation details, examples, and tests."
                            "</main></body></html>"
                        ),
                        "url": f"https://example.test/{task['position']}",
                    },
                    "quality_level": "L1",
                    "source_uri": f"https://example.test/{task['position']}",
                    "campaign_id": context["run_id"],
                }],
                decontaminate=False,
            )
        finally:
            local.close()
        return {"selected_url": f"https://example.test/{task['position']}"}

    def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        return json.dumps({
            "results": [
                {
                    "index": 0,
                    "question": "What does the numerical algorithm explain?",
                    "answer": "It explains numerical linear algebra with implementation details.",
                },
                {
                    "index": 1,
                    "question": "What supporting material is included?",
                    "answer": "It includes equations, examples, and tests.",
                },
            ]
        })

    monkeypatch.setattr(llm, "complete", fake_complete)
    runner = WebAgentCampaignRunner(
        root,
        task_executor=collect,
        expander_factory=FakeExpander,
    )
    report = runner.start(
        "collect math code",
        CampaignConfig(
            model="fake",
            subquery_count=2,
            workers=2,
            dataset="math_code_l1",
            auto_pipeline=str(pipeline),
            pipeline_model="fake",
            l2_dataset="math_code_l2",
            l3_dataset="math_code_l3",
            pipeline_extractor="legacy",
        ),
    )
    assert report["status"] == "completed"
    assert report["pipeline"]["ok"] is True
    assert report["pipeline"]["levels"]["L1"]["count"] == 2
    assert report["pipeline"]["levels"]["L2"]["count"] == 2
    assert report["pipeline"]["levels"]["L3"]["count"] == 2
    assert report["pipeline"]["mode"] == "streaming"
    runner.close()

    queue_db = sqlite3.connect(root / "pipeline_queue.sqlite")
    try:
        stages = queue_db.execute(
            "SELECT stage_index,name,status FROM pipeline_stages "
            "WHERE run_id=? ORDER BY stage_index",
            (report["run_id"],),
        ).fetchall()
        assert [row[1] for row in stages] == [
            "webpage_to_pt", "pt_to_sft_qa", "sft_validate"
        ]
        assert {row[2] for row in stages} == {"completed"}
        counts = queue_db.execute(
            "SELECT stage_index,status,COUNT(*) FROM pipeline_jobs "
            "WHERE run_id=? GROUP BY stage_index,status ORDER BY stage_index",
            (report["run_id"],),
        ).fetchall()
        assert counts == [
            (0, "succeeded", 2),
            (1, "succeeded", 2),
            (2, "succeeded", 2),
        ]
    finally:
        queue_db.close()

    check = DataStore.open(root)
    for level, dataset in (
        ("L1", "math_code_l1"),
        ("L2", "math_code_l2"),
        ("L3", "math_code_l3"),
    ):
        dataset_id = check.catalog.resolve_dataset(dataset)
        rows = check.catalog.query(dataset_id=dataset_id)
        assert len(rows) == (3 if level == "L1" else 2)
        assert {row["quality_level"] for row in rows} == {level}
        current = check.catalog.query(
            where=(
                "json_extract(tags_json, '$.campaign_id') = "
                f"'{report['run_id']}'"
            ),
            dataset_id=dataset_id,
        )
        assert len(current) == 2
    check.close()


def test_campaign_pipeline_model_overrides_classifier_and_qa(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    store.catalog.add_dataset(
        name="override_l1",
        source="test",
        license="unknown",
        meta={"quality_level": "L1"},
    )
    store.close()
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        """pipeline:
  name: model_override
  source:
    dataset: replaced
  operators:
    - name: domain_classify
      args:
        model: stale-model
      output:
        dataset: replaced_l2
        quality_level: L2
    - name: pt_to_sft_qa
      args:
        model: stale-model
      output:
        dataset: replaced_l3
        quality_level: L3
""",
        encoding="utf-8",
    )
    captured = {}

    class FakeResult:
        @staticmethod
        def to_dict():
            return {"ok": True}

    def fake_run_pipeline(store, spec, batch_size, progress_callback):  # noqa: ARG001
        captured["spec"] = spec
        return FakeResult()

    monkeypatch.setattr(
        "loopai.agents.Obtainer.datamixer.operators.run_pipeline",
        fake_run_pipeline,
    )
    runner = WebAgentCampaignRunner(root)
    try:
        prepared = runner._prepare_campaign_pipeline(
            "webcampaign-model-override",
            {
                "config": {
                    "auto_pipeline": str(pipeline),
                    "dataset": "override_l1",
                    "l2_dataset": "override_l2",
                    "l3_dataset": "override_l3",
                    "pipeline_model": "resolved-model",
                    "pipeline_extractor": "pipeline",
                    "pipeline_batch_size": 8,
                }
            },
        )
        runner._run_auto_pipeline(
            "webcampaign-model-override",
            {
                "config": {
                    "auto_pipeline": str(pipeline),
                    "dataset": "override_l1",
                    "l2_dataset": "override_l2",
                    "l3_dataset": "override_l3",
                    "pipeline_model": "resolved-model",
                    "pipeline_extractor": "pipeline",
                    "pipeline_batch_size": 8,
                }
            },
        )
    finally:
        runner.close()

    operators = {
        item["name"]: item for item in captured["spec"]["operators"]
    }
    assert operators["domain_classify"]["args"]["model"] == "resolved-model"
    assert operators["pt_to_sft_qa"]["args"]["model"] == "resolved-model"
    stream_operators = {
        item["name"]: item for item in prepared["spec"]["operators"]
    }
    assert stream_operators["domain_classify"]["args"]["model"] == "resolved-model"
    assert stream_operators["pt_to_sft_qa"]["args"]["model"] == "resolved-model"


def test_failed_task_retry_reprocesses_new_l1_into_l2_l3(monkeypatch, tmp_path) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    ModelPool(root).add(ModelSpec(name="fake", api_url="mock://llm"))
    store.close()
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        """pipeline:
  name: retry_levels
  source:
    dataset: source
    filter: quality_level = 'L1'
  operators:
    - name: webpage_to_pt
      args:
        engine: legacy
        min_chars: 20
      output:
        dataset: l2
        quality_level: L2
        stage: pretrain
        modality: text
    - name: pt_to_sft_qa
      args:
        model: fake
        chunk_size: 2
        max_concurrency: 1
    - name: sft_validate
      args:
        mode: filter
      output:
        dataset: l3
        quality_level: L3
        stage: sft
        modality: text
        task_type: grounded_qa
""",
        encoding="utf-8",
    )

    class FakeExpander:
        def __init__(self, root, model):  # noqa: ARG002
            pass

        def expand(self, root_query, count):  # noqa: ARG002
            return [ExpandedQuery(f"goal {i}") for i in range(count)], []

    attempts = {1: 0, 2: 0, 3: 0}

    def execute(task, context):
        position = int(task["position"])
        attempts[position + 1] += 1
        if position in {1, 2} and attempts[position + 1] == 1:
            raise RuntimeError("temporary fetch failure")
        local = DataStore.open(root)
        try:
            dataset_id = local.catalog.resolve_dataset("source")
            local.ingest_records(
                dataset_id,
                [{
                    "content": {
                        "html": (
                            "<html><body>Substantive retry page with enough "
                            "words and grounded facts for preprocessing.</body></html>"
                        ),
                        "url": f"https://example.test/retry/{position}",
                    },
                    "quality_level": "L1",
                    "source_uri": f"https://example.test/retry/{position}",
                    "campaign_id": context["run_id"],
                }],
                decontaminate=False,
            )
        finally:
            local.close()
        return {"selected_url": f"https://example.test/retry/{position}"}

    def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        items = json.loads(messages[1]["content"].split("\n\nItems:\n", 1)[1])
        return json.dumps({
            "results": [
                {
                    "index": index,
                    "question": f"What does page {index} contain?",
                    "answer": "It contains grounded facts.",
                }
                for index in range(len(items))
            ]
        })

    monkeypatch.setattr(llm, "complete", fake_complete)
    runner = WebAgentCampaignRunner(
        root,
        task_executor=execute,
        expander_factory=FakeExpander,
    )
    first = runner.start(
        "collect retry resources",
        CampaignConfig(
            model="fake",
            subquery_count=3,
            workers=2,
            task_retries=0,
            dataset="source",
            auto_pipeline=str(pipeline),
            pipeline_model="fake",
            l2_dataset="l2",
            l3_dataset="l3",
            pipeline_extractor="legacy",
        ),
    )
    assert first["status"] == "completed_with_errors"
    assert first["pipeline"]["levels"]["L1"]["count"] == 1
    midway = runner.resume(
        first["run_id"],
        retry_failed=True,
        workers=2,
        max_tasks=1,
    )
    assert midway["status"] == "paused"
    assert midway["queue"]["pending"] == 1
    # A paused web queue is no longer a barrier: the newly fetched row has
    # already flowed through every downstream stage before resume returns.
    assert midway["pipeline"]["levels"]["L1"]["count"] == 2
    assert midway["pipeline"]["levels"]["L2"]["count"] == 2
    assert midway["pipeline"]["levels"]["L3"]["count"] == 2
    second = runner.resume(first["run_id"], workers=2)
    assert second["status"] == "completed"
    assert second["queue"]["succeeded"] == 3
    assert second["pipeline"]["levels"]["L1"]["count"] == 3
    assert second["pipeline"]["levels"]["L2"]["count"] == 3
    assert second["pipeline"]["levels"]["L3"]["count"] == 3
    assert attempts[2] == 2
    assert attempts[3] == 2
    runner.close()


def test_campaign_streams_downstream_while_webagent_task_is_still_running(
    tmp_path,
) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    store.close()
    pipeline = tmp_path / "streaming.yaml"
    pipeline.write_text(
        """pipeline:
  name: concurrent_stages
  source:
    dataset: replaced
  operators:
    - name: webpage_to_pt
      args:
        engine: legacy
        min_chars: 20
      output:
        dataset: replaced_l2
        quality_level: L2
        stage: pretrain
    - name: token_count
      output:
        dataset: replaced_l3
        quality_level: L3
        stage: sft
""",
        encoding="utf-8",
    )

    class FakeExpander:
        def __init__(self, root, model):  # noqa: ARG002
            pass

        def expand(self, root_query, count):  # noqa: ARG002
            return [ExpandedQuery("one live producer")], []

    observed_l3_before_return = threading.Event()

    def execute(task, context):  # noqa: ARG001
        local = DataStore.open(root)
        try:
            dataset_id = local.catalog.resolve_dataset("live_l1")
            local.ingest_records(
                dataset_id,
                [{
                    "content": {
                        "html": (
                            "<html><body>A substantive source page with enough "
                            "grounded words for immediate pipeline processing.</body></html>"
                        )
                    },
                    "quality_level": "L1",
                    "campaign_id": context["run_id"],
                }],
                decontaminate=False,
            )
        finally:
            local.close()

        deadline = time.time() + 5
        while time.time() < deadline:
            check = DataStore.open(root)
            try:
                dataset_id = check.catalog.resolve_dataset("live_l3")
                if dataset_id and check.catalog.count(dataset_id=dataset_id):
                    observed_l3_before_return.set()
                    break
            finally:
                check.close()
            time.sleep(0.02)
        assert observed_l3_before_return.is_set()
        return {"selected_url": "https://example.test/live"}

    runner = WebAgentCampaignRunner(
        root,
        task_executor=execute,
        expander_factory=FakeExpander,
    )
    report = runner.start(
        "stream immediately",
        CampaignConfig(
            model="unused",
            subquery_count=1,
            workers=1,
            dataset="live_l1",
            auto_pipeline=str(pipeline),
            l2_dataset="live_l2",
            l3_dataset="live_l3",
            pipeline_extractor="legacy",
        ),
    )
    runner.close()

    assert observed_l3_before_return.is_set()
    assert report["status"] == "completed"
    assert report["pipeline"]["levels"]["L1"]["count"] == 1
    assert report["pipeline"]["levels"]["L2"]["count"] == 1
    assert report["pipeline"]["levels"]["L3"]["count"] == 1


def test_concurrent_datastore_ingest_uses_separate_connections(tmp_path) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    dataset_id = store.catalog.add_dataset(name="parallel")
    store.close()

    def write(index):
        local = DataStore.open(root)
        try:
            return local.ingest_records(
                dataset_id,
                [{
                    "content": {"html": f"<html>{index}</html>"},
                    "quality_level": "L1",
                    "source_uri": f"https://example.test/{index}",
                }],
                decontaminate=False,
            ).written
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(write, range(20))) == 20
    check = DataStore.open(root)
    assert check.catalog.count(dataset_id=dataset_id) == 20
    check.close()


def test_campaign_cli_defaults_to_four_workers_and_24_subqueries() -> None:
    args = build_parser().parse_args([
        "webagent", "campaign", "start", "webcrawler_dm",
        "--query", "broad resource request",
        "--model", "deepseek-proxy",
    ])
    assert args.workers == 4
    assert args.batch_size == 0
    assert args.subquery_count == 24
    assert args.max_steps == 30
    assert args.soft_step_limit == 12
    assert args.max_search_calls == 4
    assert args.search_timeout == 12.0
    assert args.max_pages == 1000
    assert args.max_depth == 2
    assert args.max_links_per_page == 1000
    assert args.proxy == ""
    assert args.no_env_proxy is False
    assert args.auto_process is False
    assert args.pipeline_extractor == "pipeline"


def test_campaign_cli_detach_enqueues_and_spawns_resume(
    monkeypatch, tmp_path, capsys
) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    store.close()
    captured = {}

    class FakeQueue:
        def set_executor(self, run_id, pid):
            captured["executor"] = (run_id, pid)

    class FakeRunner:
        def __init__(self, runner_root):
            captured["root"] = runner_root
            self.queue = FakeQueue()

        def start(self, query, config, *, enqueue_only=False):
            captured["start"] = {
                "query": query,
                "config": config,
                "enqueue_only": enqueue_only,
            }
            return {
                "run_id": "webcampaign-detached",
                "status": "queued",
                "queue": {
                    "total": 1,
                    "succeeded": 0,
                    "failed": 0,
                    "pending": 1,
                },
                "queue_path": str(root / "webagent_queue.sqlite"),
                "tasks": [],
            }

        def close(self):
            captured["closed"] = True

    class FakeProcess:
        pid = 4242

    def fake_popen(command, **kwargs):
        captured["popen"] = {"command": command, **kwargs}
        return FakeProcess()

    monkeypatch.setattr(
        "loopai.agents.Obtainer.datamixer.webagents.WebAgentCampaignRunner",
        FakeRunner,
    )
    monkeypatch.setattr(
        "loopai.agents.Obtainer.datamixer.cli.subprocess.Popen",
        fake_popen,
    )
    args = build_parser().parse_args([
        "--root", str(root),
        "--json",
        "webagent", "campaign", "start", "domain_data_acquisition",
        "--query", "collect live sources",
        "--model", "fake",
        "--detach",
        "--auto-process",
    ])

    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert captured["start"]["query"] == "collect live sources"
    assert captured["start"]["enqueue_only"] is True
    assert captured["start"]["config"].auto_pipeline
    assert captured["popen"]["command"][-4:] == [
        "webagent", "campaign", "resume", "webcampaign-detached"
    ]
    assert captured["popen"]["start_new_session"] is True
    assert captured["executor"] == ("webcampaign-detached", 4242)
    assert captured["closed"] is True
    assert payload["status"] == "queued"
    assert payload["detached"] is True
    assert payload["executor_pid"] == 4242
    assert payload["stdout"].endswith(
        ".loopai/campaign_logs/webcampaign-detached.stdout.log"
    )
    assert payload["stderr"].endswith(
        ".loopai/campaign_logs/webcampaign-detached.stderr.log"
    )
