from __future__ import annotations

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


def test_searchagent_uses_starter_model_defaults(tmp_path, monkeypatch):
    from loopai.skills.ObtainerCLI import searchagent

    starter = tmp_path / "starter.yaml"
    starter.write_text(
        """
system:
  starter_model_path: starter-model
  starter_base_url: http://starter.example/v1
  starter_api_key: starter-key
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

    assert captured["model_name"] == "starter-model"
    assert captured["base_url"] == "http://starter.example/v1"
    assert captured["api_key"] == "starter-key"
    assert captured["search_engine"] == "duckduckgo"
    assert captured["max_urls"] == 4


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
