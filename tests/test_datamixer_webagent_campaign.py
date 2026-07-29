from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from loopai.agents.Obtainer.datamixer import llm
from loopai.agents.Obtainer.datamixer.cli import build_parser
from loopai.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from loopai.agents.Obtainer.datamixer.store import DataStore
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
        return {
            "selected_url": f"https://example.test/{task['position']}",
            "worker_id": context["worker_id"],
        }

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
    assert len(report["selected_urls"]) == 8
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
    runner.close()

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
    assert midway["pipeline"]["levels"]["L1"]["count"] == 1
    second = runner.resume(first["run_id"], workers=2)
    assert second["status"] == "completed"
    assert second["queue"]["succeeded"] == 3
    assert second["pipeline"]["levels"]["L1"]["count"] == 3
    assert second["pipeline"]["levels"]["L2"]["count"] == 3
    assert second["pipeline"]["levels"]["L3"]["count"] == 3
    assert attempts[2] == 2
    assert attempts[3] == 2
    runner.close()


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
