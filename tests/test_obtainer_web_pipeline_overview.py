from __future__ import annotations

import json
import os

from api.app.utils.obtainer.web_pipeline import build_web_pipeline_overview
from loopai.agents.Obtainer.datamixer.operators.streaming import PersistentPipelineQueue
from loopai.agents.Obtainer.datamixer.store import DataStore
from loopai.agents.Obtainer.datamixer.webagents.campaign import (
    CampaignConfig,
    CampaignQueue,
    ExpandedQuery,
)


def test_web_pipeline_overview_combines_layers_queue_workers_and_stage_progress(tmp_path) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    try:
        for level, dataset in (("L1", "web_l1"), ("L2", "web_l2"), ("L3", "web_l3")):
            dataset_id = store.catalog.add_dataset(dataset)
            store.ingest_records(
                dataset_id,
                [{"content": {"text": f"{level} mathematics content"}, "domain": "math"}],
                defaults={"quality_level": level},
                decontaminate=False,
            )
    finally:
        store.close()

    queue = CampaignQueue(root / "webagent_queue.sqlite")
    try:
        config = CampaignConfig(
            dataset="web_l1",
            l2_dataset="web_l2",
            l3_dataset="web_l3",
            workers=4,
            auto_pipeline="pipeline.yaml",
        )
        queue.create_campaign("webcampaign-test", "collect math code webpages", config, [])
        queue.add_tasks(
            "webcampaign-test",
            [
                ExpandedQuery(query="matrix operations documentation", goal="math code"),
                ExpandedQuery(query="numerical methods examples", goal="examples"),
            ],
        )
        task = queue.claim_next("webcampaign-test", "worker-1")
        assert task is not None
        queue.mark_campaign("webcampaign-test", "running")
        queue.set_pipeline_report(
            "webcampaign-test",
            {
                "ok": False,
                "status": "running",
                "current_stage": "domain_classify",
                "stages": [
                    {"name": "webpage_to_pt", "state": "completed"},
                    {"name": "domain_classify", "state": "running"},
                    {"name": "pt_to_sft_qa", "state": "waiting"},
                ],
                "levels": {},
            },
        )
    finally:
        queue.close()

    processing_queue = PersistentPipelineQueue(root / "pipeline_queue.sqlite")
    try:
        spec = {"name": "web", "source": {"dataset": "web_l1"}, "operators": [{"name": "webpage_to_pt"}]}
        stage_specs = [
            {"index": 0, "name": "webpage_to_pt", "kind": "map", "version": "1", "output_dataset": "web_l2"},
            {"index": 1, "name": "domain_classify", "kind": "map", "version": "1", "output_dataset": "web_l3"},
        ]
        processing_queue.prepare_run("webcampaign-test", "web", spec, stage_specs)
        processing_queue.enqueue_sources(
            "webcampaign-test",
            [{"sample_id": "source-1", "cid": "cid-1", "created_at": 1.0}],
            (1.0, "source-1"),
        )
        jobs = processing_queue.claim_batch("webcampaign-test", 0, "processor-1", 1)
        processing_queue.finish_batch(
            "webcampaign-test",
            0,
            jobs,
            {"source-1": {"sample_id": "derived-1", "content_cid": "cid-2", "row": {"sample_id": "derived-1", "cid": "cid-2"}}},
            next_stage=1,
        )
        processing_queue.set_stage("webcampaign-test", 0, "completed")
        processing_queue.set_stage("webcampaign-test", 1, "running")
    finally:
        processing_queue.close()

    overview = build_web_pipeline_overview(root)
    campaign = overview["campaign"]
    assert campaign["run_id"] == "webcampaign-test"
    assert campaign["queue"] == {
        "total": 2,
        "pending": 1,
        "running": 1,
        "succeeded": 0,
        "failed": 0,
        "attempts": 1,
    }
    assert campaign["workers"][0]["worker_id"] == "worker-1"
    assert campaign["config"]["l2_dataset"] == "web_l2"

    layers = {row["level"]: row for row in overview["layers"]}
    assert {level: layers[level]["count"] for level in ("L1", "L2", "L3")} == {
        "L1": 1,
        "L2": 1,
        "L3": 1,
    }
    assert layers["L1"]["datasets"] == ["web_l1"]
    assert layers["L2"]["datasets"] == ["web_l2"]
    assert layers["L3"]["datasets"] == ["web_l3"]

    stages = {row["name"]: row for row in overview["stages"]}
    assert stages["collect"]["state"] == "running"
    assert stages["extract"]["state"] == "completed"
    assert stages["classify"]["state"] == "running"
    assert stages["qa"]["state"] == "waiting"
    processing = overview["pipeline"]
    queues = {row["name"]: row for row in processing["queues"]}
    assert queues["webpage_to_pt"]["processed"] == 1
    assert queues["domain_classify"]["pending"] == 1
    assert queues["domain_classify"]["processed"] == 0
    assert processing["status"] == "running"


