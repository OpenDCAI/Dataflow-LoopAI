from __future__ import annotations

import json
import math
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _to_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        number = value
    else:
        try:
            number = float(str(value))
        except (TypeError, ValueError):
            return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _normalize_selection_mode(value: Any) -> str:
    mode = str(value or "max").strip().lower()
    if mode not in {"max", "min"}:
        raise ValueError(f"selection_mode must be max or min, got: {value}")
    return mode


def _checkpoint_step(name: str) -> int | None:
    for prefix in ("checkpoint-", "global_step_"):
        if name.startswith(prefix):
            suffix = name[len(prefix):]
            return int(suffix) if suffix.isdigit() else None
    return None


def _normalize_metric_record(record: Dict[str, Any], source: str) -> Dict[str, Any] | None:
    nested_data = record.get("data")
    flattened = dict(nested_data) if isinstance(nested_data, dict) else dict(record)
    if isinstance(nested_data, dict) and "step" not in flattened:
        flattened["step"] = record.get("step")

    step = _to_number(flattened.get(
        "step",
        flattened.get("current_steps", flattened.get("global_step", flattened.get("training/global_step"))),
    ))
    normalized: Dict[str, Any] = {"source": source}
    if step is not None:
        normalized["step"] = int(step)
    for key, value in flattened.items():
        if key in {"step", "current_steps", "global_step", "timestamp", "log_line", "data"}:
            continue
        number = _to_number(value)
        if number is not None:
            normalized[key] = number
    if "training/epoch" in normalized and "epoch" not in normalized:
        normalized["epoch"] = normalized["training/epoch"]
    if "critic/rewards/mean" in normalized and "reward" not in normalized:
        normalized["reward"] = normalized["critic/rewards/mean"]
    if "actor/pg_loss" in normalized and "policy_loss" not in normalized:
        normalized["policy_loss"] = normalized["actor/pg_loss"]
    val_keys = sorted(key for key in normalized if key.startswith("val-core/"))
    if val_keys and "val_score" not in normalized:
        normalized["val_score"] = normalized[val_keys[0]]
    if "timestamp" in record:
        normalized["timestamp"] = record["timestamp"]
    if len(normalized) == 1 or (len(normalized) == 2 and "step" in normalized):
        return None
    return normalized


