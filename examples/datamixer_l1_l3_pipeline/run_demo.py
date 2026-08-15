#!/usr/bin/env python3
"""Build and run the formally registered DataMixer webpage pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopai.agents.Obtainer.datamixer.models import ModelPool
from loopai.agents.Obtainer.datamixer.operators import load_pipeline, run_pipeline
from loopai.agents.Obtainer.datamixer.store import DataStore


def _records(manifest_path: Path):
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        html_path = Path(item["local_path"])
        if not html_path.is_absolute():
            html_path = HERE.parents[1] / html_path
        yield {
            "content": {
                "html": html_path.read_text(encoding="utf-8"),
                "title": item["title"],
                "url": item["url"],
            },
            "modality": "text",
            "stage": "pretrain",
            "domain": item["domain"],
            "source": "public_webpage_demo",
            "license": item["license"],
            "source_id": item["id"],
            "source_uri": item["url"],
            "source_kind": "webpage_html",
            "retrieved_at": item["retrieved_at"],
            "http_status": item["http_status"],
            "content_type": item["content_type"],
            "provenance_note": item["provenance_note"],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--model-source-warehouse", required=True)
    parser.add_argument("--model", default="qwen3-14b-fp8")
    parser.add_argument(
        "--manifest", default=str(HERE / "source_pages" / "manifest.jsonl")
    )
    parser.add_argument("--pipeline", default=str(HERE / "pipeline.yaml"))
    parser.add_argument("--mineru-gpu", default=None)
    parser.add_argument(
        "--mineru-backend", choices=("vllm", "transformers"), default=None
    )
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    store = DataStore.init(args.warehouse)
    try:
        source_pool = ModelPool(args.model_source_warehouse)
        target_pool = ModelPool(store.root)
        target_pool.add(source_pool.get(args.model))

        dataset_id = store.catalog.resolve_dataset("webpage_demo_l1")
        if dataset_id is None:
            dataset_id = store.catalog.add_dataset(
                name="webpage_demo_l1",
                source="10 public raw HTML webpages",
                description=(
                    "Raw L1 HTML fixtures covering code, math, medical, and finance."
                ),
            )
        ingest = store.ingest_records(
            dataset_id,
            _records(Path(args.manifest)),
            defaults={"quality_level": "L1"},
            decontaminate=False,
        )

        pipeline_spec = load_pipeline(args.pipeline)
        for op in pipeline_spec.get("operators", []):
            if op.get("name") == "webpage_to_pt":
                if args.mineru_gpu is not None:
                    op.setdefault("args", {})["mineru_gpu"] = args.mineru_gpu
                if args.mineru_backend is not None:
                    op.setdefault("args", {})["mineru_backend"] = args.mineru_backend
            if op.get("name") in {"domain_classify", "pt_to_sft_qa"}:
                op.setdefault("args", {})["model"] = args.model
        result = run_pipeline(store, pipeline_spec, batch_size=10)

        counts = {}
        for name in ("webpage_demo_l1", "webpage_demo_l2_pt",
                     "webpage_demo_l3_sft"):
            ds_id = store.catalog.resolve_dataset(name)
            counts[name] = store.catalog.count(dataset_id=ds_id) if ds_id else 0
        report = {
            "warehouse": str(store.root.resolve()),
            "model": args.model,
            "ingest": ingest.__dict__,
            "pipeline": result.to_dict(),
            "dataset_counts": counts,
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            Path(args.report).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
