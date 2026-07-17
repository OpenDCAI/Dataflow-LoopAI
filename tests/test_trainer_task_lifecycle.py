import inspect
import os
import signal
from unittest.mock import Mock, patch

from loopai.skills.Trainer.nodes.training_execution_node import training_execution_node
from loopai.skills.Trainer.utils.task_manager import TaskManager
from loopai.skills.Trainer.utils.task_status import TaskStatus


def _manager(tmp_path) -> TaskManager:
    return TaskManager(
        configs_dir=str(tmp_path / "configs"),
        logs_dir=str(tmp_path / "logs"),
        runs_dir=str(tmp_path / "runs"),
        app_config={"llamafactory_dir": str(tmp_path)},
    )


def test_llamafactory_uses_dedicated_process_group(tmp_path) -> None:
    manager = _manager(tmp_path)
    task_info = {
        "status": TaskStatus.RUNNING,
        "process": None,
        "process_group_id": None,
        "cancel_requested": False,
    }
    process = Mock(pid=43210)
    process.wait.return_value = 0
    log_parser = Mock()

    with patch(
        "loopai.skills.Trainer.utils.task_manager.subprocess.Popen",
        return_value=process,
    ) as popen:
        manager._run_llamafactory_training(
            task_info,
            str(tmp_path / "train.yaml"),
            str(tmp_path / "train.log"),
            log_parser,
        )

    assert popen.call_args.kwargs["start_new_session"] is (os.name == "posix")
    if os.name == "posix":
        assert task_info["process_group_id"] == process.pid
    assert task_info["status"] == TaskStatus.COMPLETED


def test_cancel_task_terminates_entire_process_group(tmp_path) -> None:
    manager = _manager(tmp_path)
    process = Mock(pid=43210)
    process.poll.return_value = None
    process.wait.return_value = 0
    manager.tasks["run-1"] = {
        "status": TaskStatus.RUNNING,
        "process": process,
        "process_group_id": process.pid if os.name == "posix" else None,
        "cancel_requested": False,
    }

    with patch("loopai.skills.Trainer.utils.task_manager.os.killpg") as killpg:
        assert manager.cancel_task("run-1") is True

    if os.name == "posix":
        killpg.assert_called_once_with(process.pid, signal.SIGTERM)
        process.terminate.assert_not_called()
    else:
        process.terminate.assert_called_once_with()
    assert manager.tasks["run-1"]["status"] == TaskStatus.CANCELLED


def test_wait_for_completion_joins_training_future(tmp_path) -> None:
    manager = _manager(tmp_path)
    future = Mock()
    task_info = {"status": TaskStatus.COMPLETED, "future": future}
    manager.tasks["run-1"] = task_info

    assert manager.wait_for_completion("run-1") is task_info
    future.result.assert_called_once_with()


def test_training_node_has_no_fixed_one_hour_early_exit() -> None:
    source = inspect.getsource(training_execution_node)

    assert "max_wait_time" not in source
    assert "wait_for_completion" in source
