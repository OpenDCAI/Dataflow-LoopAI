"""
Dispatch node — creates and runs a DatasetAgent for each discovered source,
then applies the mapping plan to produce intermediate-format JSONL records.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loopai.logger import add_context_file_handler, get_logger, logging_context, remove_handler
from ..dataset_agent import DatasetAgent
from ..memory import PostprocessMemoryManager
from ..schemas import DatasetAgentResult, DatasetMappingPlan, DatasetSourceInfo

logger = get_logger()

CHUNK_SIZE = 10_000
ROLE_JOINER_DEFAULTS = {
    "system": "\n\n",
    "user": "\n",
    "assistant": "\n",
}


def _safe_name(name: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in (name or "unknown"))
    return safe.strip("._") or "unknown"


# ---------------------------------------------------------------------------
# Data conversion using the mapping plan
# ---------------------------------------------------------------------------

def _load_data_file(file_path: str):
    """Load a data file and return (column_names, iterable_of_dicts, total)."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        from datasets import load_dataset, DatasetDict
        builder_map = {
            ".json": "json", ".jsonl": "json",
            ".csv": "csv", ".tsv": "csv",
            ".parquet": "parquet", ".arrow": "arrow",
            ".txt": "text",
        }
        builder = builder_map.get(ext)
        if builder:
            kwargs = {"data_files": file_path}
            if ext == ".tsv":
                kwargs["delimiter"] = "\t"
            ds = load_dataset(builder, **kwargs, trust_remote_code=True)
            if isinstance(ds, DatasetDict):
                for split_name in ds:
                    split = ds[split_name]
                    yield split.column_names, split, len(split), split_name
                return
            yield ds.column_names, ds, len(ds), "train"
            return
    except Exception as e:
        logger.debug(f"[dispatch] datasets load failed for {file_path}: {e}")

    try:
        import pandas as pd
        if ext in (".json", ".jsonl"):
            df = pd.read_json(file_path, lines=(ext == ".jsonl"))
        elif ext == ".csv":
            df = pd.read_csv(file_path)
        elif ext == ".tsv":
            df = pd.read_csv(file_path, sep="\t")
        elif ext == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            return
        cols = list(df.columns)
        records = df.to_dict(orient="records")
        yield cols, records, len(records), "train"
    except Exception as e:
        logger.debug(f"[dispatch] pandas load failed for {file_path}: {e}")


def _generate_record_id(file_path: str, index: int) -> str:
    base = os.path.splitext(os.path.basename(file_path))[0]
    return f"{base}_{index:06d}"


def _dict_row(row: Any) -> Optional[Dict[str, Any]]:
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return None


def _get_path_value(data: Any, path: Any) -> Any:
    if path is None:
        return None
    if not isinstance(path, str):
        return path
    if path == "":
        return None
    if "." not in path:
        if isinstance(data, dict):
            return data.get(path)
        return None

    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
            continue
        if isinstance(current, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(current):
                current = current[idx]
                continue
        return None
    return current


def _value_to_text(value: Any, transform: Optional[str] = None) -> str:
    if value is None:
        return ""

    if transform == "json_dumps":
        try:
            return json.dumps(value, ensure_ascii=False, default=str).strip()
        except Exception:
            return str(value).strip()

    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)

    if transform in (None, "", "strip"):
        return text.strip()
    if transform == "lower":
        return text.strip().lower()
    if transform == "upper":
        return text.strip().upper()
    return text.strip()


def _resolve_transform(plan: DatasetMappingPlan, role: Optional[str], field: Optional[str]) -> Optional[str]:
    transforms = plan.field_transforms or {}
    if field and f"field:{field}" in transforms:
        return transforms[f"field:{field}"]
    if role and role in transforms:
        return transforms[role]
    return transforms.get("default")


def _resolve_joiner(plan: DatasetMappingPlan, role: Optional[str], field: Optional[str], default: str) -> str:
    joiners = plan.field_joiners or {}
    if field and f"field:{field}" in joiners:
        return joiners[f"field:{field}"]
    if role and role in joiners:
        return joiners[role]
    if "default" in joiners:
        return joiners["default"]
    return default


def _extract_text_from_spec(
    row: Dict[str, Any],
    spec: Any,
    plan: DatasetMappingPlan,
    *,
    role: Optional[str] = None,
    default_joiner: str = " ",
) -> str:
    if spec is None:
        return ""

    if isinstance(spec, list):
        parts: List[str] = []
        for field in spec:
            value = _get_path_value(row, field)
            transform = _resolve_transform(plan, role, field if isinstance(field, str) else None)
            text = _value_to_text(value, transform=transform)
            if text:
                parts.append(text)
        joiner = _resolve_joiner(plan, role, None, default_joiner)
        return joiner.join(parts).strip()

    field = spec if isinstance(spec, str) else None
    value = _get_path_value(row, spec)
    transform = _resolve_transform(plan, role, field)
    return _value_to_text(value, transform=transform)