def test_web_pipeline_overview_surfaces_unbound_active_acquisition_worker(tmp_path) -> None:
    warehouse = tmp_path / "warehouse"
    store = DataStore.init(warehouse)
    store.close()
    run_dir = tmp_path / "legacy_acquisition"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"state": "running", "pid": os.getpid(), "updated_at": 42}),
        encoding="utf-8",
    )
    (run_dir / "thread.json").write_text(
        json.dumps({"warehouse": str(tmp_path / "wrong-root"), "objective": "collect code data"}),
        encoding="utf-8",
    )
    download_dir = run_dir / "downloads"
    download_dir.mkdir()
    (download_dir / "download_progress.json").write_text(
        json.dumps({"state": "running", "processed": 2, "total": 8, "completed": 1, "failed": 1}),
        encoding="utf-8",
    )

    overview = build_web_pipeline_overview(warehouse, project_root=tmp_path)

    assert overview["acquisition"]["active"] is True
    assert overview["acquisition"]["bound_to_lake"] is False
    assert overview["acquisition"]["phase"] == "download"
    assert overview["acquisition"]["download"]["processed"] == 2


def test_web_pipeline_overview_falls_back_to_loaded_lake_when_task_unbound(tmp_path) -> None:
    warehouse = tmp_path / "warehouse"
    store = DataStore.init(warehouse)
    try:
        dataset_id = store.catalog.add_dataset("old_task_data")
        store.ingest_records(
            dataset_id,
            [{"content": {"text": "old task row"}, "quality_level": "L1"}],
            decontaminate=False,
        )
    finally:
        store.close()

    old_run = tmp_path / "outputs" / "old_acquisition_aaaaaaaa"
    old_run.mkdir(parents=True)
    (old_run / "status.json").write_text(
        json.dumps({"state": "running", "pid": os.getpid(), "updated_at": 42}),
        encoding="utf-8",
    )
    (old_run / "thread.json").write_text(
        json.dumps({"task_id": "aaaaaaaa-1111", "warehouse": str(warehouse)}),
        encoding="utf-8",
    )

    overview = build_web_pipeline_overview(
        warehouse,
        acquisition_run=old_run,
        lake_context={"obtainer_active_task_id": "aaaaaaaa-1111"},
        project_root=tmp_path,
        task_id="bbbbbbbb-2222",
    )

    # The lake is intentionally unbound from task ids: a task_id that matches
    # no acquisition must NOT blank the dashboard - it falls back to the
    # currently loaded lake and only uses task_id to filter acquisitions.
    assert overview["initialized"] is True
    assert overview["warehouse"] == str(warehouse.resolve())
    assert overview["acquisition"] is None
    assert {item["level"]: item["count"] for item in overview["layers"]} == {
        "L1": 1,
        "L2": 0,
        "L3": 0,
    }


def test_web_pipeline_overview_matches_legacy_run_task_prefix(tmp_path) -> None:
    warehouse = tmp_path / "warehouse"
    store = DataStore.init(warehouse)
    store.close()
    run_dir = tmp_path / "outputs" / "finance_acq_4eca0ac2"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps({"state": "running", "pid": os.getpid(), "updated_at": 42}),
        encoding="utf-8",
    )
    (run_dir / "thread.json").write_text(
        json.dumps({"warehouse": str(warehouse)}),
        encoding="utf-8",
    )

    overview = build_web_pipeline_overview(
        warehouse,
        acquisition_run=run_dir,
        project_root=tmp_path,
        task_id="4eca0ac2-1870-49ee-b0a9-c8450411b126",
    )

    assert overview["initialized"] is True
    assert overview["acquisition"]["run_dir"] == str(run_dir.resolve())


