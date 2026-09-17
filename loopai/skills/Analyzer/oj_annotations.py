"""Public diagnosis fields added to copies of the original Judger records."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .bucket_strategy import classify_failure_bucket

UNKNOWN_CRITIQUE = "现有记录缺少足够的判因信息，无法确定具体失败原因。"


def diagnosis_annotation(record: dict, task_type: str) -> dict:
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    tag = judge.get("overall_error_tag") or record.get("overall_error_tag")
    unknown = {"", "other", "unknown", "diagnostic_unknown"}
    if str(tag or "").strip().lower() in unknown:
        bucket = classify_failure_bucket(record, task_type=task_type)
        tag = bucket["label"] if bucket["bucket"] != "diagnostic_unknown" else ""
    if not tag:
        tags = judge.get("tags") or []
        tags = [tags] if isinstance(tags, str) else tags
        tag = "、".join(str(value) for value in tags if str(value).strip().lower() not in unknown)
    critique = (judge.get("short_critique") or record.get("short_critique")
                or record.get("brief_analysis") or judge.get("reason") or "")
    if (not isinstance(critique, str) or critique == UNKNOWN_CRITIQUE
            or any(marker in critique.replace(" ", "") for marker in ("短评生成失败", "LLMaJ运行异常"))):
        critique = ""
    return {"overall_error_tag": str(tag or "").strip(), "short_critique": critique.strip()}


def read_oj_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        rows = None
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = next((payload[key] for key in ("records", "rows", "data", "examples", "items")
                         if isinstance(payload.get(key), list)), None)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected a list of Judger records: {path}")
    return rows


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_annotated_oj(originals: list[dict], diagnosed: list[dict], output: Path, task_type: str) -> Path:
    if len(originals) != len(diagnosed):
        raise ValueError("Original and annotated OJ record counts differ")
    public = []
    for index, (original, record) in enumerate(zip(originals, diagnosed)):
        # Position is safe only when identity, prediction and verdict still agree.
        for key in ("task_id", "id", "sample_id", "sample_index", "question", "problem_prompt", "prompt",
                    "completion", "prediction", "pred_sql", "db_file", "passed", "correct"):
            if key in original and record.get(key) != original[key]:
                raise ValueError(f"Original OJ changed or rows were reordered: row {index}, field {key}")
        row = deepcopy(original)
        passed = record.get("passed", record.get("correct"))
        if type(passed) is not bool:
            raise ValueError(f"Missing boolean Judger verdict at row {index}")
        if not passed:
            annotation = diagnosis_annotation(record, task_type)
            row["overall_error_tag"] = annotation["overall_error_tag"] or "diagnostic_unknown"
            row["short_critique"] = annotation["short_critique"] or UNKNOWN_CRITIQUE
        public.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in public:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(output)
    return output


def export_bench_oj(cfg: dict, dataset: str, rows: list[dict], directory: Path, fallback_source: str) -> Path:
    sources = [item for item in cfg.get("eval_result_sources", [])
               if item.get("bench_name") == dataset or dataset in item.get("record_bench_names", [])]
    originals = []
    if sources:
        for source in sources:
            path = Path(source["path"])
            if source.get("sha256") and source_digest(path) != source["sha256"]:
                raise ValueError("Original Judger source changed after diagnosis; refusing misaligned annotation")
            originals.extend(row for row in read_oj_rows(path) if row.get("bench_name", source["bench_name"]) == dataset)
    else:
        # Older checkpoints may only retain the enriched input, not source provenance.
        originals = deepcopy(rows)
    path = write_annotated_oj(originals, rows, directory / "09_oj_enriched.jsonl", cfg.get("analyze_task_type", "code"))
    cfg.setdefault("enriched_oj_sources", {})[dataset] = {
        "source_paths": [item["path"] for item in sources] or [fallback_source],
        "original_source_verified": bool(sources),
    }
    cfg.setdefault("enriched_oj_paths", {})[dataset] = str(path.resolve())
    if len(cfg["enriched_oj_paths"]) == 1:
        cfg["enriched_oj_path"] = str(path.resolve())
    else:
        cfg.pop("enriched_oj_path", None)
    return path
