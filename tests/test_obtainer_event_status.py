from __future__ import annotations

from pathlib import Path

import pytest

from api.app.services.starter.stream_events import build_custom_info
from loopai.common.event_tool import get_event_writer
from loopai.skills.ObtainerCLI.events import emit_obtainer_event


class _RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def set_running(self, event: object) -> None:
        self.calls.append(("running", event))

    def set_completed(self, event: object) -> None:
        self.calls.append(("completed", event))

    def set_failed(self, event: object) -> None:
        self.calls.append(("failed", event))


@pytest.mark.parametrize(
    ("status", "expected_method"),
    [
        ("started", "running"),
        ("running", "running"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("interrupted", "failed"),
    ],
)
def test_emit_obtainer_event_updates_runtime_status(status: str, expected_method: str) -> None:
    writer = _RecordingWriter()

    emit_obtainer_event(
        writer,
        node="dm.status",
        status=status,
        message="status update",
    )

    assert len(writer.calls) == 1
    method, event = writer.calls[0]
    assert method == expected_method
    assert event.current == "obtainercli"
    assert event.node == "dm.status"


def test_build_custom_info_loads_obtainercli_runtime_version(monkeypatch, tmp_path: Path) -> None:
    task_id = "task-obtainer"
    writer = get_event_writer(
        name="obtainercli",
        context_id=task_id,
        log_file_path=str(tmp_path),
        version_id="obtainer-run-1",
    )
    writer.set_running({"current": "obtainercli", "message": "acquiring datasets"})
    monkeypatch.setattr(
        "api.app.services.starter.stream_events._latest_runtime_versions",
        lambda _task_id: {"obtainercli": "obtainer-run-1"},
    )

    custom_info = build_custom_info(
        task_id=task_id,
        state={"task_id": task_id, "output_dir": str(tmp_path)},
        default_output_dir=tmp_path,
        project_root_dir=tmp_path,
    )

    payload = custom_info["obtainercli.obtainercli"]
    assert payload["version_id"] == "obtainer-run-1"
    assert payload["status"] == "running"
    assert payload["message"] == "acquiring datasets"
