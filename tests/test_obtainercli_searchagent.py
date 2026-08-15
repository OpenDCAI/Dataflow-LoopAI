from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_searchagent_returns_download_candidates(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    class FakeMethodDecisionAgent:
        async def decide_method_order(self, **kwargs):
            return {
                "method_order": ["huggingface"],
                "keywords_for_hf": ["gsm8k"],
                "reasoning": "test",
            }

    class FakeHfManager:
        async def search_datasets(self, keywords, max_results=5):
            return {
                "gsm8k": [
                    {
                        "id": "openai/gsm8k",
                        "title": "GSM8K",
                        "description": "math word problems",
                        "downloads": 100,
                        "tags": ["math"],
                    }
                ]
            }

    monkeypatch.setattr(searchagent, "_make_method_decision_agent", lambda **kwargs: FakeMethodDecisionAgent())
    monkeypatch.setattr(searchagent, "_make_hf_manager", lambda: FakeHfManager())

    result = searchagent.run_searchagent(
        query="Analyzer report: need math reasoning datasets",
        objective="collect math reasoning data",
        keywords="math reasoning,gsm8k",
        output_root=tmp_path,
        model_name="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        deepsearch=False,
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert manifest["download_list"][0]["dataset_id"] == "openai/gsm8k"
    assert "selected" not in manifest["download_list"][0]
    assert manifest["download_list"][0]["download"] == {
        "method": "huggingface",
        "dataset_id": "openai/gsm8k",
    }
    assert manifest["download_list"] == manifest["candidates"]


def test_searchagent_relaxes_zero_result_huggingface_phrases(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    class FakeMethodDecisionAgent:
        async def decide_method_order(self, **kwargs):
            return {
                "method_order": ["huggingface"],
                "keywords_for_hf": ["finance QA Chinese"],
                "reasoning": "test",
            }

    class FakeHfManager:
        calls = []

        async def search_datasets(self, keywords, max_results=5):
            self.calls.append((list(keywords), max_results))
            if "finance QA" not in keywords:
                return {keyword: [] for keyword in keywords}
            return {
                "finance QA": [
                    {
                        "id": "example/finance-qa",
                        "title": "Finance QA",
                        "description": "finance questions and answers",
                    }
                ]
            }

    manager = FakeHfManager()
    monkeypatch.setattr(searchagent, "_make_method_decision_agent", lambda **kwargs: FakeMethodDecisionAgent())
    monkeypatch.setattr(searchagent, "_make_hf_manager", lambda: manager)

    result = searchagent.run_searchagent(
        query="collect Chinese finance QA",
        objective="collect Chinese finance QA",
        output_root=tmp_path,
        model_name="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        deepsearch=False,
    )

    assert result["ok"] is True
    assert len(manager.calls) == 2
    assert manager.calls[1][1] == 5
    assert result["candidates"][0]["dataset_id"] == "example/finance-qa"
    assert result["candidates"][0]["fallback_reason"] == "huggingface_zero_results_relaxed_keywords"


def test_searchagent_uses_starter_model_defaults(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    monkeypatch.delenv("OBTAINER_MODEL", raising=False)
    monkeypatch.delenv("OBTAINER_BASE_URL", raising=False)
    monkeypatch.delenv("OBTAINER_API_KEY", raising=False)
    starter = tmp_path / "starter.yaml"
    starter.write_text(
        """
system:
  codex_model: codex-model
  codex_base_url: http://codex.example/v1
  codex_api_key: codex-key
  tavily_api_key: tavily-key
default_states:
  prompt_template_dir: ./loopai/common/prompts
  output_dir: ./outputs
  obtainer:
    temperature: 0.3
    search_engine: duckduckgo
    max_urls: 4
""",
        encoding="utf-8",
    )
    captured = {}

    async def fake_run_async(**kwargs):
        captured.update(kwargs)
        return [{"objective": "x", "search_keywords": ["x"], "decision": {}, "candidates": [], "errors": []}]

    monkeypatch.setattr(searchagent, "_run_searchagent_async", fake_run_async)

    searchagent.run_searchagent(
        query="collect reasoning data",
        output_root=tmp_path,
        starter_config=starter,
        deepsearch=False,
    )

    assert captured["model_name"] == "codex-model"
    assert captured["base_url"] == "http://codex.example/v1"
    assert captured["api_key"] == "codex-key"
    assert captured["search_engine"] == "duckduckgo"
    assert captured["max_urls"] == 4


def test_searchagent_uses_nested_system_integrations(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    starter = tmp_path / "starter.yaml"
    starter.write_text(
        """
system:
  codex_model: codex-model
  codex_base_url: http://codex.example/v1
  codex_api_key: codex-key
  integrations:
    tavily:
      api_key: nested-tavily-key
    kaggle:
      username: nested-user
      key: nested-kaggle-key
default_states:
  obtainer:
    search_engine: tavily
""",
        encoding="utf-8",
    )
    captured = {}

    async def fake_run_async(**kwargs):
        captured.update(kwargs)
        return [{"objective": "x", "search_keywords": ["x"], "decision": {}, "candidates": [], "errors": []}]

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    monkeypatch.setattr(searchagent, "_run_searchagent_async", fake_run_async)

    searchagent.run_searchagent(
        query="collect reasoning data",
        output_root=tmp_path,
        starter_config=starter,
        deepsearch=False,
    )

    assert captured["tavily_api_key"] == "nested-tavily-key"
    assert captured["kaggle_username"] == "nested-user"
    assert captured["kaggle_key"] == "nested-kaggle-key"


def test_searchagent_starter_model_overrides_env_defaults(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    starter = tmp_path / "starter.yaml"
    starter.write_text(
        """
system:
  codex_model: codex-model
  codex_base_url: http://codex.example/v1
  codex_api_key: codex-key
""",
        encoding="utf-8",
    )
    captured = {}

    async def fake_run_async(**kwargs):
        captured.update(kwargs)
        return [{"objective": "x", "search_keywords": ["x"], "decision": {}, "candidates": [], "errors": []}]

    monkeypatch.setenv("OBTAINER_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("OBTAINER_BASE_URL", "http://proxy.example/v1")
    monkeypatch.setenv("OBTAINER_API_KEY", "proxy-key")
    monkeypatch.setattr(searchagent, "_run_searchagent_async", fake_run_async)

    searchagent.run_searchagent(
        query="collect reasoning data",
        output_root=tmp_path,
        starter_config=starter,
        deepsearch=False,
    )

    assert captured["model_name"] == "codex-model"
    assert captured["base_url"] == "http://codex.example/v1"
    assert captured["api_key"] == "codex-key"


def test_searchagent_uses_nested_codex_default_not_default_tier(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    starter = tmp_path / "starter.yaml"
    starter.write_text(
        """
system:
  model:
    proxy_base_url: http://127.0.0.1:8855/responseProxy/v1
    proxy_api_key: proxy-key
    default_model: local-qwen
    default_tier: medium
    codex_model: deepseek-chat
    pool:
      - tier: medium
        name: local-qwen
        model_name: qwen3-14b-fp8
        base_url: http://127.0.0.1:8000/v1
      - tier: high
        name: deepseek-chat
        model_name: deepseek-chat
        base_url: https://api.deepseek.com/v1
        api_key: deepseek-key
""",
        encoding="utf-8",
    )
    captured = {}

    async def fake_run_async(**kwargs):
        captured.update(kwargs)
        return [{"objective": "x", "search_keywords": ["x"], "decision": {}, "candidates": [], "errors": []}]

    monkeypatch.setattr(searchagent, "_run_searchagent_async", fake_run_async)
    searchagent.run_searchagent(
        query="collect finance data",
        output_root=tmp_path,
        starter_config=starter,
        deepsearch=False,
    )

    assert captured["model_name"] == "deepseek-chat"
    assert captured["base_url"] == "http://127.0.0.1:8855/responseProxy/v1"
    assert captured["api_key"] == "proxy-key"


def test_searchagent_rejects_legacy_environment_model_without_codex_config(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent
    from loopai.skills.ObtainerCLI.errors import ObtainerCliError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OBTAINER_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("OBTAINER_BASE_URL", "http://proxy.example/v1")
    monkeypatch.setenv("OBTAINER_API_KEY", "proxy-key")
    try:
        searchagent.run_searchagent(
            query="collect reasoning data",
            output_root=tmp_path,
            deepsearch=False,
        )
    except ObtainerCliError as exc:
        assert exc.error_code == "MISSING_MODEL_CONFIG"
    else:
        raise AssertionError("legacy OBTAINER_* variables must not become the default model")


def test_searchagent_deepsearch_enriches_provider_keywords(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    class FakeMethodDecisionAgent:
        async def decide_method_order(self, **kwargs):
            return {
                "method_order": ["huggingface", "web"],
                "keywords_for_hf": kwargs["search_keywords"],
                "reasoning": "test",
            }

    class FakeHfManager:
        async def search_datasets(self, keywords, max_results=5):
            assert "gsm8k benchmark" in keywords
            assert "math reasoning" in keywords
            return {
                "gsm8k benchmark": [
                    {
                        "id": "openai/gsm8k",
                        "title": "GSM8K",
                        "description": "math word problems",
                        "downloads": 100,
                        "tags": ["math"],
                    }
                ]
            }

    async def fake_deepsearch(**kwargs):
        return {
            "queries": ["math reasoning datasets"],
            "urls": ["https://example.test/math"],
            "pages": [],
            "summary": "GSM8K is a common math word problem benchmark.",
            "derived_tasks": [
                {
                    "type": "download",
                    "objective": "collect GSM8K-like data",
                    "search_keywords": ["gsm8k benchmark", "math word problems"],
                }
            ],
            "derived_keywords": ["gsm8k benchmark", "math word problems"],
            "errors": [],
        }

    monkeypatch.setattr(searchagent, "_make_method_decision_agent", lambda **kwargs: FakeMethodDecisionAgent())
    monkeypatch.setattr(searchagent, "_make_hf_manager", lambda: FakeHfManager())
    monkeypatch.setattr(searchagent, "_run_deepsearch", fake_deepsearch)

    result = searchagent.run_searchagent(
        query="need math reasoning datasets",
        objective="collect math reasoning data",
        keywords="math reasoning",
        output_root=tmp_path,
        model_name="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        deepsearch=True,
    )

    task = result["tasks"][0]
    assert task["deepsearch"]["summary"].startswith("GSM8K")
    assert "gsm8k benchmark" in task["enriched_search_keywords"]
    assert result["download_list"][0]["dataset_id"] == "openai/gsm8k"


def test_searchagent_runs_duckduckgo_deepsearch_with_structured_fallback(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    class FakeMethodDecisionAgent:
        async def decide_method_order(self, **kwargs):
            return {
                "method_order": ["huggingface"],
                "keywords_for_hf": kwargs["search_keywords"],
                "reasoning": "provider fallback",
            }

    class FakeHfManager:
        async def search_datasets(self, keywords, max_results=5):
            return {
                keywords[0]: [
                    {
                        "id": "nvidia/Nemotron-Agentic-v1",
                        "title": "Nemotron Agentic",
                        "description": "tool use data",
                    }
                ]
            }

    async def fake_deepsearch(**kwargs):
        return {
            "queries": ["agentic tool use datasets"],
            "urls": ["https://example.test/agentic"],
            "pages": [],
            "summary": "Structured HTML search returned a usable result.",
            "derived_tasks": [],
            "derived_keywords": ["agentic dataset"],
            "errors": [],
        }

    monkeypatch.setattr(searchagent, "_make_method_decision_agent", lambda **kwargs: FakeMethodDecisionAgent())
    monkeypatch.setattr(searchagent, "_make_hf_manager", lambda: FakeHfManager())
    monkeypatch.setattr(searchagent, "_run_deepsearch", fake_deepsearch)

    result = searchagent.run_searchagent(
        query="need agentic tool use datasets",
        objective="collect tool use data",
        keywords="agent,tool use",
        output_root=tmp_path,
        model_name="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        search_engine="duckduckgo",
        deepsearch=True,
    )

    task = result["tasks"][0]
    assert task["deepsearch"]["skipped"] is False
    assert task["deepsearch"]["summary"].startswith("Structured HTML search")
    assert result["download_list"][0]["dataset_id"] == "nvidia/Nemotron-Agentic-v1"


def test_searchagent_falls_back_to_kaggle_when_decision_provider_has_no_candidates(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    class FakeMethodDecisionAgent:
        async def decide_method_order(self, **kwargs):
            return {
                "method_order": ["huggingface"],
                "keywords_for_hf": ["finance qa"],
                "reasoning": "try hf first",
            }

    class FakeHfManager:
        async def search_datasets(self, keywords, max_results=5):
            return {keyword: [] for keyword in keywords}

    class FakeKaggleManager:
        async def search_datasets(self, keywords, max_results=5):
            return {
                keywords[0]: [
                    {
                        "id": "owner/finance-qa",
                        "title": "Finance QA",
                        "description": "financial question answering",
                        "url": "https://www.kaggle.com/datasets/owner/finance-qa",
                    }
                ]
            }

    monkeypatch.setattr(searchagent, "_make_method_decision_agent", lambda **kwargs: FakeMethodDecisionAgent())
    monkeypatch.setattr(searchagent, "_make_hf_manager", lambda: FakeHfManager())
    monkeypatch.setattr(searchagent, "_make_kaggle_manager", lambda **kwargs: FakeKaggleManager())

    result = searchagent.run_searchagent(
        query="need finance QA datasets",
        objective="collect finance QA data",
        keywords="finance qa",
        output_root=tmp_path,
        model_name="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        deepsearch=False,
    )

    candidate = result["download_list"][0]
    assert candidate["source"] == "kaggle"
    assert candidate["dataset_id"] == "owner/finance-qa"
    assert candidate["fallback_reason"] == "no_candidates_from_decision_methods"


def test_searchagent_provider_fallback_survives_method_decision_failure(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    class FakeMethodDecisionAgent:
        async def decide_method_order(self, **kwargs):
            raise RuntimeError("model unavailable")

    class FakeHfManager:
        async def search_datasets(self, keywords, max_results=5):
            return {
                keywords[0]: [
                    {
                        "id": "openai/gsm8k",
                        "title": "GSM8K",
                        "description": "math word problems",
                    }
                ]
            }

    class FakeKaggleManager:
        async def search_datasets(self, keywords, max_results=5):
            return {}

    monkeypatch.setattr(searchagent, "_make_method_decision_agent", lambda **kwargs: FakeMethodDecisionAgent())
    monkeypatch.setattr(searchagent, "_make_hf_manager", lambda: FakeHfManager())
    monkeypatch.setattr(searchagent, "_make_kaggle_manager", lambda **kwargs: FakeKaggleManager())

    result = searchagent.run_searchagent(
        query="need math reasoning datasets",
        objective="collect math data",
        keywords="gsm8k",
        output_root=tmp_path,
        model_name="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        deepsearch=False,
    )

    assert result["download_list"][0]["dataset_id"] == "openai/gsm8k"
    assert result["errors"][0]["stage"] == "method_decision"


def test_searchagent_runs_task_json_in_parallel_and_keeps_domain_metadata(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    task_json = tmp_path / "tasks.json"
    task_json.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "text2sql",
                        "domain": "text2sql",
                        "objective": "collect text2sql datasets",
                        "search_keywords": ["text2sql dataset"],
                    },
                    {
                        "id": "math",
                        "domain": "math",
                        "objective": "collect math reasoning datasets",
                        "search_keywords": ["math reasoning dataset"],
                    },
                    {
                        "id": "code",
                        "domain": "code",
                        "objective": "collect code repair datasets",
                        "search_keywords": ["code repair dataset"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    active = 0
    max_active = 0

    async def fake_search_single_task(**kwargs):
        nonlocal active, max_active
        task = kwargs["task"]
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        domain = task["domain"]
        return {
            "domain": domain,
            "objective": task["objective"],
            "search_keywords": task["search_keywords"],
            "enriched_search_keywords": task["search_keywords"],
            "deepsearch": {"enabled": False, "errors": []},
            "decision": {"method_order": ["huggingface"]},
            "candidates": [
                {
                    "source": "huggingface",
                    "dataset_id": f"fixture/{domain}",
                    "download": {"method": "huggingface", "dataset_id": f"fixture/{domain}"},
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(searchagent, "_search_single_task", fake_search_single_task)

    result = searchagent.run_searchagent(
        query="collect text2sql math and code datasets",
        task_json=task_json,
        output_root=tmp_path,
        model_name="test-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        deepsearch=False,
        parallelism=3,
    )

    assert max_active == 3
    assert [task["domain"] for task in result["tasks"]] == ["text2sql", "math", "code"]
    assert [candidate["task_domain"] for candidate in result["download_list"]] == [
        "text2sql",
        "math",
        "code",
    ]
    assert [candidate["task_id"] for candidate in result["download_list"]] == [
        "text2sql",
        "math",
        "code",
    ]


def test_cli_searchagent_emits_json(tmp_path, monkeypatch, capsys):
    from loopai.skills.ObtainerCLI import cli

    def fake_run_searchagent(**kwargs):
        return {
            "ok": True,
            "status": "completed",
            "schema_version": "obtainercli.searchagent.v1",
            "manifest_path": str(tmp_path / "searchagent_manifest.json"),
            "candidates": [],
            "download_list": [],
            "tasks": [],
            "errors": [],
            "error": None,
        }

    monkeypatch.setattr(cli, "run_searchagent", fake_run_searchagent)

    exit_code = cli.run(
        [
            "searchagent",
            "--query",
            "math reasoning",
            "--model-name",
            "test-model",
            "--base-url",
            "http://127.0.0.1:8000/v1",
            "--api-key",
            "test-key",
            "--output-root",
            str(tmp_path),
            "--starter-config",
            str(tmp_path / "starter.yaml"),
            "--no-deepsearch",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["schema_version"] == "obtainercli.searchagent.v1"


def test_cli_searchagent_defaults_output_root_to_version_dir(tmp_path, monkeypatch, capsys):
    from loopai.skills.ObtainerCLI import cli

    captured = {}

    def fake_run_searchagent(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "completed",
            "schema_version": "obtainercli.searchagent.v1",
            "manifest_path": str(Path(kwargs["output_root"]) / "searchagent_manifest.json"),
            "candidates": [],
            "download_list": [],
            "tasks": [],
            "errors": [],
        }

    monkeypatch.setattr(cli, "run_searchagent", fake_run_searchagent)

    exit_code = cli.run(
        [
            "searchagent",
            "--query",
            "math reasoning",
            "--model-name",
            "test-model",
            "--base-url",
            "http://127.0.0.1:8000/v1",
            "--api-key",
            "test-key",
            "--task-id",
            "task-001",
            "--output-dir",
            str(tmp_path / "outputs"),
            "--version-id",
            "loop-version-001",
            "--no-deepsearch",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert captured["output_root"] == str(tmp_path / "outputs" / "task-001" / "obtainercli" / "loop-version-001")
