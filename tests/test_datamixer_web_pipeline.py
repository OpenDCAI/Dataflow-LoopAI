from __future__ import annotations

import json

from loopai.agents.Obtainer.datamixer import llm
from loopai.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from loopai.agents.Obtainer.datamixer.operators import base, run_pipeline
from loopai.agents.Obtainer.datamixer.operators import dataflow as dataflow_module
from loopai.agents.Obtainer.datamixer.operators import webpage as webpage_module
from loopai.agents.Obtainer.datamixer.store import DataStore


def test_webpage_pipeline_materializes_l1_l2_l3(monkeypatch, tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    l1_id = store.catalog.add_dataset(name="web_l1")
    source = [
        {
            "content": {
                "html": (
                    "<html><head><title>Alpha</title></head><body><main>"
                    "<h1>Alpha</h1><p>Alpha has enough useful words for a "
                    "grounded question and answer in this pipeline test.</p>"
                    "</main></body></html>"
                ),
                "url": "https://example.test/alpha",
            },
            "domain": "math",
            "source_uri": "https://example.test/alpha",
        },
        {
            "content": {
                "html": (
                    "<html><head><title>Beta</title></head><body><article>"
                    "<h1>Beta</h1><p>Beta also contains sufficient technical "
                    "words for deterministic QA generation during testing.</p>"
                    "</article></body></html>"
                ),
                "url": "https://example.test/beta",
            },
            "domain": "code",
            "source_uri": "https://example.test/beta",
        },
    ]
    store.ingest_records(
        l1_id, source, defaults={"quality_level": "L1"}, decontaminate=False
    )
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))

    def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        if "Allowed labels:" in messages[-1]["content"]:
            item_json = messages[-1]["content"].split(
                "Items (classify each by index):\n", 1
            )[1].split("\n\nReturn ONLY", 1)[0]
            items = json.loads(item_json)
            return json.dumps({
                "results": [{
                    "index": item["index"],
                    "labels": ["math" if "Alpha" in item["text"] else "code"],
                } for item in items]
            })
        return json.dumps({
            "results": [
                {"index": 0, "question": "What is Alpha?", "answer": "Alpha is described by the source text."},
                {"index": 1, "question": "What is Beta?", "answer": "Beta is described by the source text."},
            ]
        })

    monkeypatch.setattr(llm, "complete", fake_complete)

    class FakeWordNumberFilter:
        def run(self, storage, input_key, output_key="word_number_filter_label"):
            frame = storage.read("dataframe")
            frame[output_key] = frame[input_key].map(lambda text: len(text.split()))
            storage.write(frame[frame[output_key] >= 5])

    monkeypatch.setattr(
        dataflow_module,
        "load_dataflow_operator",
        lambda name, **kwargs: FakeWordNumberFilter(),
    )
    spec = {
        "name": "web-test",
        "source": {"dataset": "web_l1", "filter": "quality_level = 'L1'"},
        "operators": [
            {
                "name": "webpage_to_pt",
                "args": {"min_chars": 20, "engine": "legacy"},
            },
            {
                "name": "dataflow",
                "args": {
                    "op": "WordNumberFilter",
                    "input_key": "text",
                    "output_key": "dataflow_word_count",
                    "kind": "filter",
                    "min_words": 5,
                    "max_words": 1000,
                },
            },
            {
                "name": "domain_classify",
                "args": {"model": "fake", "chunk_size": 2},
                "output": {
                    "dataset": "web_l2",
                    "quality_level": "L2",
                    "stage": "pretrain",
                },
            },
            {
                "name": "pt_to_sft_qa",
                "args": {"model": "fake", "chunk_size": 2},
            },
            {
                "name": "sft_validate",
                "args": {"mode": "filter"},
                "output": {
                    "dataset": "web_l3",
                    "quality_level": "L3",
                    "stage": "sft",
                    "task_type": "grounded_qa",
                },
            },
        ],
    }

    progress = []
    result = run_pipeline(store, spec, batch_size=2, progress_callback=progress.append)
    l2_id = store.catalog.resolve_dataset("web_l2")
    l3_id = store.catalog.resolve_dataset("web_l3")
    assert result.selected == 2
    assert result.materialized == 4
    assert result.outputs == {"web_l2": 2, "web_l3": 2}
    assert any(item["current_stage"] == "domain_classify" for item in progress)
    assert progress[-1]["status"] == "completed"
    assert all(item["state"] == "completed" for item in progress[-1]["stages"])
    assert store.catalog.count(dataset_id=l1_id) == 2
    assert store.catalog.count(dataset_id=l2_id) == 2
    assert store.catalog.count(dataset_id=l3_id) == 2

    l2 = next(row for row in store.catalog.query(dataset_id=l2_id)
              if row["domain"] == "math")
    l1 = store.catalog.get_sample(l2["tags"]["parent_sample_id"])
    l3 = next(
        row for row in store.catalog.query(dataset_id=l3_id)
        if row["tags"]["parent_sample_id"] == l2["sample_id"]
    )
    assert l1 is not None
    assert "<html>" in store.get_content(l1["cid"])["html"]
    assert store.get_content(l2["cid"])["document_type"] == "webpage_pt"
    assert store.get_content(l3["cid"])["messages"][1]["role"] == "assistant"
    assert l2["quality_level"] == "L2"
    assert l3["quality_level"] == "L3"
    assert l2["domain"] == "math"
    assert l2["tags"]["domain_labels"] == ["math"]
    assert l3["domain"] == "math"
    assert l3["tags"]["domain_labels"] == ["math"]
    assert l2["tags"]["parent_sample_id"] == l1["sample_id"]
    assert l3["tags"]["parent_sample_id"] == l2["sample_id"]
    assert l3["tags"]["root_sample_id"] == l1["sample_id"]
    assert l3["tags"]["sft_valid"] is True
    store.close()