def _extract_records_from_row(row: Dict[str, Any], plan: DatasetMappingPlan) -> List[Dict[str, Any]]:
    if plan.record_path:
        candidate = _get_path_value(row, plan.record_path)
        parent_fields = {
            k: v for k, v in row.items()
            if k != (plan.record_path.split(".", 1)[0] if isinstance(plan.record_path, str) else plan.record_path)
        }
        if isinstance(candidate, list):
            rows = []
            for item in candidate:
                item_dict = _dict_row(item)
                if item_dict is None:
                    continue
                merged = dict(parent_fields)
                merged.update(item_dict)
                rows.append(merged)
            rows = [r for r in rows if r is not None]
            if rows:
                return rows
        if isinstance(candidate, dict):
            merged = dict(parent_fields)
            merged.update(candidate)
            return [merged]

    list_candidates = [
        v for v in row.values()
        if isinstance(v, list) and v and all(isinstance(item, dict) for item in v)
    ]
    if len(list_candidates) == 1:
        rows = [_dict_row(item) for item in list_candidates[0]]
        rows = [r for r in rows if r is not None]
        if rows:
            return rows
    return [row]


def _iter_normalized_rows(data: Any, plan: DatasetMappingPlan):
    for raw_row in data:
        row = _dict_row(raw_row)
        if row is None:
            continue
        for normalized in _extract_records_from_row(row, plan):
            yield normalized


def _build_pt_record(row: Dict, plan: DatasetMappingPlan, file_path: str, idx: int) -> Optional[Dict]:
    text = _extract_text_from_spec(row, plan.text_field, plan, default_joiner=" ")
    if not text:
        return None
    meta = {
        k: _extract_text_from_spec(row, v, plan, default_joiner=" ")
        for k, v in (plan.meta_fields or {}).items()
    }
    return {
        "id": _generate_record_id(file_path, idx),
        "dataset_type": "pretrain",
        "text": text,
        "meta": meta,
    }


def _build_pt_record_with_reason(
    row: Dict,
    plan: DatasetMappingPlan,
    file_path: str,
    idx: int,
) -> Tuple[Optional[Dict], Optional[str]]:
    if plan.text_field is None:
        return None, "missing_text_field"
    record = _build_pt_record(row, plan, file_path, idx)
    if record is None:
        return None, "empty_text_after_mapping"
    return record, None


def _build_sft_record(row: Dict, plan: DatasetMappingPlan, file_path: str, idx: int) -> Optional[Dict]:
    if not plan.messages:
        return None
    messages = []
    for spec in plan.messages:
        role = spec.get("role")
        content_field = spec.get("content")
        loss_mask = spec.get("loss_mask")
        if not role or not content_field:
            continue
        default_joiner = ROLE_JOINER_DEFAULTS.get(role, " ")
        content = _extract_text_from_spec(
            row,
            content_field,
            plan,
            role=role,
            default_joiner=default_joiner,
        )
        if not content:
            continue
        if loss_mask is None:
            loss_mask = role == "assistant"
        messages.append({"role": role, "content": content, "loss_mask": loss_mask})

    if plan.system:
        sys_val = _extract_text_from_spec(
            row,
            plan.system,
            plan,
            role="system",
            default_joiner=ROLE_JOINER_DEFAULTS["system"],
        )
        if sys_val:
            messages.insert(0, {"role": "system", "content": sys_val, "loss_mask": False})

    if not messages:
        return None
    has_user = any(m["role"] == "user" for m in messages)
    has_assistant = any(m["role"] == "assistant" for m in messages)
    if not (has_user and has_assistant):
        return None

    meta = {
        k: _extract_text_from_spec(row, v, plan, default_joiner=" ")
        for k, v in (plan.meta_fields or {}).items()
    }
    result: Dict[str, Any] = {
        "id": _generate_record_id(file_path, idx),
        "dataset_type": "sft",
        "messages": messages,
        "meta": meta,
    }
    return result


def _build_sft_record_with_reason(
    row: Dict,
    plan: DatasetMappingPlan,
    file_path: str,
    idx: int,
) -> Tuple[Optional[Dict], Optional[str]]:
    if not plan.messages:
        return None, "missing_messages_spec"

    role_contents: Dict[str, str] = {}
    built_messages = 0
    for spec in plan.messages:
        role = spec.get("role")
        content_field = spec.get("content")
        if not role or not content_field:
            continue
        default_joiner = ROLE_JOINER_DEFAULTS.get(role, " ")
        content = _extract_text_from_spec(
            row,
            content_field,
            plan,
            role=role,
            default_joiner=default_joiner,
        )
        if content:
            role_contents[role] = content
            built_messages += 1

    if built_messages == 0:
        return None, "all_message_contents_empty"
    if "user" not in role_contents:
        return None, "missing_user_content"
    if "assistant" not in role_contents:
        return None, "missing_assistant_content"

    record = _build_sft_record(row, plan, file_path, idx)
    if record is None:
        return None, "invalid_sft_record"
    return record, None


