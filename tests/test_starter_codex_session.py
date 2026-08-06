from __future__ import annotations

import asyncio

from api.app.services.starter.codex import CodexSessionStore, CodexStarterService


def test_submit_queues_when_execution_is_active_even_if_status_is_stale(monkeypatch):
    async def exercise():
        store = CodexSessionStore()
        store.create("first", "/tmp", session_id="task-1")
        store.update("task-1", status="failed")
        release = asyncio.Event()
        active_task = asyncio.create_task(release.wait())
        store.set_task("task-1", active_task)
        service = CodexStarterService(system_config={}, session_store=store)

        async def no_snapshot(_session_id):
            return store.get("task-1")

        async def no_persist(_session_id):
            return None

        monkeypatch.setattr(service, "restore_session_snapshot", no_snapshot)
        monkeypatch.setattr(service, "persist_session_snapshot", no_persist)
        try:
            result = await service.submit("second", "/tmp", session_id="task-1")
            assert result["type"] == "queued"
            assert store.get("task-1")["pending_prompts"][0]["prompt"] == "second"
        finally:
            release.set()
            await active_task

    asyncio.run(exercise())


def test_stale_task_cannot_clear_newer_task():
    async def exercise():
        store = CodexSessionStore()
        release = asyncio.Event()
        older = asyncio.create_task(release.wait())
        newer = asyncio.create_task(release.wait())
        store.set_task("task-1", newer)
        assert store.clear_task("task-1", expected=older) is None
        assert store.get_task("task-1") is newer
        release.set()
        await asyncio.gather(older, newer)

    asyncio.run(exercise())
