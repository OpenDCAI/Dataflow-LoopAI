import asyncio
import json

from loopai.schema.states import get_state_config_schema
from loopai.skills.Looper import runner
from loopai.skills.Looper.runner import parse_looper_command


def test_parse_looper_command_query():
    payload = parse_looper_command('{"op": "query", "message": "continue and run tests"}')
    assert payload == {"op": "query", "message": "continue and run tests"}


def test_parse_looper_command_stop():
    payload = parse_looper_command({"op": "stop", "message": "ignored"})
    assert payload == {"op": "stop"}


def test_parse_looper_command_rejects_empty_query_message():
    try:
        parse_looper_command('{"op": "query", "message": ""}')
    except ValueError as exc:
        assert "non-empty message" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_conversation_since_last_id_returns_increment_only():
    conversation = [
        {"id": "a", "role": "user", "content": "step 1"},
        {"id": "b", "role": "assistant", "content": "step 2"},
        {"id": "c", "role": "user", "content": "step 3"},
    ]

    new_items, found = runner._conversation_since_last_id(conversation, "b")

    assert found is True
    assert new_items == [{"id": "c", "role": "user", "content": "step 3"}]


def test_summarize_codex_progress_reuses_previous_summary_when_no_new_messages():
    async def _run() -> None:
        summary, last_conv_id = await runner.summarize_codex_progress(
            config=runner.LooperModelConfig(model="x", api_url="http://example.com", api_key="", wire_api="chat"),
            task_id="task-1",
            state={"looper": {"historySummary": "cached summary", "last_conv_id": "conv-2"}},
            session={"status": "completed"},
            conversation=[
                {"id": "conv-1", "role": "user", "content": "start"},
                {"id": "conv-2", "role": "assistant", "content": "done"},
            ],
            runtime_status=None,
        )

        assert summary == "cached summary"
        assert last_conv_id == "conv-2"

    asyncio.run(_run())


def test_summarize_codex_progress_uses_incremental_window(monkeypatch):
    captured = {}

    async def fake_llm_text(config, messages, *, json_mode=False):
        captured["json_mode"] = json_mode
        captured["messages"] = messages
        return "incremental summary"

    monkeypatch.setattr(runner, "_llm_text", fake_llm_text)

    async def _run() -> None:
        summary, last_conv_id = await runner.summarize_codex_progress(
            config=runner.LooperModelConfig(model="x", api_url="http://example.com", api_key="", wire_api="chat"),
            task_id="task-2",
            state={"looper": {"historySummary": "old summary", "last_conv_id": "conv-2"}},
            session={"status": "running"},
            conversation=[
                {"id": "conv-1", "role": "user", "content": "start"},
                {"id": "conv-2", "role": "assistant", "content": "done"},
                {"id": "conv-3", "role": "user", "content": "new ask"},
            ],
            runtime_status=[],
        )

        assert summary == "incremental summary"
        assert last_conv_id == "conv-3"

    asyncio.run(_run())

    payload = json.loads(captured["messages"][1]["content"])
    assert payload["summary_mode"] == "incremental"
    assert payload["previous_history_summary"] == "old summary"
    assert "new ask" in payload["conversation_excerpt"]
    assert "start" not in payload["conversation_excerpt"]


def test_llm_json_retries_once_after_invalid_output(monkeypatch):
    calls = []

    async def fake_llm_text(config, messages, *, json_mode=False):
        calls.append({"json_mode": json_mode, "messages": messages})
        if len(calls) == 1:
            return '{"op":"query","message":"bad\\path"}'
        return '{"op":"query","message":"fixed path"}'

    monkeypatch.setattr(runner, "_llm_text", fake_llm_text)

    async def _run() -> None:
        payload = await runner._llm_json(
            runner.LooperModelConfig(model="x", api_url="http://example.com", api_key="", wire_api="chat"),
            [{"role": "system", "content": "return json"}],
        )
        assert payload == {"op": "query", "message": "fixed path"}

    asyncio.run(_run())

    assert len(calls) == 2
    retry_messages = calls[1]["messages"]
    assert retry_messages[-2]["role"] == "assistant"
    assert 'bad\\path' in retry_messages[-2]["content"]
    assert retry_messages[-1]["role"] == "user"
    assert "输出解析失败" in retry_messages[-1]["content"]
    assert "严格遵守以下要求" in retry_messages[-1]["content"]


def test_build_planner_user_prompt_uses_compact_sections():
    prompt = runner._build_planner_user_prompt(
        task_id="task-compact",
        state={
            "task_id": "task-compact",
            "language": "zh",
            "judger": {"eval_task_type": "code"},
            "looper": {
                "messages": [{"role": "user", "content": "very noisy"}],
                "historySummary": "previous summary",
                "last_conv_id": "conv-9",
                "command": '{"op":"query","message":"continue"}',
            },
        },
        session={
            "status": "completed",
            "active_prompt": "run analyzer",
            "pending_prompts": ["a", "b"],
            "last_error": "",
            "final_result": {
                "finalResponse": "done",
                "items": [
                    {"type": "reasoning", "text": "thought"},
                    {"type": "command_execution", "aggregated_output": "output"},
                ],
            },
        },
        conversation=[{"role": "assistant", "content": "latest reply"}],
        runtime_status=[{"node": "judger", "status": "completed"}],
        machine_env={"host": {"hostname": "demo"}},
        latest_history_summary="fresh summary",
    )

    payload = json.loads(prompt)
    assert payload["session"]["status"] == "completed"
    assert payload["session"]["pending_prompt_count"] == 2
    assert payload["session"]["final_result"]["items_count"] == 2
    assert payload["looper_context"]["latest_codex_history_summary"] == "fresh summary"
    assert payload["relevant_state"]["looper"]["last_conv_id"] == "conv-9"
    assert "messages" not in payload["relevant_state"]["looper"]
    assert payload["recent_conversation"].startswith("[1] role=assistant")
    assert "state_snapshot" not in payload
    assert "latest_conversation_excerpt" not in payload


def test_looper_schema_is_exposed():
    schema = get_state_config_schema()
    assert "looper" in schema
    assert "command" in schema["looper"]
    assert "historySummary" in schema["looper"]
    assert "last_conv_id" in schema["looper"]
