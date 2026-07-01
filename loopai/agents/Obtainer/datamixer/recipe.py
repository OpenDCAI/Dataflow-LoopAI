"""Recipe recall, sampling & export engine (L5) -- the core differentiator.

A *recipe* is a versionable, replayable data-mix configuration (see
``examples/recipe.yaml``). The engine:

1. parses the recipe and compiles each bucket's filter to a catalog query,
2. recalls candidate samples per bucket,
3. balances the mix to a token / sample budget under a sampling strategy,
4. materializes content from the store and exports a training-ready package,
5. records a manifest with the recipe fingerprint for reproducibility/lineage.

The same engine serves pretrain / SFT / preference / agent stages -- only the
schema and the export shape differ.
"""
from __future__ import annotations

import io
import copy
import random
import re
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import catalog, schema, utils
from .store import DataStore

VALID_STRATEGIES = {"weighted_token", "weighted_sample", "temperature", "epoch_repeat"}
VALID_FORMATS = {"jsonl", "webdataset", "mds", "megatron"}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class QualityTier:
    min: float          # inclusive lower bound on the quality column
    weight: float       # share of the bucket's budget drawn from this tier
    label: str = ""


@dataclass
class Bucket:
    name: str
    weight: float
    filter: str = ""
    semantic_query: str = ""     # optional L3 semantic recall within the filter
    keyword: str = ""            # optional FTS5 keyword recall within the filter
    top_k: int = 0               # 0 => unlimited
    min_sim: float = -1.0        # cosine threshold for semantic recall
    # quality-graded recall
    quality_column: str = "quality_score"
    min_quality: float | None = None      # hard floor folded into the filter
    quality_grade: bool = False           # draw best-quality-first
    quality_tiers: list = field(default_factory=list)  # [QualityTier, ...]


@dataclass
class ExportField:
    name: str
    sources: list[str] = field(default_factory=list)
    required: bool = True
    default: Any = None


class ExportSchemaError(ValueError):
    """Structured export-schema diagnostic for agents to repair recipe YAML."""

    def __init__(self, diagnostic: dict):
        self.diagnostic = diagnostic
        super().__init__(str(diagnostic.get("message") or diagnostic.get("code") or "export schema error"))


@dataclass
class Recipe:
    name: str
    stage: str | None = None
    total_tokens: float | None = None
    total_samples: int | None = None
    buckets: list[Bucket] = field(default_factory=list)
    dedup_across_buckets: bool = True
    strategy: str = "weighted_token"
    temperature: float = 1.0
    max_repeat: int = 1
    seed: int = 42
    apply_stage_filter: bool = True
    tokenizer: str | None = None      # which tokenizer the token budget is in
    split: dict | None = None         # train/val/test ratios, e.g. {train:.98,...}
    export_format: str = "jsonl"
    shard_size: str = "256MB"
    shuffle: bool = True
    export_fields: dict[str, ExportField] = field(default_factory=dict)
    export_keep: list[str] = field(default_factory=list)
    export_include_dm: bool = True
    raw: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return utils.fingerprint(self.raw)


def load_recipe(path: str) -> Recipe:
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return parse_recipe(doc)


def parse_recipe(doc: dict) -> Recipe:
    r = doc.get("recipe", doc)
    raw = copy.deepcopy(doc)
    raw_recipe = raw.setdefault("recipe", raw) if "recipe" in doc else raw
    sampling = r.get("sampling", {}) or {}
    export = r.get("export", {}) or {}
    export_fields, export_keep, export_include_dm = _parse_export_schema(export)
    strategy = sampling.get("strategy", "weighted_token")
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f"invalid strategy {strategy!r}; choose {VALID_STRATEGIES}")
    fmt = export.get("format", "jsonl")
    if fmt not in VALID_FORMATS:
        raise ValueError(f"invalid export format {fmt!r}; choose {VALID_FORMATS}")
    buckets = []
    for b in r.get("buckets", []):
        tiers = [
            QualityTier(float(qt.get("min", 0.0)), float(qt.get("weight", 0.0)),
                        qt.get("label", ""))
            for qt in (b.get("quality_tiers") or [])
        ]
        buckets.append(Bucket(
            b["name"], float(b.get("weight", 0.0)), b.get("filter", ""),
            semantic_query=b.get("semantic_query", "") or b.get("query", ""),
            keyword=b.get("keyword", ""),
            top_k=int(b.get("top_k", 0)),
            min_sim=float(b.get("min_sim", -1.0)),
            quality_column=b.get("quality_column", "quality_score"),
            min_quality=(float(b["min_quality"]) if b.get("min_quality") is not None
                         else None),
            quality_grade=bool(b.get("quality_grade", False)),
            quality_tiers=tiers,
        ))
    total_tokens = _num(r.get("total_tokens"))
    total_samples = _int(r.get("total_samples"))
    stage = r.get("stage")
    if total_tokens is not None and total_samples is not None:
        raise ValueError("recipe must set only one of total_tokens or total_samples")
    if total_tokens is None and total_samples is None:
        if str(stage or "").lower() == "sft":
            total_samples = 100_000
            raw_recipe["total_samples"] = total_samples
        else:
            raise ValueError("recipe must set total_tokens or total_samples")
    total_w = sum(b.weight for b in buckets)
    if total_w <= 0:
        raise ValueError("recipe bucket weights must sum to > 0")
    return Recipe(
        name=r.get("name", "recipe"),
        stage=stage,
        total_tokens=total_tokens,
        total_samples=total_samples,
        buckets=buckets,
        dedup_across_buckets=bool(r.get("dedup_across_buckets", True)),
        strategy=strategy,
        temperature=float(sampling.get("temperature", 1.0)),
        max_repeat=int(sampling.get("max_repeat", 1)),
        seed=int(sampling.get("seed", 42)),
        apply_stage_filter=bool(r.get("apply_stage_filter", True)),
        tokenizer=r.get("tokenizer"),
        split=_parse_split(export.get("split") or r.get("split")),
        export_format=fmt,
        shard_size=str(export.get("shard_size", "256MB")),
        shuffle=bool(export.get("shuffle", True)),
        export_fields=export_fields,
        export_keep=export_keep,
        export_include_dm=export_include_dm,
        raw=raw,
    )


