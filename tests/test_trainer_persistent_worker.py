import json
import os
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from loopai.skills.Trainer import load_events
from loopai.skills.Trainer.utils.persistent_worker import (
    PersistentWorkerHandle,
    launch_or_attach_persistent_worker,
    read_run_state,
    record_worker_event,
    update_run_state,
    wait_for_persistent_worker,
    worker_paths,
    write_worker_result,
)
from loopai.skills.Trainer.worker_entry import run_worker
from loopai.skills.Trainer.utils.training_log_parser import TrainingLogParser


def _state(tmp_path: Path) -> dict:
    run_dir = tmp_path / "task-1" / "trainer" / "version-1"
    return {
        "task_id": "task-1",
        "output_dir": str(tmp_path),
        "trainer": {
            "trainer_version_id": "version-1",
            "trainer_output_dir": str(run_dir),
            "output_dir": str(run_dir),
            "train_framework": "verl",
        },
    }


def test_run_state_updates_are_atomic_and_preserve_worker_status(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state_path = worker_paths(state["trainer"]["trainer_output_dir"])["state"]
    update_run_state(state_path, {"status": "running", "worker_pid": 123})

    record_worker_event(
        state,
        {
            "node": "training_execution",
            "status": "running",
            "progress": 0.25,
            "message": "training",
            "data": {
                "current_step": "2",
                "total_steps": "8",
                "training_pid": 456,
            },
        },
    )

    persisted = read_run_state(state_path)
    assert persisted["status"] == "running"
    assert persisted["event_sequence"] == 1
    assert persisted["current_step"] == "2"
    assert persisted["total_steps"] == "8"
    assert persisted["training_pid"] == 456


def test_launcher_uses_detached_worker_process(tmp_path: Path) -> None:
    state = _state(tmp_path)
    process = Mock(pid=321)

    with patch(
        "loopai.skills.Trainer.utils.persistent_worker.subprocess.Popen",
        return_value=process,
    ) as popen:
        handle = launch_or_attach_persistent_worker(state)

    assert handle.process is process
    assert popen.call_args.kwargs["start_new_session"] is (os.name == "posix")
    persisted = read_run_state(handle.state_path)
    assert persisted["status"] == "queued"
    assert persisted["worker_pid"] == 321
    assert state["trainer"]["trainer_worker_pid"] == 321


def test_concurrent_attach_launches_only_one_worker(tmp_path: Path) -> None:
    first_popen_started = threading.Event()
    second_popen_started = threading.Event()
    release_first_popen = threading.Event()
    process = Mock(pid=321)

    def blocking_popen(*args, **kwargs):
        if first_popen_started.is_set():
            second_popen_started.set()
        else:
            first_popen_started.set()
        assert release_first_popen.wait(timeout=5.0)
        return process

    with (
        patch(
            "loopai.skills.Trainer.utils.persistent_worker.subprocess.Popen",
            side_effect=blocking_popen,
        ) as popen,
        patch(
            "loopai.skills.Trainer.utils.persistent_worker._pid_is_alive",
            side_effect=lambda pid: int(pid or 0) == 321,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(launch_or_attach_persistent_worker, _state(tmp_path))
        assert first_popen_started.wait(timeout=5.0)
        second = executor.submit(launch_or_attach_persistent_worker, _state(tmp_path))
        assert second_popen_started.wait(timeout=0.5) is False
        assert popen.call_count == 1
        release_first_popen.set()
        first_handle = first.result(timeout=5.0)
        second_handle = second.result(timeout=5.0)

    assert popen.call_count == 1
    assert first_handle.process is process
    assert second_handle.process is None
    assert read_run_state(first_handle.state_path)["worker_pid"] == 321


def test_completed_worker_result_is_reused_without_relaunch(tmp_path: Path) -> None:
    state = _state(tmp_path)
    paths = worker_paths(state["trainer"]["trainer_output_dir"])
    write_worker_result(paths["result"], {"ok": True, "state": state})

    with patch("loopai.skills.Trainer.utils.persistent_worker.subprocess.Popen") as popen:
        handle = launch_or_attach_persistent_worker(state)

    assert handle.result_path == paths["result"]
    popen.assert_not_called()


def test_verl_progress_prefers_latest_structured_metric(tmp_path: Path) -> None:
    log_path = tmp_path / "train.log"
    metrics_path = tmp_path / "verl_metrics.jsonl"
    log_path.write_text(
        "Training Progress:   2%|▏         | 1/58 [00:01<10:00, 10.00s/it]\r"
        "Training Progress:  16%|█▌        | 9/58 [02:00<08:00, 10.00s/it]\n",
        encoding="utf-8",
    )
    metrics_path.write_text(
        "\n".join(
            [
                json.dumps({"step": 20, "data": {"training/global_step": 20}}),
                json.dumps({"step": 21, "data": {"training/global_step": 21}}),
            ]
        ),
        encoding="utf-8",
    )

    progress = TrainingLogParser().parse_verl_metrics_progress(
        str(log_path),
        str(metrics_path),
    )

    assert progress["progress_text"] == "21/58"
    assert progress["time_text"] == "Verl metrics"


def test_tqdm_parser_uses_last_carriage_return_refresh(tmp_path: Path) -> None:
    log_path = tmp_path / "train.log"
    log_path.write_text(
        "Training Progress: | 1/58 [00:01<10:00, 10.00s/it]\r"
        "Training Progress: | 9/58 [02:00<08:00, 10.00s/it]\n",
        encoding="utf-8",
    )

    progress = TrainingLogParser().parse_training_progress(str(log_path))

    assert progress["progress_text"] == "9/58"


def test_worker_entry_persists_result_and_terminal_state(tmp_path: Path) -> None:
    state = _state(tmp_path)
    paths = worker_paths(state["trainer"]["trainer_output_dir"])
    paths["run_dir"].mkdir(parents=True, exist_ok=True)
    with paths["request"].open("wb") as request_file:
        pickle.dump({"state": state}, request_file)

    def fake_training(worker_state, writer=None):
        worker_state["trainer"]["trainer_training_success"] = True
        worker_state["trainer"]["trainer_training_final_status"] = {"status": "completed"}
        return worker_state

    with (
        patch(
            "loopai.skills.Trainer.nodes.training_execution_node._training_execution_inline",
            side_effect=fake_training,
        ),
        patch("loopai.skills.Trainer.runner.finalize_trainer_result_state", side_effect=lambda value, **_: value),
        patch("loopai.skills.Trainer.utils.stream_events.emit_trainer_event"),
    ):
        assert run_worker(paths["request"]) == 0

    assert not paths["request"].exists()
    persisted = read_run_state(paths["state"])
    assert persisted["status"] == "completed"
    with paths["result"].open("rb") as result_file:
        result = pickle.load(result_file)
    assert result["ok"] is True
    assert result["state"]["trainer"]["trainer_training_success"] is True


def test_worker_entry_preserves_cancelled_terminal_state(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["trainer"]["_trainer_worker_runtime"] = {
        "task_state_loaded": True,
        "thread_id": "task-1",
        "db_path": str(tmp_path / "task.db"),
    }
    paths = worker_paths(state["trainer"]["trainer_output_dir"])
    paths["run_dir"].mkdir(parents=True, exist_ok=True)
    with paths["request"].open("wb") as request_file:
        pickle.dump({"state": state}, request_file)

    def fake_cancelled_training(worker_state, writer=None):
        worker_state["trainer"]["trainer_training_success"] = False
        worker_state["trainer"]["trainer_training_final_status"] = {"status": "cancelled"}
        worker_state["trainer"]["train_output_training_error"] = "cancelled by user"
        return worker_state

    persisted = {}

    def fake_state_update(runtime, trainer_state):
        persisted["runtime"] = runtime
        persisted["result"] = trainer_state.get("trainer_result")
        persisted["last_error"] = trainer_state.get("trainer_last_error")

    with (
        patch(
            "loopai.skills.Trainer.nodes.training_execution_node._training_execution_inline",
            side_effect=fake_cancelled_training,
        ),
        patch("loopai.skills.Trainer.runner.finalize_trainer_result_state", side_effect=lambda value, **_: value),
        patch("loopai.skills.Trainer.runner._update_trainer_task_state", side_effect=fake_state_update),
        patch("loopai.skills.Trainer.utils.stream_events.emit_trainer_event"),
    ):
        assert run_worker(paths["request"]) == 0

    assert read_run_state(paths["state"])["status"] == "cancelled"
    result = wait_for_persistent_worker(
        PersistentWorkerHandle(
            result_path=paths["result"],
            state_path=paths["state"],
            run_dir=paths["run_dir"],
        ),
        poll_interval=0.01,
    )
    assert result["trainer"]["trainer_training_final_status"]["status"] == "cancelled"
    assert result["trainer"]["trainer_result"]["ok"] is False
    assert result["trainer"]["trainer_result"]["error"]["code"] == "INTERRUPTED"
    assert persisted["result"]["ok"] is False
    assert persisted["last_error"]["code"] == "INTERRUPTED"


def test_real_detached_worker_writes_failure_result_without_training(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state["trainer"]["trainer_config_generation_success"] = False

    handle = launch_or_attach_persistent_worker(state)
    result = wait_for_persistent_worker(handle, poll_interval=0.05)

    assert result["trainer"]["trainer_training_success"] is False
    assert "配置生成未成功" in result["trainer"]["train_output_training_error"]
    assert result["trainer"]["trainer_result"]["ok"] is False
    assert "配置生成未成功" in result["trainer"]["trainer_last_error"]["detail"]
    persisted = read_run_state(handle.state_path)
    assert persisted["status"] == "failed"
    assert handle.result_path.is_file()
    events = load_events(task_id="task-1", output_dir=str(tmp_path), version_id="version-1")
    assert events[-1]["status"] == "failed"