def test_web_pipeline_overview_includes_dataflow_row_counts(tmp_path) -> None:
    warehouse = tmp_path / "warehouse"
    store = DataStore.init(warehouse)
    store.close()
    status_dir = warehouse / "runs" / "dataflow_agent"
    status_dir.mkdir(parents=True)
    (status_dir / "status.json").write_text(
        json.dumps({
            "state": "running",
            "phase": "applying",
            "input_rows": 200,
            "selected_rows": 200,
            "output_rows": 180,
            "dropped_rows": 20,
            "failed_rows": 3,
            "applied_rows": 125,
            "current_operator": "quality_filter",
        }),
        encoding="utf-8",
    )

    overview = build_web_pipeline_overview(
        warehouse,
        task_id="task-bound",
        explicit_binding=True,
    )

    assert overview["dataflow_agent"] == {
        "state": "running",
        "phase": "applying",
        "target": "",
        "dataset": "",
        "current_operator": "quality_filter",
        "input_rows": 200,
        "full_input_rows": 0,
        "full_output_rows": 0,
        "selected_rows": 200,
        "output_rows": 180,
        "dropped_rows": 20,
        "failed_rows": 3,
        "applied_rows": 125,
        "attempt": 0,
        "feedback": "",
        "error": "",
        "updated_at": None,
        "status_path": str(status_dir / "status.json"),
    }


def test_web_pipeline_overview_dataflow_snapshot_prefers_workdir(tmp_path) -> None:
    warehouse = tmp_path / "warehouse"
    store = DataStore.init(warehouse)
    store.close()

    # stale legacy status under the warehouse runs dir
    legacy = warehouse / "runs" / "dataflow_agent"
    legacy.mkdir(parents=True)
    legacy_status = legacy / "status.json"
    legacy_status.write_text(
        json.dumps({"state": "running", "phase": "applying", "input_rows": 200}),
        encoding="utf-8",
    )
    os.utime(legacy_status, (1000, 1000))

    # live work-dir status (agent-run --work-dir writes here)
    workdir = tmp_path / "outputs" / "code_dataflow_live"
    workdir.mkdir(parents=True)
    live_status = workdir / "status.json"
    live_status.write_text(
        json.dumps({
            "state": "running",
            "phase": "streaming",
            "input_rows": 132958,
            "full_input_rows": 132958,
            "full_output_rows": 0,
            "attempt": 1,
        }),
        encoding="utf-8",
    )
    os.utime(live_status, (2000, 2000))

    overview = build_web_pipeline_overview(warehouse, project_root=tmp_path)
    snap = overview["dataflow_agent"]
    assert snap["phase"] == "streaming"
    assert snap["input_rows"] == 132958
    assert snap["full_input_rows"] == 132958
    assert snap["status_path"] == str(tmp_path.resolve() / "outputs" / "code_dataflow_live" / "status.json")


def test_web_pipeline_overview_summary_is_live_catalog_counts(tmp_path) -> None:
    """The dashboard summary must reflect the live catalog, not the cached
    monitor_state summary, so the UI and the agents read the same counts."""
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    try:
        dataset_id = store.catalog.add_dataset("live_ds")
        store.ingest_records(
            dataset_id,
            [{"content": {"text": "x"}, "domain": "code"}],
            defaults={"quality_level": "L3"},
            decontaminate=False,
        )
        store.ingest_records(
            dataset_id,
            [{"content": {"text": "y"}, "domain": "math"}],
            defaults={"quality_level": "L3"},
            decontaminate=False,
        )
    finally:
        store.close()

    data = build_web_pipeline_overview(root, project_root=tmp_path)
    assert data["summary"] == {"datasets": 1, "records": 2}
