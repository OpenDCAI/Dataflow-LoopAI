from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _to_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _checkpoint_step(name: str) -> int | None:
    if not name.startswith("checkpoint-"):
        return None
    return int(name.split("-", 1)[1]) if name.split("-", 1)[1].isdigit() else None


def _normalize_metric_record(record: Dict[str, Any], source: str) -> Dict[str, Any] | None:
    step = _to_number(record.get("step", record.get("current_steps", record.get("global_step"))))
    loss = _to_number(record.get("loss"))
    eval_loss = _to_number(record.get("eval_loss"))
    if loss is None and eval_loss is None:
        return None

    normalized: Dict[str, Any] = {"source": source}
    if step is not None:
        normalized["step"] = int(step)
    if loss is not None:
        normalized["loss"] = float(loss)
    if eval_loss is not None:
        normalized["eval_loss"] = float(eval_loss)
    for key in ("epoch", "lr", "learning_rate", "timestamp"):
        if key in record:
            normalized[key] = record[key]
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
        elif all(key in payload for key in ("step", "loss")) or "eval_loss" in payload:
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


def _dedupe_metrics(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    deduped: List[Dict[str, Any]] = []
    for record in records:
        key = (record.get("step"), record.get("loss"), record.get("eval_loss"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return sorted(deduped, key=lambda item: (item.get("step") is None, item.get("step") or 0))


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

    task_id = task_id or state.get("trainer_task_id")
    trainer_task_id = trainer_task_id or state.get("trainer_training_task_id") or state.get("training_task_id")
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
            or any(child.name.startswith("checkpoint-") for child in candidate.iterdir())
        ):
            return candidate
    return None


def _load_checkpoints(training_output_dir: Path) -> List[Dict[str, Any]]:
    checkpoints: List[Dict[str, Any]] = []
    children = training_output_dir.iterdir() if training_output_dir.is_dir() else []
    for child in children:
        if not child.is_dir() or not child.name.startswith("checkpoint-"):
            continue
        checkpoints.append({
            "name": child.name,
            "step": _checkpoint_step(child.name),
            "path": str(child),
        })
    return sorted(checkpoints, key=lambda item: item.get("step") if item.get("step") is not None else -1)


def _best_metric(records: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    candidates: List[Dict[str, Any]] = []
    for record in records:
        for metric_name in ("eval_loss", "loss"):
            if metric_name not in record:
                continue
            candidates.append({
                "metric": metric_name,
                "value": record[metric_name],
                "step": record.get("step"),
                "source": record.get("source"),
                "record": record,
            })

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda item: (
            0 if item["metric"] == "eval_loss" else 1,
            item["value"],
            -(item.get("step") or 0),
        ),
    )[0]


def select_best_checkpoint(
    checkpoints: List[Dict[str, Any]],
    metric_records: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Select the best checkpoint by eval_loss first, then train loss, then latest checkpoint."""
    if not checkpoints:
        return None

    metric = _best_metric(metric_records)
    if metric is None or metric.get("step") is None:
        checkpoint = checkpoints[-1]
        return {
            **checkpoint,
            "selection_reason": "No usable loss metric found; selected the latest checkpoint.",
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
            for key in ("metric", "value", "step", "source")
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
    records = _dedupe_metrics(_read_jsonl_metrics(trainer_log_path) + _read_metrics_json(metrics_path))
    checkpoints = _load_checkpoints(resolved_dir)
    best_checkpoint = select_best_checkpoint(checkpoints, records)
    best_metric = best_checkpoint.get("metric") if best_checkpoint else _best_metric(records)

    eval_records = [item for item in records if "eval_loss" in item]
    loss_records = [item for item in records if "loss" in item]
    latest_metric = records[-1] if records else None
    summary = {
        "training_output_dir": str(resolved_dir),
        "checkpoint_count": len(checkpoints),
        "metric_count": len(records),
        "eval_loss_count": len(eval_records),
        "loss_count": len(loss_records),
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
            "checkpoints": checkpoints,
            "metric_records": records,
            "best_checkpoint": best_checkpoint,
            "best_metric": best_metric,
            "summary": summary,
        },
        "error": None,
    }
