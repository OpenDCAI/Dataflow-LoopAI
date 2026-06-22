from __future__ import annotations

import random
from pathlib import Path
from typing import Iterable

from .config import resolve_lake_root
from .errors import CandidateNotEnoughError
from .lock import commit_lock
from .models import parse_tags, sha256_text, utc_now
from .tables import append_rows, ensure_tables, read_table, write_jsonl


def _tag_index(tag_rows: list[dict]) -> dict[str, dict[str, set[str]]]:
    index: dict[str, dict[str, set[str]]] = {}
    for tag in tag_rows:
        index.setdefault(tag["tag_name"], {}).setdefault(str(tag["tag_value"]), set()).add(tag["record_id"])
    return index


def _matching_tag_ids(index: dict[str, dict[str, set[str]]], include: dict[str, str], exclude: dict[str, str]) -> set[str] | None:
    included: set[str] | None = None
    for name, value in include.items():
        ids = set(index.get(name, {}).get(value, set()))
        included = ids if included is None else included & ids
    excluded: set[str] = set()
    for name, value in exclude.items():
        excluded |= set(index.get(name, {}).get(value, set()))
    if included is None:
        return None if not excluded else set().union(*[ids for values in index.values() for ids in values.values()]) - excluded
    return included - excluded


def _equal_quotas(num_groups: int, budget: int) -> list[int]:
    if num_groups <= 0:
        return []
    base = budget // num_groups
    remainder = budget % num_groups
    return [base + (1 if i < remainder else 0) for i in range(num_groups)]


def _tag_values_by_record(tag_rows: list[dict], tag_name: str) -> dict[str, str]:
    values = {}
    for row in tag_rows:
        if row.get("tag_name") == tag_name:
            values[row["record_id"]] = str(row.get("tag_value", ""))
    return values


def _stratified_sample(
    candidates: list[dict],
    *,
    tag_rows: list[dict],
    balance_by: str,
    n: int,
    rng: random.Random,
) -> list[dict]:
    if not balance_by.startswith("tag:"):
        raise ValueError("First version supports --balance-by tag:<name>")
    tag_name = balance_by.split(":", 1)[1]
    values = _tag_values_by_record(tag_rows, tag_name)
    groups: dict[str, list[dict]] = {}
    for record in candidates:
        value = values.get(record["record_id"], "")
        if value:
            groups.setdefault(value, []).append(record)
    if not groups:
        return []
    group_keys = sorted(groups.keys())
    quotas = _equal_quotas(len(group_keys), n)
    selected: list[dict] = []
    leftovers: list[dict] = []
    for key, quota in zip(group_keys, quotas):
        group = list(groups[key])
        rng.shuffle(group)
        selected.extend(group[:quota])
        leftovers.extend(group[quota:])
    if len(selected) < min(n, len(candidates)):
        rng.shuffle(leftovers)
        selected.extend(leftovers[: n - len(selected)])
    return selected[:n]


def sample_records(
    *,
    lake: str | Path,
    output: str | Path,
    domain: str | None = None,
    processing_level: str | None = None,
    source_kind: str | None = None,
    task_type: str | None = None,
    include_tags: Iterable[str] | None = None,
    exclude_tags: Iterable[str] | None = None,
    n: int = 100,
    allow_smaller: bool = False,
    seed: int = 42,
    strategy: str = "random",
    balance_by: str = "",
) -> dict:
    if strategy not in {"random", "stratified"}:
        raise ValueError("First version supports strategy=random|stratified")
    lake_root = resolve_lake_root(lake)
    ensure_tables(lake_root)
    include = parse_tags(include_tags)
    exclude = parse_tags(exclude_tags)
    records = read_table(lake_root, "records")
    tag_rows = read_table(lake_root, "record_tags")

    core_filtered = []
    for record in records:
        if domain and record.get("domain") != domain:
            continue
        if processing_level and record.get("processing_level") != processing_level:
            continue
        if source_kind and record.get("source_kind") != source_kind:
            continue
        if task_type and record.get("task_type") != task_type:
            continue
        core_filtered.append(record)

    tag_ids = _matching_tag_ids(_tag_index(tag_rows), include, exclude)
    if tag_ids is not None:
        candidates = [record for record in core_filtered if record["record_id"] in tag_ids]
    else:
        candidates = core_filtered

    if len(candidates) < n and not allow_smaller:
        raise CandidateNotEnoughError(n, len(candidates))
    rng = random.Random(seed)
    if strategy == "stratified":
        if not balance_by:
            raise ValueError("strategy=stratified requires balance_by")
        selected = _stratified_sample(candidates, tag_rows=tag_rows, balance_by=balance_by, n=n, rng=rng)
    else:
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        selected = shuffled[: min(n, len(shuffled))]
    write_jsonl(output, selected)
    warnings = []
    status = "success"
    if len(candidates) < n and allow_smaller:
        status = "success_with_warnings"
        warnings.append(
            {
                "code": "ALLOW_SMALLER_TRIGGERED",
                "message": f"Requested {n} records but exported {len(selected)} because --allow-smaller was set.",
            }
        )
    export_id = "export_" + sha256_text(f"{Path(output).resolve()}:{utc_now()}")[:24]
    with commit_lock(lake_root):
        append_rows(
            lake_root,
            "exports",
            [
                {
                    "export_id": export_id,
                    "query": {
                        "domain": domain,
                        "processing_level": processing_level,
                        "source_kind": source_kind,
                        "task_type": task_type,
                    "include_tags": include,
                    "exclude_tags": exclude,
                    "balance_by": balance_by,
                },
                    "strategy": strategy,
                    "seed": seed,
                    "requested_size": n,
                    "actual_size": len(selected),
                    "output_uri": str(Path(output).resolve()),
                    "format": "jsonl",
                    "record_ids_sha256": sha256_text(",".join(sorted(row["record_id"] for row in selected))),
                    "created_at": utc_now(),
                }
            ],
        )
    return {
        "ok": True,
        "command": "sample",
        "status": status,
        "warnings": warnings,
        "lake_root": str(lake_root),
        "requested_size": n,
        "actual_size": len(selected),
        "output_uri": str(Path(output).resolve()),
    }
