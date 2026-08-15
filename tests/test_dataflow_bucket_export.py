from __future__ import annotations

import json

from loopai.agents.Obtainer.datamixer.dataflow_agent import (
    _simplify_bucket_filter,
    bucket_export_plan_from_mix_plan,
    bucket_export_plan_from_recipe,
    export_bucketed_jsonl,
)
from loopai.agents.Obtainer.datamixer.store import DataStore


def test_simplify_bucket_filter_drops_l4_fields() -> None:
    where = _simplify_bucket_filter(
        "(dataset_id='ds-a' OR dataset_id='ds-b') AND domain='code' "
        "AND quality_score>=3 AND json_extract(tags_json,'$.pii_flag')=0 "
        "AND json_extract(tags_json,'$.refusal_flag')=0"
    )
    assert where == "dataset_id IN ('ds-a','ds-b') AND domain='code'"


def test_simplify_bucket_filter_in_clause() -> None:
    where = _simplify_bucket_filter(
        "dataset_id IN ('ds-x', \"ds-y\") AND domain='code'"
    )
    assert where == "dataset_id IN ('ds-x','ds-y') AND domain='code'"


def test_bucket_export_plan_from_recipe_rounds_up_1_5x(tmp_path) -> None:
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        "recipe:\n"
        "  name: t\n"
        "  stage: sft\n"
        "  total_samples: 1000\n"
        "  buckets:\n"
        "    - name: a\n"
        "      weight: 0.25\n"
        "      filter: \"dataset_id='ds-a' AND domain='code' AND quality_score>=3\"\n"
        "    - name: b\n"
        "      weight: 0.75\n"
        "      filter: \"(dataset_id='ds-b' OR dataset_id='ds-c') AND domain='code'\"\n",
        encoding="utf-8",
    )
    plan = bucket_export_plan_from_recipe(recipe)
    assert plan[0] == {
        "name": "a",
        "target_records": 250,
        "export_rows": 375,  # ceil(250 * 1.5)
        "where": "dataset_id IN ('ds-a') AND domain='code'",
    }
    assert plan[1]["export_rows"] == 1125  # ceil(750 * 1.5)
    assert plan[1]["where"] == "dataset_id IN ('ds-b','ds-c') AND domain='code'"


def test_bucket_export_plan_from_mix_plan(tmp_path) -> None:
    mix = tmp_path / "mix_plan.json"
    mix.write_text(json.dumps({
        "domain": "code",
        "target_records": 1000,
        "buckets": [
            {"name": "b1", "target_records": 400, "dataset": "ds-a"},
            {"name": "b2", "target_records": 600, "dataset": "ds-b"},
        ],
    }), encoding="utf-8")
    plan = bucket_export_plan_from_mix_plan(mix)
    assert plan[0]["export_rows"] == 600  # ceil(400 * 1.5)
    assert plan[0]["where"] == "dataset_id='ds-a' AND domain='code'"


def _ingest(store: DataStore, dataset_id: str, rows: int) -> None:
    store.ingest_records(
        dataset_id,
        [{"content": {"text": f"{dataset_id}-row-{i}"}, "domain": "code"}
         for i in range(rows)],
        defaults={"quality_level": "L3"},
        decontaminate=False,
    )


def test_export_bucketed_jsonl_samples_1_5x_per_bucket(tmp_path) -> None:
    root = tmp_path / "warehouse"
    store = DataStore.init(root)
    try:
        _ingest(store, "ds-a", 4)
        _ingest(store, "ds-b", 10)
    finally:
        store.close()

    store = DataStore.open(root)
    try:
        out = tmp_path / "full_input.jsonl"
        result = export_bucketed_jsonl(
            store,
            buckets=[
                {"name": "a", "target_records": 2, "export_rows": 3,
                 "where": "dataset_id='ds-a' AND domain='code'"},
                {"name": "b", "target_records": 8, "export_rows": 12,
                 "where": "dataset_id='ds-b' AND domain='code'"},
            ],
            field="content",
            out=out,
        )
        # bucket a has only 4 rows -> 3 sampled; bucket b has 10 -> all 10
        assert result["buckets"] == {"a": 3, "b": 10}
        assert result["total"] == 13

        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 13
        sample_ids = [json.loads(line)["sample_id"] for line in lines]
        assert len(sample_ids) == len(set(sample_ids))
        # every row keeps sample_id + content
        assert all("content" in json.loads(line) for line in lines)

        # same seed -> same sample (repeatable)
        out2 = tmp_path / "full_input2.jsonl"
        result2 = export_bucketed_jsonl(
            store,
            buckets=[
                {"name": "a", "target_records": 2, "export_rows": 3,
                 "where": "dataset_id='ds-a' AND domain='code'"},
                {"name": "b", "target_records": 8, "export_rows": 12,
                 "where": "dataset_id='ds-b' AND domain='code'"},
            ],
            field="content",
            out=out2,
        )
        assert result2["total"] == 13
        lines2 = out2.read_text(encoding="utf-8").strip().splitlines()
        assert [json.loads(l)["sample_id"] for l in lines2] == sample_ids
    finally:
        store.close()