def _apply_mapping_plan(
    source_info: DatasetSourceInfo,
    plan: DatasetMappingPlan,
    output_dir: str,
    category: str,
) -> Tuple[int, List[str]]:
    """Apply a mapping plan to all data files in the dataset and write JSONL."""
    total_records = 0
    output_files: List[str] = []
    chunk_idx = 1
    chunk_count = 0
    current_file = None
    rows_seen = 0
    drop_reason_counts: Dict[str, int] = {}

    def _open_chunk():
        nonlocal chunk_idx, chunk_count, current_file
        path = os.path.join(output_dir, f"{category.upper()}_{chunk_idx:05d}.jsonl")
        current_file = open(path, "a", encoding="utf-8")
        output_files.append(path)
        chunk_count = 0

    def _write_record(record: Dict):
        nonlocal total_records, chunk_count, chunk_idx, current_file
        if current_file is None:
            _open_chunk()
        current_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        total_records += 1
        chunk_count += 1
        if chunk_count >= CHUNK_SIZE:
            current_file.close()
            chunk_idx += 1
            _open_chunk()

    data_files = [
        os.path.join(source_info.dataset_dir, f) for f in source_info.data_files
    ]

    for file_path in data_files:
        if not os.path.isfile(file_path):
            logger.warning(f"[dispatch] Data file not found: {file_path}")
            continue
        logger.info(f"[dispatch] Mapping {file_path} with plan for {source_info.dataset_name}")
        for columns, data, count, split_name in _load_data_file(file_path):
            row_idx = 0
            for row_dict in _iter_normalized_rows(data, plan):
                rows_seen += 1
                if category == "PT":
                    record, drop_reason = _build_pt_record_with_reason(row_dict, plan, file_path, row_idx)
                else:
                    record, drop_reason = _build_sft_record_with_reason(row_dict, plan, file_path, row_idx)

                if record is not None:
                    _write_record(record)
                elif drop_reason:
                    drop_reason_counts[drop_reason] = drop_reason_counts.get(drop_reason, 0) + 1
                row_idx += 1

    if current_file is not None:
        current_file.close()

    logger.info(
        f"[dispatch] {source_info.dataset_name}: rows_seen={rows_seen}, "
        f"records_written={total_records}, files={len(output_files)}"
    )
    if drop_reason_counts:
        sorted_reasons = sorted(drop_reason_counts.items(), key=lambda x: x[1], reverse=True)
        top_reasons = ", ".join([f"{k}={v}" for k, v in sorted_reasons[:5]])
        logger.info(f"[dispatch] {source_info.dataset_name}: top_drop_reasons: {top_reasons}")
    return total_records, output_files


def _write_unqualified_records(
    source_info: DatasetSourceInfo,
    plan: DatasetMappingPlan,
    unqualified_dir: str,
) -> Tuple[int, List[str]]:
    """Write raw rows into unqualified folder for manual processing."""
    os.makedirs(unqualified_dir, exist_ok=True)
    safe_name = _safe_name(source_info.dataset_name)
    output_file = os.path.join(unqualified_dir, f"{safe_name}.jsonl")
    written = 0

    data_files = [
        os.path.join(source_info.dataset_dir, f) for f in source_info.data_files
    ]

    with open(output_file, "w", encoding="utf-8") as fh:
        for file_path in data_files:
            if not os.path.isfile(file_path):
                logger.warning(f"[dispatch] Data file not found for unqualified dump: {file_path}")
                continue
            for _, data, _, split_name in _load_data_file(file_path):
                for row_idx, row_dict in enumerate(_iter_normalized_rows(data, plan)):
                    payload = {
                        "__dataset_name__": source_info.dataset_name,
                        "__source_type__": source_info.source_type,
                        "__file_path__": file_path,
                        "__split__": split_name,
                        "__row_idx__": row_idx,
                        "__quality_label__": plan.quality_label,
                        "__quality_reason__": plan.quality_reason,
                        "raw": row_dict,
                    }
                    fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                    written += 1

    logger.info(
        f"[dispatch] {source_info.dataset_name}: unqualified dump written, "
        f"records={written}, file={output_file}"
    )
    return written, [output_file]


# ---------------------------------------------------------------------------
# Public dispatch function
# ---------------------------------------------------------------------------