def _parse_export_schema(export: dict) -> tuple[dict[str, ExportField], list[str], bool]:
    schema_doc = export.get("schema") or {}
    if not schema_doc and export.get("mapping"):
        schema_doc = {"fields": export.get("mapping")}
    if not schema_doc:
        return {}, [], True
    fields_doc = schema_doc.get("fields") or schema_doc.get("mapping") or {}
    fields: dict[str, ExportField] = {}
    for name, spec in fields_doc.items():
        required = True
        default = None
        if isinstance(spec, str):
            sources = [spec]
        elif isinstance(spec, list):
            sources = [str(x) for x in spec]
        elif isinstance(spec, dict):
            raw_sources = spec.get("sources", spec.get("source", []))
            if isinstance(raw_sources, str):
                sources = [raw_sources]
            else:
                sources = [str(x) for x in (raw_sources or [])]
            required = bool(spec.get("required", True))
            default = spec.get("default")
        else:
            raise ValueError(f"bad export schema field {name!r}: {spec!r}")
        fields[str(name)] = ExportField(str(name), sources, required, default)
    keep = [str(x) for x in (schema_doc.get("keep") or [])]
    include_dm = bool(schema_doc.get("include_dm", True))
    return fields, keep, include_dm


def _num(v) -> float | None:
    return float(v) if v is not None else None


def _int(v) -> int | None:
    return int(v) if v is not None else None


def _parse_split(v) -> dict | None:
    if not v:
        return None
    if not isinstance(v, dict):
        raise ValueError(f"split must be a mapping of name->ratio, got {v!r}")
    split = {k: float(x) for k, x in v.items()}
    if any(x < 0 for x in split.values()) or sum(split.values()) <= 0:
        raise ValueError(f"split ratios must be positive: {split}")
    return split


