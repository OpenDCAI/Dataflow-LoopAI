from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from loopai.schema.system_runtime import (
    resolve_integration_value,
    resolve_runtime_model_config,
)
from .errors import ObtainerCliError
from .models import utc_now


PROVIDER_METHODS = ("huggingface", "kaggle")
TRUTHY = {"1", "true", "yes", "on"}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in TRUTHY


def _should_skip_web_deepsearch(*, search_engine: str, tavily_api_key: str) -> bool:
    engine = str(search_engine or "").strip().lower()
    if engine == "duckduckgo" and not tavily_api_key and not _env_truthy("OBTAINER_SEARCHAGENT_ALLOW_DUCKDUCKGO_DEEPSEARCH"):
        return True
    return False


def _starter_config_candidates(starter_config: str | Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    explicit = starter_config or os.getenv("STARTER_CONFIG") or os.getenv("STARTER_CONFIG_PATH")
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            Path("starter.yaml"),
            Path("examples/config/starter.yaml"),
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _starter_db_candidates() -> list[Path]:
    candidates = [
        Path("api") / "db" / "db.sqlite3",
        Path.cwd() / "api" / "db" / "db.sqlite3",
    ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser().resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _load_starter_config_from_db() -> dict[str, Any]:
    for path in _starter_db_candidates():
        if not path.exists():
            continue
        try:
            con = sqlite3.connect(str(path))
            try:
                row = con.execute(
                    "select config from starterconfig where name=?",
                    ("starter",),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if not row or not row[0]:
            continue
        try:
            loaded = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return {}


def _load_starter_config(starter_config: str | Path | None = None) -> dict[str, Any]:
    if starter_config is None:
        db_config = _load_starter_config_from_db()
        if db_config:
            return db_config
    for path in _starter_config_candidates(starter_config):
        if not path.exists():
            continue
        try:
            try:
                from omegaconf import OmegaConf

                cfg = OmegaConf.load(str(path))
                loaded = OmegaConf.to_container(cfg, resolve=True)
            except ImportError:
                import yaml

                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            if starter_config is None:
                continue
            raise ObtainerCliError(
                "STARTER_CONFIG_LOAD_FAILED",
                f"failed to load starter config: {path}",
                hint=str(exc),
            ) from exc
    return {}


def _resolve_runtime_defaults(starter_config: str | Path | None = None) -> dict[str, Any]:
    cfg = _load_starter_config(starter_config)
    system = cfg.get("system", {}) if isinstance(cfg.get("system"), dict) else {}
    default_states = cfg.get("default_states", {}) if isinstance(cfg.get("default_states"), dict) else {}
    obtainer = default_states.get("obtainer", {}) if isinstance(default_states.get("obtainer"), dict) else {}
    runtime_model = resolve_runtime_model_config(
        system,
        requested=_first_non_empty(
            obtainer.get("model_path"),
            system.get("starter_model_path"),
            system.get("starter_model_name"),
        ),
        legacy_model=obtainer.get("model_path"),
        legacy_base_url=obtainer.get("base_url"),
        legacy_api_key=obtainer.get("api_key"),
    )
    return {
        "model_name": runtime_model.model,
        "base_url": runtime_model.base_url,
        "api_key": runtime_model.api_key,
        "temperature": obtainer.get("temperature"),
        "prompt_template_dir": default_states.get("prompt_template_dir"),
        "search_engine": obtainer.get("search_engine"),
        "max_urls": obtainer.get("max_urls"),
        "tavily_api_key": resolve_integration_value(
            system,
            "tavily",
            "api_key",
            env_keys=("TAVILY_API_KEY",),
            legacy_system_keys=("tavily_api_key",),
            legacy_values=(obtainer.get("tavily_api_key"),),
        ),
        "kaggle_username": resolve_integration_value(
            system,
            "kaggle",
            "username",
            env_keys=("KAGGLE_USERNAME",),
            legacy_system_keys=("kaggle_username",),
            legacy_values=(obtainer.get("kaggle_username"),),
        ),
        "kaggle_key": resolve_integration_value(
            system,
            "kaggle",
            "key",
            env_keys=("KAGGLE_KEY",),
            legacy_system_keys=("kaggle_key",),
            legacy_values=(obtainer.get("kaggle_key"),),
        ),
        "output_root": default_states.get("output_dir"),
    }


def _read_query(query: str = "", query_file: str | Path | None = None) -> str:
    pieces: list[str] = []
    if query:
        pieces.append(str(query).strip())
    if query_file:
        path = Path(query_file)
        if not path.exists():
            raise ObtainerCliError(
                "QUERY_FILE_NOT_FOUND",
                f"query file not found: {path}",
                hint="Pass an existing Analyzer report or use --query.",
            )
        pieces.append(path.read_text(encoding="utf-8", errors="ignore"))
    text = "\n\n".join(piece for piece in pieces if piece.strip()).strip()
    if not text:
        raise ObtainerCliError(
            "EMPTY_QUERY",
            "searchagent query is empty",
            hint="Pass --query, --query-file, or --task-json.",
        )
    return text


def _split_keywords(value: str, fallback: str) -> list[str]:
    keywords = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if keywords:
        return keywords
    return [fallback]


def _normalize_keywords(value: Any, fallback: str = "") -> list[str]:
    if isinstance(value, str):
        return _split_keywords(value, fallback) if value.strip() else ([fallback] if fallback else [])
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [fallback] if fallback else []


def _merge_keywords(*groups: Any, limit: int = 12) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for keyword in _normalize_keywords(group):
            key = keyword.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(keyword)
            if len(merged) >= limit:
                return merged
    return merged


def _extract_urls_from_text(value: str, *, limit: int = 20) -> list[str]:
    urls = re.findall(r"https?://[^\s\]\)>'\"]+", value or "")
    return list(dict.fromkeys(urls))[:limit]


def _load_tasks(task_json: str | Path | None) -> list[dict[str, Any]]:
    if not task_json:
        return []
    path = Path(task_json)
    if not path.exists():
        raise ObtainerCliError(
            "TASK_JSON_NOT_FOUND",
            f"task json not found: {path}",
            hint="Pass an existing JSON file or omit --task-json.",
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_tasks = payload.get("tasks") or payload.get("subtasks") or [payload]
    elif isinstance(payload, list):
        raw_tasks = payload
    else:
        raise ObtainerCliError(
            "INVALID_TASK_JSON",
            "task json must be an object or list",
            hint='Expected {"tasks": [...]} or a list of task objects.',
        )
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(raw_tasks, 1):
        if not isinstance(item, dict):
            raise ObtainerCliError(
                "INVALID_TASK_JSON",
                f"task #{index} must be an object",
                hint="Each task needs objective/search_keywords fields.",
            )
        task = dict(item)
        task.setdefault("type", "download")
        if not task.get("objective"):
            task["objective"] = task.get("query") or task.get("description") or f"download task {index}"
        if not task.get("search_keywords"):
            task["search_keywords"] = task.get("keywords") or task["objective"]
        tasks.append(task)
    return tasks


def _build_single_task(query_text: str, objective: str = "", keywords: str = "") -> dict[str, Any]:
    task_objective = objective.strip() if objective else query_text
    return {
        "type": "download",
        "objective": task_objective,
        "search_keywords": _split_keywords(keywords, task_objective),
        "status": "pending",
    }


def _require_model_config(model_name: str, base_url: str, api_key: str) -> None:
    missing = []
    if not model_name:
        missing.append("model_name")
    if not base_url:
        missing.append("base_url")
    if not api_key:
        missing.append("api_key")
    if missing:
        raise ObtainerCliError(
            "MISSING_MODEL_CONFIG",
            f"searchagent requires model config: {', '.join(missing)}",
            hint="Set system.starter_model_path/starter_base_url/starter_api_key in starter.yaml, or pass --model-name/--base-url/--api-key.",
        )


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _make_prompt_loader(prompt_template_dir: str):
    if not prompt_template_dir:
        return None
    try:
        from loopai.common.prompts import PromptLoader

        return PromptLoader(prompt_template_dir)
    except Exception:
        return None


def _make_method_decision_agent(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: Any,
):
    from loopai.agents.Obtainer.utils.download_method_decision import DownloadMethodDecisionAgent

    return DownloadMethodDecisionAgent(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        prompt_loader=prompt_loader,
    )


def _make_hf_manager():
    from loopai.agents.Obtainer.utils.hf_manager import HuggingFaceManager

    return HuggingFaceManager(disable_cache=False)


def _make_query_generator(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: Any,
):
    from loopai.agents.Obtainer.utils.query_generator import QueryGenerator

    return QueryGenerator(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        prompt_loader=prompt_loader,
    )


def _make_summary_agent(
    *,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: Any,
    max_download_subtasks: int | None,
):
    from loopai.agents.Obtainer.utils.summary_agent import SummaryAgent

    return SummaryAgent(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        prompt_loader=prompt_loader,
        max_download_subtasks=max_download_subtasks,
    )


def _make_kaggle_manager(*, kaggle_username: str = "", kaggle_key: str = ""):
    from loopai.agents.Obtainer.utils.kaggle_manager import KaggleManager

    return KaggleManager(
        disable_cache=False,
        kaggle_username=kaggle_username or None,
        kaggle_key=kaggle_key or None,
    )


def _flatten_hf_candidates(search_results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for keyword, rows in (search_results or {}).items():
        for row in rows or []:
            dataset_id = row.get("id") or row.get("dataset_id")
            if not dataset_id or dataset_id in seen:
                continue
            seen.add(str(dataset_id))
            candidates.append(
                {
                    "source": "huggingface",
                    "dataset_id": dataset_id,
                    "title": row.get("title") or dataset_id,
                    "description": row.get("description", ""),
                    "downloads": row.get("downloads", 0),
                    "size": row.get("size"),
                    "tags": row.get("tags", []),
                    "matched_keyword": keyword,
                    "url": f"https://huggingface.co/datasets/{dataset_id}",
                    "download": {
                        "method": "huggingface",
                        "dataset_id": dataset_id,
                    },
                }
            )
    return candidates


def _flatten_kaggle_candidates(search_results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for keyword, rows in (search_results or {}).items():
        for row in rows or []:
            dataset_id = row.get("id") or row.get("ref")
            if not dataset_id or dataset_id in seen:
                continue
            seen.add(str(dataset_id))
            candidates.append(
                {
                    "source": "kaggle",
                    "dataset_id": dataset_id,
                    "title": row.get("title") or dataset_id,
                    "description": row.get("description", ""),
                    "downloads": row.get("downloads", 0),
                    "size": row.get("size"),
                    "tags": row.get("tags", []),
                    "matched_keyword": keyword,
                    "url": row.get("url") or f"https://www.kaggle.com/datasets/{dataset_id}",
                    "download": {
                        "method": "kaggle",
                        "dataset_id": dataset_id,
                    },
                }
            )
    return candidates


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        source = str(candidate.get("source") or candidate.get("download", {}).get("method") or "")
        dataset_id = str(candidate.get("dataset_id") or candidate.get("url") or "")
        key = (source, dataset_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


async def _search_provider_methods(
    *,
    methods: list[str],
    hf_keywords: list[str],
    kaggle_keywords: list[str],
    max_results_per_source: int,
    kaggle_username: str,
    kaggle_key: str,
    fallback_reason: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for method in methods:
        if method == "huggingface":
            try:
                hf_manager = _make_hf_manager()
                hf_results = await hf_manager.search_datasets(hf_keywords, max_results=max_results_per_source)
                rows = _flatten_hf_candidates(hf_results)
                if fallback_reason:
                    for row in rows:
                        row["fallback_reason"] = fallback_reason
                candidates.extend(rows)
            except Exception as exc:
                errors.append({"method": method, "error": str(exc), "fallback_reason": fallback_reason or None})
        elif method == "kaggle":
            try:
                kaggle_manager = _make_kaggle_manager(
                    kaggle_username=kaggle_username,
                    kaggle_key=kaggle_key,
                )
                kaggle_results = await kaggle_manager.search_datasets(kaggle_keywords, max_results=max_results_per_source)
                rows = _flatten_kaggle_candidates(kaggle_results)
                if fallback_reason:
                    for row in rows:
                        row["fallback_reason"] = fallback_reason
                candidates.extend(rows)
            except Exception as exc:
                errors.append({"method": method, "error": str(exc), "fallback_reason": fallback_reason or None})
    return candidates, errors


async def _search_web_for_deepsearch(
    *,
    query: str,
    search_engine: str,
    tavily_api_key: str,
    max_urls: int,
) -> tuple[str, list[str]]:
    from loopai.agents.Obtainer.utils.web_tools import WebTools

    search_results = await WebTools.search_web(
        query,
        search_engine=search_engine,
        tavily_api_key=tavily_api_key,
    )
    urls = WebTools.extract_urls_from_search_results(search_results)
    if not urls:
        urls = _extract_urls_from_text(search_results, limit=max_urls)
    return search_results, urls[:max_urls]


async def _run_deepsearch(
    *,
    objective: str,
    query_text: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: Any,
    search_engine: str,
    max_urls: int,
    max_deep_queries: int,
    max_deep_pages: int,
    deep_context_chars: int,
    tavily_api_key: str,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    queries: list[str] = []
    urls: list[str] = []
    pages: list[dict[str, Any]] = []
    context_parts: list[str] = []

    try:
        query_generator = _make_query_generator(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
        )
        generated = await query_generator.generate_queries(
            objective=objective,
            message=query_text,
        )
        queries = [q for q in generated if q][:max_deep_queries]
    except Exception as exc:
        errors.append({"stage": "query_generation", "error": str(exc)})

    if not queries:
        queries = [objective or query_text]

    for research_query in queries:
        try:
            search_results, found_urls = await _search_web_for_deepsearch(
                query=research_query,
                search_engine=search_engine,
                max_urls=max_urls,
                tavily_api_key=tavily_api_key,
            )
            urls.extend(found_urls)
            context_parts.append(f"Search query: {research_query}\n{search_results[:4000]}")
        except Exception as exc:
            errors.append({"stage": "web_search", "query": research_query, "error": str(exc)})

    unique_urls = list(dict.fromkeys(urls))[:max_urls]
    for url in unique_urls[:max_deep_pages]:
        try:
            from loopai.agents.Obtainer.utils.web_tools import WebTools

            page_content = await WebTools.read_with_jina_reader(url)
            text = str(page_content.get("text") or "")
            page_record = {
                "url": url,
                "chars": len(text),
                "text_preview": text[:500],
            }
            pages.append(page_record)
            if text.strip():
                context_parts.append(f"URL: {url}\n{text[:deep_context_chars]}")
        except Exception as exc:
            errors.append({"stage": "page_read", "url": url, "error": str(exc)})

    context = "\n\n---\n\n".join(context_parts)[:deep_context_chars]
    summary = ""
    derived_tasks: list[dict[str, Any]] = []
    derived_keywords: list[str] = []
    if context.strip():
        try:
            summary_agent = _make_summary_agent(
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                prompt_loader=prompt_loader,
                max_download_subtasks=3,
            )
            summary_plan = await summary_agent.generate_subtasks(
                objective=objective,
                context=context,
                message=query_text,
            )
            summary = str(summary_plan.get("summary") or "")
            raw_tasks = summary_plan.get("new_sub_tasks") or []
            if isinstance(raw_tasks, list):
                derived_tasks = [task for task in raw_tasks if isinstance(task, dict)]
            for task in derived_tasks:
                derived_keywords.extend(_normalize_keywords(task.get("search_keywords"), task.get("objective", "")))
        except Exception as exc:
            errors.append({"stage": "summary", "error": str(exc)})

    return {
        "queries": queries,
        "urls": unique_urls,
        "pages": pages,
        "summary": summary,
        "derived_tasks": derived_tasks,
        "derived_keywords": _merge_keywords(derived_keywords, limit=12),
        "errors": errors,
    }


async def _search_single_task(
    *,
    task: dict[str, Any],
    query_text: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: Any,
    search_engine: str,
    max_urls: int,
    max_results_per_source: int,
    tavily_api_key: str,
    kaggle_username: str,
    kaggle_key: str,
    deepsearch: bool,
    max_deep_queries: int,
    max_deep_pages: int,
    deep_context_chars: int,
) -> dict[str, Any]:
    objective = str(task.get("objective") or query_text)
    task_domain = str(task.get("domain") or task.get("capability_domain") or "").strip()
    search_keywords = task.get("search_keywords") or objective
    keyword_list = _normalize_keywords(search_keywords, objective)

    deepsearch_result: dict[str, Any] = {
        "enabled": deepsearch,
        "skipped": False,
        "skip_reason": "",
        "queries": [],
        "urls": [],
        "pages": [],
        "summary": "",
        "derived_tasks": [],
        "derived_keywords": [],
        "errors": [],
    }
    if deepsearch and _should_skip_web_deepsearch(search_engine=search_engine, tavily_api_key=tavily_api_key):
        deepsearch_result.update({
            "skipped": True,
            "skip_reason": "duckduckgo_deepsearch_disabled_without_tavily",
        })
    elif deepsearch:
        deepsearch_result = await _run_deepsearch(
            objective=objective,
            query_text=query_text,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            search_engine=search_engine,
            max_urls=max_urls,
            max_deep_queries=max_deep_queries,
            max_deep_pages=max_deep_pages,
            deep_context_chars=deep_context_chars,
            tavily_api_key=tavily_api_key,
        )
        deepsearch_result["enabled"] = True
    enriched_keywords = _merge_keywords(
        deepsearch_result.get("derived_keywords", []),
        keyword_list,
        objective,
        limit=12,
    )

    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = list(deepsearch_result.get("errors", []))
    try:
        method_agent = _make_method_decision_agent(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
        )
        decision = await method_agent.decide_method_order(
            user_original_request="\n\n".join(
                part
                for part in [
                    query_text,
                    f"Deepsearch summary: {deepsearch_result.get('summary', '')}" if deepsearch_result.get("summary") else "",
                ]
                if part
            ),
            current_task_objective=objective,
            search_keywords=enriched_keywords,
        )
    except Exception as exc:
        decision = {
            "method_order": list(PROVIDER_METHODS),
            "keywords_for_hf": enriched_keywords,
            "reasoning": "fallback: method decision failed; using provider search order",
        }
        errors.append({"stage": "method_decision", "error": str(exc)})
    method_order = [
        method
        for method in decision.get("method_order", list(PROVIDER_METHODS))
        if method in PROVIDER_METHODS
    ] or list(PROVIDER_METHODS)
    hf_keywords = _merge_keywords(decision.get("keywords_for_hf"), enriched_keywords, limit=12)
    provider_candidates, provider_errors = await _search_provider_methods(
        methods=method_order,
        hf_keywords=hf_keywords,
        kaggle_keywords=enriched_keywords,
        max_results_per_source=max_results_per_source,
        kaggle_username=kaggle_username,
        kaggle_key=kaggle_key,
    )
    candidates.extend(provider_candidates)
    errors.extend(provider_errors)

    missing_provider_methods = [method for method in PROVIDER_METHODS if method not in set(method_order)]
    if missing_provider_methods and not candidates:
        fallback_candidates, fallback_errors = await _search_provider_methods(
            methods=missing_provider_methods,
            hf_keywords=hf_keywords,
            kaggle_keywords=enriched_keywords,
            max_results_per_source=max_results_per_source,
            kaggle_username=kaggle_username,
            kaggle_key=kaggle_key,
            fallback_reason="no_candidates_from_decision_methods",
        )
        candidates.extend(fallback_candidates)
        errors.extend(fallback_errors)

    candidates = _dedupe_candidates(candidates)

    for candidate in candidates:
        if task_domain:
            candidate.setdefault("task_domain", task_domain)
        candidate.setdefault("task_objective", objective)

    return {
        "domain": task_domain or None,
        "objective": objective,
        "search_keywords": keyword_list,
        "enriched_search_keywords": enriched_keywords,
        "deepsearch": deepsearch_result,
        "decision": decision,
        "candidates": candidates,
        "errors": errors,
    }


async def _run_searchagent_async(
    *,
    query_text: str,
    tasks: list[dict[str, Any]],
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float,
    prompt_loader: Any,
    search_engine: str,
    max_urls: int,
    max_results_per_source: int,
    tavily_api_key: str,
    kaggle_username: str,
    kaggle_key: str,
    deepsearch: bool,
    max_deep_queries: int,
    max_deep_pages: int,
    deep_context_chars: int,
    parallelism: int = 3,
) -> list[dict[str, Any]]:
    import asyncio

    semaphore = asyncio.Semaphore(max(1, int(parallelism or 1)))

    async def _run_one(index: int, task: dict[str, Any]) -> dict[str, Any]:
        isolated_task = dict(task)
        isolated_task.setdefault("task_index", index)
        async with semaphore:
            result = await _search_single_task(
                task=isolated_task,
                query_text=query_text,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                temperature=temperature,
                prompt_loader=prompt_loader,
                search_engine=search_engine,
                max_urls=max_urls,
                max_results_per_source=max_results_per_source,
                tavily_api_key=tavily_api_key,
                kaggle_username=kaggle_username,
                kaggle_key=kaggle_key,
                deepsearch=deepsearch,
                max_deep_queries=max_deep_queries,
                max_deep_pages=max_deep_pages,
                deep_context_chars=deep_context_chars,
            )
        result["task_index"] = index
        task_id = isolated_task.get("id") or isolated_task.get("name")
        task_domain = isolated_task.get("domain") or isolated_task.get("capability_domain")
        if task_id:
            result["task_id"] = str(task_id)
        for candidate in result.get("candidates", []) or []:
            candidate.setdefault("task_index", index)
            if task_id:
                candidate.setdefault("task_id", str(task_id))
            if task_domain:
                candidate.setdefault("task_domain", str(task_domain))
        return result

    return list(
        await asyncio.gather(
            *[_run_one(index, task) for index, task in enumerate(tasks)]
        )
    )


def run_searchagent(
    *,
    query: str = "",
    query_file: str | Path | None = None,
    task_json: str | Path | None = None,
    objective: str = "",
    keywords: str = "",
    output_root: str | Path = "./outputs",
    model_name: str = "",
    base_url: str = "",
    api_key: str = "",
    temperature: float | None = None,
    prompt_template_dir: str = "",
    starter_config: str | Path | None = None,
    search_engine: str = "",
    max_urls: int | None = None,
    max_results_per_source: int = 5,
    tavily_api_key: str = "",
    kaggle_username: str = "",
    kaggle_key: str = "",
    deepsearch: bool = True,
    max_deep_queries: int = 3,
    max_deep_pages: int = 3,
    deep_context_chars: int = 12000,
    parallelism: int = 3,
    debug: bool = False,
) -> dict[str, Any]:
    needs_starter = bool(starter_config) or not (model_name and base_url and api_key)
    starter_defaults = _resolve_runtime_defaults(starter_config) if needs_starter else {}
    model_name = model_name or starter_defaults.get("model_name") or os.getenv("OBTAINER_MODEL", "")
    base_url = base_url or starter_defaults.get("base_url") or os.getenv("OBTAINER_BASE_URL", "")
    api_key = api_key or starter_defaults.get("api_key") or os.getenv("OBTAINER_API_KEY", "")
    temperature = float(_first_non_empty(temperature, starter_defaults.get("temperature"), 0.7))
    prompt_template_dir = prompt_template_dir or starter_defaults.get("prompt_template_dir") or ""
    search_engine = search_engine or starter_defaults.get("search_engine") or "tavily"
    max_urls = int(_first_non_empty(max_urls, starter_defaults.get("max_urls"), 10))
    tavily_api_key = tavily_api_key or starter_defaults.get("tavily_api_key") or os.getenv("TAVILY_API_KEY", "")
    kaggle_username = kaggle_username or starter_defaults.get("kaggle_username") or os.getenv("KAGGLE_USERNAME", "")
    kaggle_key = kaggle_key or starter_defaults.get("kaggle_key") or os.getenv("KAGGLE_KEY", "")
    output_root = output_root or starter_defaults.get("output_root") or "./outputs"
    _require_model_config(model_name, base_url, api_key)

    query_text = _read_query(query=query, query_file=query_file) if not task_json else str(query or "").strip()
    tasks = _load_tasks(task_json)
    if not tasks:
        if not query_text:
            query_text = _read_query(query=query, query_file=query_file)
        tasks = [_build_single_task(query_text, objective=objective, keywords=keywords)]

    output_path = Path(output_root)
    output_path.mkdir(parents=True, exist_ok=True)
    import asyncio

    prompt_loader = _make_prompt_loader(prompt_template_dir)
    task_results = asyncio.run(
        _run_searchagent_async(
            query_text=query_text,
            tasks=tasks,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            prompt_loader=prompt_loader,
            search_engine=search_engine,
            max_urls=max_urls,
            max_results_per_source=max_results_per_source,
            tavily_api_key=tavily_api_key,
            kaggle_username=kaggle_username,
            kaggle_key=kaggle_key,
            deepsearch=deepsearch,
            max_deep_queries=max_deep_queries,
            max_deep_pages=max_deep_pages,
            deep_context_chars=deep_context_chars,
            parallelism=parallelism,
        )
    )
    candidates = []
    errors = []
    for task_result in task_results:
        candidates.extend(task_result.get("candidates", []))
        errors.extend(task_result.get("errors", []))
    manifest = {
        "ok": True,
        "status": "completed",
        "schema_version": "obtainercli.searchagent.v1",
        "created_at": utc_now(),
        "query": query_text,
        "output_root": str(output_path.resolve()),
        "tasks": task_results,
        "candidates": candidates,
        "download_list": candidates,
        "errors": errors,
    }
    manifest_path = output_path / "searchagent_manifest.json"
    _write_manifest(manifest_path, manifest)
    return {
        **manifest,
        "manifest_path": str(manifest_path.resolve()),
    }
