from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from api.app.services.config.service import _sync_model_pool_services_to_lake
from loopai.skills.ObtainerCLI.config import read_lake_config


def test_sync_model_pool_services_writes_lake_pointer(monkeypatch, tmp_path) -> None:
    lake_dir = tmp_path / ".loopai"
    lake_dir.mkdir()
    link = lake_dir / "lake.yaml"
    link.write_text(
        "\n".join(
            [
                "root: /tmp/lake",
                "warehouse: /tmp/lake/warehouse",
                "catalog: datamixer",
                "backend: datamixer",
                "namespace: loopai",
                "auto_embed: false",
                "embedding_provider: openai-compatible",
                "embedding_base_url: http://127.0.0.1:8000/v1",
                "embedding_api_key:",
                "embedding_model: BAAI/bge-small-zh-v1.5",
                "embedding_backend: local-jsonl",
                "embedding_text_field: text",
                "auto_embed_async: true",
                "auto_embed_batch_size: 512",
                "obtainer_webagent: domain_data_acquisition",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("api.app.services.config.service.LOOPAI_DIR", tmp_path)

    _sync_model_pool_services_to_lake(
        {
            "system": {
                "model": {
                    "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
                    "embedding": {
                        "provider": "openai-compatible",
                        "base_url": "http://127.0.0.1:9000/v1",
                        "api_key": "",
                        "model": "bge-m3",
                        "backend": "local-jsonl",
                        "text_field": "text",
                    },
                    "mineru": {
                        "url": "http://10.0.0.5:7986",
                        "python": "/opt/mineru/bin/python",
                        "model": "/models/mineru-html",
                        "gpu": "1",
                        "transport": "http",
                        "backend": "vllm",
                    },
                }
            }
        }
    )

    values = read_lake_config(link)
    assert values["embedding_base_url"] == "http://127.0.0.1:9000/v1"
    assert values["embedding_model"] == "bge-m3"
    assert values["mineru_url"] == "http://10.0.0.5:7986"
    assert values["mineru_python"] == "/opt/mineru/bin/python"
    assert values["mineru_gpu"] == "1"
    assert values["root"] == "/tmp/lake"
    assert values["obtainer_webagent"] == "domain_data_acquisition"


def test_sync_model_pool_services_noop_without_pointer(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("api.app.services.config.service.LOOPAI_DIR", tmp_path)
    # Must not raise and must not create a lake pointer.
    _sync_model_pool_services_to_lake(
        {
            "system": {
                "model": {
                    "embedding": {"model": "bge-m3"},
                    "mineru": {"url": "http://127.0.0.1:7986"},
                }
            }
        }
    )
    assert not (tmp_path / ".loopai" / "lake.yaml").exists()