_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGT]?B)\s*$", re.IGNORECASE)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def parse_size(s: str) -> int:
    m = _SIZE_RE.match(s)
    if not m:
        raise ValueError(f"bad shard_size: {s!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


# ---------------------------------------------------------------------------
# Planning / recall
# ---------------------------------------------------------------------------

def _bucket_filter(recipe: Recipe, bucket: Bucket) -> str:
    parts = []
    if bucket.filter:
        parts.append(f"({bucket.filter})")
    if recipe.apply_stage_filter and recipe.stage:
        parts.append(f"stage = '{recipe.stage}'")
    if bucket.min_quality is not None:
        parts.append(f"{bucket.quality_column} >= {bucket.min_quality}")
    return " AND ".join(parts)


_FAILURE_TAG_KEYS = ("bug_type", "failure_type", "error_type",
                     "failure_taxonomy", "error_category")
_FAILURE_CLASSES = ("syntax", "logic", "runtime", "assertion")


def _validate_failure_taxonomy_filter(bucket: Bucket) -> None:
    text = f"{bucket.name} {bucket.filter} {bucket.semantic_query} {bucket.keyword}".lower()
    expected = [name for name in _FAILURE_CLASSES if name in text]
    if not expected:
        return
    has_semantic_key = any(key in bucket.filter for key in _FAILURE_TAG_KEYS)
    has_expected_value = any(value in bucket.filter.lower() for value in expected)
    if not (has_semantic_key and has_expected_value):
        raise ValueError(
            "failure-taxonomy bucket "
            f"{bucket.name!r} must filter by a semantic failure tag "
            f"({_FAILURE_TAG_KEYS}); broad proxies such as lang/domain are not sufficient"
        )


# columns carried on each candidate (also surfaced in export provenance)
_CAND_COLS = "sample_id,cid,n_tokens,domain,lang,stage,quality_score"


def _bucket_candidates(store: DataStore, recipe: Recipe, bucket: Bucket) -> list[dict]:
    """Recall a bucket's candidate samples: scalar filter, then optional
    semantic / keyword recall from the L3 index (ranked by relevance)."""
    flt = _bucket_filter(recipe, bucket)
    rows = store.catalog.query(where=flt or None, columns=_CAND_COLS)
    if bucket.quality_grade and not (bucket.semantic_query or bucket.keyword):
        rows.sort(key=lambda r: r.get(bucket.quality_column) or -1, reverse=True)
    if not (bucket.semantic_query or bucket.keyword):
        return rows
    restrict = {r["sample_id"] for r in rows}
    by_id = {r["sample_id"]: r for r in rows}
    if bucket.semantic_query:
        ranked = store.index.semantic_recall(
            bucket.semantic_query, top_k=bucket.top_k or len(restrict),
            restrict=restrict, min_sim=bucket.min_sim,
        )
    else:
        ranked = store.index.keyword_recall(
            bucket.keyword, top_k=bucket.top_k or len(restrict), restrict=restrict
        )
    return [by_id[sid] for sid, _ in ranked if sid in by_id]


def _is_ranked(bucket: Bucket) -> bool:
    return bool(bucket.semantic_query or bucket.keyword)


def _effective_weights(recipe: Recipe) -> dict[str, float]:
    weights = {b.name: b.weight for b in recipe.buckets}
    if recipe.strategy == "temperature" and recipe.temperature != 1.0:
        t = recipe.temperature
        adj = {k: (w ** (1.0 / t)) for k, w in weights.items()}
        s = sum(adj.values()) or 1.0
        return {k: v / s for k, v in adj.items()}
    s = sum(weights.values()) or 1.0
    return {k: v / s for k, v in weights.items()}


@dataclass
class BucketPlan:
    name: str
    weight: float
    filter: str
    available_samples: int
    available_tokens: int
    target_tokens: int
    target_samples: int
    tiers: list = field(default_factory=list)   # quality-tier availability


def _tier_availability(store, recipe, bucket, budget_kind, bucket_target):
    """Per-tier available/target for a bucket with quality_tiers (for plan)."""
    base = _bucket_filter(recipe, bucket)
    col = bucket.quality_column
    tiers = sorted(bucket.quality_tiers, key=lambda x: x.min, reverse=True)
    tw = sum(t.weight for t in tiers) or 1.0
    out = []
    prev_hi = None
    for t in tiers:
        cond = f"{col} >= {t.min}" + (f" AND {col} < {prev_hi}" if prev_hi is not None else "")
        prev_hi = t.min
        flt = (f"({base}) AND ({cond})") if base else cond
        n = store.catalog.count(where=flt)
        tk = store.catalog.sum_tokens(where=flt)
        share = t.weight / tw
        out.append({
            "tier": t.label or f">={t.min}", "min": t.min, "weight": t.weight,
            "available_samples": n, "available_tokens": tk,
            "target_samples": int(round(share * bucket_target)) if budget_kind == "sample" else 0,
            "target_tokens": int(share * bucket_target) if budget_kind == "token" else 0,
        })
    return out


def plan(store: DataStore, recipe: Recipe) -> dict:
    """Compute per-bucket availability and targets without materializing."""
    weights = _effective_weights(recipe)
    budget_kind = "token" if recipe.total_tokens else "sample"
    plans: list[BucketPlan] = []
    for b in recipe.buckets:
        _validate_failure_taxonomy_filter(b)
        flt = _bucket_filter(recipe, b)
        if _is_ranked(b):
            cands = _bucket_candidates(store, recipe, b)
            avail_n = len(cands)
            avail_t = sum(c.get("n_tokens") or 0 for c in cands)
        else:
            avail_n = store.catalog.count(where=flt or None)
            avail_t = store.catalog.sum_tokens(where=flt or None)
        w = weights[b.name]
        tgt_tok = int(w * recipe.total_tokens) if recipe.total_tokens else 0
        tgt_smp = int(round(w * recipe.total_samples)) if recipe.total_samples else 0
        tiers = []
        if b.quality_tiers:
            tiers = _tier_availability(
                store, recipe, b, budget_kind,
                tgt_tok if budget_kind == "token" else tgt_smp)
        plans.append(
            BucketPlan(b.name, w, flt, avail_n, avail_t, tgt_tok, tgt_smp, tiers)
        )
    warnings = []
    for p in plans:
        if p.available_samples == 0:
            warnings.append(f"bucket '{p.name}' recalled 0 samples")
        elif budget_kind == "token" and p.available_tokens < p.target_tokens and recipe.max_repeat <= 1:
            warnings.append(
                f"bucket '{p.name}' short on tokens "
                f"({p.available_tokens} < {p.target_tokens}); enable epoch_repeat"
            )
        elif budget_kind == "sample" and p.available_samples < p.target_samples and recipe.max_repeat <= 1:
            warnings.append(
                f"bucket '{p.name}' short on samples "
                f"({p.available_samples} < {p.target_samples}); enable epoch_repeat"
            )
    # token-budget integrity: warn if n_tokens were produced by a tokenizer other
    # than the one the recipe budgets in (the mix ratios would be distorted).
    token_sources = {d["value"] or "unknown": d["n"]
                     for d in store.catalog.distribution("tokenizer")}
    if budget_kind == "token":
        want = recipe.tokenizer
        others = {k: v for k, v in token_sources.items() if k != want}
        if want and others:
            warnings.append(
                f"token budget is in '{want}' but samples were counted with "
                f"{others}; re-run token_count with tokenizer={want}")
        elif not want and ("heuristic" in token_sources or "unknown" in token_sources):
            warnings.append(
                "token budget uses *estimated* (heuristic) n_tokens; set a recipe "
                "'tokenizer:' and re-count for exact budgeting")
    return {
        "recipe": recipe.name,
        "fingerprint": recipe.fingerprint,
        "stage": recipe.stage,
        "strategy": recipe.strategy,
        "budget_kind": budget_kind,
        "tokenizer": recipe.tokenizer,
        "token_sources": token_sources,
        "total_tokens": recipe.total_tokens,
        "total_samples": recipe.total_samples,
        "buckets": [p.__dict__ for p in plans],
        "warnings": warnings,
    }


def preview(store: DataStore, recipe: Recipe, per_bucket: int = 3) -> dict:
    """Recall preview: the plan plus a few real sampled rows per bucket."""
    p = plan(store, recipe)
    rng = random.Random(recipe.seed)
    samples = {}
    for b in recipe.buckets:
        cands = _bucket_candidates(store, recipe, b)
        if not _is_ranked(b):
            rng.shuffle(cands)
        previews = []
        for c in cands[:per_bucket]:
            smp = store.catalog.get_sample(c["sample_id"]) or {}
            row = {
                "sample_id": c["sample_id"], "cid": c["cid"],
                "domain": smp.get("domain"), "lang": smp.get("lang"),
                "quality_score": smp.get("quality_score"),
                "n_tokens": smp.get("n_tokens"),
            }
            try:
                row["preview"] = utils.extract_text(store.get_content(c["cid"]))[:160]
            except KeyError:
                row["preview"] = None
            previews.append(row)
        samples[b.name] = previews
    p["samples"] = samples
    return p


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_META_KEYS = ("domain", "lang", "stage", "quality_score", "n_tokens")


@dataclass
class Selected:
    sample_id: str
    cid: str
    n_tokens: int
    bucket: str
    meta: dict = field(default_factory=dict)
    tier: str = ""


def _meta_of(c: dict) -> dict:
    return {k: c.get(k) for k in _META_KEYS if c.get(k) is not None}


def _select_bucket(
    candidates: list[dict],
    budget_kind: str,
    target: float,
    rng: random.Random,
    max_repeat: int,
    used_cids: set[str],
    dedup_across: bool,
    bucket_name: str,
    preserve_order: bool = False,
    tier: str = "",
) -> tuple[list[Selected], int, int, str]:
    pool = candidates[:]
    if not preserve_order:
        rng.shuffle(pool)
    if dedup_across:
        pool = [c for c in pool if c["cid"] not in used_cids]
    selected: list[Selected] = []
    realized_tokens = 0
    realized_count = 0
    if not pool or target <= 0:
        return selected, 0, 0, "empty" if not pool else "ok"
    idx = 0
    repeats = 0
    while True:
        if budget_kind == "token" and realized_tokens >= target:
            break
        if budget_kind == "sample" and realized_count >= target:
            break
        if idx >= len(pool):
            if repeats < max_repeat - 1:
                repeats += 1
                idx = 0
            else:
                return selected, realized_tokens, realized_count, "insufficient"
        c = pool[idx]
        idx += 1
        selected.append(
            Selected(c["sample_id"], c["cid"], c.get("n_tokens") or 0,
                     bucket_name, _meta_of(c), tier)
        )
        realized_tokens += c.get("n_tokens") or 0
        realized_count += 1
        if dedup_across:
            used_cids.add(c["cid"])
    return selected, realized_tokens, realized_count, "ok"


def _select_tiered(candidates, bucket, budget_kind, target, rng, max_repeat,
                   used, dedup_across):
    """Quality-graded recall: split the bucket budget across quality tiers and
    draw from each tier's quality band. Tiers are sorted high->low by ``min``."""
    col = bucket.quality_column
    tiers = sorted(bucket.quality_tiers, key=lambda x: x.min, reverse=True)
    tw = sum(t.weight for t in tiers) or 1.0
    all_sel: list[Selected] = []
    rtok = rcnt = 0
    statuses = []
    reports = []
    prev_hi = None
    for t in tiers:
        band = [c for c in candidates
                if c.get(col) is not None
                and c[col] >= t.min
                and (prev_hi is None or c[col] < prev_hi)]
        prev_hi = t.min
        label = t.label or f">={t.min}"
        sub = target * (t.weight / tw)
        sel, st, sc, status = _select_bucket(
            band, budget_kind, sub, rng, max_repeat, used, dedup_across,
            bucket.name, preserve_order=bucket.quality_grade, tier=label)
        all_sel.extend(sel)
        rtok += st
        rcnt += sc
        statuses.append(status)
        reports.append({"tier": label, "min": t.min, "weight": t.weight,
                        "available": len(band), "realized_samples": sc,
                        "realized_tokens": st, "status": status})
    overall = "ok" if all(s in ("ok", "empty") for s in statuses) else "insufficient"
    return all_sel, rtok, rcnt, overall, reports


def grade(store: DataStore, where: str | None = None,
          column: str = "quality_score",
          thresholds: list[float] | None = None) -> dict:
    """Quality-grade a selection: count samples/tokens per quality band.

    ``thresholds`` are descending cut points (default ``[0.8, 0.6, 0.4]``) that
    produce bands A>=0.8, 0.6<=B<0.8, 0.4<=C<0.6, D<0.4, plus an ``unscored``
    band for NULLs.
    """
    if column not in schema.CORE_FIELD_NAMES:
        raise ValueError(f"not a core column: {column}")
    thresholds = sorted(thresholds or [0.8, 0.6, 0.4], reverse=True)
    base = catalog.validate_filter(where) if where else ""
    bands = []
    labels = ["A", "B", "C", "D", "E", "F"]
    prev_hi = None
    for i, lo in enumerate(thresholds):
        cond = f"{column} >= {lo}" + (f" AND {column} < {prev_hi}" if prev_hi is not None else "")
        bands.append((labels[i], lo, prev_hi, cond))
        prev_hi = lo
    bands.append((labels[len(thresholds)], None, prev_hi, f"{column} < {prev_hi}"))

    def _count(cond):
        flt = f"({cond})" + (f" AND ({base})" if base else "")
        n = store.catalog.count(where=flt)
        tk = store.catalog.sum_tokens(where=flt)
        return n, tk

    out = []
    for label, lo, hi, cond in bands:
        n, tk = _count(cond)
        out.append({"tier": label, "min": lo, "max": hi, "samples": n, "tokens": tk})
    nflt = f"{column} IS NULL" + (f" AND ({base})" if base else "")
    nn = store.catalog.count(where=nflt)
    if nn:
        out.append({"tier": "unscored", "min": None, "max": None,
                    "samples": nn, "tokens": store.catalog.sum_tokens(where=nflt)})
    return {"column": column, "thresholds": thresholds, "filter": where,
            "tiers": out}


def select(store: DataStore, recipe: Recipe) -> tuple[list[Selected], dict]:
    weights = _effective_weights(recipe)
    budget_kind = "token" if recipe.total_tokens else "sample"
    rng = random.Random(recipe.seed)
    used: set[str] = set()
    all_selected: list[Selected] = []
    report = []
    for b in recipe.buckets:
        cands = _bucket_candidates(store, recipe, b)
        w = weights[b.name]
        if budget_kind == "token":
            target = w * (recipe.total_tokens or 0)
        else:
            target = round(w * (recipe.total_samples or 0))
        if b.quality_tiers:
            sel, rtok, rcnt, status, tiers = _select_tiered(
                cands, b, budget_kind, target, rng, recipe.max_repeat, used,
                recipe.dedup_across_buckets)
        else:
            sel, rtok, rcnt, status = _select_bucket(
                cands, budget_kind, target, rng, recipe.max_repeat, used,
                recipe.dedup_across_buckets, b.name, preserve_order=_is_ranked(b))
            tiers = None
        all_selected.extend(sel)
        entry = {
            "bucket": b.name,
            "weight": round(w, 4),
            "target": int(target),
            "realized_samples": rcnt,
            "realized_tokens": rtok,
            "status": status,
        }
        if tiers is not None:
            entry["tiers"] = tiers
        report.append(entry)
    if recipe.shuffle:
        rng.shuffle(all_selected)
    summary = {
        "selected_samples": len(all_selected),
        "selected_tokens": sum(s.n_tokens for s in all_selected),
        "buckets": report,
    }
    return all_selected, summary


# ---------------------------------------------------------------------------
# Export / materialization
# ---------------------------------------------------------------------------

def diff(a: Recipe, b: Recipe) -> dict:
    """Structural diff between two recipe versions (for L6 version compare)."""
    wa = {bk.name: bk.weight for bk in a.buckets}
    wb = {bk.name: bk.weight for bk in b.buckets}
    names = sorted(set(wa) | set(wb))
    bucket_changes = []
    for n in names:
        x, y = wa.get(n), wb.get(n)
        if x != y:
            bucket_changes.append({
                "bucket": n, "from": x, "to": y,
                "status": "added" if x is None else
                          "removed" if y is None else "changed",
            })
    fields = {}
    for f in ("stage", "total_tokens", "total_samples", "strategy",
              "temperature", "max_repeat", "dedup_across_buckets",
              "export_format"):
        if getattr(a, f) != getattr(b, f):
            fields[f] = {"from": getattr(a, f), "to": getattr(b, f)}
    return {
        "a": {"name": a.name, "fingerprint": a.fingerprint},
        "b": {"name": b.name, "fingerprint": b.fingerprint},
        "identical": a.fingerprint == b.fingerprint,
        "bucket_changes": bucket_changes,
        "field_changes": fields,
    }


def _record(store: DataStore, sel: Selected) -> dict:
    content = store.get_content(sel.cid)
    if isinstance(content, dict):
        rec = dict(content)
    else:
        rec = {"text": content}
    sample = store.catalog.get_sample(sel.sample_id) or {}
    for k, v in (sample.get("tags") or {}).items():
        if k not in rec and v is not None:
            rec[k] = v
    prov = {"bucket": sel.bucket, "sample_id": sel.sample_id, "cid": sel.cid}
    if sel.tier:
        prov["tier"] = sel.tier
    prov.update(sel.meta)        # domain/lang/stage/quality_score/n_tokens
    rec["_dm"] = prov
    return rec


_SFT_SCHEMA_SUGGESTION = {
    "export": {
        "format": "jsonl",
        "schema": {
            "fields": {
                "instruction": {
                    "sources": ["instruction", "INSTRUCTION", "question", "prompt", "input", "text"],
                },
                "input": {
                    "sources": ["input"],
                    "required": False,
                    "default": "",
                },
                "output": {
                    "sources": ["output", "RESPONSE", "answer", "solution", "result"],
                },
            },
            "keep": ["source_uri", "source_domain", "split"],
            "include_dm": True,
        },
    },
}
_SFT_SCHEMA_TEMPLATE = yaml.safe_dump(_SFT_SCHEMA_SUGGESTION, sort_keys=False)


def _requires_export_schema(recipe: Recipe) -> bool:
    return str(recipe.stage or "").lower() == "sft" and recipe.export_format in {
        "jsonl", "mds", "webdataset",
    }


def _export_schema_config_report(recipe: Recipe) -> dict:
    return {
        "enabled": bool(recipe.export_fields),
        "required": _requires_export_schema(recipe),
        "recipe_name": recipe.name,
        "recipe_stage": recipe.stage,
        "format": recipe.export_format,
        "fields": {
            name: {
                "sources": list(field_spec.sources),
                "required": field_spec.required,
                "default": field_spec.default,
            }
            for name, field_spec in recipe.export_fields.items()
        },
        "keep": list(recipe.export_keep),
        "include_dm": recipe.export_include_dm,
    }


def _export_schema_diagnostic(
    recipe: Recipe,
    *,
    code: str,
    message: str,
    problems: list[dict],
    failures: list[dict] | None = None,
    shape_mismatch: list[dict] | None = None,
) -> dict:
    diagnostic = {
        "valid": False,
        "code": code,
        "message": message,
        "recipe_name": recipe.name,
        "recipe_stage": recipe.stage,
        "format": recipe.export_format,
        "config": _export_schema_config_report(recipe),
        "problems": problems,
        "required_sft_shapes": [
            {"fields": ["messages"], "description": "chat-style SFT rows"},
            {"fields": ["instruction", "output"], "description": "instruction-response SFT rows"},
        ],
        "action_required": (
            "Modify the recipe YAML export.schema.fields mapping, then run "
            "`recipe validate` again before `recipe export`."
        ),
        "suggested_yaml": copy.deepcopy(_SFT_SCHEMA_SUGGESTION),
        "suggested_yaml_text": _SFT_SCHEMA_TEMPLATE,
    }
    if failures is not None:
        diagnostic["failures"] = failures
    if shape_mismatch is not None:
        diagnostic["shape_mismatch"] = shape_mismatch
    return diagnostic


def _validate_export_schema_config(recipe: Recipe) -> None:
    if not _requires_export_schema(recipe):
        return
    if not recipe.export_fields:
        raise ExportSchemaError(_export_schema_diagnostic(
            recipe,
            code="missing_export_schema_mapping",
            message=(
                "SFT recipe export needs recipe.export.schema.fields. DataMixer will not "
                "write final training data from heterogeneous source keys without an "
                "explicit YAML mapping."
            ),
            problems=[
                {
                    "path": "recipe.export.schema.fields",
                    "reason": "missing",
                    "repair": "Add a mapping from source keys to the final SFT keys.",
                }
            ],
        ))
    field_names = set(recipe.export_fields)
    if "messages" not in field_names and not {"instruction", "output"} <= field_names:
        raise ExportSchemaError(_export_schema_diagnostic(
            recipe,
            code="missing_required_sft_fields",
            message=(
                "SFT recipe export schema must define either `messages` or both "
                "`instruction` and `output`."
            ),
            problems=[
                {
                    "path": "recipe.export.schema.fields",
                    "reason": "required SFT output keys are incomplete",
                    "present_fields": sorted(field_names),
                    "repair": "Map either messages, or map both instruction and output.",
                }
            ],
        ))


def _lookup_path(rec: dict, path: str) -> Any:
    cur: Any = rec
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _mapped_record(rec: dict, recipe: Recipe) -> tuple[dict, list[str]]:
    if not recipe.export_fields:
        return rec, []
    out: dict[str, Any] = {}
    missing: list[str] = []
    for name, field_spec in recipe.export_fields.items():
        value = None
        for source in field_spec.sources:
            candidate = _lookup_path(rec, source)
            if _present(candidate):
                value = candidate
                break
        if not _present(value):
            if field_spec.default is not None:
                value = field_spec.default
            elif field_spec.required:
                missing.append(name)
                continue
            else:
                value = ""
        out[name] = value
    for key in recipe.export_keep:
        value = _lookup_path(rec, key)
        if _present(value):
            out[key] = value
    if recipe.export_include_dm and "_dm" in rec:
        out["_dm"] = rec["_dm"]
    return out, missing


def _assert_export_schema(store: DataStore, selected: list[Selected], recipe: Recipe) -> dict:
    _validate_export_schema_config(recipe)
    if not recipe.export_fields:
        return {"enabled": False}
    checked = 0
    failures = []
    shape: tuple[str, ...] | None = None
    shape_mismatch = []
    for sel in selected:
        rec = _record(store, sel)
        mapped, missing = _mapped_record(rec, recipe)
        checked += 1
        keys = tuple(sorted(mapped.keys()))
        if shape is None:
            shape = keys
        elif keys != shape and len(shape_mismatch) < 5:
            shape_mismatch.append({
                "sample_id": sel.sample_id,
                "bucket": sel.bucket,
                "keys": list(keys),
                "expected_keys": list(shape),
                "available_source_keys": sorted(rec.keys()),
            })
        if missing and len(failures) < 10:
            failures.append({
                "sample_id": sel.sample_id,
                "bucket": sel.bucket,
                "missing": missing,
                "available_source_keys": sorted(rec.keys()),
            })
    if failures or shape_mismatch:
        problems = []
        if failures:
            problems.append({
                "reason": "mapped required fields are missing for selected samples",
                "repair": (
                    "Add the real source keys to export.schema.fields.<field>.sources, "
                    "normalize the records with DataMixer/DataFlow before export, or "
                    "exclude buckets that cannot produce the required SFT fields."
                ),
            })
        if shape_mismatch:
            problems.append({
                "reason": "mapped records do not have one uniform final key set",
                "repair": (
                    "Move optional output keys into export.schema.fields with required:false "
                    "and defaults, or remove optional keep keys that are not present on every row."
                ),
            })
        raise ExportSchemaError(_export_schema_diagnostic(
            recipe,
            code="export_schema_mapping_failed",
            message=(
                "SFT recipe export schema did not produce a complete uniform final schema "
                "for the selected records."
            ),
            problems=problems,
            failures=failures,
            shape_mismatch=shape_mismatch,
        ))
    return {
        "enabled": True,
        "checked": checked,
        "fields": list(recipe.export_fields),
        "keep": list(recipe.export_keep),
        "include_dm": recipe.export_include_dm,
        "keys": list(shape or ()),
    }


def _selected_from_snapshot(store: DataStore, snap: dict) -> tuple[list, dict]:
    sel = [Selected(m["sample_id"], m["cid"], m.get("n_tokens") or 0, "snapshot")
           for m in snap["members"]]
    summary = {"selected_samples": len(sel),
               "selected_tokens": sum(s.n_tokens for s in sel),
               "buckets": [], "from_snapshot": snap["id"]}
    return sel, summary


def export(store: DataStore, recipe: Recipe, out_dir: str | None = None,
           from_snapshot: str | None = None, snapshot: bool = False) -> dict:
    from . import snapshot as snap_mod
    if from_snapshot:
        selected, summary = _selected_from_snapshot(
            store, snap_mod.get(store, from_snapshot))
    else:
        p = plan(store, recipe)
        blockers = [
            warning for warning in p.get("warnings", [])
            if "recalled 0 samples" in warning or "short on" in warning
        ]
        if blockers:
            raise ValueError("recipe plan has blocking shortages: " + "; ".join(blockers))
        selected, summary = select(store, recipe)
    export_schema_report = _assert_export_schema(store, selected, recipe)
    export_id = "exp-" + utils.fingerprint(
        {"r": recipe.fingerprint, "ts": time.time()}
    )
    out = Path(out_dir) if out_dir else (store.exports_dir / export_id)
    out.mkdir(parents=True, exist_ok=True)
    shard_bytes = parse_size(recipe.shard_size)

    # train/val/test split: deterministically partition the selection, each split
    # to its own subdir with its own digest (reproducible, leakage-free).
    splits = None
    if recipe.split:
        parts = _split_selection(selected, recipe.split, recipe.seed)
        splits = {}
        for name, items in parts.items():
            sub = out / name
            sub.mkdir(parents=True, exist_ok=True)
            sfiles = _materialize(store, items, sub, recipe, shard_bytes)
            splits[name] = {"count": len(items),
                            "tokens": sum(s.n_tokens for s in items),
                            "digest": snap_mod.selection_digest(items),
                            "files": sfiles}
        files = []
    else:
        files = _materialize(store, selected, out, recipe, shard_bytes)

    # the data digest pins the *actual* set chosen, so the export is reproducible
    # as the pair (recipe_fingerprint, dataset_digest).
    dataset_digest = snap_mod.selection_digest(selected)
    snapshot_id = from_snapshot
    if snapshot and not from_snapshot:
        ids = {s.sample_id for s in selected}
        # record a snapshot of exactly this selection
        snap = {"id": "snap-" + utils.fingerprint({"d": dataset_digest}),
                "name": f"{recipe.name}@export", "created_at": time.time(),
                "count": len(selected), "digest": dataset_digest,
                "tokens": summary.get("selected_tokens", 0),
                "members": [{"sample_id": s.sample_id, "cid": s.cid,
                             "n_tokens": s.n_tokens} for s in selected]}
        sd = Path(store.root) / "snapshots"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / f"{snap['id']}.json").write_text(utils.canonical_json(snap).decode())
        snapshot_id = snap["id"]

    manifest = {
        "kind": "export",
        "export_id": export_id,
        "timestamp": time.time(),
        "recipe_name": recipe.name,
        "recipe_fingerprint": recipe.fingerprint,
        "recipe": recipe.raw,
        "tokenizer": recipe.tokenizer,
        "dataset_digest": dataset_digest,
        "snapshot_id": snapshot_id,
        "format": recipe.export_format,
        "export_schema": export_schema_report,
        "summary": summary,
        "splits": splits,
        "files": files,
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        utils.canonical_json(manifest).decode()
    )
    return {
        "export_id": export_id,
        "out_dir": str(out),
        "format": recipe.export_format,
        "recipe_fingerprint": recipe.fingerprint,
        "dataset_digest": dataset_digest,
        "snapshot_id": snapshot_id,
        "manifest_path": str(manifest_path),
        "splits": {k: v["count"] for k, v in splits.items()} if splits else None,
        "files": len(files),
        **summary,
    }


