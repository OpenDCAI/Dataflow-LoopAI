from __future__ import annotations

import json
import os

from api.app.utils.obtainer.web_pipeline import build_web_pipeline_overview
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