def _read_jsonl_metrics(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []

    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                normalized = _normalize_metric_record(raw, "trainer_log")
                if normalized is not None:
                    records.append(normalized)
    return records


def _iter_metric_items(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if isinstance(payload, dict):
        metrics = payload.get("metrics")
        if isinstance(metrics, list):
            for item in metrics:
                if isinstance(item, dict):
                    yield item
        elif "step" in payload or "eval_loss" in payload or "training/global_step" in payload:
            yield payload


def _read_metrics_json(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    records: List[Dict[str, Any]] = []
    for item in _iter_metric_items(payload):
        normalized = _normalize_metric_record(item, "metrics_json")
        if normalized is not None:
            records.append(normalized)
    return records


def _read_verl_jsonl_metrics(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                normalized = _normalize_metric_record(item, "verl_file_logger")
                if normalized is not None:
                    records.append(normalized)
    return records


def _dedupe_metrics(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for record in records:
        key = json.dumps(
            {k: v for k, v in record.items() if k not in {"source", "timestamp"}},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return sorted(deduped, key=lambda item: (item.get("step") is None, item.get("step") or 0))


def load_live_training_metrics(training_output_dir: str | Path) -> Dict[str, Any]:
    """Return the UI metrics contract for either Verl or LLaMAFactory."""
    root = Path(training_output_dir).expanduser().resolve()
    metrics_dir = root / "metrics"
    verl_metrics_path = metrics_dir / "verl_metrics.jsonl"
    metrics_path = metrics_dir / "metrics.json"

    if verl_metrics_path.is_file():
        records = _dedupe_metrics(_read_verl_jsonl_metrics(verl_metrics_path))

        # Keep the existing terminal-log panel without feeding the generic
        # console parser's noisy PID/Progress fields into chart selection.
        log_records: List[Dict[str, Any]] = []
        if metrics_path.is_file():
            try:
                with metrics_path.open("r", encoding="utf-8") as file:
                    raw_metrics = json.load(file)
            except (OSError, json.JSONDecodeError):
                raw_metrics = {}
            for item in _iter_metric_items(raw_metrics):
                if item.get("log_line"):
                    log_records.append({"log_line": item["log_line"]})

        return {
            "task_info": {
                "framework": "verl",
                "total_metrics": len(records),
                "metrics_source": str(verl_metrics_path),
            },
            "metrics": records + log_records[-200:],
        }

    if metrics_path.is_file():
        try:
            with metrics_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Trainer metrics file: {metrics_path}") from exc
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {
                "task_info": {
                    "framework": "llamafactory",
                    "total_metrics": len(payload),
                    "metrics_source": str(metrics_path),
                },
                "metrics": payload,
            }
        raise ValueError(f"invalid Trainer metrics payload: {metrics_path}")

    raise FileNotFoundError(
        f"Trainer metrics do not exist: {verl_metrics_path} or {metrics_path}"
    )


def _find_training_output_dir(
    *,
    task_id: str | None = None,
    output_dir: str = "./outputs",
    trainer_task_id: str | None = None,
    training_output_dir: str | None = None,
    trainer_state: Optional[Dict[str, Any]] = None,
) -> Path | None:
    candidates: List[Path] = []
    state = trainer_state or {}

    for value in (
        training_output_dir,
        state.get("trainer_output_dir"),
        state.get("output_dir"),
        (state.get("train_config") or {}).get("output_dir") if isinstance(state.get("train_config"), dict) else None,
    ):
        if value:
            candidates.append(Path(str(value)))

    task_id = task_id or state.get("task_id")
    trainer_task_id = (
        trainer_task_id
        or state.get("trainer_version_id")
        or state.get("trainer_task_id")
        or state.get("trainer_training_task_id")
        or state.get("training_task_id")
    )
    if task_id and trainer_task_id:
        candidates.append(Path(output_dir) / str(task_id) / "trainer" / str(trainer_task_id))

    if task_id:
        trainer_root = Path(output_dir) / str(task_id) / "trainer"
        if trainer_root.is_dir():
            children = [child for child in trainer_root.iterdir() if child.is_dir()]
            candidates.extend(sorted(children, key=lambda item: item.stat().st_mtime, reverse=True))

    candidates.append(Path(output_dir))

    for candidate in candidates:
        if candidate.is_dir() and (
            (candidate / "trainer_log.jsonl").exists()
            or (candidate / "metrics" / "metrics.json").exists()
            or (candidate / "metrics" / "verl_metrics.jsonl").exists()
            or any(_checkpoint_step(child.name) is not None for child in candidate.iterdir())
            or (
                (candidate / "checkpoints").is_dir()
                and any(_checkpoint_step(child.name) is not None for child in (candidate / "checkpoints").iterdir())
            )
        ):
            return candidate
    return None


def _checkpoint_roots(training_output_dir: Path, trainer_state: Optional[Dict[str, Any]]) -> List[Path]:
    roots: List[Path] = []
    config = (trainer_state or {}).get("train_config") or {}
    overrides = config.get("overrides") if isinstance(config, dict) else {}
    configured = overrides.get("trainer.default_local_dir") if isinstance(overrides, dict) else None
    for candidate in (
        Path(str(configured)) if configured else None,
        training_output_dir,
        training_output_dir / "checkpoints",
    ):
        if candidate and candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _has_huggingface_weights(path: Path) -> bool:
    weight_names = {
        "model.safetensors",
        "model.safetensors.index.json",
        "pytorch_model.bin",
        "pytorch_model.bin.index.json",
    }
    return path.is_dir() and any((path / name).is_file() for name in weight_names)


def _load_checkpoints(
    training_output_dir: Path,
    trainer_state: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    checkpoints: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for root in _checkpoint_roots(training_output_dir, trainer_state):
        for child in root.iterdir():
            step = _checkpoint_step(child.name)
            if not child.is_dir() or step is None or str(child) in seen:
                continue
            seen.add(str(child))
            actor_path = child / "actor" if child.name.startswith("global_step_") else child
            huggingface_path = actor_path / "huggingface"
            has_huggingface_weights = _has_huggingface_weights(huggingface_path)
            model_path = huggingface_path if has_huggingface_weights else actor_path
            checkpoints.append({
                "name": child.name,
                "step": step,
                "path": str(model_path),
                "checkpoint_path": str(child),
                "backend": "verl" if child.name.startswith("global_step_") else "llamafactory",
                "needs_merge": child.name.startswith("global_step_") and not has_huggingface_weights,
            })
    return sorted(checkpoints, key=lambda item: item.get("step") if item.get("step") is not None else -1)


def _best_metric(
    records: List[Dict[str, Any]],
    selection_metric: str | None = None,
    selection_mode: str | None = None,
) -> Dict[str, Any] | None:
    normalized_mode = _normalize_selection_mode(selection_mode)
    if selection_metric:
        grouped: List[Dict[str, Any]] = []
        for record in records:
            matched = {}
            for key, value in record.items():
                number = _to_number(value)
                if fnmatch(key, selection_metric) and number is not None:
                    matched[key] = float(number)
            if matched:
                step = _to_number(record.get("step"))
                grouped.append({
                    "metric": selection_metric,
                    "value": sum(matched.values()) / len(matched),
                    "step": int(step) if step is not None else None,
                    "source": record.get("source"),
                    "matched_metrics": matched,
                    "record": record,
                })
        if grouped:
            if normalized_mode == "max":
                return max(grouped, key=lambda item: (item["value"], item.get("step") or 0))
            return min(grouped, key=lambda item: (item["value"], -(item.get("step") or 0)))

    candidates: List[Dict[str, Any]] = []
    metric_order = ("eval_loss", "loss", "val_score", "reward")
    for record in records:
        for metric_name in metric_order:
            if metric_name not in record:
                continue
            value = _to_number(record[metric_name])
            if value is None:
                continue
            step = _to_number(record.get("step"))
            candidates.append({
                "metric": metric_name,
                "value": value,
                "step": int(step) if step is not None else None,
                "source": record.get("source"),
                "record": record,
            })

    if not candidates:
        return None

    for metric_name in metric_order:
        metric_candidates = [item for item in candidates if item["metric"] == metric_name]
        if not metric_candidates:
            continue
        maximize = metric_name in {"val_score", "reward"}
        if maximize:
            return max(metric_candidates, key=lambda item: (item["value"], item.get("step") or 0))
        return min(metric_candidates, key=lambda item: (item["value"], -(item.get("step") or 0)))
    return None


def select_best_checkpoint(
    checkpoints: List[Dict[str, Any]],
    metric_records: List[Dict[str, Any]],
    selection_metric: str | None = None,
    selection_mode: str | None = None,
) -> Dict[str, Any] | None:
    """Select a checkpoint and align metric steps to persisted checkpoint steps."""
    if not checkpoints:
        return None

    metric = _best_metric(metric_records, selection_metric, selection_mode)
    if metric is None or metric.get("step") is None:
        checkpoint = checkpoints[-1]
        return {
            **checkpoint,
            "selection_reason": "No usable selection metric found; selected the latest checkpoint.",
            "metric": None,
        }

    target_step = int(metric["step"])
    exact = [item for item in checkpoints if item.get("step") == target_step]
    if exact:
        checkpoint = exact[-1]
        reason = f"Matched checkpoint step {target_step} with best {metric['metric']}."
    else:
        previous = [item for item in checkpoints if item.get("step") is not None and item["step"] <= target_step]
        if previous:
            checkpoint = previous[-1]
            reason = f"Selected nearest checkpoint not after best metric step {target_step}."
        else:
            checkpoint = min(
                checkpoints,
                key=lambda item: abs((item.get("step") or 0) - target_step),
            )
            reason = f"Selected nearest checkpoint to best metric step {target_step}."

    return {
        **checkpoint,
        "selection_reason": reason,
        "metric": {
            key: metric[key]
            for key in ("metric", "value", "step", "source", "matched_metrics")
            if key in metric
        },
    }


def analyze_results(
    task_id: str | None = None,
    output_dir: str = "./outputs",
    trainer_task_id: str | None = None,
    training_output_dir: str | None = None,
    trainer_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyze Trainer artifacts and return a compact result summary."""
    resolved_dir = _find_training_output_dir(
        task_id=task_id,
        output_dir=output_dir,
        trainer_task_id=trainer_task_id,
        training_output_dir=training_output_dir,
        trainer_state=trainer_state,
    )
    if resolved_dir is None:
        return {
            "ok": False,
            "status": "failed",
            "message": "Trainer output directory was not found.",
            "data": None,
            "error": {
                "type": "FileNotFoundError",
                "code": "NOT_FOUND",
                "detail": "No Trainer output directory with checkpoints or metrics was found.",
                "recoverable": True,
            },
        }

    trainer_log_path = resolved_dir / "trainer_log.jsonl"
    metrics_path = resolved_dir / "metrics" / "metrics.json"
    verl_metrics_path = resolved_dir / "metrics" / "verl_metrics.jsonl"
    records = _dedupe_metrics(
        _read_jsonl_metrics(trainer_log_path)
        + _read_metrics_json(metrics_path)
        + _read_verl_jsonl_metrics(verl_metrics_path)
    )
    state = trainer_state or {}
    train_config = state.get("train_config") or {}
    result_config = train_config.get("result") if isinstance(train_config, dict) else {}
    # The approved YAML is the source of truth. Runtime defaults only fill
    # fields that the user did not set in the reviewed configuration.
    selection_metric = (
        result_config.get("selection_metric") if isinstance(result_config, dict) else None
    ) or state.get("verl_selection_metric")
    selection_mode = (
        result_config.get("selection_mode") if isinstance(result_config, dict) else None
    ) or state.get("verl_selection_mode")
    checkpoints = _load_checkpoints(resolved_dir, trainer_state)
    best_checkpoint = select_best_checkpoint(
        checkpoints,
        records,
        selection_metric=selection_metric,
        selection_mode=selection_mode,
    )
    best_metric = best_checkpoint.get("metric") if best_checkpoint else _best_metric(
        records, selection_metric, selection_mode
    )

    eval_records = [item for item in records if "eval_loss" in item]
    loss_records = [item for item in records if "loss" in item]
    latest_metric = records[-1] if records else None
    summary = {
        "training_output_dir": str(resolved_dir),
        "checkpoint_count": len(checkpoints),
        "metric_count": len(records),
        "eval_loss_count": len(eval_records),
        "loss_count": len(loss_records),
        "reward_count": len([item for item in records if "reward" in item]),
        "latest_metric": latest_metric,
        "best_checkpoint_name": best_checkpoint.get("name") if best_checkpoint else None,
        "best_checkpoint_path": best_checkpoint.get("path") if best_checkpoint else None,
        "best_metric": best_metric,
    }

    return {
        "ok": True,
        "status": "completed",
        "message": "Trainer results analyzed.",
        "data": {
            "task_id": task_id,
            "trainer_training_task_id": trainer_task_id or (trainer_state or {}).get("trainer_training_task_id"),
            "training_output_dir": str(resolved_dir),
            "trainer_log_path": str(trainer_log_path) if trainer_log_path.exists() else None,
            "metrics_path": str(metrics_path) if metrics_path.exists() else None,
            "verl_metrics_path": str(verl_metrics_path) if verl_metrics_path.exists() else None,
            "checkpoints": checkpoints,
            "metric_records": records,
            "best_checkpoint": best_checkpoint,
            "best_metric": best_metric,
            "summary": summary,
        },
        "error": None,
    }
