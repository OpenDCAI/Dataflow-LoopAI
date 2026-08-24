from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from loopai.skills.Trainer.rewards import (
    is_verl_builtin_data_source,
    normalize_reward_preset,
)


_REQUIRED_COLUMNS = {"prompt", "data_source", "reward_model"}
_SUPPORTED_SOURCE_SUFFIXES = {".json", ".jsonl", ".parquet"}
_ADAPTERS = {"auto", "native", "messages", "alpaca", "qa"}
_ANSWER_FIELDS = (
    "ground_truth",
    "answer",
    "target",
    "label",
    "solution",
    "reference_answer",
    "reference",
    "output",
    "response",
    "completion",
)
_RESPONSE_DERIVED_FIELDS = {"output", "response", "completion", "assistant"}
_ROLE_ALIASES = {
    "human": "user",
    "user": "user",
    "gpt": "assistant",
    "assistant": "assistant",
    "bot": "assistant",
    "system": "system",
    "tool": "tool",
}
_BOXED_START = re.compile(r"\\boxed\s*\{")
_GSM8K_ANSWER = re.compile(r"####\s*([^\n]+)")
_ANSWER_TAG = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.IGNORECASE | re.DOTALL)
_FINAL_ANSWER = re.compile(
    r"(?:final\s+answer|answer|最终答案|答案)\s*[:：]\s*([^\n]+)",
    re.IGNORECASE,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _write_rejections(path: Path, rejected: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as stream:
        for item in rejected:
            stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    temp_path.replace(path)


def _require_pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised in minimal deployments.
        raise RuntimeError(
            "Trainer needs pyarrow to convert generated JSON/JSONL data into Verl Parquet"
        ) from exc
    return pa, pq


def _is_native_verl_parquet(path_value: Any) -> bool:
    if not path_value:
        return False
    path = Path(str(path_value)).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".parquet":
        return False
    try:
        _, pq = _require_pyarrow()
        return _REQUIRED_COLUMNS.issubset(pq.ParquetFile(path).schema_arrow.names)
    except Exception:
        return False


def _source_files(path_value: str) -> List[Path]:
    path = Path(path_value).expanduser().resolve()
    if path.is_file():
        if path.suffix.lower() not in _SUPPORTED_SOURCE_SUFFIXES:
            raise ValueError(
                f"unsupported Verl source file: {path}; expected JSON, JSONL, or Parquet"
            )
        return [path]
    if path.is_dir():
        files = sorted(
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in _SUPPORTED_SOURCE_SUFFIXES
        )
        if files:
            return files
        raise ValueError(f"no JSON, JSONL, or Parquet files found under: {path}")
    raise FileNotFoundError(f"Verl source dataset does not exist: {path}")


def _record_item(record: Dict[str, Any], path: Path, index: int) -> Dict[str, Any]:
    return {
        "record": record,
        "source_path": str(path),
        "source_index": index,
    }


def _load_source_records(path_value: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    files = _source_files(path_value)
    _, pq = _require_pyarrow()

    for path in files:
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            try:
                rows = pq.read_table(path).to_pylist()
            except Exception as exc:
                raise ValueError(f"unable to read source Parquet {path}: {exc}") from exc
            for index, row in enumerate(rows):
                if isinstance(row, dict):
                    records.append(_record_item(row, path, index))
                else:
                    rejected.append({
                        "source_path": str(path),
                        "source_index": index,
                        "error": "record is not a mapping",
                    })
            continue

        if suffix == ".jsonl":
            with path.open("r", encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        rejected.append({
                            "source_path": str(path),
                            "source_index": index,
                            "error": f"invalid JSON: {exc}",
                        })
                        continue
                    if isinstance(row, dict):
                        records.append(_record_item(row, path, index))
                    else:
                        rejected.append({
                            "source_path": str(path),
                            "source_index": index,
                            "error": "record is not a mapping",
                        })
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON source {path}: {exc}") from exc
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = next(
                (
                    payload[key]
                    for key in ("data", "records", "items", "examples")
                    if isinstance(payload.get(key), list)
                ),
                [payload],
            )
        else:
            rows = []
            rejected.append({
                "source_path": str(path),
                "source_index": 0,
                "error": "JSON root must be a mapping or list",
            })
        for index, row in enumerate(rows):
            if isinstance(row, dict):
                records.append(_record_item(row, path, index))
            else:
                rejected.append({
                    "source_path": str(path),
                    "source_index": index,
                    "error": "record is not a mapping",
                })

    return records, rejected, [str(path) for path in files]


def _normalize_message(message: Any) -> Dict[str, str] | None:
    if not isinstance(message, dict):
        return None
    raw_role = message.get("role", message.get("from"))
    raw_content = message.get("content", message.get("value"))
    role = _ROLE_ALIASES.get(str(raw_role or "").strip().lower())
    if role is None or raw_content is None:
        return None
    if isinstance(raw_content, list):
        content = "\n".join(str(item) for item in raw_content if item is not None)
    else:
        content = str(raw_content)
    content = content.strip()
    if not content:
        return None
    return {"role": role, "content": content}


def _normalize_messages(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [item for item in (_normalize_message(message) for message in value) if item]


def _last_boxed_value(text: str) -> str | None:
    matches = list(_BOXED_START.finditer(text))
    for match in reversed(matches):
        start = match.end()
        depth = 1
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    value = text[start:index].strip()
                    return value or None
    return None


def _concise_answer(text: str) -> str | None:
    value = text.strip()
    if not value or "\n" in value or len(value) > 128:
        return None
    if len(value.split()) > 20:
        return None
    return value


def _unwrap_answer(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("target", "answer", "ground_truth", "value"):
            if key in value:
                return value[key]
    return value


def _normalize_ground_truth(value: Any, preset: str, *, response_derived: bool) -> Any:
    value = _unwrap_answer(value)
    if value is None:
        raise ValueError("missing reference answer/ground truth")

    if preset == "qa_exact_match":
        targets = value if isinstance(value, (list, tuple, set)) else [value]
        normalized = [str(item).strip() for item in targets if str(item).strip()]
        if not normalized:
            raise ValueError("QA ground truth is empty")
        return {"target": normalized}

    if isinstance(value, (list, tuple, set)):
        values = [item for item in value if item is not None and str(item).strip()]
        if len(values) != 1:
            raise ValueError("generated math data requires exactly one reference answer")
        value = values[0]
    if isinstance(value, dict):
        if response_derived:
            raise ValueError("assistant response cannot be converted to a scalar ground truth")
        return copy.deepcopy(value)

    text = str(value).strip()
    if not text:
        raise ValueError("reference answer/ground truth is empty")

    boxed = _last_boxed_value(text)
    if boxed is not None:
        return boxed
    gsm_match = _GSM8K_ANSWER.search(text)
    if gsm_match:
        return gsm_match.group(1).strip()
    answer_match = _ANSWER_TAG.search(text)
    if answer_match:
        return answer_match.group(1).strip()
    final_matches = list(_FINAL_ANSWER.finditer(text))
    if final_matches:
        return final_matches[-1].group(1).strip()
    if "</think>" in text.lower():
        tail = re.split(r"</think>", text, flags=re.IGNORECASE)[-1].strip()
        concise_tail = _concise_answer(tail)
        if concise_tail:
            return concise_tail

    if response_derived and preset in {
        "gsm8k_exact",
        "math_boxed",
        "math_dapo",
        "prime_math",
        "geometry",
    }:
        concise = _concise_answer(text)
        if concise is None:
            raise ValueError(
                "assistant response contains reasoning but no reliable final-answer marker "
                "(\\boxed{...}, ####, or <answer>...</answer>)"
            )
        return concise
    return text


def _answer_from_record(record: Dict[str, Any]) -> Tuple[Any, str]:
    reward_model = record.get("reward_model")
    if isinstance(reward_model, dict) and reward_model.get("ground_truth") not in (None, ""):
        return reward_model["ground_truth"], "reward_model.ground_truth"
    for field in _ANSWER_FIELDS:
        if record.get(field) not in (None, ""):
            return record[field], field
    raise ValueError("record has no answer/target/ground_truth/output field")


def _detect_adapter(record: Dict[str, Any], requested: str) -> str:
    if requested != "auto":
        return requested
    if (
        isinstance(record.get("prompt"), list)
        and record.get("data_source")
        and isinstance(record.get("reward_model"), dict)
    ):
        return "native"
    if any(
        isinstance(record.get(field), list)
        for field in ("messages", "conversation", "conversations")
    ):
        return "messages"
    if "instruction" in record and any(field in record for field in _ANSWER_FIELDS):
        return "alpaca"
    if any(key in record for key in ("question", "query", "problem", "prompt")):
        return "qa"
    raise ValueError("unable to detect data adapter; set verl_data_adapter explicitly")


def _prompt_suffix(preset: str) -> str:
    if preset == "gsm8k_exact":
        return 'End the response with "#### <final answer>".'
    if preset == "qa_exact_match":
        return "Put only the final answer inside <answer>...</answer>."
    if preset in {"math_boxed", "math_dapo", "prime_math", "geometry"}:
        return "Put the final answer inside \\boxed{...}."
    return ""


def _ensure_reward_format(prompt: List[Dict[str, str]], preset: str) -> List[Dict[str, str]]:
    suffix = _prompt_suffix(preset)
    if not suffix:
        return prompt
    result = copy.deepcopy(prompt)
    for index in range(len(result) - 1, -1, -1):
        if result[index].get("role") != "user":
            continue
        content = str(result[index].get("content") or "")
        markers = ("\\boxed", "####", "<answer>")
        if not any(marker.lower() in content.lower() for marker in markers):
            result[index]["content"] = content.rstrip() + "\n\n" + suffix
        break
    return result


def _metadata_json(record: Dict[str, Any]) -> str:
    metadata: Dict[str, Any] = {}
    for field in (
        "meta",
        "metadata",
        "extra_info",
        "id",
        "uuid",
        "category",
        "dataset",
        "data_source",
        "ability",
    ):
        if field in record:
            metadata[field] = record[field]
    return _json_text(metadata)


def _adapt_record(
    item: Dict[str, Any],
    *,
    requested_adapter: str,
    reward_preset: str,
    reward_mode: str,
    configured_data_source: str,
) -> Tuple[Dict[str, Any], str]:
    record = item["record"]
    adapter = _detect_adapter(record, requested_adapter)
    answer_value: Any
    answer_field: str

    if adapter == "native":
        prompt = _normalize_messages(record.get("prompt"))
        if not prompt:
            raise ValueError("native prompt must be a non-empty chat message list")
        answer_value, answer_field = _answer_from_record(record)
    elif adapter == "messages":
        messages = _normalize_messages(
            record.get("messages")
            or record.get("conversation")
            or record.get("conversations")
        )
        assistant_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if messages[index]["role"] == "assistant"),
            -1,
        )
        if assistant_index <= 0:
            raise ValueError("messages data requires a final assistant reference after a prompt")
        prompt = messages[:assistant_index]
        if not any(message["role"] == "user" for message in prompt):
            raise ValueError("messages prompt has no user message")
        try:
            answer_value, answer_field = _answer_from_record(record)
        except ValueError:
            answer_value = messages[assistant_index]["content"]
            answer_field = "assistant"
    elif adapter == "alpaca":
        instruction = str(record.get("instruction") or "").strip()
        context = str(record.get("input") or "").strip()
        if not instruction:
            raise ValueError("Alpaca record requires a non-empty instruction")
        content = instruction
        if context:
            content += "\n\nInput:\n" + context
        prompt = [{"role": "user", "content": content}]
        answer_value, answer_field = _answer_from_record(record)
    else:
        raw_prompt = next(
            (record.get(key) for key in ("question", "query", "problem", "prompt") if record.get(key)),
            None,
        )
        if isinstance(raw_prompt, list):
            prompt = _normalize_messages(raw_prompt)
        else:
            prompt = [{"role": "user", "content": str(raw_prompt or "").strip()}]
        if not prompt or not prompt[-1].get("content"):
            raise ValueError("QA record requires a non-empty question/query/problem/prompt")
        answer_value, answer_field = _answer_from_record(record)

    ground_truth = _normalize_ground_truth(
        answer_value,
        reward_preset,
        response_derived=answer_field in _RESPONSE_DERIVED_FIELDS,
    )
    prompt = _ensure_reward_format(prompt, reward_preset)
    if not prompt:
        raise ValueError("adapted prompt is empty")

    original_source = str(record.get("data_source") or "").strip()
    if configured_data_source:
        data_source = configured_data_source
    elif reward_mode == "auto" and original_source:
        data_source = original_source
    elif reward_mode == "custom":
        data_source = original_source or "loopai/custom"
    elif reward_preset not in {"auto", "verl_builtin", "custom"}:
        data_source = f"loopai/{reward_preset}"
    else:
        data_source = original_source
    if not data_source:
        raise ValueError("adapted record has no data_source; choose a named reward preset")

    source_record_id = str(
        record.get("id")
        or record.get("uuid")
        or f"{Path(item['source_path']).name}:{item['source_index']}"
    )
    reward_model = {"style": "rule", "ground_truth": ground_truth}
    if adapter == "native" and isinstance(record.get("reward_model"), dict):
        reward_model.update({
            key: copy.deepcopy(value)
            for key, value in record["reward_model"].items()
            if key != "ground_truth"
        })
        reward_model["ground_truth"] = ground_truth
    result = {
        "prompt": prompt,
        "data_source": data_source,
        "reward_model": reward_model,
        "extra_info": {
            "split": "",
            "index": 0,
            "source_path": item["source_path"],
            "source_record_id": source_record_id,
            "metadata_json": _metadata_json(record),
        },
    }
    return result, adapter


def _candidate_text(
    state: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    source_paths: Iterable[str],
) -> str:
    trainer = state.get("trainer") or {}
    parts: List[str] = [
        str(trainer.get("train_input_task_description") or ""),
        *(str(path) for path in source_paths),
    ]
    for item in records[:50]:
        record = item.get("record") or {}
        for key in ("data_source", "dataset", "source", "category", "task", "domain"):
            if record.get(key):
                parts.append(str(record[key]))
        metadata = record.get("meta") or record.get("metadata")
        if isinstance(metadata, dict):
            parts.extend(str(value) for value in metadata.values())
    return " ".join(parts).lower()


def _infer_reward_preset(
    state: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    source_paths: Sequence[str],
) -> Tuple[str, str]:
    text = _candidate_text(state, records, source_paths)
    rules = (
        ("gsm8k_exact", ("gsm8k",)),
        ("geometry", ("geometry3k", "geo3k", "geometry", "几何")),
        ("math_dapo", ("math_dapo", "dapo", "aime")),
        ("prime_math", ("numina", "prime_math", "prime math")),
        (
            "qa_exact_match",
            (
                "searchr1",
                "search_r1",
                "triviaqa",
                "hotpotqa",
                "natural questions",
                "exact match qa",
                "问答",
            ),
        ),
        (
            "math_boxed",
            ("math-500", "lighteval/math", "mathematics", "math", "algebra", "arithmetic", "数学", "代数", "算术"),
        ),
    )
    for preset, keywords in rules:
        matched = next((keyword for keyword in keywords if keyword in text), None)
        if matched:
            return preset, f"matched dataset/task keyword: {matched}"

    sampled_outputs: List[str] = []
    for item in records[:100]:
        record = item.get("record") or {}
        for field in ("output", "response", "completion", "answer", "solution"):
            if record.get(field) is not None:
                sampled_outputs.append(str(record[field]))
                break
    if any(_GSM8K_ANSWER.search(value) for value in sampled_outputs):
        return "gsm8k_exact", "detected #### final-answer markers"
    if any(_last_boxed_value(value) is not None for value in sampled_outputs):
        return "math_boxed", "detected \\boxed{...} final-answer markers"
    if any(_ANSWER_TAG.search(value) for value in sampled_outputs):
        return "qa_exact_match", "detected <answer>...</answer> markers"

    raise ValueError(
        "Trainer cannot safely infer a reward for generated Verl data. "
        "Set verl_reward_mode=preset with verl_reward_preset, or use custom reward mode."
    )


def _all_builtin_sources(records: Sequence[Dict[str, Any]]) -> bool:
    sources = {
        str((item.get("record") or {}).get("data_source") or "").strip()
        for item in records
    }
    return bool(sources) and "" not in sources and all(
        is_verl_builtin_data_source(source) for source in sources
    )


def _resolve_reward(
    state: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    source_paths: Sequence[str],
    *,
    source_is_native: bool,
) -> Tuple[str, str, Dict[str, Any]]:
    trainer = state.setdefault("trainer", {})
    mode = str(trainer.get("verl_reward_mode") or "auto").strip().lower()
    origin = str(trainer.get("verl_reward_origin") or "").strip().lower()
    if origin not in {"auto", "user"}:
        origin = "auto" if mode == "auto" else "user"
    # ``preset`` with origin=auto is a recommendation produced for an older
    # generated dataset.  Re-enter auto mode so the current records determine
    # the reward contract.
    if origin == "auto" and mode == "preset":
        mode = "auto"
        trainer["verl_reward_mode"] = "auto"
        trainer["verl_reward_preset"] = "auto"
    trainer["verl_reward_origin"] = origin
    if mode == "custom":
        trainer["verl_reward_origin"] = "user"
        return "custom", "custom", {
            "applied": False,
            "mode": "custom",
            "reason": "user supplied a custom reward",
        }
    if mode == "preset":
        preset = normalize_reward_preset(trainer.get("verl_reward_preset"))
        if preset in {"auto", "verl_builtin"} and not source_is_native and not _all_builtin_sources(records):
            raise ValueError(
                "Generated data has no Verl-supported data_source for the built-in reward router; "
                "choose a named verl_reward_preset or a custom reward."
            )
        trainer["verl_reward_origin"] = "user"
        return mode, preset, {
            "applied": False,
            "mode": mode,
            "preset": preset,
            "reason": "user-selected reward preset",
        }
    if mode != "auto":
        raise ValueError("verl_reward_mode must be auto, preset, or custom")

    if source_is_native or _all_builtin_sources(records):
        trainer["verl_reward_mode"] = "auto"
        trainer["verl_reward_preset"] = "auto"
        trainer["verl_reward_origin"] = "auto"
        return "auto", "auto", {
            "applied": False,
            "mode": "auto",
            "preset": "auto",
            "reason": "native Verl data_source routing",
        }

    preset, reason = _infer_reward_preset(state, records, source_paths)
    trainer["verl_reward_mode"] = "preset"
    trainer["verl_reward_preset"] = preset
    trainer["verl_reward_origin"] = "auto"
    recommendation = {
        "applied": True,
        "mode": "preset",
        "preset": preset,
        "reason": reason,
        "source": "trainer_generated_data_inference",
    }
    trainer["verl_reward_recommendation"] = recommendation
    return "preset", preset, recommendation


def _reward_contract_signature(reward: Dict[str, Any]) -> Tuple[str, str, str, str]:
    mode = str(reward.get("mode") or "auto").strip().lower()
    return (
        mode,
        str(reward.get("preset") or ""),
        str(reward.get("function_path") or "") if mode == "custom" else "",
        str(reward.get("function_name") or "compute_score") if mode == "custom" else "",
    )


def _record_fingerprint(record: Dict[str, Any]) -> str:
    return hashlib.sha256(
        _json_text({
            "prompt": record.get("prompt"),
            "data_source": record.get("data_source"),
            "ground_truth": (record.get("reward_model") or {}).get("ground_truth"),
        }).encode("utf-8")
    ).hexdigest()


def _deduplicate(records: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    seen = set()
    result: List[Dict[str, Any]] = []
    duplicates = 0
    for record in records:
        key = _record_fingerprint(record)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.append(record)
    return result, duplicates


def _assign_split(records: Sequence[Dict[str, Any]], split: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for index, record in enumerate(records):
        item = copy.deepcopy(record)
        item.setdefault("extra_info", {})["split"] = split
        item["extra_info"]["index"] = index
        result.append(item)
    return result


def _split_records(
    records: Sequence[Dict[str, Any]],
    *,
    ratio: float,
    seed: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(records) < 2:
        raise ValueError(
            "At least two valid generated records are required when no validation dataset is supplied"
        )
    if not math.isfinite(ratio) or not 0.0 < ratio < 1.0:
        raise ValueError("verl_validation_ratio must be between 0 and 1")
    indices = list(range(len(records)))
    random.Random(seed).shuffle(indices)
    validation_count = max(1, min(len(records) - 1, int(round(len(records) * ratio))))
    validation_indices = set(indices[:validation_count])
    train = [record for index, record in enumerate(records) if index not in validation_indices]
    validation = [record for index, record in enumerate(records) if index in validation_indices]
    return train, validation


def _write_parquet(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    pa, pq = _require_pyarrow()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        table = pa.Table.from_pylist(list(records))
        pq.write_table(table, temp_path)
        temp_path.replace(path)
    except Exception as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise ValueError(f"unable to write Verl Parquet {path}: {exc}") from exc


def _adapt_items(
    items: Sequence[Dict[str, Any]],
    *,
    requested_adapter: str,
    reward_preset: str,
    reward_mode: str,
    configured_data_source: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    adapted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    adapter_counts: Dict[str, int] = {}
    for item in items:
        try:
            record, adapter = _adapt_record(
                item,
                requested_adapter=requested_adapter,
                reward_preset=reward_preset,
                reward_mode=reward_mode,
                configured_data_source=configured_data_source,
            )
            adapted.append(record)
            adapter_counts[adapter] = adapter_counts.get(adapter, 0) + 1
        except Exception as exc:
            rejected.append({
                "source_path": item.get("source_path"),
                "source_index": item.get("source_index"),
                "error": str(exc),
            })
    return adapted, rejected, adapter_counts


def prepare_verl_grpo_datasets(state: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare task-generated data for Verl and update Trainer's current paths.

    Native train/validation Parquet remains untouched. JSON, JSONL, non-native
    Parquet, or a native Parquet without validation is normalized into a
    version-scoped train/validation pair plus an auditable manifest.
    """
    trainer = state.setdefault("trainer", {})
    source_value = trainer.get("verl_source_dataset_path") or trainer.get(
        "train_input_dataset_path"
    )
    if not source_value:
        raise ValueError("Verl requires verl_source_dataset_path or train_input_dataset_path")
    source_path = str(Path(str(source_value)).expanduser().resolve())

    eval_value = trainer.get("verl_source_eval_dataset_path") or trainer.get(
        "train_input_eval_dataset_path"
    )
    eval_path = str(Path(str(eval_value)).expanduser().resolve()) if eval_value else ""
    source_native = _is_native_verl_parquet(source_path)
    eval_native = _is_native_verl_parquet(eval_path) if eval_path else False

    output_dir = Path(
        str(trainer.get("trainer_output_dir") or trainer.get("output_dir") or "./outputs")
    ).expanduser().resolve()
    raw_validation_ratio = trainer.get("verl_validation_ratio")
    validation_ratio = float(
        0.05 if raw_validation_ratio in (None, "") else raw_validation_ratio
    )
    raw_split_seed = trainer.get("verl_split_seed")
    split_seed = int(42 if raw_split_seed in (None, "") else raw_split_seed)
    prepared_dir = output_dir / "prepared_data"
    manifest_path = prepared_dir / "dataset_manifest.json"
    rejection_path = prepared_dir / "rejected_rows.jsonl"
    prepared_dir.mkdir(parents=True, exist_ok=True)

    if source_native and eval_native:
        reward_mode = str(trainer.get("verl_reward_mode") or "auto").strip().lower()
        reward_origin = str(trainer.get("verl_reward_origin") or "").strip().lower()
        if reward_origin not in {"auto", "user"}:
            reward_origin = "auto" if reward_mode == "auto" else "user"
        if reward_origin == "auto":
            reward_mode = "auto"
            reward_preset = "auto"
            trainer["verl_reward_mode"] = reward_mode
            trainer["verl_reward_preset"] = reward_preset
            recommendation = {
                "applied": False,
                "mode": "auto",
                "preset": "auto",
                "reason": "native Verl data_source routing",
            }
        else:
            reward_preset = (
                "custom"
                if reward_mode == "custom"
                else normalize_reward_preset(
                    "auto" if reward_mode == "auto" else trainer.get("verl_reward_preset")
                )
            )
            recommendation = {
                "applied": False,
                "mode": reward_mode,
                "preset": reward_preset,
                "reason": "user-selected reward contract",
            }
        trainer["verl_reward_origin"] = reward_origin
        manifest = {
            "version": 1,
            "status": "reused_native_parquet",
            "source_path": source_path,
            "train_path": source_path,
            "validation_path": eval_path,
            "source_paths": [source_path, eval_path],
            "train_sha256": _sha256_file(Path(source_path)),
            "validation_sha256": _sha256_file(Path(eval_path)),
            "reward": {
                "mode": reward_mode,
                "origin": reward_origin,
                "preset": reward_preset,
                "recommendation": recommendation,
            },
        }
        _write_json(manifest_path, manifest)
        _write_rejections(rejection_path, [])
        trainer["train_input_dataset_path"] = source_path
        trainer["train_input_eval_dataset_path"] = eval_path
        trainer["verl_data_manifest_path"] = str(manifest_path)
        trainer["verl_data_prepare_result"] = manifest
        trainer["verl_reward_recommendation"] = recommendation
        return manifest

    train_items, load_rejected, source_files = _load_source_records(source_path)
    if not train_items:
        raise ValueError(f"no readable records found in Verl source dataset: {source_path}")

    reward_mode, reward_preset, recommendation = _resolve_reward(
        state,
        train_items,
        source_files,
        source_is_native=source_native,
    )
    previous_reward = trainer.get("_trainer_previous_reward")
    eval_is_explicit = bool(
        trainer.get("_trainer_eval_explicit")
        or trainer.get("verl_source_eval_dataset_path")
    )
    current_reward = {
        "mode": reward_mode,
        "preset": reward_preset,
        "function_path": trainer.get("verl_reward_function_path"),
        "function_name": trainer.get("verl_reward_function_name"),
    }
    if (
        eval_path
        and not eval_is_explicit
        and isinstance(previous_reward, dict)
        and _reward_contract_signature(previous_reward)
        != _reward_contract_signature(current_reward)
    ):
        # A validation set prepared for another preset/custom function cannot
        # validate the current policy reliably. Split the new source instead.
        eval_path = ""
        eval_native = False
        trainer["train_input_eval_dataset_path"] = ""
    requested_adapter = str(trainer.get("verl_data_adapter") or "auto").strip().lower()
    if requested_adapter not in _ADAPTERS:
        raise ValueError(f"verl_data_adapter must be one of: {', '.join(sorted(_ADAPTERS))}")
    configured_data_source = str(trainer.get("verl_data_source") or "").strip()

    train_records, train_rejected, adapter_counts = _adapt_items(
        train_items,
        requested_adapter=requested_adapter,
        reward_preset=reward_preset,
        reward_mode=reward_mode,
        configured_data_source=configured_data_source,
    )
    train_records, train_duplicates = _deduplicate(train_records)
    if not train_records:
        first_error = (train_rejected or load_rejected or [{}])[0].get("error")
        raise ValueError(f"all generated Verl records were rejected: {first_error or 'unknown error'}")

    rejected = [*load_rejected, *train_rejected]
    reused_validation = False
    validation_duplicates = 0
    cross_split_duplicates = 0
    if eval_path and eval_native:
        validation_records: List[Dict[str, Any]] = []
        final_eval_path = eval_path
        final_train_records = _assign_split(train_records, "train")
        reused_validation = True
    elif eval_path:
        eval_items, eval_load_rejected, eval_source_files = _load_source_records(eval_path)
        validation_records, validation_rejected, eval_adapter_counts = _adapt_items(
            eval_items,
            requested_adapter=requested_adapter,
            reward_preset=reward_preset,
            reward_mode=reward_mode,
            configured_data_source=configured_data_source,
        )
        validation_records, validation_duplicates = _deduplicate(validation_records)
        rejected.extend(eval_load_rejected)
        rejected.extend(validation_rejected)
        for adapter, count in eval_adapter_counts.items():
            adapter_counts[adapter] = adapter_counts.get(adapter, 0) + count
        source_files.extend(eval_source_files)
        if not validation_records:
            raise ValueError("all generated Verl validation records were rejected")
        validation_keys = {_record_fingerprint(record) for record in validation_records}
        filtered_train_records = [
            record
            for record in train_records
            if _record_fingerprint(record) not in validation_keys
        ]
        cross_split_duplicates = len(train_records) - len(filtered_train_records)
        train_records = filtered_train_records
        if not train_records:
            raise ValueError("all generated training records overlap the validation dataset")
        final_train_records = _assign_split(train_records, "train")
        validation_records = _assign_split(validation_records, "validation")
        final_eval_path = str(prepared_dir / "validation.parquet")
        _write_parquet(Path(final_eval_path), validation_records)
    else:
        split_train, split_validation = _split_records(
            train_records,
            ratio=validation_ratio,
            seed=split_seed,
        )
        final_train_records = _assign_split(split_train, "train")
        validation_records = _assign_split(split_validation, "validation")
        final_eval_path = str(prepared_dir / "validation.parquet")
        _write_parquet(Path(final_eval_path), validation_records)

    final_train_path = str(prepared_dir / "train.parquet")
    _write_parquet(Path(final_train_path), final_train_records)
    _write_rejections(rejection_path, rejected)

    manifest = {
        "version": 1,
        "status": "prepared",
        "source_path": source_path,
        "source_paths": sorted(set(source_files)),
        "train_path": final_train_path,
        "validation_path": final_eval_path,
        "reused_validation": reused_validation,
        "input_records": len(train_items),
        "train_records": len(final_train_records),
        "validation_records": (
            None if reused_validation else len(validation_records)
        ),
        "rejected_records": len(rejected),
        "duplicate_records": train_duplicates + validation_duplicates + cross_split_duplicates,
        "cross_split_duplicates": cross_split_duplicates,
        "adapter_counts": adapter_counts,
        "requested_adapter": requested_adapter,
        "reward": {
            "mode": reward_mode,
            "origin": trainer.get("verl_reward_origin") or "user",
            "preset": reward_preset,
            "recommendation": recommendation,
        },
        "split": {
            "ratio": validation_ratio,
            "seed": split_seed,
        },
        "rejection_path": str(rejection_path),
        "train_sha256": _sha256_file(Path(final_train_path)),
        "validation_sha256": _sha256_file(Path(final_eval_path)),
    }
    _write_json(manifest_path, manifest)

    trainer["train_input_dataset_path"] = final_train_path
    trainer["train_input_eval_dataset_path"] = final_eval_path
    trainer["verl_data_manifest_path"] = str(manifest_path)
    trainer["verl_data_prepare_result"] = manifest
    trainer["verl_reward_recommendation"] = recommendation
    return manifest


__all__ = ["prepare_verl_grpo_datasets"]
