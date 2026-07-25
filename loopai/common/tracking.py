"""Helpers for removing the retired SwanLab integration from persisted inputs."""

from __future__ import annotations

from typing import Any, Mapping


_RETIRED_TRACKER_MARKER = "swanlab"
_DROP_VALUE = object()


def is_retired_tracking_key(key: Any) -> bool:
    """Return whether a mapping key belongs to the retired tracker."""
    return _RETIRED_TRACKER_MARKER in str(key).lower()


def is_retired_tracking_value(value: Any) -> bool:
    """Return whether a scalar config value selects the retired tracker."""
    return isinstance(value, str) and value.strip().lower() == _RETIRED_TRACKER_MARKER


def contains_retired_tracking_reference(value: Any) -> bool:
    """Return whether free-form text references the retired tracker."""
    return _RETIRED_TRACKER_MARKER in str(value).lower()


def strip_retired_tracking_fields(value: Any) -> Any:
    """Return a copy with retired tracker fields removed recursively.

    Old StarterConfig rows, task states, and worker payloads may still contain
    fields that are no longer part of the Trainer schema.  Filtering by key at
    every trust boundary prevents those legacy secrets from being re-exposed.
    """
    if isinstance(value, Mapping):
        return {
            key: strip_retired_tracking_fields(item)
            for key, item in value.items()
            if not is_retired_tracking_key(key)
        }
    if isinstance(value, list):
        return [strip_retired_tracking_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_retired_tracking_fields(item) for item in value)
    return value


def _strip_retired_training_config_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if is_retired_tracking_key(key):
                continue
            cleaned_item = _strip_retired_training_config_value(item)
            if cleaned_item is not _DROP_VALUE:
                cleaned[key] = cleaned_item
        return cleaned
    if isinstance(value, list):
        return [
            cleaned_item
            for item in value
            if (cleaned_item := _strip_retired_training_config_value(item)) is not _DROP_VALUE
        ]
    if isinstance(value, tuple):
        return tuple(
            cleaned_item
            for item in value
            if (cleaned_item := _strip_retired_training_config_value(item)) is not _DROP_VALUE
        )
    return _DROP_VALUE if is_retired_tracking_value(value) else value


def strip_retired_tracking_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Remove retired tracker keys and logger selections from a config."""
    cleaned = _strip_retired_training_config_value(config)
    if not isinstance(cleaned, dict):
        raise TypeError("training config must be a mapping")
    return cleaned


def force_local_training_metrics(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a training config that only uses LoopAI's local metric files."""
    cleaned = strip_retired_tracking_config(config)
    cleaned["report_to"] = "none"
    return cleaned


def strip_retired_tracking_environment(environment: Mapping[str, Any]) -> dict[str, str]:
    """Copy an environment without credentials or flags for the retired tracker."""
    return {
        str(key): str(value)
        for key, value in environment.items()
        if not is_retired_tracking_key(key) and str(key).upper() != "TRAIN_USE_SWANLAB"
    }


def assert_no_retired_tracking(config: Mapping[str, Any]) -> None:
    """Reject an old approved YAML that would initialize the retired tracker."""
    retired_keys: list[str] = []

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                if is_retired_tracking_key(key):
                    retired_keys.append(path)
                visit(item, path)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, f"{prefix}[{index}]")
        elif is_retired_tracking_value(value):
            retired_keys.append(prefix or "<root>")

    visit(config)

    report_to = config.get("report_to")
    report_targets = report_to if isinstance(report_to, (list, tuple)) else [report_to]
    if any(_RETIRED_TRACKER_MARKER in str(target).lower() for target in report_targets):
        retired_keys.append("report_to")

    if retired_keys:
        fields = ", ".join(sorted(set(retired_keys)))
        raise ValueError(
            "prepared Trainer YAML contains retired SwanLab settings "
            f"({fields}); prepare the config again before training"
        )