def _materialize(store, selected, out: Path, recipe: Recipe, shard_bytes) -> list:
    if recipe.export_format == "jsonl":
        return _export_jsonl(store, selected, out, recipe, shard_bytes, gz=False)
    if recipe.export_format == "mds":
        return _export_jsonl(store, selected, out, recipe, shard_bytes, gz=True)
    if recipe.export_format == "webdataset":
        return _export_webdataset(store, selected, out, recipe, shard_bytes)
    if recipe.export_format == "megatron":
        return _export_megatron(store, selected, out, recipe.tokenizer)
    raise ValueError(recipe.export_format)  # pragma: no cover - guarded at parse time


def _export_megatron(store, selected, out: Path, tokenizer_spec) -> list:
    from . import megatron, tokenizers
    tok = tokenizers.resolve(tokenizer_spec)

    def docs():
        for sel in selected:
            yield tok.encode(utils.extract_text(store.get_content(sel.cid)))

    info = megatron.write_indexed(out / "dataset", docs())
    return [
        {"name": "dataset.bin", "bytes": (out / "dataset.bin").stat().st_size,
         "documents": info["documents"], "total_tokens": info["total_tokens"],
         "tokenizer": tok.name, "exact_tokens": tok.exact},
        {"name": "dataset.idx", "bytes": (out / "dataset.idx").stat().st_size},
    ]


