import asyncio

from api.app.controllers import starter
from loopai.common.exception import ErrorCode
from loopai.skills.Looper import runner


def test_run_looper_once_emits_interrupted_error_on_cancellation(monkeypatch):
    captured = {}

    class DummyWriter:
        def set_running(self, payload):
            captured.setdefault("running", []).append(payload)

        def set_failed(self, payload):
            captured["failed_payload"] = payload

        def set_completed(self, payload):
            captured["completed_payload"] = payload

    async def fake_summarize_codex_progress(**kwargs):
        raise asyncio.CancelledError("Interrupted by user.")

    def fake_emit_error(exc, **kwargs):
        captured["emit_error"] = {
            "exc": exc,
            "kwargs": kwargs,
        }
        return {"status": "failed"}

    monkeypatch.setattr(runner, "get_event_writer", lambda *args, **kwargs: DummyWriter())
    monkeypatch.setattr(runner, "summarize_codex_progress", fake_summarize_codex_progress)
    monkeypatch.setattr(runner, "emit_error", fake_emit_error)

    async def _run() -> None:
        try:
            await runner.run_looper_once(
                task_id="task-cancel",
                state={"task_id": "task-cancel", "looper": {}},
                system_config={},
                conversation=[],
                session={},
                runtime_status=[],
            )
        except asyncio.CancelledError as exc:
            assert str(exc) == "Interrupted by user."
        else:
            raise AssertionError("expected CancelledError")

    asyncio.run(_run())

    assert captured["emit_error"]["kwargs"]["code"] == ErrorCode.INTERRUPTED
    assert captured["emit_error"]["kwargs"]["recoverable"] is False
    assert captured["emit_error"]["kwargs"]["message"] == "Looper planning pass interrupted."


def test_looper_terminate_endpoint_cancels_registered_task():
    async def sleeper():
        await asyncio.sleep(10)

    async def _run() -> None:
        task = asyncio.create_task(sleeper())
        starter.looper_run_store.set_task("session-1", task)

        response = await starter.starter_codex_session_looper_terminate("session-1")
        assert response["message"] == "Looper termination requested"
        assert response["data"]["status"] == "terminating"

        await asyncio.sleep(0)
        assert task.cancelled() is True
        starter.looper_run_store.clear_task("session-1")

    asyncio.run(_run())


def test_looper_endpoint_rejects_parallel_run(monkeypatch):
    class FakeTaskModel:
        @staticmethod
        async def get_or_none(task_id):
            return object()

    async def sleeper():
        await asyncio.sleep(10)

    async def _run() -> None:
        task = asyncio.create_task(sleeper())
        starter.looper_run_store.set_task("session-2", task)
        monkeypatch.setattr(starter, "TaskModel", FakeTaskModel)

        response = await starter.starter_codex_session_looper("session-2")
        assert response["code"] == 409
        assert response["message"] == "Looper is still running"

        task.cancel()
        await asyncio.sleep(0)
        starter.looper_run_store.clear_task("session-2")

    asyncio.run(_run())
