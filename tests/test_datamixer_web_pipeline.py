from __future__ import annotations

import json
import threading
import time

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


def test_dataflow_bridge_supports_native_keyword_run_contract(monkeypatch, tmp_path) -> None:
    calls = {}

    class FakeText2SQLFilter:
        def run(self, storage, input_sql_key="sql", input_db_id_key="db_id"):
            frame = storage.read("dataframe")
            calls["columns"] = list(frame.columns)
            calls["sql_key"] = input_sql_key
            calls["db_key"] = input_db_id_key
            storage.write(frame[frame[input_sql_key].str.startswith("SELECT")])

    monkeypatch.setattr(
        dataflow_module,
        "load_dataflow_operator",
        lambda name, **kwargs: FakeText2SQLFilter(),
    )
    op = dataflow_module.DataFlowBridge(
        op="SQLExecutionFilter",
        kind="filter",
        run_kwargs={"input_sql_key": "SQL", "input_db_id_key": "db_id"},
        field_map={"SQL": "tags.sql", "db_id": "tags.db_id"},
    )
    ctx = base.OperatorContext(root=str(tmp_path))
    rows = [
        {"content": "ok", "tags": {"sql": "SELECT 1", "db_id": "a"}},
        {"content": "bad", "tags": {"sql": "DELETE FROM x", "db_id": "a"}},
    ]
    out = op.process(rows, ctx)
    assert len(out) == 1
    assert calls == {
        "columns": ["tags", "sql", "db_id", "SQL", "raw_content", "__dm_idx"],
        "sql_key": "SQL",
        "db_key": "db_id",
    }


def test_dataflow_bridge_preserves_prepared_input_record(monkeypatch, tmp_path) -> None:
    seen = {}

    class FakeCodeFilter:
        def run(self, storage, input_key, output_key="label"):
            frame = storage.read("dataframe")
            seen["record"] = frame.iloc[0][input_key]
            frame[output_key] = 1
            storage.write(frame)

    monkeypatch.setattr(
        dataflow_module,
        "load_dataflow_operator",
        lambda name, **kwargs: FakeCodeFilter(),
    )
    op = dataflow_module.DataFlowBridge(
        op="CodeLengthSampleFilter",
        input_key="code_sample",
        output_key="native_code_label",
        kind="filter",
    )
    record = {"text": "print(1)", "lines": ["print(1)"], "language": "python"}
    out = op.process(
        [{"content": "different extracted text", "code_sample": record}],
        base.OperatorContext(root=str(tmp_path)),
    )
    assert len(out) == 1
    assert seen["record"] == record
    assert out[0]["native_code_label"] == 1
    assert op.spec.name == "dataflow.CodeLengthSampleFilter"
    assert out[0]["native_dataflow_operators"][0]["operator"] == (
        "CodeLengthSampleFilter"
    )


def test_dataflow_bridge_serializes_native_lazy_imports(monkeypatch, tmp_path) -> None:
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    class FakeOperator:
        def run(self, storage, input_key):  # pragma: no cover - setup-only test
            storage.write(storage.read("dataframe"))

    def fake_load(name, **kwargs):  # noqa: ARG001
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.03)
        with state_lock:
            state["active"] -= 1
        return FakeOperator()

    monkeypatch.setattr(dataflow_module, "load_dataflow_operator", fake_load)
    ctx = base.OperatorContext(root=str(tmp_path))
    ops = [
        dataflow_module.DataFlowBridge(op="One"),
        dataflow_module.DataFlowBridge(op="Two"),
    ]
    threads = [threading.Thread(target=op.setup, args=(ctx,)) for op in ops]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["max_active"] == 1


def test_webpage_operators_are_registered() -> None:
    assert base.is_registered("webpage_to_pt")
    assert base.is_registered("pt_to_sft_qa")
    assert base.is_registered("domain_classify")


def test_code_record_prepare_extracts_valid_mineru_html_code_blocks() -> None:
    op = base.create("code_record_prepare", extract_code_blocks=True)
    row = {
        "content": {
            "main_html": (
                '<main><div class="highlight-python"><pre>'
                '<span>print</span>("Hello")\n'
                "for name in names:\n"
                '    <span>print</span>(name)'
                "</pre></div>"
                "<pre>For dotted expressions such as string.a, it will evaluate "
                "the expression and return its value.</pre></main>"
            ),
            "text": (
                "A Python example with enough surrounding documentation text. "
                "The rendered output below is prose rather than source code."
            ),
            "source_url": "https://docs.example.test/tutorial",
        }
    }

    output = op.process([row], base.OperatorContext())

    assert output == [row]
    assert row["language"] == "python"
    assert row["code_block_count"] == 1
    assert row["code_blocks"] == [{
        "language": "python",
        "code": 'print("Hello")\nfor name in names:\n    print(name)',
    }]


