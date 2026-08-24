"""Record-level topic quality validation for per-item LLM gates.

Batch ``dm ingest`` is metadata-driven and writes every row directly into the
lake. Per-record quality approval is a downstream concern: the WebAgent L2
``topic_quality_filter`` operator validates that a passing row carries a
classifier label (focus relevance decided by ``domain_classify`` against the
campaign's exploration keywords) plus at least ``min_semantic_signals``
distinct semantic categories whose evidence can be found in the row's original
content. No vertical (e.g. finance) is hard-wired, so the same operator follows
whichever topic the WebAgent is exploring. Source names, URLs, and dataset
aliases are provenance only and never decide whether a row qualifies.
"""
from __future__ import annotations

import hashlib
from typing import Any

_EVIDENCE_METADATA_KEYS = {
    "dataset",
    "domain",
    "domain_labels",
    "labels",
    "domain_confidence",
    "domain_classifier",
    "domain_classifier_model",
    "finance_semantic_signals",
    "finance_signal_generator",
    "lang",
    "license",
    "modality",
    "processing_level",
    "quality_level",
    "source",
    "source_dataset_id",
    "source_kind",
    "source_uri",
    "split",
    "stage",
    "tags",
    "task_type",
}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return ""


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _all_content_text(value: Any) -> str:
    """Flatten every textual payload field, including SFT outputs/answers."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_all_content_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_all_content_text(item) for item in value)
    return str(value)


def _content_for_evidence(record: dict, content_key: str) -> Any:
    if content_key in record:
        return record[content_key]
    return {key: value for key, value in record.items() if key not in _EVIDENCE_METADATA_KEYS}


def _source_evidence(record: dict, defaults: dict, content_key: str) -> dict[str, str]:
    content = record.get(content_key)
    content_meta = content if isinstance(content, dict) else {}
    source_uri = str(_first_non_empty(
        record.get("source_uri"), content_meta.get("source_uri"), defaults.get("source_uri"), "",
    )).strip()
    source_dataset_id = str(_first_non_empty(
        record.get("source_dataset_id"),
        content_meta.get("source_dataset_id"),
        defaults.get("source_dataset_id"),
        "",
    )).strip()
    source = str(_first_non_empty(
        record.get("source"), content_meta.get("source"), defaults.get("source"), "",
    )).strip()
    return {
        "source_uri": source_uri,
        "source_dataset_id": source_dataset_id,
        "source": source,
    }


def validated_semantic_signals(
    raw_signals: Any,
    content: Any,
    *,
    allowed_types: set[str] | frozenset[str] | None = None,
    min_confidence: float = 0.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return accepted/rejected structured signals with auditable reasons.

    ``allowed_types`` is optional: when omitted the signal vocabulary is open,
    which suits themes whose semantic categories are not enumerated yet.
    """
    content_text = _normal_text(_all_content_text(content))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    values = raw_signals if isinstance(raw_signals, list) else []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            rejected.append({"index": index, "reason": "signal_must_be_object"})
            continue
        signal_type = str(item.get("type") or item.get("category") or "").strip().casefold()
        evidence = " ".join(str(item.get("evidence") or item.get("span") or "").split())
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = -1.0
        reason = ""
        if allowed_types is not None and signal_type not in allowed_types:
            reason = "unsupported_signal_type"
        elif signal_type in seen_types:
            reason = "duplicate_signal_type"
        elif confidence < min_confidence or confidence > 1.0:
            reason = "signal_confidence_below_threshold"
        elif len(evidence) < 3:
            reason = "signal_evidence_missing"
        elif _normal_text(evidence) not in content_text:
            reason = "signal_evidence_not_in_content"
        if reason:
            rejected.append({
                "index": index,
                "type": signal_type,
                "evidence": evidence[:160],
                "confidence": confidence,
                "reason": reason,
            })
            continue
        seen_types.add(signal_type)
        accepted.append({
            "type": signal_type,
            "evidence": evidence[:160],
            "confidence": round(confidence, 6),
        })
    return accepted, rejected


