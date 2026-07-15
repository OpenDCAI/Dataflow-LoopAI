import json

from loopai.schema.states import get_state_config_schema
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


def test_looper_schema_is_exposed():
    schema = get_state_config_schema()
    assert "looper" in schema
    assert "command" in schema["looper"]
    assert "historySummary" in schema["looper"]
