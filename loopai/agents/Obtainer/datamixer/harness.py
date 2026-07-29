"""A minimal intelligent ingestion harness (L4 / agentic).

Goal: ingest a dataset of *any* (text-based) file format without the user
hand-specifying the layout. An LLM from the **model pool** reviews a sample of
the file and emits a structured *ingest plan*; the harness then executes the
plan deterministically in Python (the model plans, code parses -- safe and
reproducible). If a plan fails to parse, the error is fed back and the model
re-plans (a small agent loop). With no model, a heuristic planner is used.

Plan schema (the model returns exactly this JSON)::

    {
      "format": "jsonl|json_array|csv|tsv|lines|blocks",
      "array_path": "data",            # optional: dotted path to the array (json)
      "content_key": "content",        # optional: record key already holding the body
      "content_fields": ["text"],      # else: record fields that form the content
      "metadata_fields": ["domain"],   # record fields lifted into schema metadata
      "field_rename": {"category": "domain"},
      "dataset_defaults": {"stage": "sft", "modality": "text"},
      "review": "one-line human summary of the format and tags"
    }
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from . import llm, schema
from .models import ModelPool

FORMATS = ("jsonl", "json_array", "csv", "tsv", "lines", "blocks")
_CONTENT_HINTS = ("content", "text", "instruction", "response", "prompt",
                  "chosen", "rejected", "messages", "trajectory")
_META_HINTS = ("domain", "lang", "stage", "source", "license", "task_type",
               "modality", "reward", "trajectory_len", "tool_calls")


# ---------------------------------------------------------------------------
# Sniffing + heuristic planner
# ---------------------------------------------------------------------------

def sniff(path, filename=None, sample_bytes=4000) -> dict:
    p = Path(path)
    name = filename or p.name
    ext = Path(name).suffix.lower().lstrip(".")
    raw = p.read_bytes()[:sample_bytes]
    head = raw.decode("utf-8", "replace")
    return {"filename": name, "ext": ext, "bytes": p.stat().st_size, "head": head}


def _first_json_obj(head: str):
    for line in head.splitlines():
        line = line.strip()
        if line:
            try:
                return json.loads(line)
            except (json.JSONDecodeError, ValueError):
                return None
    return None


def _infer_record(plan: dict, rec) -> None:
    if not isinstance(rec, dict):
        return
    if "content" in rec:
        plan["content_key"] = "content"
    else:
        cf = [k for k in _CONTENT_HINTS if k in rec]
        if cf:
            plan["content_fields"] = cf
    plan["metadata_fields"] = [k for k in _META_HINTS if k in rec]


def heuristic_plan(s: dict) -> dict:
    head, ext = s["head"], s["ext"]
    stripped = head.lstrip()
    plan = {"format": "lines", "content_fields": [], "content_key": None,
            "metadata_fields": [], "field_rename": {}, "dataset_defaults": {},
            "review": "heuristic plan (no model)"}
    if ext in ("jsonl", "ndjson") or (stripped.startswith("{") and "}\n{" in head):
        plan["format"] = "jsonl"
        _infer_record(plan, _first_json_obj(head))
    elif ext == "json" or stripped.startswith("["):
        plan["format"] = "json_array"
        try:
            data = json.loads(head)
            if isinstance(data, list) and data:
                _infer_record(plan, data[0])
        except (json.JSONDecodeError, ValueError):
            pass
    elif ext in ("csv",):
        plan["format"] = "csv"
        plan["metadata_fields"] = _csv_meta(head, ",")
    elif ext in ("tsv",):
        plan["format"] = "tsv"
        plan["metadata_fields"] = _csv_meta(head, "\t")
    elif "\n\n" in head and ext in ("txt", "md", "markdown", ""):
        plan["format"] = "blocks"
    return plan


def _csv_meta(head: str, delim: str) -> list:
    try:
        cols = next(csv.reader(io.StringIO(head), delimiter=delim))
    except StopIteration:
        return []
    return [c for c in cols if c in _META_HINTS]


# ---------------------------------------------------------------------------
# LLM planner
# ---------------------------------------------------------------------------

_SYS = (
    "You are a dataset-ingestion planner for an LLM "
    "training-data platform. Inspect the file sample and output a JSON plan for "
    "how to split it into records, extract each record's content, and assign "
    "metadata tags. Map tags to the platform's unified schema fields when "
    "possible. Output ONLY the JSON plan, no prose."
)


def _plan_schema_hint() -> str:
    return (
        f"Allowed format: {list(FORMATS)}\n"
        f"Unified schema metadata fields you may target: "
        f"{list(schema.CORE_FIELD_NAMES)}\n"
        "Return ONLY this JSON object:\n"
        '{"format": <one of the allowed>, "array_path": <dotted path or null>, '
        '"content_key": <string or null>, "content_fields": [<string>...], '
        '"metadata_fields": [<string>...], "field_rename": {<from>: <to>}, '
        '"dataset_defaults": {<schema_field>: <value>}, "review": <string>}'
    )


def llm_plan(model_spec, s: dict, prev_error: str | None = None) -> dict:
    user = (
        f"filename: {s['filename']}\nextension: {s['ext']}\nbytes: {s['bytes']}\n\n"
        f"--- sample (head) ---\n{s['head']}\n--- end sample ---\n\n"
        f"{_plan_schema_hint()}"
    )
    if prev_error:
        user += f"\n\nYour previous plan failed: {prev_error}\nFix it."
    messages = [{"role": "system", "content": _SYS},
                {"role": "user", "content": user}]
    return _normalize(llm.parse_json(llm.complete(model_spec, messages, json_mode=True)))


def _normalize(plan: dict) -> dict:
    plan.setdefault("format", "lines")
    if plan["format"] not in FORMATS:
        plan["format"] = "lines"
    for k, dv in (("content_fields", []), ("metadata_fields", []),
                  ("field_rename", {}), ("dataset_defaults", {})):
        if not isinstance(plan.get(k), type(dv)):
            plan[k] = dv
    plan.setdefault("content_key", None)
    plan.setdefault("array_path", None)
    plan.setdefault("review", "")
    return plan


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def parse_records(path, plan: dict) -> list:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    fmt = plan["format"]
    if fmt == "jsonl":
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    if fmt == "json_array":
        data = json.loads(text)
        if plan.get("array_path"):
            for key in str(plan["array_path"]).split("."):
                data = data[key]
        return list(data) if isinstance(data, list) else [data]
    if fmt in ("csv", "tsv"):
        delim = "," if fmt == "csv" else "\t"
        return list(csv.DictReader(io.StringIO(text), delimiter=delim))
    if fmt == "blocks":
        return [{"text": b.strip()} for b in re.split(r"\n\s*\n", text) if b.strip()]
    return [{"text": ln} for ln in text.splitlines() if ln.strip()]   # lines


def build_ingest_records(records, plan: dict):
    rename = plan.get("field_rename") or {}
    cfields = plan.get("content_fields") or []
    ckey = plan.get("content_key")
    mfields = plan.get("metadata_fields") or []
    defaults = plan.get("dataset_defaults") or {}
    for rec in records:
        if isinstance(rec, dict):
            rec = {rename.get(k, k): v for k, v in rec.items()}
            if ckey and ckey in rec:
                content = rec[ckey]
            elif cfields:
                content = {f: rec[f] for f in cfields if f in rec} or rec
            else:
                content = rec
            meta = dict(defaults)
            for f in mfields:
                if f in rec:
                    meta[f] = rec[f]
        else:
            content = rec if not isinstance(rec, str) else {"text": rec}
            meta = dict(defaults)
        yield {"content": content, **meta}


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def agent_ingest(store, path, *, quality_level: str, filename=None, model=None,
                 dataset=None, max_iters=3, dry_run=False) -> dict:
    quality_level = schema.validate_quality_level(quality_level)
    s = sniff(path, filename)
    plan = None
    planner = "heuristic"
    err = None
    if model:
        spec = ModelPool(store.root).get(model)
        for _ in range(max(1, max_iters)):
            try:
                cand = llm_plan(spec, s, err)
                if not parse_records(path, cand)[:5]:
                    raise ValueError("plan produced 0 records")
                plan, planner = cand, "llm:" + model
                break
            except Exception as e:  # noqa: BLE001 - feed back to the model
                err = str(e)[:200]
        if plan is None:
            plan = heuristic_plan(s)
            planner = "heuristic (llm failed: %s)" % err
    else:
        plan = heuristic_plan(s)

    records = parse_records(path, plan)
    built = list(build_ingest_records(records, plan))
    report = {
        "filename": s["filename"], "ext": s["ext"], "format": plan["format"],
        "planner": planner, "review": plan.get("review", ""),
        "records_detected": len(records), "plan": plan,
        "sample": built[:3], "quality_level": quality_level,
    }
    if dry_run:
        report["dry_run"] = True
        return report
    name = dataset or Path(s["filename"]).stem or "agent_dataset"
    ds_id = store.catalog.resolve_dataset(name) or store.catalog.add_dataset(
        name, source=s["filename"])
    res = store.ingest_records(
        ds_id,
        built,
        defaults={"quality_level": quality_level},
        content_key="content",
    )
    report.update({"dataset": name, "dataset_id": ds_id,
                   "ingested": res.ingested, "new_blobs": res.new_blobs,
                   "deduped_blobs": res.deduped_blobs})
    return report