async def dispatch_node(
    sources: List[DatasetSourceInfo],
    category: str,
    user_query: str,
    datasets_background: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    tavily_api_key: Optional[str],
    output_dir: str,
    memory_manager: Optional[PostprocessMemoryManager] = None,
    max_concurrent: int = 3,
) -> List[DatasetAgentResult]:
    """Run a DatasetAgent for each source, apply mapping, return results."""
    os.makedirs(output_dir, exist_ok=True)
    unqualified_dir = os.path.join(output_dir, "unqualified")
    os.makedirs(unqualified_dir, exist_ok=True)
    results: List[DatasetAgentResult] = []
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_root = os.path.normpath(os.path.join(output_dir, "..", "..", "log"))
    run_log_dir = os.path.join(log_root, f"postprocess_v2_{run_id}")
    os.makedirs(run_log_dir, exist_ok=True)

    logger.info(
        f"[dispatch] Start run {run_id}, sources={len(sources)}, max_concurrent={max_concurrent}, "
        f"dataset_log_dir={run_log_dir}"
    )

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _process_one(source: DatasetSourceInfo) -> DatasetAgentResult:
        async with semaphore:
            safe_dataset = _safe_name(source.dataset_name)
            dataset_log = os.path.join(run_log_dir, f"{safe_dataset}.log")
            context_id = f"{run_id}:{safe_dataset}"
            root_logger = get_logger()
            dataset_handler = add_context_file_handler(root_logger, dataset_log, context_id)
            logger.info(f"[dispatch] Dataset {source.dataset_name} log file: {dataset_log}")

            try:
                with logging_context(context_id):
                    logger.info(
                        f"[dispatch] Dataset start: source={source.source_type}, "
                        f"name={source.dataset_name}, dir={source.dataset_dir}"
                    )
                    agent = DatasetAgent(
                        source_info=source,
                        category=category,
                        user_query=user_query,
                        datasets_background=datasets_background,
                        model_name=model_name,
                        base_url=base_url,
                        api_key=api_key,
                        temperature=temperature,
                        tavily_api_key=tavily_api_key,
                        memory_manager=memory_manager,
                    )
                    result = await agent.run()
                    result.log_file = dataset_log

                    if result.success and result.mapping_plan and result.mapping_plan.confidence > 0:
                        try:
                            if (result.mapping_plan.quality_label or "").lower() == "unqualified":
                                logger.info(
                                    f"[dispatch] Dataset {source.dataset_name}: mapping marked unqualified, "
                                    f"reason={result.mapping_plan.quality_reason}"
                                )
                                count, files = _write_unqualified_records(
                                    source, result.mapping_plan, unqualified_dir
                                )
                                result.unqualified_records = count
                                result.unqualified_files = files
                                logger.info(
                                    f"[dispatch] Dataset {source.dataset_name}: unqualified export done, "
                                    f"records={count}, files={len(files)}"
                                )
                            else:
                                logger.info(f"[dispatch] Dataset {source.dataset_name}: applying mapping plan")
                                count, files = _apply_mapping_plan(
                                    source, result.mapping_plan, output_dir, category,
                                )
                                result.records_processed = count
                                result.output_files = files
                                logger.info(
                                    f"[dispatch] Dataset {source.dataset_name}: mapping done, "
                                    f"records={count}, files={len(files)}"
                                )
                        except Exception as e:
                            logger.error(f"[dispatch] Mapping failed for {source.dataset_name}: {e}", exc_info=True)
                            result.error = f"Mapping failed: {str(e)}"
                            result.success = False

                    logger.info(
                        f"[dispatch] Dataset end: name={source.dataset_name}, success={result.success}, "
                        f"records={result.records_processed}"
                    )
            except Exception as e:
                with logging_context(context_id):
                    logger.error(
                        f"[dispatch] Dataset task failed unexpectedly for {source.dataset_name}: {e}",
                        exc_info=True,
                    )
                result = DatasetAgentResult(
                    dataset_name=source.dataset_name,
                    source_type=source.source_type,
                    dataset_dir=source.dataset_dir,
                    success=False,
                    error=str(e),
                    log_file=dataset_log,
                )
            finally:
                remove_handler(root_logger, dataset_handler)

            logger.info(
                f"[dispatch] Dataset summary: name={result.dataset_name}, success={result.success}, "
                f"records={result.records_processed}, log={result.log_file}"
            )
            return result

    tasks = [_process_one(src) for src in sources]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for item in completed:
        if isinstance(item, Exception):
            logger.error(f"[dispatch] Agent task exception: {item}", exc_info=True)
            results.append(DatasetAgentResult(
                dataset_name="unknown",
                source_type="unknown",
                dataset_dir="",
                success=False,
                error=str(item),
            ))
        else:
            results.append(item)

    logger.info(
        f"[dispatch] Run {run_id} completed, datasets={len(results)}, "
        f"success={sum(1 for r in results if r.success)}, failed={sum(1 for r in results if not r.success)}"
    )
    return results