def _split_selection(selected, split: dict, seed: int) -> dict:
    """Deterministically partition a selection into named splits by ratio.

    Order-independent: sort by sample_id, shuffle with the recipe seed, then
    slice — so the same (selection, split, seed) always yields the same splits
    and there is no leakage between them (disjoint by construction)."""
    items = sorted(selected, key=lambda s: s.sample_id)
    random.Random(seed).shuffle(items)
    names = list(split.keys())
    ratios = [split[n] for n in names]
    total = sum(ratios) or 1.0
    n = len(items)
    out, start = {}, 0
    for i, name in enumerate(names):
        if i == len(names) - 1:
            end = n
        else:
            end = start + int(round(n * ratios[i] / total))
        out[name] = items[start:end]
        start = end
    return out


def _export_jsonl(store, selected, out: Path, recipe: Recipe, shard_bytes, gz: bool) -> list[dict]:
    import gzip
    files = []
    shard_idx = 0
    written = 0
    buf = io.BytesIO()

    def flush():
        nonlocal shard_idx, buf, written
        if written == 0:
            return
        ext = "jsonl.gz" if gz else "jsonl"
        name = f"part-{shard_idx:05d}.{ext}"
        data = buf.getvalue()
        if gz:
            data = gzip.compress(data, 6)
        (out / name).write_bytes(data)
        files.append({"name": name, "bytes": len(data), "records": written})
        shard_idx += 1
        buf = io.BytesIO()
        written = 0

    for sel in selected:
        rec, _missing = _mapped_record(_record(store, sel), recipe)
        line = utils.canonical_json(rec) + b"\n"
        buf.write(line)
        written += 1
        if buf.tell() >= shard_bytes:
            flush()
    flush()
    return files


def _export_webdataset(store, selected, out: Path, recipe: Recipe, shard_bytes) -> list[dict]:
    files = []
    shard_idx = 0
    count = 0
    tar = None

    def open_shard():
        nonlocal tar, shard_idx
        name = f"part-{shard_idx:06d}.tar"
        tar = tarfile.open(out / name, "w")
        return name

    name = open_shard()
    for sel in selected:
        rec, _missing = _mapped_record(_record(store, sel), recipe)
        payload = utils.canonical_json(rec)
        info = tarfile.TarInfo(name=f"{sel.sample_id}.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        count += 1
        if (out / name).stat().st_size >= shard_bytes:
            tar.close()
            files.append({"name": name, "bytes": (out / name).stat().st_size})
            shard_idx += 1
            name = open_shard()
    tar.close()
    sz = (out / name).stat().st_size
    if count and (not files or files[-1]["name"] != name):
        files.append({"name": name, "bytes": sz})
    return files
