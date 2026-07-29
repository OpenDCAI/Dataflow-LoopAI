import json

import pytest

from api.app.services.task.service import TaskServiceError, get_train_status


def _run_dir(tmp_path, task_id="task-1", version_id="version-1"):
    path = tmp_path / task_id / "trainer" / version_id
    (path / "metrics").mkdir(parents=True)
    return path


def test_verl_status_prefers_structured_metrics_and_normalizes_curves(tmp_path) -> None:
    run_dir = _run_dir(tmp_path)
    metrics_dir = run_dir / "metrics"
    records = [
        {
            "step": 0,
            "data": {
                "val-core/math/acc/mean@1": 0.42,
            },
        },
        {
            "step": 1,
            "data": {
                "training/global_step": 1,
                "critic/rewards/mean": 0.5,
                "actor/pg_loss": 0.125,
            },
        },
    ]
    (metrics_dir / "verl_metrics.jsonl").write_text(
        "\n".join(json.dumps(item) for item in records),
        encoding="utf-8",
    )
    (metrics_dir / "metrics.json").write_text(
        json.dumps(
            {
                "task_info": {},
                "metrics": [
                    {
                        "step": 999,
                        "pid": 123,
                        "log_line": "Training Progress: 1/10",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = get_train_status(str(tmp_path), "task-1", "version-1")

    assert payload["task_info"]["framework"] == "verl"
    chart_records = [item for item in payload["metrics"] if "step" in item]
    assert chart_records[0]["val_score"] == 0.42
    assert chart_records[1]["reward"] == 0.5
    assert chart_records[1]["policy_loss"] == 0.125
    assert all("pid" not in item for item in chart_records)
    assert payload["metrics"][-1] == {"log_line": "Training Progress: 1/10"}


def test_sft_status_keeps_existing_metrics_contract(tmp_path) -> None:
    run_dir = _run_dir(tmp_path)
    expected = {
        "task_info": {"total_metrics": 1},
        "metrics": [{"step": 1, "loss": 0.25}],
    }
    (run_dir / "metrics" / "metrics.json").write_text(
        json.dumps(expected),
        encoding="utf-8",
    )

    assert get_train_status(str(tmp_path), "task-1", "version-1") == expected


def test_train_status_reports_missing_metrics(tmp_path) -> None:
    _run_dir(tmp_path)

    with pytest.raises(TaskServiceError) as exc_info:
        get_train_status(str(tmp_path), "task-1", "version-1")

    assert exc_info.value.code == 404