def test_code_domain_quality_accepts_code_within_top_three_labels() -> None:
    op = base.create(
        "domain_quality_filter",
        target_domain="code",
        min_chars=20,
        require_primary_label=False,
        max_target_label_rank=3,
    )
    accepted = {
        "content": {
            "text": "A tutorial defines a Python function: def show(): return 'ok'."
        },
        "domain_labels": ["education", "documentation", "code"],
        "domain_confidence": 0.94,
        "domain_classifier": "domain_classify@1.3",
        "domain_classifier_model": "qwen3-14b-fp8",
        "code_blocks": [{
            "language": "python",
            "code": "def show():\n    return 'ok'",
        }],
    }
    rejected = {
        **accepted,
        "domain_labels": ["education", "documentation", "science", "code"],
    }

    output = op.process([accepted, rejected], base.OperatorContext())

    assert output == [accepted]
    assert accepted["domain_quality_report"]["target_label_rank"] == 3
    assert rejected["domain_quality_findings"] == [
        "target_domain_rank_below_limit"
    ]


def test_code_sft_allows_qwen_to_rewrite_source_code() -> None:
    generator = base.create("pt_to_sft_code")
    generator.model_name = "qwen3-14b-fp8"
    row = {
        "content": {
            "text": "A source example uses print('source').",
            "source_url": "https://docs.example.test/code",
        },
        "code_blocks": [{"language": "python", "code": "print('source')"}],
    }
    data = {"results": [{
        "index": 0,
        "question": "How can I print an adapted value?",
        "explanation": "Call Python's print function with the desired value.",
        "language": "python",
        "code": "value = 'adapted'\nprint(value)",
    }]}

    generator.apply([row], data)
    generator.validate_output([row], data)
    output = base.create("code_sft_validate").process(
        [row], base.OperatorContext()
    )

    assert output == [row]
    assert row["code_sft_valid"] is True
    assert "value = 'adapted'" in row["content"]["messages"][1]["content"]


def test_code_sft_rejects_obviously_undefined_python_globals() -> None:
    row = {
        "content": {
            "messages": [
                {"role": "user", "content": "Format a table."},
                {
                    "role": "assistant",
                    "content": (
                        "Use an f-string.\n\n```python\n"
                        "for name, phone in table.items():\n"
                        "    print(f'{name}: {phone}')\n```"
                    ),
                },
            ]
        },
        "_qa_code_blocks": [{
            "language": "python",
            "code": "print(f'{name}: {phone}')",
        }],
    }

    output = base.create("code_sft_validate").process(
        [row], base.OperatorContext()
    )

    assert output == []
    assert row["code_sft_error"] == "python_undefined_globals:table"


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
        pool = ModelPool(store.root)
        pool.add(ModelSpec(name="fake", api_url="mock://llm"))
        pool.set_default("fake")

        seen_labels = []

        def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
            seen_labels.extend(messages[-1]["content"].split("\n", 1)[0:1])
            return json.dumps({
                "results": [{
                    "index": 0,
                    "labels": ["robotics", "engineering"],
                    "confidence": 0.93,
                }]
            })

        monkeypatch.setattr(llm, "complete", fake_complete)
        op = base.create("domain_classify", chunk_size=1)
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
        assert updated["tags"]["domain_confidence"] == 0.93
        assert updated["tags"]["domain_classifier"] == "domain_classify@1.3"
        assert updated["tags"]["domain_classifier_model"] == "fake"
        assert updated["tags"]["semantic_signals"] == []
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
        engine="mineru",
        mineru_transport="worker",
        mineru_python=__file__,
        mineru_model=__file__,
        mineru_batch_size=1,
        min_chars=5,
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
    op = webpage_module.WebpageToPT(
        engine="mineru",
        mineru_transport="worker",
        mineru_python=__file__,
        mineru_model=__file__,
        min_chars=5,
    )
    op.setup(None)
    rows = [{"content": {"html": "<html><nav>noise</nav></html>"}}]
    op.process(rows, None)
    op.teardown(None)
    assert rows[0]["pt_extractor"] == "mineru_html+main_html_fallback"
    assert "Selected" in rows[0]["content"]["text"]
    assert "noise" not in rows[0]["content"]["text"]