def validate_topic_records(
    records: list[dict],
    *,
    defaults: dict,
    content_key: str,
    allowed_signal_types: set[str] | frozenset[str] | None = None,
    min_semantic_signals: int = 2,
    min_classifier_confidence: float = 0.8,
    min_signal_confidence: float = 0.7,
) -> tuple[list[dict], dict]:
    """Validate rows whose LLM judgement must match the WebAgent's focus.

    Relevance itself is decided by ``domain_classify`` (which received the
    campaign's ``focus_keywords``): a row with an empty label list is unrelated
    and is rejected here.  Everything else is deterministic admission on the
    classifier identity, confidence floor and grounded semantic signals, with
    no vertical-specific vocabulary.
    """
    if min_semantic_signals < 0:
        raise ValueError("topic minimum semantic signals cannot be negative")
    if not 0.0 <= min_classifier_confidence <= 1.0:
        raise ValueError("topic classifier confidence must be between 0 and 1")
    if not 0.0 <= min_signal_confidence <= 1.0:
        raise ValueError("topic signal confidence must be between 0 and 1")

    accepted_records: list[dict] = []
    decisions: list[dict[str, Any]] = []
    for row_number, record in enumerate(records, 1):
        content = _content_for_evidence(record, content_key)
        evidence = _source_evidence(record, defaults, content_key)
        labels = _first_non_empty(record.get("domain_labels"), record.get("labels"), [])
        labels = [str(label).casefold() for label in labels] if isinstance(labels, list) else []
        try:
            classifier_confidence = float(record.get("domain_confidence"))
        except (TypeError, ValueError):
            classifier_confidence = 0.0
        classifier = str(record.get("domain_classifier") or "").strip()
        classifier_model = str(record.get("domain_classifier_model") or "").strip()
        signals, rejected_signals = validated_semantic_signals(
            record.get("semantic_signals"),
            content,
            allowed_types=allowed_signal_types,
            min_confidence=min_signal_confidence,
        )
        reasons: list[str] = []
        if not classifier.startswith("domain_classify@") or not classifier_model:
            reasons.append("topic_classifier_identity_missing")
        if not labels:
            reasons.append("topic_relevance_missing")
        if classifier_confidence < min_classifier_confidence:
            reasons.append("topic_classifier_confidence_below_threshold")
        if len(signals) < min_semantic_signals:
            reasons.append("topic_semantic_signals_below_minimum")

        text = _all_content_text(content)
        decision = {
            "row_number": row_number,
            "record_fingerprint": hashlib.sha256(text.encode("utf-8")).hexdigest()[:20],
            "source_evidence": evidence,
            "classifier": classifier,
            "classifier_model": classifier_model,
            "domain_labels": labels,
            "topic_confidence": round(max(0.0, min(1.0, classifier_confidence)), 6),
            "accepted_semantic_signals": signals,
            "rejected_semantic_signals": rejected_signals,
            "accepted": not reasons,
            "rejection_reasons": reasons,
        }
        decisions.append(decision)
        if not reasons:
            accepted_records.append(record)

    rejected_count = len(records) - len(accepted_records)
    if not accepted_records:
        code = "topic_ingest_quality_rejected"
    elif rejected_count:
        code = "topic_ingest_partially_accepted"
    else:
        code = "topic_ingest_quality_passed"
    report = {
        "ok": bool(accepted_records),
        "all_records_passed": rejected_count == 0,
        "code": code,
        "input_records": len(records),
        "accepted_records": len(accepted_records),
        "rejected_records": rejected_count,
        "policy": {
            "min_semantic_signals": min_semantic_signals,
            "min_classifier_confidence": min_classifier_confidence,
            "min_signal_confidence": min_signal_confidence,
            "allowed_signal_types": sorted(allowed_signal_types) if allowed_signal_types is not None else None,
        },
        "records": decisions,
    }
    return accepted_records, report
