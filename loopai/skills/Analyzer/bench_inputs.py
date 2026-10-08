from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def _source(name: Any, path: Any, task_type: Any = None) -> Dict[str, Any]:
    value = str(path or "").strip()
    fallback_name = Path(value).stem if value else "bench"
    return {
        "bench_name": str(name or fallback_name),
        "path": value,
        "task_type": str(task_type or ""),
        "bench_name_explicit": bool(name),
    }


def _from_value(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, (str, Path)):
        return [_source(None, value)] if str(value).strip() else []
    if isinstance(value, dict):
        if any(key in value for key in ("output_result_path", "eval_result_path", "result_path", "path")):
            return [_source(
                value.get("bench_name") or value.get("name"),
                value.get("output_result_path") or value.get("eval_result_path") or value.get("result_path") or value.get("path"),
                value.get("task_type"),
            )]
        sources = []
        for name, item in value.items():
            if isinstance(item, dict):
                if any(key in item for key in ("output_result_path", "eval_result_path", "result_path", "path")):
                    sources.extend(_from_value({**item, "bench_name": item.get("bench_name") or name}))
            elif isinstance(item, (str, Path)) and item:
                sources.append(_source(name, item))
        return sources
    if isinstance(value, (list, tuple)):
        sources: List[Dict[str, Any]] = []
        for item in value:
            sources.extend(_from_value(item))
        return sources
    return []


def resolve_eval_result_sources(state: Dict[str, Any], task_type: str) -> List[Dict[str, Any]]:
    """Resolve one or more Judger result files without breaking string input."""
    judger = state.get("judger") if isinstance(state.get("judger"), dict) else {}
    analyzer = state.get("analyzer") if isinstance(state.get("analyzer"), dict) else {}

    sources: List[Dict[str, Any]] = []
    for key in ("bench_result", "extra_bench_result"):
        sources.extend(_from_value(judger.get(key)))

    compatible = [
        item for item in sources
        if not item.get("task_type") or item.get("task_type") == task_type
    ]
    if sources:
        sources = compatible
    else:
        sources = _from_value(
            judger.get("output_result_paths")
            or judger.get("output_result_path")
            or analyzer.get("eval_result_paths")
            or analyzer.get("eval_result_path")
        )

    if task_type == "code":
        from .code_bench_inputs import expand_bench_sources
        sources = expand_bench_sources(sources)

    deduplicated: List[Dict[str, Any]] = []
    seen = set()
    used_names = CounterLike()
    for item in sources:
        path = item.get("path", "")
        if not path or path in seen:
            continue
        seen.add(path)
        base_name = item.get("bench_name") or Path(path).stem
        suffix = used_names.next(base_name)
        item = dict(item)
        item["bench_name"] = base_name if suffix == 1 else f"{base_name}-{suffix}"
        deduplicated.append(item)
    return deduplicated


class CounterLike:
    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}

    def next(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]
