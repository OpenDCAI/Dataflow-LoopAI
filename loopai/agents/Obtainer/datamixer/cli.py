"""DataMixer command-line interface.

Design for agents *and* humans:

* Every command accepts ``--json`` and then prints a single machine-readable
  JSON object to stdout (nothing else), so an agent can parse results reliably.
* Without ``--json`` the same data is rendered as compact human text.
* No interactive prompts, ever. Deterministic given ``--seed``. Errors go to
  stderr as ``{"error": ...}`` (json mode) and exit non-zero.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__, schema
from .store import DataStore, StoreError, read_jsonl


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------

def _emit(args, data, text_fn=None) -> None:
    if getattr(args, "json", False) or text_fn is None:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        text_fn(data)


def _fail(args, msg: str, code: int = 1) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"error": msg}, ensure_ascii=False))
    else:
        print(f"error: {msg}", file=sys.stderr)
    return code


def _open(args) -> DataStore:
    return DataStore.open(args.root)


def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f}{unit}" if unit != "B" else f"{int(f)}B"
        f /= 1024
    return f"{f:.1f}TB"


def _parse_kv(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"expected key=value, got {it!r}")
        k, v = it.split("=", 1)
        # best-effort typing
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _write_lineage(root: Path, doc: dict) -> str:
    lineage = root / "lineage"
    lineage.mkdir(exist_ok=True)
    run_id = str(doc.get("run_id") or doc.get("export_id") or "run")
    path = lineage / f"{run_id}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _safe_dataset_doc_name(dataset: str) -> str:
    keep = []
    for ch in dataset:
        keep.append(ch if ch.isalnum() or ch in ("-", "_", ".") else "_")
    name = "".join(keep).strip("._") or "dataset"
    return f"{name}.md"


def _register_dataset_card(root: Path, dataset: str, card: str | None) -> str | None:
    if not card:
        return None
    src = Path(card).expanduser()
    if not src.exists() or not src.is_file():
        raise ValueError(f"dataset card not found: {card}")
    if src.suffix.lower() != ".md":
        raise ValueError(f"dataset card must be a Markdown .md file: {card}")
    body = src.read_text(encoding="utf-8").strip()
    if not body:
        raise ValueError(f"dataset card is empty: {card}")
    out_dir = root / "dataset_cards"
    out_dir.mkdir(exist_ok=True)
    dst = out_dir / _safe_dataset_doc_name(dataset)
    if src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    return str(dst)


def _is_empty_value(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _nested_value(obj, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _record_field_value(record: dict, field: str, content_key: str):
    direct = _nested_value(record, field)
    if direct is not None:
        return direct
    content = record.get(content_key)
    if isinstance(content, dict):
        return _nested_value(content, field)
    return None


def _validate_ingest_payload(
    *,
    file_path: str,
    content_key: str,
    derived_fields: list[str],
    source_row_count: int | None,
) -> dict:
    if not derived_fields and source_row_count is None:
        return {"rows": None, "derived_fields": []}
    rows = 0
    missing: dict[str, list[int]] = {field: [] for field in derived_fields}
    for idx, rec in enumerate(read_jsonl(file_path), start=1):
        rows += 1
        for field in derived_fields:
            if _is_empty_value(_record_field_value(rec, field, content_key)):
                if len(missing[field]) < 10:
                    missing[field].append(idx)
    if source_row_count is not None and rows != source_row_count:
        raise ValueError(
            f"normalized row count changed: expected {source_row_count}, got {rows}"
        )
    bad = {k: v for k, v in missing.items() if v}
    if bad:
        details = "; ".join(f"{k} empty/missing at rows {v}" for k, v in bad.items())
        raise ValueError(f"derived fields must be non-empty: {details}")
    return {"rows": rows, "derived_fields": derived_fields}


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.path or args.root or ".")
    DataStore.init(root, codec=args.codec).close()
    _emit(args, {"initialized": str(root.resolve()), "codec": args.codec},
          lambda d: print(f"initialized warehouse at {d['initialized']} "
                          f"(codec={d['codec']})"))
    return 0


def cmd_schema(args) -> int:
    _emit(args, schema.describe(),
          lambda d: print("\n".join(
              f"{f['name']:<18} {f['type']:<8} {f['dimension']:<11}"
              f"{'idx' if f['indexed'] else '   '}  {f['description']}"
              for f in d["fields"])))
    return 0


def cmd_dataset_add(args) -> int:
    s = _open(args)
    ds_id = s.catalog.add_dataset(
        name=args.name, source=args.source, license=args.license,
        owner=args.owner, description=args.description,
    )
    s.close()
    _emit(args, {"dataset_id": ds_id, "name": args.name},
          lambda d: print(f"added dataset {d['dataset_id']} ({d['name']})"))
    return 0


def cmd_dataset_update(args) -> int:
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset)
    if not ds_id:
        s.close()
        return _fail(args, f"dataset not found: {args.dataset}")
    fields = {
        key: getattr(args, key)
        for key in ("name", "source", "license", "owner", "description")
        if getattr(args, key) is not None
    }
    updated = s.catalog.update_dataset(ds_id, **fields)
    s.close()
    if updated is None:
        return _fail(args, f"dataset not found: {args.dataset}")
    _emit(args, updated, lambda d: print(json.dumps(d, ensure_ascii=False, indent=2)))
    return 0


def cmd_dataset_delete(args) -> int:
    if not args.yes:
        s = _open(args)
        ds_id = s.catalog.resolve_dataset(args.dataset)
        ds = s.catalog.get_dataset(ds_id) if ds_id else None
        s.close()
        if ds is None:
            return _fail(args, f"dataset not found: {args.dataset}")
        _emit(
            args,
            {
                "confirm_required": True,
                "dataset": ds,
                "message": f"delete dataset {ds_id} and its samples?",
            },
            lambda d: print(d["message"]),
        )
        return 0
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset)
    if not ds_id:
        s.close()
        return _fail(args, f"dataset not found: {args.dataset}")
    try:
        result = s.delete_dataset(ds_id, reason=args.reason or "webui delete")
    except (KeyError, ValueError) as exc:
        s.close()
        return _fail(args, str(exc))
    s.close()
    _emit(args, result, lambda d: print(
        f"deleted dataset {d['dataset_id']} ({d['dataset_name']}): "
        f"{d['erased']} samples, {d['blobs_deleted']} blobs"))
    return 0


def cmd_dataset_list(args) -> int:
    s = _open(args)
    rows = s.catalog.list_datasets()
    s.close()

    def txt(rows):
        if not rows:
            print("(no datasets)")
            return
        for r in rows:
            print(f"{r['id']}  {r['name']:<24} samples={r['n_samples']:<8} "
                  f"license={r.get('license') or '-'}")
    _emit(args, {"datasets": rows}, lambda d: txt(d["datasets"]))
    return 0


def cmd_dataset_show(args) -> int:
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset)
    ds = s.catalog.get_dataset(ds_id) if ds_id else None
    if not ds:
        s.close()
        return _fail(args, f"dataset not found: {args.dataset}")
    ds["n_samples"] = s.catalog.count(dataset_id=ds_id)
    s.close()
    _emit(args, ds, lambda d: print(json.dumps(d, ensure_ascii=False, indent=2)))
    return 0


def cmd_ingest(args) -> int:
    import time

    s = _open(args)
    benchmark_set = getattr(args, "benchmark_set", None)
    if benchmark_set:
        from . import contam
        # Explicit reference-data ingestion is separate from training ingest:
        # a registered benchmark otherwise filters out its own records.
        ds_id = s.catalog.resolve_dataset(args.dataset)
        registered = next((item for item in contam.list_sets(s.root)
                           if item.get("name") == benchmark_set), None)
        if (not ds_id or not registered or
                (registered.get("benchmark_dataset") or {}).get("id") != ds_id):
            s.close()
            return _fail(args, "--benchmark-set must name a guard linked to the existing target dataset")
        if args.stage != "eval":
            s.close()
            return _fail(args, "--benchmark-set requires --stage eval; reference records are not training data")
        for batch in s.catalog.iter_query(dataset_id=ds_id):
            if any(row.get("stage") != "eval" for row in batch):
                s.close()
                return _fail(args, "--benchmark-set target contains records outside stage=eval; use a separate benchmark dataset")
    try:
        validation = _validate_ingest_payload(
            file_path=args.file,
            content_key=args.content_key,
            derived_fields=args.derived_field or [],
            source_row_count=args.source_row_count,
        )
    except FileNotFoundError:
        s.close()
        return _fail(args, f"file not found: {args.file}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        s.close()
        return _fail(args, f"invalid JSONL in {args.file}: {e}")
    except (ValueError, OSError) as e:
        s.close()
        return _fail(args, str(e))
    defaults = {}
    for key in ("stage", "domain", "lang", "source", "license", "modality",
                "task_type"):
        v = getattr(args, key, None)
        if v is not None:
            defaults[key] = v
    try:
        defaults.update(_parse_kv(args.tag))
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    tagged_quality_level = defaults.get("quality_level")
    if tagged_quality_level is not None and tagged_quality_level != args.quality_level:
        s.close()
        return _fail(
            args,
            f"tag quality_level {tagged_quality_level!r} conflicts with "
            f"--quality-level {args.quality_level!r}",
        )
    defaults["quality_level"] = args.quality_level
    for key in ("processing_level", "source_kind", "source_uri", "split",
                "loop_uuid", "version_id"):
        value = getattr(args, key, None)
        if value:
            defaults[key] = value
    if args.idempotency_key:
        defaults["idempotency_key"] = args.idempotency_key
    try:
        records = list(read_jsonl(args.file))
        for row_number, record in enumerate(records, 1):
            if not isinstance(record, dict):
                raise ValueError(f"ingest record {row_number} must be an object")
            if benchmark_set:
                if record.get("stage") not in (None, "", "eval"):
                    raise ValueError(f"benchmark record {row_number} must use stage=eval")
                # Keep references excluded by the usual contamination filter,
                # even when their question is too short for n-gram matching.
                record.update(stage="eval", is_contaminated=1,
                              contam_source=benchmark_set, guard_only=True)
            row_domain = str(record.get("domain") or "").strip().casefold()
            batch_domain = str(defaults.get("domain") or "").strip().casefold()
            if row_domain == "finance" and batch_domain != "finance":
                raise ValueError(
                    f"ingest record {row_number} declares domain=finance; rerun the batch "
                    "with --domain finance so the batch-level domain metadata matches"
                )
            if batch_domain == "finance" and row_domain and row_domain != "finance":
                raise ValueError(
                    f"ingest record {row_number} domain {row_domain!r} conflicts with "
                    "--domain finance"
                )
            if (
                args.domain
                and record.get("domain") not in (None, "", args.domain)
            ):
                raise ValueError(
                    f"ingest record {row_number} domain {record['domain']!r} "
                    f"conflicts with --domain {args.domain!r}"
                )
            if (
                "quality_level" in record
                and record["quality_level"] != args.quality_level
            ):
                raise ValueError(
                    f"ingest record {row_number} quality_level "
                    f"{record['quality_level']!r} conflicts with --quality-level "
                    f"{args.quality_level!r}"
                )
    except FileNotFoundError:
        s.close()
        return _fail(args, f"file not found: {args.file}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        s.close()
        return _fail(args, f"invalid JSONL in {args.file}: {e}")
    except (ValueError, OSError) as e:
        s.close()
        return _fail(args, str(e))
    started = time.time()
    run_id = "ingest-" + __import__("hashlib").sha256(
        f"{args.dataset}:{args.file}:{started}".encode("utf-8")
    ).hexdigest()[:16]
    try:
        dataset_card = _register_dataset_card(
            s.root, args.dataset, args.dataset_card
        )
    except (ValueError, OSError) as e:
        s.close()
        return _fail(args, str(e))
    ds_id = s.catalog.resolve_dataset(args.dataset)
    if ds_id is None:
        ds_id = s.catalog.add_dataset(
            name=args.dataset, source=args.source, license=args.license
        )
    try:
        res = s.ingest_records(
            ds_id, records, defaults=defaults,
            content_key=args.content_key,
            allow_duplicates=args.allow_duplicates,
            tokenizer=args.tokenizer,
            decontaminate=not bool(benchmark_set),
            io_workers=getattr(args, "io_workers", 1),
        )
    except FileNotFoundError:
        s.close()
        return _fail(args, f"file not found: {args.file}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        s.close()
        return _fail(args, f"invalid JSONL in {args.file}: {e}")
    except (ValueError, RuntimeError) as e:        # bad/unavailable tokenizer
        s.close()
        return _fail(args, str(e))
    except OSError as e:
        s.close()
        return _fail(args, f"cannot read {args.file}: {e}")
    root = s.root
    s.close()
    out = {
        "dataset_id": ds_id, "ingested": res.ingested,
        "written": res.written, "merged": res.merged,
        "new_blobs": res.new_blobs, "deduped_blobs": res.deduped_blobs,
        "contaminated": res.contaminated,
        "contam_sources": res.contam_sources,
        "quality_level": args.quality_level,
    }
    if benchmark_set:
        out["benchmark_set"] = benchmark_set
        out["reference_only"] = True
    if dataset_card:
        out["dataset_card"] = dataset_card
    if validation.get("derived_fields"):
        out["derived_fields"] = validation["derived_fields"]
        out["validated_rows"] = validation["rows"]
    lineage_path = _write_lineage(
        root,
        {
            "kind": "ingest",
            "run_id": run_id,
            "timestamp": started,
            "finished_at": time.time(),
            "dataset_id": ds_id,
            "dataset": args.dataset,
            "input_uri": args.file,
            "defaults": defaults,
            "dataset_card": dataset_card,
            "validation": validation,
            "result": out,
        },
    )
    out["lineage"] = lineage_path
    _emit(args, out, lambda d: print(
        f"read {d['ingested']} records into {d['dataset_id']}: "
        f"{d['written']} new samples, {d['merged']} merged (duplicate id); "
        f"blobs: {d['new_blobs']} new, {d['deduped_blobs']} dedup hits"
        + (
            f"; benchmark-contaminated skipped: {d['contaminated']} {d['contam_sources']}"
            if d.get("contaminated") else ""
        )))
    return 0


def cmd_query(args) -> int:
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset) if args.dataset else None
    try:
        rows = s.catalog.query(
            where=args.filter, dataset_id=ds_id, limit=args.limit,
            columns=args.columns,
        )
        total = s.catalog.count(where=args.filter, dataset_id=ds_id)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    s.close()

    def txt(d):
        print(f"# {d['returned']}/{d['total']} samples")
        for r in d["rows"]:
            print(json.dumps(r, ensure_ascii=False))
    _emit(args, {"total": total, "returned": len(rows), "rows": rows},
          lambda d: txt(d))
    return 0


# keys the warehouse owns; never round-tripped as writable sample fields
_RESERVED_FIELDS = {"content", "sample_id", "dataset_id", "cid", "created_at",
                    "version", "tags", "tags_json", "embedding"}


def cmd_export_jsonl(args) -> int:
    """Dump a dataset to flat JSONL for DataFlow's FileStorage.

    Each line is ``{sample_id, <field>: <text>, ...scalar metadata}`` so a
    file-based DataFlow pipeline (e.g. one authored by the DataFlow-Skills
    ``generating-dataflow-pipeline`` skill) can read ``--field`` as its
    ``input_key`` and the ``sample_id`` lets ``apply-jsonl`` merge results back.
    """
    from . import utils
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset) if args.dataset else None
    field = args.field
    written = 0
    try:
        out_path = Path(args.out)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            for batch in s.catalog.iter_query(where=args.filter, dataset_id=ds_id,
                                              batch_size=args.batch_size):
                for r in batch:
                    try:
                        content = s.get_content(r["cid"])
                    except KeyError:
                        content = None
                    rec = {"sample_id": r["sample_id"],
                           field: utils.extract_text(content) if content else ""}
                    for k, v in r.items():
                        if k in _RESERVED_FIELDS or k == field or v is None:
                            continue
                        rec[k] = v
                    for k, v in (r.get("tags") or {}).items():   # flatten tags
                        if k not in rec and v is not None:
                            rec[k] = v
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                    if args.limit and written >= args.limit:
                        raise _StopExport
    except _StopExport:
        pass
    except ValueError as e:                       # bad filter
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, {"out": args.out, "exported": written, "field": field},
          lambda d: print(f"exported {d['exported']} samples to {d['out']} "
                          f"(text field '{d['field']}')"))
    return 0


def cmd_apply_jsonl(args) -> int:
    """Merge a DataFlow-processed JSONL back into the warehouse by sample_id.

    Reads ``--file`` (the output of a DataFlow FileStorage pipeline), and for
    each record writes its non-reserved fields onto the matching sample (custom
    keys land in the sample's tags). The text ``--field`` is ignored, so the
    stored content is never overwritten.
    """
    s = _open(args)
    key, field = args.key, args.field
    updated = missing = skipped = seen = 0
    try:
        records = read_jsonl(args.file)
    except FileNotFoundError:
        s.close()
        return _fail(args, f"file not found: {args.file}")
    try:
        for rec in records:
            if not isinstance(rec, dict):
                skipped += 1
                continue
            seen += 1
            sid = rec.get(key)
            if not sid:
                skipped += 1
                continue
            if not s.catalog.get_sample(sid):
                missing += 1
                continue
            upd = {k: v for k, v in rec.items()
                   if k not in _RESERVED_FIELDS and k != key and k != field}
            if upd:
                s.catalog.update_fields(sid, upd)
                updated += 1
        s.catalog.commit()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        s.close()
        return _fail(args, f"invalid JSONL in {args.file}: {e}")
    s.close()
    _emit(args, {"seen": seen, "updated": updated, "missing": missing,
                 "skipped": skipped},
          lambda d: print(f"applied {d['updated']} updates "
                          f"({d['missing']} sample_id not found, "
                          f"{d['skipped']} skipped)"))
    return 0


def _find_latest_badcase_questions(output_dir: str, task_id: str | None) -> str | None:
    """Locate the newest ``badcase_questions_*.jsonl`` from the last Analyzer round.

    Analyzer writes the manifest under ``<output_dir>/<task_id>/analyzer`` (and,
    for Math reports, a nested bundle subdirectory), so we glob that subtree and
    return the most recently modified file. When ``task_id`` is unknown we fall
    back to scanning the whole output tree.
    """
    base = Path(output_dir or "./outputs").expanduser()
    roots: list[Path] = []
    if task_id:
        roots.append(base / task_id / "analyzer")
    roots.append(base)
    seen: set[Path] = set()
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        candidates.extend(root.rglob("badcase_questions_*.jsonl"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(latest)


def _badcase_queries(path: str, *, field: str) -> list[dict]:
    """Read the Analyzer bad-case manifest and keep one recall query per row."""
    queries: list[dict] = []
    for rec in read_jsonl(path):
        if not isinstance(rec, dict):
            continue
        text = rec.get(field)
        if not text or not str(text).strip():
            continue
        queries.append({
            "question_id": rec.get("question_id") or rec.get("source_action_id"),
            "query": str(text).strip(),
            "domain": rec.get("domain"),
            "capability_bucket": rec.get("capability_bucket"),
        })
    return queries


def cmd_recall_badcases(args) -> int:
    """Bad-case-driven multi-route recall + candidate outflow for DataFlowAgent.

    Reads the last Analyzer round's ``badcase_questions_*.jsonl`` (auto-located
    when ``--from`` is omitted), runs one recall per bad-case question over the
    freshly-embedded lake, unions the hits (dedup by ``sample_id``), and writes
    the candidate set to a flat JSONL that becomes the DataFlowAgent input.
    ``--limit`` is the per-question top-k (recall breadth) the agent controls.
    """
    from . import utils

    total_limit = getattr(args, "total_limit", None)
    if total_limit is not None and total_limit <= 0:
        return _fail(args, "--total-limit must be positive")
    io_workers = getattr(args, "io_workers", 8)
    if io_workers <= 0:
        return _fail(args, "--io-workers must be positive")

    src = args.source or _find_latest_badcase_questions(args.output_dir, args.task_id)
    if not src:
        return _fail(args, "no badcase_questions_*.jsonl found; pass --from explicitly")
    if not Path(src).is_file():
        return _fail(args, f"badcase questions file not found: {src}")
    try:
        queries = _badcase_queries(src, field=args.question_field)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return _fail(args, f"invalid JSONL in {src}: {e}")
    if not queries:
        return _fail(args, f"no usable '{args.question_field}' queries in {src}")

    s = _open(args)

    # Match query embeddings to the model that built the index. When a real
    # encoder server is configured (e.g. bge via an OpenAI-compatible endpoint),
    # embed the bad-case questions through it so query and document vectors
    # share the same space and dimensionality; otherwise fall back to the
    # dependency-free hashing embedder.
    if not args.keyword and s.index.vector_config.get("backend") == "lancedb":
        if getattr(args, "embed_model", "") and args.embed_model != s.index.vector_config["model"]:
            s.close()
            return _fail(args, "query embedding model differs from the persisted vector index")
        if getattr(args, "embed_base_url", ""):
            s.index.vectors.metadata["base_url"] = args.embed_base_url
    elif not args.keyword and getattr(args, "embed_base_url", ""):
        from loopai.skills.ObtainerCLI.index import _openai_compatible_embeddings

        base_url = args.embed_base_url
        api_key = getattr(args, "embed_api_key", "") or ""
        model = getattr(args, "embed_model", "") or "BAAI/bge-small-zh-v1.5"

        def _bge_query_embedder(text: str):
            vectors = _openai_compatible_embeddings(
                base_url=base_url, api_key=api_key, model=model, inputs=[text]
            )
            return vectors[0]

        s.index.query_embedder = _bge_query_embedder

    restrict = None
    if args.filter:
        try:
            rows = s.catalog.query(where=args.filter, columns="sample_id")
        except ValueError as e:
            s.close()
            return _fail(args, str(e))
        restrict = {r["sample_id"] for r in rows}

    # Multi-route recall: keep the best score per sample across all queries.
    best_score: dict[str, float] = {}
    hit_by: dict[str, str] = {}
    per_query: list[dict] = []
    for q in queries:
        try:
            if args.keyword:
                hits = s.index.keyword_recall(q["query"], top_k=args.limit,
                                              restrict=restrict)
            else:
                hits = s.index.semantic_recall(q["query"], top_k=args.limit,
                                               restrict=restrict, min_sim=args.min_sim)
        except Exception as e:  # index missing / embedding failure
            s.close()
            return _fail(args, f"recall failed for question {q.get('question_id')}: {e}")
        per_query.append({"question_id": q["question_id"], "hits": len(hits)})
        for sid, score in hits:
            score = float(score)
            if sid not in best_score or score > best_score[sid]:
                best_score[sid] = score
                hit_by[sid] = q["question_id"] or ""

    union = sorted(best_score, key=lambda x: best_score[x], reverse=True)
    recalled_before_limit = len(union)
    if total_limit is not None:
        union = union[:total_limit]
    written = 0
    field = args.field

    def recalled_content():
        from concurrent.futures import ThreadPoolExecutor
        def load(smp):
            try:
                return s.get_content(smp["cid"])
            except KeyError:
                return None
        with ThreadPoolExecutor(max_workers=io_workers) as pool:
            for start in range(0, len(union), io_workers * 4):
                # Catalog stays on its owner thread; only immutable CAS reads
                # are concurrent. map preserves the ranked candidate order.
                samples = [s.catalog.get_sample(sid) for sid in union[start:start + io_workers * 4]]
                samples = [smp for smp in samples if smp]
                yield from zip(samples, pool.map(load, samples))
    try:
        out_path = Path(args.out)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            for smp, content in recalled_content():
                sid = smp["sample_id"]
                rec = {"sample_id": sid,
                       field: utils.extract_text(content) if content else "",
                       "recall_score": round(best_score[sid], 4),
                       "recall_question_id": hit_by.get(sid) or None}
                for k, v in smp.items():
                    if k in _RESERVED_FIELDS or k == field or v is None:
                        continue
                    rec[k] = v
                for k, v in (smp.get("tags") or {}).items():
                    if k not in rec and v is not None:
                        rec[k] = v
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
    except OSError as e:
        s.close()
        return _fail(args, f"failed to write {args.out}: {e}")
    s.close()

    result = {
        "out": args.out,
        "source": src,
        "questions": len(queries),
        "recalled": len(union),
        "recalled_before_limit": recalled_before_limit,
        "total_limit": total_limit,
        "exported": written,
        "mode": "keyword" if args.keyword else "semantic",
        "limit": args.limit,
        "per_query": per_query,
    }
    _emit(args, result,
          lambda d: print(f"recalled {d['recalled']} candidates from "
                          f"{d['questions']} bad-case questions -> "
                          f"{d['exported']} rows in {d['out']}"))
    return 0


class _StopExport(Exception):
    """Internal: stop streaming once an export --limit is reached."""


def cmd_sample_show(args) -> int:
    s = _open(args)
    smp = s.catalog.get_sample(args.sample_id)
    if not smp:
        s.close()
        return _fail(args, f"sample not found: {args.sample_id}")
    if args.content:
        try:
            smp["content"] = s.get_content(smp["cid"])
        except KeyError:
            smp["content"] = None
    s.close()
    _emit(args, smp, lambda d: print(json.dumps(d, ensure_ascii=False, indent=2)))
    return 0


def cmd_stats(args) -> int:
    s = _open(args)
    st = s.storage_stats()
    s.close()

    def txt(d):
        print(f"samples           : {d['samples']}")
        print(f"unique blobs      : {d['unique_blobs']}")
        print(f"logical raw       : {_human_bytes(d['logical_raw_bytes'])}")
        print(f"unique raw        : {_human_bytes(d['unique_raw_bytes'])}")
        print(f"stored blobs      : {_human_bytes(d['stored_blob_bytes'])}")
        print(f"catalog db        : {_human_bytes(d['catalog_db_bytes'])}")
        print(f"total on disk     : {_human_bytes(d['total_on_disk_bytes'])}")
        print(f"dedup ratio       : {d['dedup_ratio']}x")
        print(f"compression ratio : {d['compression_ratio']}x")
        print(f"content savings   : {d['content_savings_ratio']}x "
              f"(dedup x compression)")
        print(f"token sources     : {d.get('token_sources', {})}")
    _emit(args, st, txt)
    return 0


def cmd_status(args) -> int:
    s = _open(args)
    storage = s.storage_stats()
    index = s.index.stats()
    datasets = s.catalog.list_datasets()
    columns = s.catalog.columns_overview()
    root = str(s.root)
    s.close()
    out = {
        "warehouse": root,
        "samples": storage["samples"],
        "datasets": len(datasets),
        "storage": storage,
        "index": index,
        "columns": columns,
    }
    _emit(args, out, lambda d: print(
        f"warehouse={d['warehouse']} datasets={d['datasets']} "
        f"samples={d['samples']} vectors={d['index']['vectors']} "
        f"fulltext={d['index']['fulltext_docs']}"
    ))
    return 0


def cmd_catalog_repair_indexes(args) -> int:
    s = _open(args)
    before = [str(row[0]) for row in s.catalog.conn.execute("PRAGMA quick_check")]
    s.catalog.conn.execute("REINDEX")
    s.catalog.conn.commit()
    s.catalog.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = [str(row[0]) for row in s.catalog.conn.execute("PRAGMA quick_check")]
    root = str(s.root)
    s.close()
    out = {
        "warehouse": root,
        "operation": "REINDEX",
        "before": before,
        "after": after,
        "ok": after == ["ok"],
    }
    _emit(args, out, lambda d: print(
        f"warehouse={d['warehouse']} operation={d['operation']} ok={d['ok']}"
    ))
    return 0 if out["ok"] else 1


def cmd_dist(args) -> int:
    s = _open(args)
    try:
        rows = s.catalog.distribution(args.column, where=args.filter)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    s.close()

    def txt(d):
        for r in d["distribution"]:
            print(f"{str(r['value']):<20} n={r['n']:<8} tokens={r['tokens']}")
    _emit(args, {"column": args.column, "distribution": rows}, txt)
    return 0


def cmd_domain_list(args) -> int:
    """List the durable domain vocabulary used by ``domain_classify``."""
    s = _open(args)
    rows = s.catalog.sync_domain_classes()
    s.catalog.commit()
    s.close()
    _emit(args, {"domains": rows}, lambda d: print("\n".join(
        f"{row['name']:<24} {row['source']}" for row in d["domains"]
    )))
    return 0


def cmd_domain_add(args) -> int:
    s = _open(args)
    created = s.catalog.register_domain_classes(args.name, source="user")
    rows = s.catalog.sync_domain_classes()
    s.catalog.commit()
    s.close()
    _emit(args, {"registered": created, "domains": rows}, lambda d: print(
        "registered: " + (", ".join(d["registered"]) or "(already present)")
    ))
    return 0


def cmd_domain_sync(args) -> int:
    s = _open(args)
    rows = s.catalog.sync_domain_classes()
    s.catalog.commit()
    s.close()
    _emit(args, {"domains": rows, "count": len(rows)}, lambda d: print(
        f"synchronised {d['count']} domain classes"
    ))
    return 0


def cmd_hist(args) -> int:
    s = _open(args)
    try:
        h = s.catalog.histogram(args.column, bins=args.bins, where=args.filter)
    except (ValueError, Exception) as e:  # noqa: BLE001
        s.close()
        return _fail(args, str(e))
    s.close()

    def txt(d):
        if not d["bins"]:
            print("(no numeric data)")
            return
        peak = max((b["n"] for b in d["bins"]), default=1) or 1
        for b in d["bins"]:
            bar = "#" * int(40 * b["n"] / peak)
            print(f"[{b['lo']:.3f},{b['hi']:.3f})  {b['n']:>6}  {bar}")
    _emit(args, h, txt)
    return 0


def cmd_columns(args) -> int:
    s = _open(args)
    ov = s.catalog.columns_overview()
    s.close()

    def txt(d):
        print(f"# {d['total_samples']} samples")
        print("core columns (queryable / exportable):")
        for c in d["core"]:
            print(f"  {c['name']:<18} {c['type']:<8} non_null={c['non_null']:<8}"
                  f"{'idx' if c['indexed'] else ''}")
        if d["tags"]:
            print("custom tag keys (json_extract(tags_json,'$.<key>')):")
            for tg in d["tags"]:
                print(f"  {tg['key']:<18} e.g. {tg['example']}")
    _emit(args, ov, txt)
    return 0


def cmd_grade(args) -> int:
    from . import recipe as R
    s = _open(args)
    thresholds = None
    if args.tiers:
        try:
            thresholds = [float(x) for x in args.tiers.split(",")]
        except ValueError:
            s.close()
            return _fail(args, f"bad --tiers: {args.tiers!r}")
    try:
        g = R.grade(s, where=args.filter, column=args.column, thresholds=thresholds)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    s.close()

    def txt(d):
        print(f"# quality grade on {d['column']} (thresholds {d['thresholds']})")
        peak = max((t["samples"] for t in d["tiers"]), default=1) or 1
        for t in d["tiers"]:
            rng = (f">={t['min']}" if t["max"] is None and t["min"] is not None
                   else f"<{t['max']}" if t["min"] is None and t["max"] is not None
                   else f"[{t['min']},{t['max']})" if t["min"] is not None
                   else "unscored")
            bar = "#" * int(36 * t["samples"] / peak)
            print(f"  {t['tier']:<8} {rng:<14} n={t['samples']:<8} "
                  f"tok={t['tokens']:<10} {bar}")
    _emit(args, g, txt)
    return 0


def cmd_op_list(args) -> int:
    from .operators import available, param_info
    specs = [
        {"name": sp.name, "version": sp.version, "kind": sp.kind,
         "stateful": sp.stateful, "gpu_required": sp.gpu_required,
         "description": sp.description, "params": param_info(sp.name)}
        for sp in available()
    ]
    _emit(args, {"operators": specs}, lambda d: print("\n".join(
        f"{o['name']:<16} v{o['version']:<6} {o['kind']:<8} {o['description']}"
        for o in d["operators"])))
    return 0


def cmd_op_run(args) -> int:
    from array import array
    from .operators import create, base
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset) if args.dataset else None
    try:
        kwargs = _parse_kv(args.arg)
        op = create(args.name, **kwargs)
    except (KeyError, ValueError) as e:
        s.close()
        return _fail(args, str(e))
    ctx = base.OperatorContext(run_id="op-run", seed=args.seed,
                               root=str(s.root), extra=kwargs)
    op.setup(ctx)
    reserved = {"content", "sample_id", "dataset_id", "cid", "created_at",
                "version", "tags", "embedding"}
    processed = updated = 0

    def writeback(rows):
        nonlocal updated
        for r in rows:
            sid = r.get("sample_id")
            if not sid:
                continue
            if r.get("embedding") is not None:
                s.index.vectors.add(sid, array("f", r["embedding"]))
            upd = {k: v for k, v in r.items() if k not in reserved}
            if upd:
                s.catalog.update_fields(sid, upd)
                updated += 1
        s.catalog.commit()

    # stream in constant memory: batch -> attach content -> process -> write back
    for batch in s.catalog.iter_query(where=args.filter, dataset_id=ds_id,
                                      batch_size=args.batch_size):
        processed += len(batch)
        for r in batch:
            try:
                r["content"] = s.get_content(r["cid"])
            except KeyError:
                r["content"] = None
        writeback(op.process(batch, ctx))
    flushed = op.finalize(ctx)
    if flushed:
        for b in flushed:
            writeback(b)
    op.teardown(ctx)
    if "text_embed" == args.name:
        s.index.vectors.flush()
    s.close()
    result = {"operator": args.name, "processed": processed, "updated": updated}
    if hasattr(op, "usage_report"):
        result["llm_usage"] = op.usage_report()
    _emit(args, result,
          lambda d: print(f"ran {d['operator']}: processed {d['processed']}, "
                          f"updated {d['updated']} samples"
                          + (f"\n  llm_usage: {d['llm_usage']}"
                             if d.get("llm_usage") else "")))
    return 0


def _load_texts(path: str, text_field: str = "text"):
    """Read benchmark texts: JSONL (take ``text_field`` or whole record) or one
    text per line for any other extension."""
    if path.endswith((".jsonl", ".ndjson")):
        for rec in read_jsonl(path):
            if isinstance(rec, dict):
                yield str(rec.get(text_field) or
                          " ".join(str(v) for v in rec.values()))
            else:
                yield str(rec)
    else:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line


_DECONTAM_WORKER_SETS = None
_DECONTAM_WORKER_THRESHOLD = 0.8


def _default_decontam_workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def _decontam_worker_init(root: str, against, threshold: float) -> None:
    global _DECONTAM_WORKER_SETS, _DECONTAM_WORKER_THRESHOLD
    from . import contam

    _DECONTAM_WORKER_SETS = contam.load_sets(root, against)
    _DECONTAM_WORKER_THRESHOLD = threshold


def _decontam_match_batch(batch: list[tuple[str, str]]) -> list[tuple[str, bool, str]]:
    from . import contam

    sets = _DECONTAM_WORKER_SETS
    if sets is None:
        raise RuntimeError("decontamination worker was not initialized")
    out = []
    for sample_id, text in batch:
        hit, source = contam.match(text, sets, _DECONTAM_WORKER_THRESHOLD)
        out.append((sample_id, hit, source))
    return out


def _decontaminate_catalog(
    store,
    *,
    against: list[str] | None = None,
    threshold: float = 0.8,
    apply: bool = False,
    dataset_id: str | None = None,
    where: str | None = None,
    workers: int | None = None,
    batch_size: int = 512,
) -> dict:
    from collections import Counter
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    from . import contam, utils

    sets = contam.load_sets(store.root, against)
    if not sets:
        raise ValueError("no contamination sets registered; run `datamixer contam add` first")

    workers = max(1, int(workers or _default_decontam_workers()))
    batch_size = max(1, int(batch_size or 512))
    by_source: Counter = Counter()
    scanned = 0
    contaminated = 0
    removed = 0

    def row_text(row: dict) -> tuple[str, str]:
        try:
            content = store.get_content(row["cid"])
        except KeyError:
            content = None
        return row.get("sample_id"), utils.extract_text(content)

    def apply_matches(matches: list[tuple[str, bool, str]]) -> None:
        nonlocal contaminated, removed
        delete_ids = []
        for sample_id, hit, source in matches:
            if not sample_id:
                continue
            if hit:
                contaminated += 1
                by_source[source or "unknown"] += 1
                if apply:
                    delete_ids.append(sample_id)
                else:
                    store.catalog.update_fields(sample_id, {
                        "is_contaminated": 1,
                        "contam_source": source})
            elif not apply:
                store.catalog.update_fields(sample_id, {
                    "is_contaminated": 0,
                    "contam_source": None})
        if delete_ids:
            removed += store.catalog.delete_by_ids(delete_ids)
        elif not apply:
            store.catalog.commit()

    if workers == 1:
        for rows in store.catalog.iter_query(where=where, dataset_id=dataset_id,
                                             batch_size=batch_size):
            batch = [row_text(row) for row in rows]
            scanned += len(batch)
            matches = []
            for sample_id, text in batch:
                hit, source = contam.match(text, sets, threshold)
                matches.append((sample_id, hit, source))
            apply_matches(matches)
    else:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_decontam_worker_init,
            initargs=(str(store.root), against, threshold),
        ) as pool:
            pending = set()

            def drain(done) -> None:
                for fut in done:
                    apply_matches(fut.result())

            for rows in store.catalog.iter_query(where=where, dataset_id=dataset_id,
                                                 batch_size=batch_size):
                batch = [row_text(row) for row in rows]
                scanned += len(batch)
                pending.add(pool.submit(_decontam_match_batch, batch))
                if len(pending) >= workers * 2:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    drain(done)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                drain(done)

    if not apply:
        store.catalog.commit()
    return {
        "scanned": scanned,
        "contaminated": contaminated,
        "by_source": dict(by_source),
        "removed": removed,
        "applied": bool(apply),
        "workers": workers,
        "batch_size": batch_size,
    }


def cmd_contam_add(args) -> int:
    from . import contam
    s = _open(args)
    try:
        workers = max(1, int(args.workers or _default_decontam_workers()))
        # Reject invalid scope before register() can create or replace the set.
        # Otherwise a failed command leaves a guard that later affects ingest.
        scope = getattr(args, "filter", None)
        if scope:
            s.catalog.count(where=scope)
        benchmark_dataset = None
        benchmark_dataset_arg = getattr(args, "benchmark_dataset", None)
        if benchmark_dataset_arg:
            dataset_id = s.catalog.resolve_dataset(benchmark_dataset_arg)
            if not dataset_id:
                raise ValueError(f"benchmark dataset not found: {benchmark_dataset_arg}")
            dataset = s.catalog.get_dataset(dataset_id)
            benchmark_dataset = {"id": dataset_id, "name": dataset["name"]}
        meta = contam.register(s.root, args.name,
                               _load_texts(args.file, args.text_field),
                               ngram=args.ngram,
                               workers=workers,
                               batch_size=args.batch_size,
                               benchmark_dataset=benchmark_dataset)
        audit = _decontaminate_catalog(
            s,
            against=[args.name],
            threshold=args.threshold,
            apply=True,
            where=scope,
            workers=workers,
            batch_size=args.batch_size,
        )
    except FileNotFoundError:
        s.close()
        return _fail(args, f"file not found: {args.file}")
    except (json.JSONDecodeError, OSError) as e:
        s.close()
        return _fail(args, f"cannot read {args.file}: {e}")
    except (ValueError, KeyError) as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    out = {**meta, "auto_decontamination": audit}
    _emit(args, out, lambda d: print(
        f"registered contamination set '{d['name']}': {d['num_texts']} texts, "
        f"{d['num_ngrams']} {d['ngram']}-grams "
        f"using {d.get('build_workers', 1)} build workers; "
        f"auto-decontaminated: scanned {d['auto_decontamination']['scanned']}, "
        f"removed {d['auto_decontamination']['removed']} "
        f"using {d['auto_decontamination']['workers']} workers"))
    return 0


def cmd_contam_list(args) -> int:
    from . import contam
    s = _open(args)
    sets = contam.list_sets(s.root)
    s.close()
    _emit(args, {"sets": sets}, lambda d: print("\n".join(
        f"{m['name']:<20} ngram={m['ngram']:<3} texts={m['num_texts']:<8} "
        f"ngrams={m['num_ngrams']}" for m in d["sets"]) or "(no sets)"))
    return 0


def cmd_decontaminate(args) -> int:
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset) if args.dataset else None
    against = ([x.strip() for x in args.against.split(",") if x.strip()]
               if args.against else None)
    try:
        res = _decontaminate_catalog(
            s,
            against=against,
            threshold=args.threshold,
            apply=args.apply,
            dataset_id=ds_id,
            where=args.filter,
            workers=args.workers,
            batch_size=args.batch_size,
        )
    except (ValueError, KeyError) as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, res, lambda d: print(
        f"scanned {d['scanned']}, contaminated {d['contaminated']} "
        f"{dict(d['by_source'])}" + (f", removed {d['removed']}"
                                     if d['applied'] else "")
        + f", workers {d['workers']}"))
    return 0


def cmd_pii_redact(args) -> int:
    s = _open(args)
    ds_id = s.catalog.resolve_dataset(args.dataset) if args.dataset else None
    types = None
    if args.types:
        from . import pii
        types = [t.strip().upper() for t in args.types.split(",") if t.strip()]
        bad = [t for t in types if t not in pii.PATTERNS]
        if bad:
            s.close()
            return _fail(args, f"unknown PII types {bad}; "
                               f"choose from {list(pii.PATTERNS)}")
    try:
        res = s.redact_pii(where=args.filter, dataset_id=ds_id, types=types,
                           dry_run=args.dry_run)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, res, lambda d: print(
        f"scanned {d['scanned']}, "
        + ("would redact " if not d["applied"] else "redacted ")
        + f"{d['redacted']} samples {d['by_type']}"))
    return 0


def cmd_erase(args) -> int:
    s = _open(args)
    if not args.sample_id and not args.filter:
        s.close()
        return _fail(args, "provide a sample id or --filter to erase")
    ds_id = s.catalog.resolve_dataset(args.dataset) if args.dataset else None
    try:
        res = s.erase(sample_ids=[args.sample_id] if args.sample_id else None,
                      where=args.filter, dataset_id=ds_id, reason=args.reason)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, res, lambda d: print(
        f"erased {d['erased']} samples (blobs deleted {d['blobs_deleted']}, "
        f"vectors removed {d['vectors_removed']}); audit logged"))
    return 0


def cmd_model_add(args) -> int:
    from .models import ModelPool, ModelSpec, DEFAULTS
    s = _open(args)
    try:
        spec = ModelSpec(
            name=args.name, api_url=args.api_url, api_key=args.key or "",
            response_format=args.format, model=args.model or args.name,
            note=args.note or "",
            temperature=args.temperature if args.temperature is not None
            else DEFAULTS["temperature"],
            max_tokens=args.max_tokens or DEFAULTS["max_tokens"],
            timeout=args.timeout or DEFAULTS["timeout"],
            max_concurrency=args.max_concurrency or DEFAULTS["max_concurrency"],
            top_p=args.top_p if args.top_p is not None else DEFAULTS["top_p"],
        )
        ModelPool(s.root).add(spec)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, {"registered": args.name, "format": args.format,
                 "model": spec.model},
          lambda d: print(f"registered model '{d['registered']}' "
                          f"(format={d['format']}, model={d['model']})"))
    return 0


def cmd_model_list(args) -> int:
    from .models import ModelPool
    s = _open(args)
    pool = ModelPool(s.root)
    models = pool.list()
    default_model = pool.default_name()
    s.close()
    _emit(args, {"models": models, "default_model": default_model}, lambda d: print("\n".join(
        f"{m['name']:<16} {m['response_format']:<10} {m['model']:<22} "
        f"{m['api_url']}{'  [default]' if m['name'] == d['default_model'] else ''}"
        for m in d["models"]) or "(no models)"))
    return 0


def cmd_model_show(args) -> int:
    from .models import ModelPool
    s = _open(args)
    try:
        spec = ModelPool(s.root).get(args.name)
    except KeyError as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, spec.masked(),
          lambda d: print(json.dumps(d, ensure_ascii=False, indent=2)))
    return 0


def cmd_model_remove(args) -> int:
    from .models import ModelPool
    s = _open(args)
    ok = ModelPool(s.root).remove(args.name)
    s.close()
    if not ok:
        return _fail(args, f"model not found: {args.name}")
    _emit(args, {"removed": args.name},
          lambda d: print(f"removed model '{d['removed']}'"))
    return 0


def cmd_agent_ingest(args) -> int:
    from . import harness, codex
    s = _open(args)
    engine = args.engine
    note = None
    builtin_model = args.model
    # auto: prefer the real Codex agent when a model + runtime are available;
    # otherwise use the offline heuristic (do NOT silently run the LLM planner).
    if engine == "auto":
        if args.model and not args.dry_run and codex.sdk_available():
            engine = "codex"
        else:
            engine = "builtin"
            if args.model and not codex.sdk_available():
                note = ("LoopAI Codex runner not available — used the offline heuristic. "
                        "Run `loopai-obtainercli dm --lake .datamixer/lake.yaml codex-check`; install with "
                        "`corepack yarn install` in codex-runner.")
                builtin_model = None
    try:
        if engine == "codex":
            if args.dry_run:
                s.close()
                return _fail(args, "the codex engine does not support --dry-run; "
                                   "use --engine builtin for a dry run")
            rep = codex.codex_ingest(
                s, args.path, model=args.model, dataset=args.dataset,
                quality_level=args.quality_level, timeout=args.timeout)
        else:
            rep = harness.agent_ingest(
                s, args.path, filename=args.filename, model=builtin_model,
                dataset=args.dataset, max_iters=args.max_iters,
                dry_run=args.dry_run, quality_level=args.quality_level)
            rep.setdefault("engine", "builtin")
            if note:
                rep["note"] = note
    except (KeyError, ValueError, FileNotFoundError, codex.CodexError) as e:
        s.close()
        return _fail(args, str(e))
    s.close()

    def txt(d):
        print(f"engine    : {d.get('engine')}")
        if d.get("planner"):
            print(f"planner   : {d['planner']}")
        if d.get("format"):
            print(f"format    : {d['format']}", end="")
            print(f"  (records detected: {d['records_detected']})"
                  if d.get("records_detected") is not None else "")
        if d.get("review"):
            print(f"review    : {d['review']}")
        if d.get("dry_run"):
            print("dry-run (not ingested). plan:")
            print(json.dumps(d["plan"], ensure_ascii=False, indent=2))
        else:
            print(f"ingested  : {d.get('ingested')} into '{d.get('dataset')}'"
                  + (f" ({d['dataset_id']})" if d.get("dataset_id") else ""))
    _emit(args, rep, txt)
    return 0


def cmd_codex_check(args) -> int:
    from . import codex
    st = codex.runtime_status()
    _emit(args, st, lambda d: print(
        f"corepack  : {d['corepack'] or 'NOT FOUND'}\n"
        f"runner    : {d['runner'] or 'NOT FOUND'}\n"
        f"codex_home: {d['codex_home'] or 'NOT FOUND'}\n"
        f"codex_sdk : {'yes' if d['codex_sdk'] else 'no'}\n"
        f"instr     : {d['instructions'] or 'NOT FOUND'}\n"
        f"ready     : {d['ready']}\n"
        f"hint      : {d['install_hint']}"))
    return 0


def cmd_dataflow_agent_run(args) -> int:
    from . import codex
    from .dataflow_agent import run_dataflow_agent
    s = _open(args) if not args.input_file else None
    try:
        rep = run_dataflow_agent(
            s,
            target=args.target,
            model=args.model,
            dataset=args.dataset,
            where=args.filter,
            field=args.field,
            expected_outputs=args.expected_outputs,
            work_dir=args.work_dir,
            trial_rows=args.trial_rows,
            trial_rows_per_dataset=args.trial_rows_per_dataset,
            apply=args.apply,
            recipe_path=args.recipe,
            mix_plan_path=args.mix_plan,
            skeleton_path=args.skeleton,
            input_file=args.input_file,
            full_input_file=args.full_input_file,
            resume_thread_id=args.resume_thread_id,
        )
    except (KeyError, ValueError, FileNotFoundError, codex.CodexError) as e:
        if s:
            s.close()
        return _fail(args, str(e))
    if s:
        s.close()

    def txt(d):
        ar = d.get("agent_result", {})
        mode = ar.get("mode", "unknown")
        print(f"dataflow agent-run: {mode}")
        print(f"target    : {d['target']}")
        print(f"trial rows: {d['trial_rows_exported']}")
        upstream = d.get("upstream") or {}
        if upstream.get("delivered_pipeline"):
            print(f"delivered : pipeline {ar.get('pipeline_path', '')} "
                  f"(full run is executed upstream via chunked runner)")
            print(f"full input: {upstream.get('full_input_jsonl', '')} "
                  f"({d.get('full_rows_exported', 0)} rows exported)")
            print(f"upstream  : {upstream.get('chunked_run_command', '')}")
            print(f"apply     : {upstream.get('apply_command', '')}")
        else:
            print(f"full rows : {d.get('full_rows_exported', 0)} in -> "
                  f"{d.get('full_rows_out', 0)} out")
            print(f"work dir  : {d['work_dir']}")
            if ar.get("pipeline_path"):
                print(f"pipeline  : {ar['pipeline_path']}")
            if ar.get("processed_jsonl"):
                print(f"processed : {ar['processed_jsonl']}")
            if d.get("merge"):
                print(f"merged    : {d['merge']}")
    _emit(args, rep, txt)
    return 0


def cmd_pipeline_run(args) -> int:
    from .operators import load_pipeline, run_pipeline
    s = _open(args)
    try:
        spec = load_pipeline(args.file)
        res = run_pipeline(s, spec, batch_size=args.batch_size, seed=args.seed)
    except (KeyError, ValueError, FileNotFoundError) as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, res.to_dict(), lambda d: print(
        f"pipeline {d['pipeline']} run {d['run_id']}: "
        f"selected={d['selected']} written={d['written']} "
        f"dropped={d['dropped']} ({d['elapsed_s']}s)\n" +
        "\n".join(f"  {st['name']:<16} {st['kind']:<8} "
                  f"{st['rows_in']}->{st['rows_out']}" for st in d["stages"])))
    return 0


def _load_recipe(args):
    from .recipe import load_recipe
    return load_recipe(args.file)


def cmd_recipe_validate(args) -> int:
    from . import recipe as R
    try:
        r = _load_recipe(args)
        R._validate_export_schema_config(r)
    except R.ExportSchemaError as e:
        out = {
            "valid": False,
            "name": getattr(locals().get("r", None), "name", ""),
            "error": str(e),
            "export_schema": e.diagnostic,
            "next_action": e.diagnostic.get("action_required", ""),
        }
        _emit(args, out, lambda d: print(
            f"recipe export schema is not ready: {d['export_schema']['code']}\n"
            f"{d['next_action']}"))
        return 0
    except (ValueError, FileNotFoundError) as e:
        return _fail(args, str(e))
    out = {"valid": True, "name": r.name, "fingerprint": r.fingerprint,
           "stage": r.stage, "strategy": r.strategy,
           "buckets": [b.name for b in r.buckets],
           "export_schema": R._export_schema_config_report(r)}
    _emit(args, out, lambda d: print(
        f"OK  recipe '{d['name']}' fp={d['fingerprint']} "
        f"strategy={d['strategy']} buckets={d['buckets']}"))
    return 0


def cmd_recipe_plan(args) -> int:
    from . import recipe as R
    s = _open(args)
    try:
        r = _load_recipe(args)
        p = R.plan(s, r)
    except (ValueError, FileNotFoundError) as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, p, lambda d: _print_plan(d))
    return 0


def _print_plan(d):
    print(f"recipe={d['recipe']} fp={d['fingerprint']} strategy={d['strategy']} "
          f"budget={d['budget_kind']}")
    for b in d["buckets"]:
        print(f"  {b['name']:<14} w={b['weight']:.3f} "
              f"avail={b['available_samples']}smp/{b['available_tokens']}tok "
              f"target={b['target_tokens']}tok/{b['target_samples']}smp")
        for t in b.get("tiers", []):
            print(f"      tier {t['tier']:<10} w={t['weight']:.2f} "
                  f"avail={t['available_samples']}smp "
                  f"target={t['target_tokens']}tok/{t['target_samples']}smp")
    for w in d["warnings"]:
        print(f"  ! {w}")


def cmd_recipe_preview(args) -> int:
    from . import recipe as R
    s = _open(args)
    try:
        r = _load_recipe(args)
        p = R.preview(s, r, per_bucket=args.per_bucket)
    except (ValueError, FileNotFoundError) as e:
        s.close()
        return _fail(args, str(e))
    s.close()

    def txt(d):
        _print_plan(d)
        for name, rows in d["samples"].items():
            print(f"  -- {name} --")
            for row in rows:
                print(f"     [{row.get('domain')}/{row.get('lang')}] "
                      f"q={row.get('quality_score')} :: {row.get('preview')}")
    _emit(args, p, txt)
    return 0


def cmd_recipe_export(args) -> int:
    from . import recipe as R
    s = _open(args)
    try:
        r = _load_recipe(args)
        res = R.export(s, r, out_dir=args.out,
                       from_snapshot=args.from_snapshot, snapshot=args.snapshot)
    except R.ExportSchemaError as e:
        s.close()
        out = {
            "ok": False,
            "blocked": True,
            "error": e.diagnostic.get("message") or str(e),
            "error_code": e.diagnostic.get("code", "export_schema_error"),
            "export_schema": e.diagnostic,
            "next_action": e.diagnostic.get("action_required", ""),
        }
        _emit(args, out, lambda d: print(
            f"export blocked: {d['error_code']}\n{d['error']}\n{d['next_action']}"))
        return 1
    except R.ExportQualityError as e:
        s.close()
        out = {
            "ok": False,
            "blocked": True,
            "error": e.diagnostic.get("message") or str(e),
            "error_code": e.diagnostic.get("code", "export_quality_error"),
            "finance_quality": e.diagnostic,
            "next_action": (
                "Fix rejected sources, run domain_classify with the resolved Codex model, "
                "complete source-level manual review, then export again."
            ),
        }
        _emit(args, out, lambda d: print(
            f"export blocked: {d['error_code']}\n{d['error']}\n{d['next_action']}"))
        return 1
    except (ValueError, FileNotFoundError, KeyError) as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, res, lambda d: print(
        f"exported {d['selected_samples']} samples "
        f"({d['selected_tokens']} tokens) -> {d['out_dir']} "
        f"[{d['format']}, {d['files']} shards, id={d['export_id']}]\n"
        f"dataset_digest={d['dataset_digest']}"
        + (f"  snapshot={d['snapshot_id']}" if d.get('snapshot_id') else "")))
    return 0


def cmd_snapshot_create(args) -> int:
    from . import snapshot
    s = _open(args)
    try:
        ds_id = s.catalog.resolve_dataset(args.dataset) if args.dataset else None
        meta = snapshot.create(s, name=args.name, where=args.filter,
                               dataset=args.dataset)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, meta, lambda d: print(
        f"snapshot {d['id']} ({d['name'] or '-'}): {d['count']} samples, "
        f"{d['tokens']} tokens, digest={d['digest'][:16]}…"))
    return 0


def cmd_snapshot_list(args) -> int:
    from . import snapshot
    s = _open(args)
    snaps = snapshot.list_snapshots(s)
    s.close()
    _emit(args, {"snapshots": snaps}, lambda d: print("\n".join(
        f"{m['id']:<24} {str(m['name'] or '-'):<18} count={m['count']:<8} "
        f"digest={m['digest'][:16]}…" for m in d["snapshots"]) or "(no snapshots)"))
    return 0


def cmd_snapshot_diff(args) -> int:
    from . import snapshot
    s = _open(args)
    try:
        d = snapshot.diff(s, args.a, args.b)
    except KeyError as e:
        s.close()
        return _fail(args, str(e))
    s.close()
    _emit(args, d, lambda r: print(
        f"{r['a']['id']} vs {r['b']['id']}: "
        f"{'IDENTICAL' if r['identical'] else 'DIFFERENT'} "
        f"(+{r['n_added']} / -{r['n_removed']})"))
    return 0


def cmd_index_build(args) -> int:
    s = _open(args)
    try:
        if args.backend == "lancedb":
            if args.fulltext_only or not args.vector_only:
                raise ValueError("LanceDB requires --vector-only; no full-text build is performed")
            from .vector_db import build_lance_index
            res = build_lance_index(
                s, base_url=args.embed_base_url, model=args.embed_model,
                text_field=args.text_field, where=args.filter, batch_size=args.batch_size,
                api_key=args.embed_api_key, query_prefix=args.query_prefix,
                embedding_revision=args.embedding_revision,
            )
        else:
            res = s.index.rebuild(
                s, vector=not args.fulltext_only, fulltext=not args.vector_only,
                io_workers=getattr(args, "io_workers", 8),
            )
    except (ValueError, RuntimeError, ImportError) as exc:
        return _fail(args, str(exc))
    finally:
        s.close()
    _emit(args, res, lambda d: print(
        f"indexed {d['indexed']} samples "
        f"(vectors={d['vectors']}, fulltext_docs={d['fulltext_docs']})"))
    return 0


def cmd_index_disable_fulltext(args) -> int:
    from .vector_db import disable_fulltext
    if not args.yes:
        return _fail(args, "stop all index writers first, then pass --yes to archive only derived FTS files")
    s = _open(args)
    try:
        result = disable_fulltext(s.root)
    finally:
        s.close()
    _emit(args, result, lambda d: print(json.dumps(d, ensure_ascii=False)))
    return 0


def cmd_index_stats(args) -> int:
    s = _open(args)
    st = s.index.stats()
    s.close()
    _emit(args, st, lambda d: print(
        f"vectors={d['vectors']} dim={d['dim']} fulltext_docs={d['fulltext_docs']}"))
    return 0


def cmd_recall(args) -> int:
    s = _open(args)
    if not args.query and not args.match:
        s.close()
        return _fail(args, "provide --query (semantic) or --match (keyword)")
    restrict = None
    if args.filter:
        try:
            rows = s.catalog.query(where=args.filter, columns="sample_id")
        except ValueError as e:
            s.close()
            return _fail(args, str(e))
        restrict = {r["sample_id"] for r in rows}
    if args.query:
        hits = s.index.semantic_recall(args.query, top_k=args.limit,
                                       restrict=restrict, min_sim=args.min_sim)
        mode = "semantic"
    else:
        hits = s.index.keyword_recall(args.match, top_k=args.limit,
                                      restrict=restrict)
        mode = "keyword"
    results = []
    for sid, score in hits:
        smp = s.catalog.get_sample(sid) or {}
        item = {"sample_id": sid, "score": round(score, 4),
                "domain": smp.get("domain"), "lang": smp.get("lang")}
        if args.preview and smp.get("cid"):
            try:
                item["preview"] = utils_extract(s, smp["cid"])
            except KeyError:
                item["preview"] = None
        results.append(item)
    s.close()

    def txt(d):
        print(f"# {d['mode']} recall: {len(d['results'])} hits")
        for r in d["results"]:
            line = f"{r['score']:.3f}  [{r.get('domain')}/{r.get('lang')}]  {r['sample_id']}"
            if "preview" in r:
                line += f"  :: {r['preview']}"
            print(line)
    _emit(args, {"mode": mode, "results": results}, txt)
    return 0


def utils_extract(store, cid: str) -> str:
    from . import utils as U
    return U.extract_text(store.get_content(cid))[:160]


def cmd_recipe_diff(args) -> int:
    from .recipe import load_recipe, diff
    try:
        a = load_recipe(args.a)
        b = load_recipe(args.b)
    except (ValueError, FileNotFoundError) as e:
        return _fail(args, str(e))
    d = diff(a, b)

    def txt(d):
        print(f"{d['a']['name']}({d['a']['fingerprint']}) vs "
              f"{d['b']['name']}({d['b']['fingerprint']}) "
              f"{'IDENTICAL' if d['identical'] else 'DIFFERENT'}")
        for c in d["bucket_changes"]:
            print(f"  bucket {c['bucket']:<14} {c['status']:<8} "
                  f"{c['from']} -> {c['to']}")
        for f, c in d["field_changes"].items():
            print(f"  field  {f:<14} {c['from']} -> {c['to']}")
    _emit(args, d, txt)
    return 0


def cmd_serve(args) -> int:
    from .console import serve
    s = _open(args)
    try:
        serve(s, host=args.host, port=args.port, recipe_path=args.recipe,
              read_only=args.read_only, token=args.token)
    except ValueError as e:
        s.close()
        return _fail(args, str(e))
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
    return 0


def cmd_lineage_list(args) -> int:
    s = _open(args)
    root = s.root
    s.close()
    items = []
    for sub, kind in (("lineage", "pipeline_run"), ("exports", "export")):
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("**/*.json")):
            if p.name == "manifest.json" or sub == "lineage":
                try:
                    doc = json.loads(p.read_text())
                    items.append({
                        "kind": doc.get("kind"),
                        "id": doc.get("export_id") or doc.get("run_id"),
                        "path": str(p.relative_to(root)),
                    })
                except (json.JSONDecodeError, OSError):
                    continue
    _emit(args, {"lineage": items}, lambda d: print("\n".join(
        f"{i['kind']:<14} {i['id']:<24} {i['path']}" for i in d["lineage"])))
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="datamixer",
        description="Decentralized full-pipeline LLM data platform (MVP).",
    )
    p.add_argument("--version", action="version", version=f"datamixer {__version__}")
    p.add_argument("--root", default=None,
                   help="warehouse path (default: discover upward from CWD)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output (accepted before or after the "
                        "subcommand)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create a warehouse")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--codec", default="zlib", choices=["zlib", "lzma", "raw"])
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("schema", help="print the unified tag schema contract")
    sp.set_defaults(func=cmd_schema)

    catalog = sub.add_parser("catalog", help="catalog maintenance").add_subparsers(
        dest="sub", required=True)
    a = catalog.add_parser(
        "repair-indexes",
        help="rebuild SQLite catalog indexes and verify integrity",
    )
    a.set_defaults(func=cmd_catalog_repair_indexes)

    # dataset group
    ds = sub.add_parser("dataset", help="dataset registry").add_subparsers(
        dest="sub", required=True)
    a = ds.add_parser("add", help="register a dataset")
    a.add_argument("--name", required=True)
    a.add_argument("--source"); a.add_argument("--license")
    a.add_argument("--owner"); a.add_argument("--description")
    a.set_defaults(func=cmd_dataset_add)
    a = ds.add_parser("update", help="update dataset registry metadata")
    a.add_argument("dataset")
    a.add_argument("--name"); a.add_argument("--source")
    a.add_argument("--license"); a.add_argument("--owner")
    a.add_argument("--description")
    a.set_defaults(func=cmd_dataset_update)
    a = ds.add_parser("delete", help="delete a dataset and all its samples")
    a.add_argument("dataset")
    a.add_argument("--reason", default=None)
    a.add_argument("--yes", action="store_true", help="confirm deletion")
    a.set_defaults(func=cmd_dataset_delete)
    a = ds.add_parser("list", help="list datasets")
    a.set_defaults(func=cmd_dataset_list)
    a = ds.add_parser("show", help="show one dataset")
    a.add_argument("dataset")
    a.set_defaults(func=cmd_dataset_show)

    sp = sub.add_parser("ingest", help="ingest a JSONL file into a dataset")
    sp.add_argument("dataset", help="dataset name or id (created if missing)")
    sp.add_argument("--file", required=True)
    sp.add_argument(
        "--quality-level",
        dest="quality_level",
        required=True,
        choices=schema.QUALITY_LEVELS,
    )
    sp.add_argument("--content-key", default="content")
    sp.add_argument("--benchmark-set", default=None,
                    help="ingest evaluation references into the dataset linked to this guard; requires --stage eval")
    sp.add_argument("--dataset-card", dest="dataset_card", default=None,
                    help="Markdown dataset card to register under dataset_cards/ before ingest")
    sp.add_argument("--derived-field", dest="derived_field", action="append", default=[],
                    help="derived field that must exist and be non-empty in every normalized row; repeatable")
    sp.add_argument("--source-row-count", dest="source_row_count", type=int, default=None,
                    help="expected normalized row count, used to ensure derivation did not drop rows")
    sp.add_argument("--io-workers", type=int, default=1,
                    help="bounded concurrent content-blob writes; catalog remains ordered and single-threaded")
    sp.add_argument("--allow-duplicates", dest="allow_duplicates",
                    action="store_true",
                    help="keep every record as its own sample (preserve "
                         "multiplicity) instead of merging identical (content+tags)")
    sp.add_argument("--tokenizer", default=None,
                    help="tokenizer for n_tokens: heuristic (default) | "
                         "tiktoken:o200k_base | hf:<model> | char | whitespace")
    for k in ("stage", "domain", "lang", "source", "license", "modality",
              "task-type"):
        sp.add_argument(f"--{k}", dest=k.replace("-", "_"))
    sp.add_argument("--processing-level", dest="processing_level", default=None,
                    help="DataMixer tag for pipeline processing level")
    sp.add_argument("--source-kind", dest="source_kind", default=None,
                    help="DataMixer tag for source platform/category")
    sp.add_argument("--source-uri", dest="source_uri", default=None,
                    help="DataMixer tag for source URI when it is common to the file")
    sp.add_argument("--split", default=None)
    sp.add_argument("--loop-uuid", dest="loop_uuid", default=None)
    sp.add_argument("--version-id", dest="version_id", default=None)
    sp.add_argument("--idempotency-key", dest="idempotency_key", default=None)
    sp.add_argument("--tag", action="append", default=[],
                    help="extra DataMixer metadata/tag as key=value; repeatable")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("agent-ingest",
                        help="intelligent ingest of any-format file")
    sp.add_argument("path", help="input file (any format)")
    sp.add_argument(
        "--quality-level",
        dest="quality_level",
        required=True,
        choices=schema.QUALITY_LEVELS,
    )
    sp.add_argument("--engine", choices=["auto", "codex", "builtin"], default="auto",
                    help="codex = LoopAI Codex runner; builtin = offline heuristic")
    sp.add_argument("--model", default=None,
                    help="model-pool name used by the LoopAI runner")
    sp.add_argument("--dataset", default=None,
                    help="target dataset (default: from filename)")
    sp.add_argument("--filename", default=None,
                    help="original filename hint (builtin engine format detection)")
    sp.add_argument("--max-iters", dest="max_iters", type=int, default=3)
    sp.add_argument("--timeout", type=int, default=600,
                    help="LoopAI runner timeout (seconds)")
    sp.add_argument("--dry-run", action="store_true",
                    help="review + plan only, do not ingest (builtin engine)")
    sp.set_defaults(func=cmd_agent_ingest)

    sp = sub.add_parser("codex-check", help="check LoopAI Codex runner availability")
    sp.set_defaults(func=cmd_codex_check)

    sp = sub.add_parser("query", help="query samples with a SQL-ish filter")
    sp.add_argument("--filter", default=None)
    sp.add_argument("--dataset", default=None)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--columns", default="sample_id,dataset_id,domain,lang,"
                                          "stage,quality_score,n_tokens,cid")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("export-jsonl",
                        help="dump a dataset to flat JSONL for DataFlow FileStorage")
    sp.add_argument("--dataset", default=None)
    sp.add_argument("--filter", default=None)
    sp.add_argument("--field", default="raw_content",
                    help="text column name DataFlow reads as input_key")
    sp.add_argument("--out", required=True, help="output JSONL path")
    sp.add_argument("--limit", type=int, default=0, help="cap rows (0 = all)")
    sp.add_argument("--batch-size", type=int, default=512)
    sp.set_defaults(func=cmd_export_jsonl)

    sp = sub.add_parser("apply-jsonl",
                        help="merge a DataFlow-processed JSONL back by sample_id")
    sp.add_argument("--file", required=True, help="processed JSONL path")
    sp.add_argument("--key", default="sample_id", help="join key column")
    sp.add_argument("--field", default="raw_content",
                    help="text column to ignore (never overwrites content)")
    sp.set_defaults(func=cmd_apply_jsonl)

    df = sub.add_parser("dataflow",
                        help="agent-orchestrated DataFlow pipeline planning").add_subparsers(
        dest="sub", required=True)
    a = df.add_parser("agent-run",
                      help="use Codex SDK to plan, generate, and trial-run a DataFlow pipeline")
    a.add_argument("--target", required=True,
                   help="downstream processing goal for the operator chain")
    a.add_argument("--model", required=True,
                   help="DataMixer model-pool name used by Codex SDK")
    a.add_argument("--dataset", default=None)
    a.add_argument("--filter", default=None)
    a.add_argument("--field", default="raw_content",
                   help="text/input field exported for DataFlow FileStorage")
    a.add_argument("--expected-outputs", default=None,
                   help="optional comma/list description of fields expected from the pipeline")
    a.add_argument("--work-dir", default=None,
                   help="directory for trial input, generated pipeline, and outputs")
    a.add_argument("--trial-rows", type=int, default=20,
                   help="rows exported for the agent's trial run")
    a.add_argument("--trial-rows-per-dataset", type=int, default=None,
                   help="maximum trial rows from each selected dataset")
    a.add_argument("--input-file", default=None,
                   help="direct JSONL input file (skips lake export)")
    a.add_argument("--full-input-file", default=None,
                   help="direct JSONL for full run (defaults to --input-file if omitted)")
    a.add_argument("--resume-thread-id", default=None,
                   help="continue a previous DataFlow Codex thread with this target and work directory")
    a.add_argument("--recipe", default=None,
                   help="export recipe.yaml: full input is sampled per bucket to "
                        "ceil(bucket_target * 1.5) rows instead of a whole-lake export")
    a.add_argument("--mix-plan", default=None,
                   help="sft-export mix_plan.json: same 1.5x per-bucket sampling, "
                        "using the plan's target_records/dataset entries")
    a.add_argument("--apply", action="store_true",
                   help="deprecated: the DataFlow agent now only delivers the "
                        "trial-verified pipeline; the upper layer runs the chunked "
                        "scaffold over full_input.jsonl and merges with apply-jsonl")
    a.add_argument("--skeleton", default=None,
                   help="fixed skeleton pipeline the agent must fill in; locks "
                        "pre-steps (validate/filter) and post-steps (rollout "
                        "difficulty / strong-model graded retry), leaving only "
                        "the front-normalization and middle-shaping regions editable")
    a.set_defaults(func=cmd_dataflow_agent_run)

    sm = sub.add_parser("sample", help="inspect a sample").add_subparsers(
        dest="sub", required=True)
    a = sm.add_parser("show")
    a.add_argument("sample_id")
    a.add_argument("--content", action="store_true")
    a.set_defaults(func=cmd_sample_show)

    sp = sub.add_parser("stats", help="storage efficiency stats")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("status", help="warehouse status summary")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("columns", help="list queryable/exportable columns + tags")
    sp.set_defaults(func=cmd_columns)

    sp = sub.add_parser("grade", help="quality-grade a selection into tiers")
    sp.add_argument("--filter", default=None)
    sp.add_argument("--column", default="quality_score")
    sp.add_argument("--tiers", default=None, help="descending cut points, e.g. 0.8,0.6,0.4")
    sp.set_defaults(func=cmd_grade)

    sp = sub.add_parser("dist", help="categorical distribution of a column")
    sp.add_argument("column")
    sp.add_argument("--filter", default=None)
    sp.set_defaults(func=cmd_dist)

    dm = sub.add_parser(
        "domain", help="persistent lake-local domain taxonomy"
    ).add_subparsers(dest="sub", required=True)
    a = dm.add_parser("list", help="list built-in, registered, and observed domains")
    a.set_defaults(func=cmd_domain_list)
    a = dm.add_parser("add", help="register one or more domain labels")
    a.add_argument("name", nargs="+", help="domain label(s), e.g. text2sql robotics")
    a.set_defaults(func=cmd_domain_add)
    a = dm.add_parser("sync", help="discover domain values already stored in samples")
    a.set_defaults(func=cmd_domain_sync)

    sp = sub.add_parser("hist", help="numeric histogram of a column")
    sp.add_argument("column")
    sp.add_argument("--bins", type=int, default=10)
    sp.add_argument("--filter", default=None)
    sp.set_defaults(func=cmd_hist)

    # operator group
    op = sub.add_parser("op", help="data-processing operators").add_subparsers(
        dest="sub", required=True)
    a = op.add_parser("list", help="list registered operators")
    a.set_defaults(func=cmd_op_list)
    a = op.add_parser("run", help="run one operator over selected samples")
    a.add_argument("name")
    a.add_argument("--filter", default=None)
    a.add_argument("--dataset", default=None)
    a.add_argument("--arg", action="append", default=[], help="key=value")
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--batch-size", dest="batch_size", type=int, default=512,
                   help="stream the catalog in batches of this size (constant memory)")
    a.set_defaults(func=cmd_op_run)

    # contamination / decontamination group
    ct = sub.add_parser("contam", help="benchmark contamination sets"
                        ).add_subparsers(dest="sub", required=True)
    a = ct.add_parser("add", help="register a benchmark set from a file")
    a.add_argument("--name", required=True)
    a.add_argument("--file", required=True, help="JSONL (text field) or one text/line")
    a.add_argument("--benchmark-dataset", default=None,
                   help="associated DataMixer benchmark dataset name or ID")
    a.add_argument("--text-field", dest="text_field", default="text")
    a.add_argument("--ngram", type=int, default=13)
    a.add_argument("--threshold", type=float, default=0.8,
                   help="overlap threshold for automatic removal from existing samples")
    a.add_argument("--filter", default=None,
                   help="limit automatic decontamination to this catalog filter (default: whole catalog)")
    a.add_argument("--workers", type=int, default=None,
                   help="parallel workers for automatic decontamination")
    a.add_argument("--batch-size", dest="batch_size", type=int, default=512,
                   help="samples per decontamination batch")
    a.set_defaults(func=cmd_contam_add)
    a = ct.add_parser("list", help="list registered benchmark sets")
    a.set_defaults(func=cmd_contam_list)

    sp = sub.add_parser("decontaminate",
                        help="flag/remove samples overlapping a benchmark set")
    sp.add_argument("--against", default=None,
                    help="comma-separated set names (default: all)")
    sp.add_argument("--dataset", default=None)
    sp.add_argument("--filter", default=None)
    sp.add_argument("--threshold", type=float, default=0.8)
    sp.add_argument("--apply", action="store_true",
                    help="delete contaminated samples (default: only flag them)")
    sp.add_argument("--workers", type=int, default=None,
                    help="parallel workers for decontamination matching")
    sp.add_argument("--batch-size", dest="batch_size", type=int, default=512,
                    help="samples per decontamination batch")
    sp.set_defaults(func=cmd_decontaminate)

    # compliance: PII redaction + erasure
    sp = sub.add_parser("pii-redact",
                        help="mask PII in sample content (rewrites blobs)")
    sp.add_argument("--dataset", default=None)
    sp.add_argument("--filter", default=None)
    sp.add_argument("--types", default=None,
                    help="comma-separated: EMAIL,PHONE,IP,CREDIT_CARD,SSN,API_KEY")
    sp.add_argument("--dry-run", action="store_true",
                    help="report what would be redacted without rewriting")
    sp.set_defaults(func=cmd_pii_redact)

    sp = sub.add_parser("erase",
                        help="right-to-erasure: remove samples from catalog/CAS/index")
    sp.add_argument("sample_id", nargs="?", default=None)
    sp.add_argument("--filter", default=None)
    sp.add_argument("--dataset", default=None)
    sp.add_argument("--reason", default=None)
    sp.set_defaults(func=cmd_erase)

    # model pool group
    mp = sub.add_parser("model", help="LLM model pool").add_subparsers(
        dest="sub", required=True)
    a = mp.add_parser("add", help="register an LLM model")
    a.add_argument("--name", required=True)
    a.add_argument("--api-url", dest="api_url", required=True)
    a.add_argument("--key", default=None, help="API key, or env:VARNAME")
    a.add_argument("--format", default="openaichat",
                   choices=["openaichat", "response"], help="response format")
    a.add_argument("--model", default=None, help="provider model id")
    a.add_argument("--note", default=None)
    a.add_argument("--temperature", type=float, default=None)
    a.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    a.add_argument("--timeout", type=int, default=None)
    a.add_argument("--max-concurrency", dest="max_concurrency", type=int, default=None)
    a.add_argument("--top-p", dest="top_p", type=float, default=None)
    a.set_defaults(func=cmd_model_add)
    a = mp.add_parser("list"); a.set_defaults(func=cmd_model_list)
    a = mp.add_parser("show"); a.add_argument("name")
    a.set_defaults(func=cmd_model_show)
    a = mp.add_parser("remove"); a.add_argument("name")
    a.set_defaults(func=cmd_model_remove)

    pl = sub.add_parser("pipeline", help="operator pipelines").add_subparsers(
        dest="sub", required=True)
    a = pl.add_parser("run")
    a.add_argument("file")
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--batch-size", dest="batch_size", type=int, default=512)
    a.set_defaults(func=cmd_pipeline_run)

    # index group (L3)
    ix = sub.add_parser("index", help="vector + full-text index (L3)"
                        ).add_subparsers(dest="sub", required=True)
    a = ix.add_parser("build", help="(re)build indexes from the catalog")
    a.add_argument("--io-workers", type=int, default=8,
                   help="bounded blob readers for legacy-flat build (default 8); index writes remain ordered")
    a.add_argument("--vector-only", action="store_true")
    a.add_argument("--fulltext-only", action="store_true")
    a.add_argument("--backend", choices=["legacy-flat", "lancedb"], default="legacy-flat")
    a.add_argument("--embed-base-url", default=os.getenv("OBTAINERCLI_EMBED_BASE_URL", ""))
    a.add_argument("--embed-model", default=os.getenv("OBTAINERCLI_EMBED_MODEL_NAME", ""))
    a.add_argument("--embed-api-key", default=os.getenv("OBTAINERCLI_EMBED_API_KEY", ""))
    a.add_argument("--text-field", default="canonical_problem", help="task-only field; no fallback")
    a.add_argument("--filter", default=None)
    a.add_argument("--batch-size", type=int, default=64)
    a.add_argument("--query-prefix", default="")
    a.add_argument("--embedding-revision", default="")
    a.set_defaults(func=cmd_index_build)
    a = ix.add_parser("stats"); a.set_defaults(func=cmd_index_stats)
    a = ix.add_parser("disable-fulltext", help="disable FTS and recoverably archive its derived files")
    a.add_argument("--yes", action="store_true")
    a.set_defaults(func=cmd_index_disable_fulltext)

    sp = sub.add_parser("recall", help="semantic / keyword sample recall")
    sp.add_argument("--query", default=None, help="semantic (vector) query")
    sp.add_argument("--match", default=None, help="keyword (FTS5) query")
    sp.add_argument("--filter", default=None, help="restrict to a scalar filter")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--min-sim", type=float, default=-1.0)
    sp.add_argument("--preview", action="store_true")
    sp.set_defaults(func=cmd_recall)

    sp = sub.add_parser("recall-badcases",
                        help="multi-route recall from Analyzer bad-case questions "
                             "-> candidate JSONL for DataFlowAgent")
    sp.add_argument("--io-workers", type=int, default=8,
                    help="bounded concurrent candidate blob reads (default 8); preserves ranked output order")
    sp.add_argument("--from", dest="source", default=None,
                    help="badcase_questions_*.jsonl; defaults to the latest Analyzer round")
    sp.add_argument("--out", required=True, help="candidate JSONL output path")
    sp.add_argument("--limit", type=int, default=6000,
                    help="per-question recall top-k (recall breadth)")
    sp.add_argument("--total-limit", type=int, default=None,
                    help="maximum unique candidates after multi-route deduplication and ranking")
    sp.add_argument("--filter", default=None,
                    help="restrict recall to a scalar filter (e.g. domain routing)")
    sp.add_argument("--min-sim", type=float, default=-1.0,
                    help="minimum cosine similarity for semantic recall")
    sp.add_argument("--keyword", action="store_true",
                    help="use keyword (FTS5) recall instead of semantic (vector)")
    sp.add_argument("--question-field", default="question",
                    help="field in the manifest holding the recall query text")
    sp.add_argument("--field", default="raw_content",
                    help="text column DataFlow reads as input_key")
    sp.add_argument("--embed-base-url", default=os.getenv("OBTAINERCLI_EMBED_BASE_URL", ""),
                    help="OpenAI-compatible embedding server for query vectors "
                         "(match the model that built the index, e.g. bge); "
                         "empty = use the built-in hashing embedder")
    sp.add_argument("--embed-model", default=os.getenv("OBTAINERCLI_EMBED_MODEL_NAME", ""),
                    help="embedding model name sent to the embedding server")
    sp.add_argument("--embed-api-key", default=os.getenv("OBTAINERCLI_EMBED_API_KEY", ""),
                    help="optional bearer token for the embedding server")
    sp.add_argument("--output-dir", default=os.getenv("OUTPUT_DIR", "./outputs"),
                    help="base output dir used to auto-locate the Analyzer manifest")
    sp.add_argument("--task-id", default=os.getenv("TASK_ID", ""),
                    help="task id used to auto-locate the Analyzer manifest")
    sp.set_defaults(func=cmd_recall_badcases)

    sp = sub.add_parser("serve", help="launch the web console (L6)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8848)
    sp.add_argument("--recipe", default=None,
                    help="recipe file to load into the mix editor")
    sp.add_argument("--read-only", dest="read_only", action="store_true",
                    help="reject mutating commands via /api/cli")
    sp.add_argument("--token", default=None,
                    help="require this token (X-DM-Token header) on /api/cli")
    sp.set_defaults(func=cmd_serve)

    # recipe group
    rc = sub.add_parser("recipe", help="data-mix recipes").add_subparsers(
        dest="sub", required=True)
    a = rc.add_parser("validate"); a.add_argument("file")
    a.set_defaults(func=cmd_recipe_validate)
    a = rc.add_parser("plan"); a.add_argument("file")
    a.set_defaults(func=cmd_recipe_plan)
    a = rc.add_parser("preview"); a.add_argument("file")
    a.add_argument("--per-bucket", type=int, default=3)
    a.set_defaults(func=cmd_recipe_preview)
    a = rc.add_parser("export"); a.add_argument("file")
    a.add_argument("--out", default=None)
    a.add_argument("--from-snapshot", dest="from_snapshot", default=None,
                   help="materialize an exact data snapshot (reproducible)")
    a.add_argument("--snapshot", action="store_true",
                   help="record a snapshot of the selected set and link it")
    a.set_defaults(func=cmd_recipe_export)
    a = rc.add_parser("diff", help="compare two recipe versions")
    a.add_argument("a"); a.add_argument("b")
    a.set_defaults(func=cmd_recipe_diff)

    # snapshot group (data-state versioning)
    sn = sub.add_parser("snapshot", help="immutable dataset snapshots"
                        ).add_subparsers(dest="sub", required=True)
    a = sn.add_parser("create", help="snapshot the current sample set")
    a.add_argument("--name", default=None)
    a.add_argument("--dataset", default=None)
    a.add_argument("--filter", default=None)
    a.set_defaults(func=cmd_snapshot_create)
    a = sn.add_parser("list"); a.set_defaults(func=cmd_snapshot_list)
    a = sn.add_parser("diff"); a.add_argument("a"); a.add_argument("b")
    a.set_defaults(func=cmd_snapshot_diff)

    ln = sub.add_parser("lineage", help="lineage & export manifests"
                        ).add_subparsers(dest="sub", required=True)
    a = ln.add_parser("list"); a.set_defaults(func=cmd_lineage_list)

    return p


def _hoist_globals(argv: list[str]) -> list[str]:
    """Move the global flags (--json / --root) to the front so they are accepted
    in any position, e.g. ``datamixer query --filter x --json`` works the same as
    ``datamixer --json query --filter x``. Keeps the contract's "every command
    accepts --json" promise without redefining the flags on every subparser."""
    head: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            head.append(a)
        elif a == "--root":
            head.append(a)
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                head.append(argv[i + 1])
                i += 1
        elif a.startswith("--root="):
            head.append(a)
        else:
            rest.append(a)
        i += 1
    return head + rest


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_hoist_globals(raw))
    try:
        return args.func(args)
    except StoreError as e:
        return _fail(args, str(e))
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 - contract: never leak a stack trace
        return _fail(args, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