def test_webpage_operator_prefers_persistent_mineru_http_service(
    monkeypatch,
) -> None:
    seen = {}

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def start(self):
            seen["started"] = True

        def process(self, htmls):
            seen["htmls"] = htmls
            return [{
                "main_html": "<main><h1>Selected</h1><p>Persistent service content.</p></main>",
                "main_content": None,
                "error": None,
            }]

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(
        webpage_module, "_MinerUHTMLHTTPClient", FakeHTTPClient
    )
    op = webpage_module.WebpageToPT(
        engine="mineru",
        mineru_url="http://127.0.0.1:7986",
        mineru_transport="auto",
        min_chars=5,
    )
    op.setup(None)
    rows = [{"content": {"html": "<html><nav>noise</nav></html>"}}]
    op.process(rows, None)
    op.teardown(None)

    assert seen["base_url"] == "http://127.0.0.1:7986"
    assert seen["started"] is True
    assert seen["htmls"] == ["<html><nav>noise</nav></html>"]
    assert seen["closed"] is True
    assert op.mineru_transport == "http"
    assert rows[0]["pt_extractor"] == "mineru_html+main_html_fallback"
    assert "Persistent service content" in rows[0]["content"]["text"]
    assert "noise" not in rows[0]["content"]["text"]


def test_pt_to_sft_qa_repairs_an_empty_json_object(monkeypatch, tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    replies = iter([
        "{}",
        json.dumps({
            "results": [{
                "index": 0,
                "question": "What does the source explain?",
                "answer": "It explains a grounded financial concept.",
            }]
        }),
    ])
    calls = []

    def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(llm, "complete", fake_complete)
    op = webpage_module.PTToSFTQA(
        model="fake", chunk_size=1, output_retries=1
    )
    ctx = base.OperatorContext(root=str(store.root))
    op.setup(ctx)
    rows = [{
        "content": {"text": "A grounded source document.", "source_url": "x"},
        "domain": "finance",
    }]
    try:
        op.process(rows, ctx)
    finally:
        op.teardown(ctx)
        store.close()

    assert len(calls) == 2
    assert "previous JSON was incomplete" in calls[1][-1]["content"]
    assert rows[0]["qa_generated"] is True
    assert rows[0]["qa_model"] == "fake"
    assert rows[0]["content"]["messages"][0]["role"] == "user"


def test_pt_to_sft_qa_rejects_persistently_empty_output(
    monkeypatch, tmp_path
) -> None:
    import pytest

    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: "{}")
    op = webpage_module.PTToSFTQA(
        model="fake", chunk_size=1, output_retries=1
    )
    ctx = base.OperatorContext(root=str(store.root))
    op.setup(ctx)
    try:
        with pytest.raises(RuntimeError, match="invalid model output"):
            op.process([{"content": {"text": "grounded source"}}], ctx)
        assert list((store.root / "llm_cache").glob("*.txt")) == []
    finally:
        op.teardown(ctx)
        store.close()


def test_topic_quality_filter_rejects_without_two_grounded_signals() -> None:
    op = base.create(
        "topic_quality_filter",
        min_semantic_signals=2,
        min_classifier_confidence=0.8,
        min_signal_confidence=0.7,
    )
    accepted = {
        "content": {"text": "Revenue increased while the company filed Form 10-K."},
        "domain_labels": ["finance"],
        "domain_confidence": 0.95,
        "domain_classifier": "domain_classify@1.3",
        "domain_classifier_model": "qwen3-14b-fp8",
        "semantic_signals": [
            {"type": "accounting_metric", "evidence": "Revenue", "confidence": 0.9},
            {"type": "regulatory_or_compliance", "evidence": "Form 10-K", "confidence": 0.9},
        ],
    }
    rejected = {
        "content": {"text": "Revenue is mentioned without another signal."},
        "domain_labels": ["finance"],
        "domain_confidence": 0.95,
        "domain_classifier": "domain_classify@1.3",
        "domain_classifier_model": "qwen3-14b-fp8",
        "semantic_signals": [
            {"type": "accounting_metric", "evidence": "Revenue", "confidence": 0.9}
        ],
    }

    output = op.process([accepted, rejected], base.OperatorContext())

    assert output == [accepted]
    assert accepted["topic_quality_passed"] is True
    assert rejected["topic_quality_passed"] is False
    assert rejected["topic_quality_findings"] == [
        "topic_semantic_signals_below_minimum"
    ]
