from __future__ import annotations

import sys
import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loopai.schema.states import get_state_config_schema
from loopai.schema.system_runtime import (
    migrate_legacy_credentials,
    resolve_integration_value,
    resolve_runtime_model_config,
)
from loopai.skills.WebCrawler.runtime_config import resolve_webcrawler_runtime_config
from api.app.utils.config.credential_migration import migrate_persisted_credentials
from loopai.agents.Constructor import runtime_config as constructor_runtime


def _system() -> dict:
    return {
        "api_port": 8855,
        "model": {
            "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
            "proxy_api_key": "proxy-key",
            "default_tier": "medium",
            "pool": [
                {
                    "tier": "medium",
                    "name": "crawler",
                    "model_name": "upstream-model",
                    "base_url": "http://upstream.example/v1",
                    "api_key": "upstream-key",
                    "wire_api": "chat",
                }
            ],
        },
        "integrations": {
            "tavily": {"api_key": "tavily-key"},
            "kaggle": {"username": "user", "key": "kaggle-key"},
            "rag": {"base_url": "http://rag.example/v1", "api_key": "rag-key"},
        },
    }


def test_state_schema_excludes_runtime_credentials_but_keeps_trainer_key():
    schema = get_state_config_schema("en")

    assert "api_key" not in schema["obtainer"]
    assert "tavily_api_key" not in schema["obtainer"]
    assert "rag_api_key" not in schema["obtainer"]
    assert "kaggle_key" not in schema["obtainer"]
    assert "api_key" not in schema["constructor"]
    assert "deepseek_api_key" not in schema["webcrawler"]
    assert "tavily_api_key" not in schema["webcrawler"]
    assert "swanlab_api_key" in schema["trainer"]


def test_migrate_legacy_credentials_moves_integrations_and_leaves_trainer():
    config = {
        "system": {
            "tavily_api_key": "tavily-key",
            "kaggle_username": "user",
            "kaggle_key": "kaggle-key",
        },
        "default_states": {
            "obtainer": {
                "api_key": "model-key",
                "tavily_api_key": "old-tavily",
                "rag_api_key": "rag-key",
                "kaggle_username": "old-user",
                "kaggle_key": "old-key",
            },
            "constructor": {"api_key": "constructor-key"},
            "webcrawler": {
                "deepseek_api_key": "deepseek-key",
                "tavily_api_key": "crawler-tavily",
            },
            "trainer": {"swanlab_api_key": "swanlab-key"},
        },
    }

    migrate_legacy_credentials(config)

    assert config["system"]["integrations"] == {
        "tavily": {"api_key": "tavily-key"},
        "kaggle": {"username": "user", "key": "kaggle-key"},
        "rag": {"base_url": "", "api_key": "rag-key"},
    }
    assert "tavily_api_key" not in config["system"]
    assert "api_key" not in config["default_states"]["obtainer"]
    assert "api_key" not in config["default_states"]["constructor"]
    assert "deepseek_api_key" not in config["default_states"]["webcrawler"]
    assert config["default_states"]["trainer"]["swanlab_api_key"] == "swanlab-key"


def test_nested_integration_supports_environment_references(monkeypatch):
    monkeypatch.setenv("TEST_TAVILY_KEY", "from-env")
    system = {"integrations": {"tavily": {"api_key": "env:TEST_TAVILY_KEY"}}}

    assert resolve_integration_value(system, "tavily", "api_key") == "from-env"


def test_runtime_model_uses_model_pool_proxy():
    resolved = resolve_runtime_model_config(_system(), requested="crawler")

    assert resolved.model == "crawler"
    assert resolved.base_url == "http://127.0.0.1:8855/responseProxy/v1"
    assert resolved.api_key == "proxy-key"
    assert resolved.source == "system.model.pool"


def test_unknown_runtime_model_falls_back_to_resolved_pool_alias():
    resolved = resolve_runtime_model_config(_system(), requested="unknown-model")

    assert resolved.model == "crawler"
    assert resolved.base_url == "http://127.0.0.1:8855/responseProxy/v1"


def test_constructor_resolves_model_key_without_writing_it_to_state(monkeypatch):
    state = {"task_id": "task-1", "constructor": {"model_path": "crawler"}}
    monkeypatch.setattr(
        constructor_runtime,
        "load_runtime_system_config",
        lambda **_kwargs: _system(),
    )

    resolved = constructor_runtime.resolve_constructor_model_config(state)

    assert resolved.api_key == "proxy-key"
    assert "api_key" not in state["constructor"]


def test_webcrawler_resolves_system_credentials_without_storing_them(monkeypatch):
    # setenv records restoration even though runtime code mutates os.environ.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    state = {
        "task_id": "webcrawler-default",
        "webcrawler": {"model": "crawler", "temperature": 0.2},
    }

    result = resolve_webcrawler_runtime_config(state, system_config=_system())

    assert result["required_fields_ok"] is True
    assert state["webcrawler"]["model"] == "crawler"
    assert state["webcrawler"]["deepseek_api_base"] == "http://127.0.0.1:8855/responseProxy/v1"
    assert "deepseek_api_key" not in state["webcrawler"]
    assert "tavily_api_key" not in state["webcrawler"]
    assert "missing_required_fields" in result["prefill_guide"]
    assert result["prefill_guide"]["missing_required_fields"] == []
    assert result["prefill_guide"]["missing_system_fields"] == []
    assert __import__("os").environ["DEEPSEEK_API_KEY"] == "proxy-key"
    assert __import__("os").environ["TAVILY_API_KEY"] == "tavily-key"


def test_sqlite_migration_removes_task_credentials_and_keeps_trainer(tmp_path):
    db_path = tmp_path / "config.sqlite3"
    config = {
        "system": {"tavily_api_key": "tavily-key"},
        "default_states": {
            "obtainer": {"api_key": "model-key", "tavily_api_key": "old-key"},
            "trainer": {"swanlab_api_key": "swanlab-key"},
        },
    }
    state = {
        "obtainer": {"api_key": "model-key", "tavily_api_key": "old-key"},
        "webcrawler": {"deepseek_api_key": "deepseek-key"},
        "trainer": {"swanlab_api_key": "swanlab-key"},
    }
    con = sqlite3.connect(db_path)
    con.execute("create table starterconfig (id integer primary key, config text)")
    con.execute("create table taskmodel (id integer primary key, config text, state text)")
    con.execute("insert into starterconfig values (?, ?)", (1, json.dumps(config)))
    con.execute("insert into taskmodel values (?, ?, ?)", (1, json.dumps(config), json.dumps(state)))
    con.commit()
    con.close()

    result = migrate_persisted_credentials(db_path)

    assert result == {"starter_rows": 1, "task_config_rows": 1, "task_state_rows": 1}
    con = sqlite3.connect(db_path)
    starter = json.loads(con.execute("select config from starterconfig").fetchone()[0])
    task_config, task_state = con.execute("select config, state from taskmodel").fetchone()
    con.close()
    task_config = json.loads(task_config)
    task_state = json.loads(task_state)
    assert starter["system"]["integrations"]["tavily"]["api_key"] == "tavily-key"
    assert "api_key" not in task_config["default_states"]["obtainer"]
    assert "api_key" not in task_state["obtainer"]
    assert "deepseek_api_key" not in task_state["webcrawler"]
    assert task_state["trainer"]["swanlab_api_key"] == "swanlab-key"