def test_webpage_operators_are_registered() -> None:
    assert base.is_registered("webpage_to_pt")
    assert base.is_registered("pt_to_sft_qa")
    assert base.is_registered("domain_classify")


def test_domain_classifier_uses_persistent_and_lake_observed_classes(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    try:
        dataset_id = store.catalog.add_dataset("pages")
        store.ingest_records(
            dataset_id,
            [{
                "content": {"text": "A robotics control system uses sensors."},
                "domain": "robotics",
            }],
            defaults={"quality_level": "L2"},
            decontaminate=False,
        )
        ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))

        seen_labels = []

        def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
            seen_labels.extend(messages[-1]["content"].split("\n", 1)[0:1])
            return json.dumps({
                "results": [{"index": 0, "labels": ["robotics", "engineering"]}]
            })

        monkeypatch.setattr(llm, "complete", fake_complete)
        op = base.create("domain_classify", model="fake", chunk_size=1)
        ctx = base.OperatorContext(root=str(store.root))
        op.setup(ctx)
        assert "code" in op.labels  # persistent baseline
        assert "robotics" in op.labels  # synchronised from this lake

        row = store.catalog.query(dataset_id=dataset_id)[0]
        row["content"] = store.get_content(row["cid"])
        op.process([row], ctx)
        store.catalog.update_fields(
            row["sample_id"],
            {key: value for key, value in row.items()
             if key not in {"content", "sample_id", "dataset_id", "cid", "tags"}},
        )
        store.catalog.commit()
        op.teardown(ctx)

        updated = store.catalog.get_sample(row["sample_id"])
        assert seen_labels and "robotics" in seen_labels[0]
        assert updated["domain"] == "robotics"
        assert updated["tags"]["domain_labels"] == ["robotics", "engineering"]
        domains = store.catalog.list_domain_classes()
        assert {item["name"] for item in domains} >= {"code", "robotics"}
    finally:
        store.close()


def test_webpage_operator_can_use_isolated_mineru_client(monkeypatch) -> None:
    class FakeMinerUClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            pass

        def process(self, htmls):
            return [
                {
                    "main_html": "<main><p>main</p></main>",
                    "main_content": "# Main\n\nMinerU content",
                    "error": None,
                }
                for _ in htmls
            ]

        def close(self):
            pass

    monkeypatch.setattr(webpage_module, "_MinerUHTMLClient", FakeMinerUClient)
    op = webpage_module.WebpageToPT(
        engine="mineru", mineru_batch_size=1, min_chars=5
    )
    op.setup(None)
    rows = [{
        "content": {"html": "<html><body>raw</body></html>", "url": "https://x"},
        "source_uri": "https://x",
    }]
    op.process(rows, None)
    op.teardown(None)
    assert rows[0]["pt_extractor"] == "mineru_html"
    assert rows[0]["content"]["text"] == "# Main\n\nMinerU content"
    assert rows[0]["content"]["main_html"] == "<main><p>main</p></main>"


def test_webpage_operator_uses_mineru_main_html_when_conversion_fails(
    monkeypatch,
) -> None:
    class FakeMinerUClient:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def process(self, htmls):
            return [{
                "main_html": "<main><h1>Selected</h1><p>正文内容 preserved.</p></main>",
                "main_content": None,
                "error": "MathML conversion failed",
            }]

        def close(self):
            pass

    monkeypatch.setattr(webpage_module, "_MinerUHTMLClient", FakeMinerUClient)
    op = webpage_module.WebpageToPT(engine="mineru", min_chars=5)
    op.setup(None)
    rows = [{"content": {"html": "<html><nav>noise</nav></html>"}}]
    op.process(rows, None)
    op.teardown(None)
    assert rows[0]["pt_extractor"] == "mineru_html+main_html_fallback"
    assert "Selected" in rows[0]["content"]["text"]
    assert "noise" not in rows[0]["content"]["text"]
