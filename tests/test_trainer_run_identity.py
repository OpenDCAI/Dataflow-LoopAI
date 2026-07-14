from pathlib import Path

from api.app.services.starter.stream_events import build_custom_info
from loopai.common.event_tool import get_event_writer
from loopai.skills.Trainer.utils.stream_events import (
    emit_trainer_event,
    prepare_trainer_run,
)
from loopai.skills.Trainer import load_events


TRAINER_ID_FIELDS = (
    "trainer_version_id",
    "trainer_task_id",
    "trainer_training_task_id",
    "training_task_id",
)


def _state(tmp_path: Path) -> dict:
    return {
        "task_id": "task-1",
        "output_dir": str(tmp_path),
        "trainer": {},
    }


def _assert_unified_ids(state: dict, expected: str) -> None:
    trainer_state = state["trainer"]
    assert all(trainer_state[field] == expected for field in TRAINER_ID_FIELDS)


def test_prepare_trainer_run_uses_writer_version_as_canonical_id(tmp_path: Path) -> None:
    state = _state(tmp_path)

    writer = prepare_trainer_run(state, force_new=True)
    version_id = str(writer.version_id)

    _assert_unified_ids(state, version_id)
    expected_dir = tmp_path / "task-1" / "trainer" / version_id
    assert Path(state["trainer"]["trainer_output_dir"]) == expected_dir
    assert writer.event_path == tmp_path / "task-1" / "trainer.pkl"


def test_prepare_trainer_run_reuses_canonical_id_within_one_run(tmp_path: Path) -> None:
    state = _state(tmp_path)
    first_writer = prepare_trainer_run(state, force_new=True)

    second_writer = prepare_trainer_run(state)

    assert second_writer.version_id == first_writer.version_id
    _assert_unified_ids(state, str(first_writer.version_id))


def test_prepare_trainer_run_can_start_a_new_version(tmp_path: Path) -> None:
    state = _state(tmp_path)
    first_version = str(prepare_trainer_run(state, force_new=True).version_id)

    second_version = str(prepare_trainer_run(state, force_new=True).version_id)

    assert second_version != first_version
    _assert_unified_ids(state, second_version)


def test_first_persisted_event_gets_the_same_canonical_id(tmp_path: Path) -> None:
    state = _state(tmp_path)

    event = emit_trainer_event(
        None,
        state,
        "field_validation",
        "running",
        message="starting",
    )

    version_id = str(event.version_id)
    _assert_unified_ids(state, version_id)
    event_path = Path(state["trainer"]["trainer_event_log_path"])
    assert event_path == tmp_path / "task-1" / "trainer.pkl"
    assert event_path.is_file()

    events = load_events(task_id="task-1", output_dir=str(tmp_path))
    assert events[-1]["version_id"] == version_id

    custom_info = build_custom_info(
        task_id="task-1",
        state=state,
        default_output_dir=tmp_path,
        project_root_dir=tmp_path,
    )
    assert custom_info["trainer.trainer"]["version_id"] == version_id


def test_status_reader_supports_legacy_versioned_event_path(tmp_path: Path) -> None:
    state = _state(tmp_path)
    legacy_dir = tmp_path / "task-1" / "trainer" / "legacy-version"
    state["trainer"]["trainer_output_dir"] = str(legacy_dir)
    legacy_writer = get_event_writer(
        name="trainer",
        context_id="task-1",
        log_file_path=str(tmp_path),
        version_id="legacy-version",
        event_dir=legacy_dir,
    )
    legacy_writer.set_running({"current": "trainer.run", "message": "legacy"})

    custom_info = build_custom_info(
        task_id="task-1",
        state=state,
        default_output_dir=tmp_path,
        project_root_dir=tmp_path,
    )

    assert custom_info["trainer.trainer.run"]["version_id"] == "legacy-version"
