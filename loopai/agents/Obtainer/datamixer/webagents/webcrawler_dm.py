"""Query -> relevant resource URLs -> bounded raw HTML crawl -> DataMixer L1.

The small observe/action/tool loop is inspired by the MIT-licensed
``browser-use`` project (https://github.com/browser-use/browser-use), but is
implemented locally so DataMixer does not inherit that project's large runtime
surface.  The terminal action is deliberately a tool call:
``submit_resource_urls``.  A prose answer from the model can never complete a
run.
"""
from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import socket
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote_plus, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .. import llm
from ..models import ModelPool
from ..store import DataStore
from .base import WebAgent, WebAgentSpec, register


_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source",
    "spm", "yclid",
}
_TRACKING_PREFIXES = ("utm_",)
_SKIP_EXTENSIONS = {
    ".7z", ".avi", ".css", ".csv", ".doc", ".docx", ".exe", ".gif",
    ".gz", ".ico", ".jpeg", ".jpg", ".js", ".json", ".m4a", ".mov",
    ".mp3", ".mp4", ".ogg", ".png", ".ppt", ".pptx", ".rar", ".rss",
    ".svg", ".tar", ".tgz", ".wav", ".webm", ".webp", ".xls", ".xlsx",
    ".xml", ".zip",
}
_BOT_MARKERS = (
    "access denied", "attention required", "captcha", "cf-chl-", "cloudflare",
    "enable javascript", "just a moment", "robot check", "verify you are human",
)
_PROXY_ENV_KEYS = (
    "WEBCRAWLER_DM_PROXY",
    "HTTPS_PROXY", "https_proxy",
    "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy",
)
_LLM_SUMMARY_PROVIDERS = {
    "baidu", "bing", "duckduckgo_html", "github", "tavily",
}
_PROXY_DOH_URL = "https://1.1.1.1/dns-query"
_RUBRIC_DIMENSIONS = (
    "query_coverage",
    "source_authority",
    "content_substance",
    "crawl_yield",
    "complementary_value",
)


def resolve_network_proxy(
    explicit: str = "",
    *,
    use_env: bool = True,
) -> tuple[str, str]:
    """Resolve one shared proxy for HTTPX and Playwright."""

    value = str(explicit or "").strip()
    if value:
        return value, "config"
    if use_env:
        for key in _PROXY_ENV_KEYS:
            value = str(os.environ.get(key) or "").strip()
            if value:
                return value, f"env:{key}"
    return "", ""


def mask_proxy_url(proxy: str) -> str:
    value = str(proxy or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if not parts.hostname:
            return "<configured>"
        host = parts.hostname
        port = f":{parts.port}" if parts.port else ""
        credentials = "***@" if parts.username or parts.password else ""
        return f"{parts.scheme}://{credentials}{host}{port}"
    except (TypeError, ValueError):
        return "<configured>"


def playwright_proxy_settings(proxy: str) -> dict[str, str] | None:
    """Convert a proxy URL into Playwright's server/credential structure."""

    value = str(proxy or "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname:
        raise ValueError(f"invalid proxy URL: {mask_proxy_url(value)}")
    port = f":{parts.port}" if parts.port else ""
    out = {"server": f"{parts.scheme}://{parts.hostname}{port}"}
    if parts.username:
        out["username"] = unquote(parts.username)
    if parts.password:
        out["password"] = unquote(parts.password)
    return out


def playwright_process_env() -> dict[str, str] | None:
    """Return a child environment for an optional user-space browser runtime."""

    configured = str(
        os.environ.get("PLAYWRIGHT_RUNTIME_PREFIX") or ""
    ).strip()
    prefix = Path(configured) if configured else Path.cwd() / ".cache" / "playwright-runtime"
    library_dir = prefix / "lib"
    if not library_dir.is_dir():
        return None
    env = dict(os.environ)
    existing = str(env.get("LD_LIBRARY_PATH") or "").strip()
    env["LD_LIBRARY_PATH"] = (
        f"{library_dir}{os.pathsep}{existing}" if existing else str(library_dir)
    )
    fontconfig_dir = prefix / "etc" / "fonts"
    if fontconfig_dir.is_dir():
        env["FONTCONFIG_PATH"] = str(fontconfig_dir)
    share_dir = prefix / "share"
    if share_dir.is_dir():
        existing_share = str(env.get("XDG_DATA_DIRS") or "").strip()
        env["XDG_DATA_DIRS"] = (
            f"{share_dir}{os.pathsep}{existing_share}"
            if existing_share else str(share_dir)
        )
    return env


@dataclass
class WebCrawlerDMConfig:
    """Bounded runtime configuration for ``webcrawler_dm``."""

    model: str | None = None
    max_steps: int = 30
    soft_step_limit: int = 16
    max_search_calls: int = 4
    max_pages: int = 1000
    max_depth: int = 2
    max_links_per_page: int = 1000
    search_provider: str = "auto"
    search_results: int = 8
    github_token_env: str = "GITHUB_TOKEN"
    search_timeout: float = 15.0
    tavily_api_key: str = ""
    search_llm_summary: bool = True
    search_summary_results: int = 5
    search_summary_chars: int = 4000
    proxy: str = ""
    use_env_proxy: bool = True
    browser_backend: str = "auto"
    use_playwright_stealth: bool = True
    headless: bool = True
    same_domain_only: bool = True
    respect_robots_txt: bool = True
    allow_private_network: bool = False
    request_delay: float = 0.5
    timeout: float = 25.0
    max_retries: int = 2
    max_html_bytes: int = 4_000_000
    page_cache_entries: int = 64
    max_observation_chars: int = 3000
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
    license: str = "unknown"

    def __post_init__(self) -> None:
        # At least two actions are required for a cold start: search + submit.
        self.max_steps = max(2, int(self.max_steps))
        self.soft_step_limit = max(2, min(self.max_steps, int(self.soft_step_limit)))
        self.max_search_calls = max(1, int(self.max_search_calls))
        self.max_pages = max(1, int(self.max_pages))
        self.max_depth = max(0, int(self.max_depth))
        self.max_links_per_page = max(1, int(self.max_links_per_page))
        self.search_results = max(1, int(self.search_results))
        self.search_timeout = max(1.0, float(self.search_timeout))
        self.search_summary_results = max(1, min(20, int(self.search_summary_results)))
        self.search_summary_chars = max(500, min(20_000, int(self.search_summary_chars)))
        self.request_delay = max(0.0, float(self.request_delay))
        self.timeout = max(1.0, float(self.timeout))
        self.max_retries = max(0, int(self.max_retries))
        self.max_html_bytes = max(16_384, int(self.max_html_bytes))
        self.page_cache_entries = max(2, min(1024, int(self.page_cache_entries)))
        if self.search_provider not in {
            "auto", "baidu", "bing", "github", "tavily", "duckduckgo_html"
        }:
            raise ValueError(
                "search_provider must be auto, baidu, bing, github, tavily, or duckduckgo_html"
            )
        if self.browser_backend not in {"auto", "httpx", "playwright"}:
            raise ValueError("browser_backend must be auto, httpx, or playwright")

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("tavily_api_key"):
            data["tavily_api_key"] = "***"
        if data.get("proxy"):
            data["proxy"] = mask_proxy_url(data["proxy"])
        return data

    def resolved_proxy(self) -> tuple[str, str]:
        return resolve_network_proxy(self.proxy, use_env=self.use_env_proxy)


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0
    provider: str = ""
    search_snippet: str = ""
    summary: str = ""
    # Compatibility-only fields for older consumers. They are never used as
    # an acceptance/rejection gate; the multi-dimensional rubric below owns
    # selection evidence.
    relevance_score: float = 0.0
    relevant: bool | None = None
    llm_enriched: bool = False
    summary_model: str = ""
    fetch_mode: str = ""
    summary_error: str = ""
    rubric_scores: dict[str, float] = field(default_factory=dict)
    rubric_evidence: dict[str, str] = field(default_factory=dict)
    rubric_decision: str = "uncertain"
    decision_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["content"] = self.summary or self.snippet
        return data


@dataclass
class LinkCandidate:
    url: str
    anchor: str = ""
    score: float = 0.0
    same_domain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FetchedPage:
    requested_url: str
    final_url: str
    html: str
    title: str
    text_preview: str
    status: int
    content_type: str
    headers: dict[str, str]
    fetch_mode: str
    stealth_applied: bool = False
    canonical_hint: str = ""

    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.canonical_hint or self.final_url)


@dataclass
class WebCrawlerDMResult:
    run_id: str
    query: str
    selected_url: str
    selected_urls: list[str]
    submitted_by_tool: bool
    agent_steps: int
    max_steps: int
    model: str | None
    dataset: str
    dataset_id: str
    pages_fetched: int
    pages_ingested: int
    pages_failed: int
    crawl_depth: int
    search_provider: str
    browser: dict[str, Any]
    trace: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    lineage_path: str = ""
    campaign_id: str = ""
    parent_query: str = ""
    subgoal_index: int | None = None

    def to_dict(self, include_trace: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_trace:
            data.pop("trace", None)
        return data


class WebCrawlerDMError(RuntimeError):
    pass


def _normalize_rubric(
    item: dict[str, Any],
) -> tuple[dict[str, float], dict[str, str], str, str]:
    """Validate one grounded five-dimension URL rubric from the LLM.

    A single low dimension can never make a candidate an exclusion.  Exclusion
    is trusted only with a complete rubric, a grounded reason, and at least two
    independently weak (1-2) dimensions.  Invalid/partial output remains
    ``uncertain`` for the main web agent to inspect.
    """

    raw_rubric = item.get("rubric")
    raw_rubric = raw_rubric if isinstance(raw_rubric, dict) else {}
    scores: dict[str, float] = {}
    evidence: dict[str, str] = {}
    for dimension in _RUBRIC_DIMENSIONS:
        value = raw_rubric.get(dimension)
        if not isinstance(value, dict):
            continue
        try:
            score = float(value.get("score"))
        except (TypeError, ValueError):
            continue
        if not 1.0 <= score <= 5.0:
            continue
        scores[dimension] = score
        evidence[dimension] = re.sub(
            r"\s+", " ", str(value.get("evidence") or "")
        ).strip()[:500]

    reason = re.sub(
        r"\s+", " ", str(item.get("decision_reason") or "")
    ).strip()[:1000]
    raw_decision = str(item.get("decision") or "uncertain").strip().lower()
    decision_aliases = {
        "include": "supporting",
        "relevant": "supporting",
        "reject": "exclude",
        "irrelevant": "exclude",
    }
    decision = decision_aliases.get(raw_decision, raw_decision)
    if decision not in {"core", "supporting", "exclude", "uncertain"}:
        decision = "uncertain"
    if len(scores) != len(_RUBRIC_DIMENSIONS) or not reason:
        decision = "uncertain"
    if decision == "exclude":
        weak_dimensions = sum(score <= 2.0 for score in scores.values())
        if weak_dimensions < 2:
            decision = "uncertain"
            reason = (
                reason + "; exclusion downgraded to uncertain because fewer "
                "than two rubric dimensions were weak"
            )[:1000]
    return scores, evidence, decision, reason


def canonicalize_url(url: str) -> str:
    """Return a stable HTTP(S) URL used for queue and content deduplication."""

    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _TRACKING_QUERY_KEYS or lowered.startswith(_TRACKING_PREFIXES):
            continue
        pairs.append((key, value))
    query = urlencode(sorted(pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def _hostname(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _same_site(host: str, root_host: str) -> bool:
    return bool(
        host and root_host
        and (host == root_host or host.endswith("." + root_host)
             or root_host.endswith("." + host))
    )


def _looks_like_html_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return not any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.canonical_hint = ""
        self._skip_depth = 0
        self._in_title = False
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attrs_dict = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag in {"script", "style", "noscript", "svg", "canvas", "template"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "a" and attrs_dict.get("href"):
            self._anchor_href = attrs_dict["href"]
            self._anchor_parts = []
        elif tag == "link" and "canonical" in attrs_dict.get("rel", "").lower():
            self.canonical_hint = attrs_dict.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "canvas", "template"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor_href is not None:
            anchor = " ".join(self._anchor_parts).strip()
            self.links.append((self._anchor_href, anchor))
            self._anchor_href = None
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if self._in_title:
            self.title_parts.append(cleaned)
        if self._anchor_href is not None:
            self._anchor_parts.append(cleaned)
        if len(" ".join(self.text_parts)) < 8000:
            self.text_parts.append(cleaned)

    def summary(self) -> tuple[str, str, str]:
        title = " ".join(self.title_parts).strip()
        text = " ".join(self.text_parts).strip()
        return title[:500], text[:4000], self.canonical_hint


def parse_page(html: str) -> _PageParser:
    parser = _PageParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser


def extract_related_links(
    page: FetchedPage,
    query: str,
    *,
    max_links: int,
    same_domain_only: bool,
) -> list[LinkCandidate]:
    """Extract a bounded set of crawlable links without semantic heuristics."""

    del query  # Relevance is judged by the tool-calling LLM, not keyword rules.
    parser = parse_page(page.html)
    root_host = _hostname(page.final_url)
    seen: set[str] = set()
    ranked: list[LinkCandidate] = []
    for href, anchor in parser.links:
        if href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        url = canonicalize_url(urljoin(page.final_url, href))
        if not url or url in seen or url == page.canonical_url or not _looks_like_html_url(url):
            continue
        seen.add(url)
        host = _hostname(url)
        same = _same_site(host, root_host)
        if same_domain_only and not same:
            continue
        ranked.append(LinkCandidate(
            url=url,
            anchor=anchor[:300],
            score=1.0 if same else 0.0,
            same_domain=same,
        ))
    ranked.sort(key=lambda item: (-item.score, len(item.url), item.url))
    return ranked[:max_links]


class WebSearchClient:
    """Small provider adapter with bounded, proxy-aware search backends."""

    def __init__(self, config: WebCrawlerDMConfig):
        self.config = config
        self.last_attempts: list[dict[str, Any]] = []
        self.last_provider = ""

    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        limit = max_results or self.config.search_results
        provider = self.configured_primary_provider()
        errors = []
        providers = [provider]
        if self.config.search_provider in {"auto", "tavily"}:
            providers.extend(
                p for p in ("bing", "baidu", "duckduckgo_html") if p != provider
            )
        self.last_attempts = []
        self.last_provider = ""
        for item in providers:
            try:
                deduped = self.search_provider(query, item, limit)
                if deduped:
                    self.last_provider = item
                    self.last_attempts.append({
                        "provider": item,
                        "ok": True,
                        "results": len(deduped),
                    })
                    return deduped
                self.last_attempts.append({
                    "provider": item,
                    "ok": False,
                    "error": "no_results",
                })
            except Exception as exc:  # noqa: BLE001 - provider fallback
                error = f"{type(exc).__name__}: {exc}"[:1000]
                errors.append(f"{item}: {error}")
                self.last_attempts.append({
                    "provider": item,
                    "ok": False,
                    "error": error,
                })
        detail = "; ".join(errors) if errors else f"no results from {', '.join(providers)}"
        raise WebCrawlerDMError("web search failed: " + detail)

    def search_provider(
        self,
        query: str,
        provider: str,
        limit: int,
    ) -> list[SearchResult]:
        """Search exactly one provider for LLM-rubric-aware continuation."""

        if provider == "tavily":
            rows = self._tavily(query, limit)
        elif provider == "bing":
            rows = self._bing(query, limit)
        elif provider == "baidu":
            rows = self._baidu(query, limit)
        elif provider == "github":
            rows = self._github(query, limit)
        elif provider == "duckduckgo_html":
            rows = self._duckduckgo_html(query, limit)
        else:
            raise ValueError(f"unknown search provider: {provider}")
        deduped = self._dedup(rows, limit)
        for row in deduped:
            row.search_snippet = row.search_snippet or row.snippet
        return deduped

    def configured_primary_provider(self) -> str:
        if self.config.search_provider != "auto":
            return self.config.search_provider
        return "tavily" if self.config.tavily_api_key else "bing"

    def primary_provider(self) -> str:
        """Provider currently serving results, or the first configured attempt."""
        return self.last_provider or self.configured_primary_provider()

    @staticmethod
    def _dedup(
        rows: list[SearchResult],
        limit: int,
    ) -> list[SearchResult]:
        out = []
        seen = set()
        for row in rows:
            url = canonicalize_url(row.url)
            if not url or url in seen:
                continue
            seen.add(url)
            row.url = url
            row.rank = len(out) + 1
            out.append(row)
            if len(out) >= limit:
                break
        return out

    def _github(self, query: str, limit: int) -> list[SearchResult]:
        import httpx

        proxy, _ = self.config.resolved_proxy()
        token = os.environ.get(self.config.github_token_env, "") \
            if self.config.github_token_env else ""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "webcrawler_dm",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = httpx.get(
            "https://api.github.com/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": min(30, max(10, limit * 3)),
            },
            headers=headers,
            timeout=self.config.search_timeout,
            proxy=proxy or None,
            trust_env=False,
        )
        response.raise_for_status()
        return [
            SearchResult(
                url=str(item.get("html_url") or ""),
                title=str(item.get("full_name") or item.get("name") or ""),
                snippet=(
                    f"{item.get('description') or ''} "
                    f"language={item.get('language') or 'unknown'} "
                    f"stars={item.get('stargazers_count') or 0}"
                ).strip()[:1000],
                provider="github",
            )
            for item in response.json().get("items", [])
        ]

    def _tavily(self, query: str, limit: int) -> list[SearchResult]:
        if not self.config.tavily_api_key:
            raise ValueError("Tavily API key is not configured")
        import httpx

        proxy, _ = self.config.resolved_proxy()
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.config.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": limit,
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=self.config.search_timeout,
            proxy=proxy or None,
            trust_env=False,
        )
        if response.status_code >= 400:
            detail = re.sub(
                r"tvly-[A-Za-z0-9_-]+", "<redacted>", response.text[:1000]
            ).strip()
            raise WebCrawlerDMError(
                f"Tavily HTTP {response.status_code}: {detail or 'empty response'}"
            )
        return [
            SearchResult(
                url=str(row.get("url") or ""),
                title=str(row.get("title") or ""),
                snippet=str(row.get("content") or "")[:1200],
                provider="tavily",
            )
            for row in response.json().get("results", [])
        ]

    def _bing(self, query: str, limit: int) -> list[SearchResult]:
        import httpx

        proxy, _ = self.config.resolved_proxy()
        response = httpx.get(
            "https://www.bing.com/search",
            params={"q": query, "count": max(5, limit), "setlang": "en-US"},
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
            timeout=self.config.search_timeout,
            proxy=proxy or None,
            trust_env=False,
        )
        response.raise_for_status()
        rows = []
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(response.text, "html.parser")
            for item in soup.select("li.b_algo"):
                link = item.select_one("h2 a[href]")
                if link is None:
                    continue
                caption = item.select_one(".b_caption p")
                rows.append(SearchResult(
                    url=str(link.get("href") or ""),
                    title=link.get_text(" ", strip=True),
                    snippet=caption.get_text(" ", strip=True) if caption else "",
                    provider="bing",
                ))
                if len(rows) >= limit:
                    break
        except ImportError:
            parser = parse_page(response.text)
            for href, anchor in parser.links:
                url = canonicalize_url(urljoin(str(response.url), href))
                if not url or _hostname(url).endswith("bing.com"):
                    continue
                rows.append(SearchResult(url=url, title=anchor, provider="bing"))
                if len(rows) >= limit:
                    break
        if rows:
            return rows
        return self._bing_rss(query, limit)

    def _bing_rss(self, query: str, limit: int) -> list[SearchResult]:
        """Use Bing's RSS output when the HTML page is an anti-bot shell."""
        import html
        import httpx
        from xml.etree import ElementTree

        proxy, _ = self.config.resolved_proxy()
        response = httpx.get(
            "https://www.bing.com/search",
            params={
                "q": query,
                "format": "rss",
                "count": max(5, limit),
                "setlang": "en-US",
            },
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
            timeout=self.config.search_timeout,
            proxy=proxy or None,
            trust_env=False,
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        rows = []
        for item in root.findall(".//item"):
            url = str(item.findtext("link") or "").strip()
            title = html.unescape(str(item.findtext("title") or "")).strip()
            description = html.unescape(
                str(item.findtext("description") or "")
            )
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"\s+", " ", description).strip()
            if not url:
                continue
            rows.append(SearchResult(
                url=url,
                title=title,
                snippet=description[:1200],
                provider="bing",
                fetch_mode="bing_rss",
            ))
            if len(rows) >= limit:
                break
        return rows

    def _baidu(self, query: str, limit: int) -> list[SearchResult]:
        import httpx

        proxy, _ = self.config.resolved_proxy()
        response = httpx.get(
            "https://www.baidu.com/s",
            params={"wd": query, "rn": max(10, limit)},
            headers={
                "User-Agent": self.config.user_agent,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
            follow_redirects=True,
            timeout=self.config.search_timeout,
            proxy=proxy or None,
            trust_env=False,
        )
        response.raise_for_status()
        rows = []
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(response.text, "html.parser")
            for item in soup.select("div.result, div.c-container"):
                link = item.select_one("h3 a[href]")
                if link is None:
                    continue
                caption = item.select_one(
                    ".c-abstract, .content-right_8Zs40, .c-span-last, .content-right"
                )
                rows.append(SearchResult(
                    url=str(link.get("href") or ""),
                    title=link.get_text(" ", strip=True),
                    snippet=caption.get_text(" ", strip=True) if caption else "",
                    provider="baidu",
                ))
                if len(rows) >= limit:
                    break
        except ImportError:
            parser = parse_page(response.text)
            for href, anchor in parser.links:
                url = canonicalize_url(urljoin(str(response.url), href))
                if not url or _hostname(url).endswith("baidu.com"):
                    continue
                rows.append(SearchResult(url=url, title=anchor, provider="baidu"))
                if len(rows) >= limit:
                    break
        return rows

    def _duckduckgo_html(self, query: str, limit: int) -> list[SearchResult]:
        import httpx

        proxy, _ = self.config.resolved_proxy()
        response = httpx.get(
            "https://html.duckduckgo.com/html/?q=" + quote_plus(query),
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
            timeout=self.config.search_timeout,
            proxy=proxy or None,
            trust_env=False,
        )
        response.raise_for_status()
        parser = parse_page(response.text)
        rows = []
        for href, anchor in parser.links:
            if "duckduckgo.com/l/" in href:
                params = dict(parse_qsl(urlsplit(href).query))
                href = params.get("uddg", href)
            url = canonicalize_url(urljoin(str(response.url), href))
            if not url or "duckduckgo.com" in _hostname(url):
                continue
            rows.append(SearchResult(url=url, title=anchor, provider="duckduckgo_html"))
            if len(rows) >= limit:
                break
        return rows


class WebPageFetcher:
    """HTTP-first fetcher with an optional Playwright + stealth fallback."""

    def __init__(self, config: WebCrawlerDMConfig):
        import httpx

        self.config = config
        self._proxy, self._proxy_source = config.resolved_proxy()
        self._http = httpx.Client(
            headers={
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            },
            follow_redirects=True,
            timeout=config.timeout,
            proxy=self._proxy or None,
            trust_env=False,
        )
        self.cache: OrderedDict[str, FetchedPage] = OrderedDict()
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}
        # With an explicit HTTP proxy the proxy, not this process, resolves the
        # destination for the actual connection.  Re-resolving the same host
        # locally before every page therefore cannot pin the connected address
        # and can create false SSRF failures when a local DNS interceptor
        # intermittently returns a reserved sink address.  Remember only hosts
        # whose first complete local answer was public.  Direct connections are
        # deliberately revalidated on every request below.
        self._proxy_validated_public_hosts: set[str] = set()
        self._playwright = None
        self._browser = None
        self._context = None
        self._stealth = None
        self._playwright_error = ""

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._http.close()

    def browser_status(self) -> dict[str, Any]:
        playwright_available = importlib.util.find_spec("playwright") is not None
        stealth_available = importlib.util.find_spec("playwright_stealth") is not None
        return {
            "backend": self.config.browser_backend,
            "playwright_available": playwright_available,
            "stealth_requested": self.config.use_playwright_stealth,
            "stealth_available": stealth_available,
            "playwright_started": self._browser is not None,
            "playwright_error": self._playwright_error,
            "proxy_enabled": bool(self._proxy),
            "proxy": mask_proxy_url(self._proxy),
            "proxy_source": self._proxy_source,
        }

    def _validate_public_url(self, url: str) -> None:
        host = _hostname(url)
        if not host:
            raise WebCrawlerDMError(f"invalid URL: {url!r}")
        if self.config.allow_private_network:
            return
        if host == "localhost" or host.endswith(".localhost"):
            raise WebCrawlerDMError(f"private/local URL is not allowed: {url}")
        if self._proxy and host in self._proxy_validated_public_hosts:
            return
        if self._proxy:
            addresses = self._resolve_addresses_through_proxy(host)
        else:
            try:
                addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
            except socket.gaierror as exc:
                raise WebCrawlerDMError(f"cannot resolve {host}: {exc}") from None
        if not addresses:
            raise WebCrawlerDMError(f"cannot resolve {host}: no A/AAAA records")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise WebCrawlerDMError(f"non-public address is not allowed: {host} -> {ip}")
        if self._proxy:
            self._proxy_validated_public_hosts.add(host)

    def _resolve_addresses_through_proxy(self, host: str) -> set[str]:
        """Resolve with public DoH over the same proxy used for page fetches.

        Local DNS is not the connection resolver in proxy mode and may return
        an interceptor sink such as ``2001::1``.  Querying DoH through the
        configured proxy aligns SSRF validation with the actual egress path
        without weakening the global-address requirement.
        """

        addresses: set[str] = set()
        errors = []
        for record_type in ("A", "AAAA"):
            try:
                response = self._http.get(
                    _PROXY_DOH_URL,
                    params={"name": host, "type": record_type},
                    headers={"Accept": "application/dns-json"},
                    timeout=min(self.config.timeout, 10.0),
                )
                response.raise_for_status()
                payload = response.json()
                if int(payload.get("Status", -1)) not in {0, 3}:
                    raise ValueError(f"DNS status {payload.get('Status')}")
                for answer in payload.get("Answer") or []:
                    if not isinstance(answer, dict) or answer.get("type") not in {1, 28}:
                        continue
                    value = str(answer.get("data") or "").strip()
                    try:
                        ipaddress.ip_address(value)
                    except ValueError:
                        continue
                    addresses.add(value)
            except Exception as exc:  # noqa: BLE001 - combine A/AAAA diagnostics
                errors.append(f"{record_type}: {type(exc).__name__}: {exc}")
        if not addresses and errors:
            raise WebCrawlerDMError(
                f"cannot resolve {host} through proxy DoH: "
                + "; ".join(errors)[:800]
            )
        return addresses

    def _wait_for_host(self, url: str) -> None:
        if self.config.request_delay <= 0:
            return
        host = _hostname(url)
        now = time.monotonic()
        wait = self.config.request_delay - (now - self._last_request.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _robots_allowed(self, url: str) -> bool:
        if not self.config.respect_robots_txt:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            robots_url = origin + "/robots.txt"
            parser = RobotFileParser()
            parser.set_url(robots_url)
            try:
                response = self._http.get(robots_url)
                if response.status_code < 400:
                    parser.parse(response.text.splitlines())
                    self._robots[origin] = parser
                else:
                    self._robots[origin] = None
            except Exception:
                self._robots[origin] = None
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.config.user_agent, url)

    def fetch(self, url: str) -> FetchedPage:
        canonical = canonicalize_url(url)
        if not canonical:
            raise WebCrawlerDMError(f"invalid HTTP(S) URL: {url!r}")
        if canonical in self.cache:
            page = self.cache.pop(canonical)
            self.cache[canonical] = page
            return page
        self._validate_public_url(canonical)
        if not self._robots_allowed(canonical):
            raise WebCrawlerDMError(f"robots.txt disallows crawling: {canonical}")

        backend = self.config.browser_backend
        errors = []
        if backend != "playwright":
            try:
                page = self._fetch_http(canonical)
                if backend == "httpx" or not self._needs_browser(page):
                    self._cache_page(canonical, page)
                    return page
                errors.append("HTTP page looked blocked or required JavaScript")
            except Exception as exc:  # noqa: BLE001 - browser fallback
                errors.append(f"httpx: {type(exc).__name__}: {exc}")
        if backend in {"auto", "playwright"}:
            try:
                page = self._fetch_playwright(canonical)
                self._cache_page(canonical, page)
                return page
            except Exception as exc:  # noqa: BLE001
                # ``_start_browser`` persists launch/runtime failures itself.
                # A per-page 403, timeout, or navigation error must not poison
                # every later fallback or overwrite its actual error evidence.
                detail = self._playwright_error or f"{type(exc).__name__}: {exc}"[:1000]
                errors.append(f"playwright: {detail}")
        raise WebCrawlerDMError(f"failed to fetch {canonical}: " + "; ".join(errors))

    def _cache_page(self, requested_url: str, page: FetchedPage) -> None:
        for key in dict.fromkeys((requested_url, page.canonical_url)):
            self.cache.pop(key, None)
            self.cache[key] = page
        while len(self.cache) > self.config.page_cache_entries:
            self.cache.popitem(last=False)

    def _fetch_http(self, url: str) -> FetchedPage:
        self._wait_for_host(url)
        last_exc = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with self._http.stream("GET", url) as response:
                    chunks = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.config.max_html_bytes:
                            raise WebCrawlerDMError(
                                f"HTML exceeds max_html_bytes={self.config.max_html_bytes}"
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    content_type = response.headers.get("content-type", "").lower()
                    if response.status_code >= 400:
                        raise WebCrawlerDMError(f"HTTP {response.status_code}")
                    if content_type and not any(
                        item in content_type for item in ("text/html", "application/xhtml+xml")
                    ):
                        raise WebCrawlerDMError(f"not an HTML response: {content_type}")
                    encoding = response.encoding or "utf-8"
                    html = body.decode(encoding, "replace")
                    parser = parse_page(html)
                    title, preview, canonical_hint = parser.summary()
                    final_url = canonicalize_url(str(response.url)) or url
                    return FetchedPage(
                        requested_url=url,
                        final_url=final_url,
                        html=html,
                        title=title,
                        text_preview=preview,
                        status=response.status_code,
                        content_type=content_type or "text/html",
                        headers={
                            key: response.headers[key]
                            for key in ("content-language", "etag", "last-modified")
                            if key in response.headers
                        },
                        fetch_mode="httpx",
                        canonical_hint=urljoin(final_url, canonical_hint) if canonical_hint else "",
                    )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < self.config.max_retries:
                    time.sleep(min(2.0, 0.25 * (2 ** attempt)))
        raise last_exc or WebCrawlerDMError("unknown HTTP fetch failure")

    @staticmethod
    def _needs_browser(page: FetchedPage) -> bool:
        # Bot-marker strings inside executable/config markup are not evidence
        # that the delivered page is a challenge. Wikipedia, for example,
        # embeds an hCaptcha edit setting in otherwise complete article HTML.
        # Restrict marker checks to parsed visible evidence; retain the
        # low-visible-text/script heuristic for actual JavaScript shells.
        lowered = f"{page.title} {page.text_preview}".lower()
        if any(marker in lowered for marker in _BOT_MARKERS):
            return True
        visible = len(re.sub(r"\s+", " ", page.text_preview))
        return visible < 180 and "<script" in page.html.lower()

    def _start_browser(self) -> None:
        if self._browser is not None:
            return
        if self._playwright_error:
            raise WebCrawlerDMError(self._playwright_error)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise WebCrawlerDMError(
                "Playwright is not installed; install playwright and run "
                "`playwright install chromium`"
            ) from None
        try:
            self._playwright = sync_playwright().start()
            launch_kwargs: dict[str, Any] = {
                "headless": self.config.headless,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            proxy_settings = playwright_proxy_settings(self._proxy)
            if proxy_settings:
                launch_kwargs["proxy"] = proxy_settings
            process_env = playwright_process_env()
            if process_env is not None:
                launch_kwargs["env"] = process_env
            self._browser = self._playwright.chromium.launch(**launch_kwargs)
            self._context = self._browser.new_context(
                user_agent=self.config.user_agent,
                locale="en-US",
                viewport={"width": 1440, "height": 1000},
            )
            if self.config.use_playwright_stealth:
                try:
                    from playwright_stealth import Stealth

                    self._stealth = Stealth(
                        navigator_user_agent_override=self.config.user_agent
                    )
                    self._stealth.apply_stealth_sync(self._context)
                except ImportError:
                    self._stealth = None
        except Exception as exc:  # noqa: BLE001 - cache unavailable browser
            self._playwright_error = f"{type(exc).__name__}: {exc}"[:1000]
            for obj in (self._context, self._browser):
                try:
                    if obj is not None:
                        obj.close()
                except Exception:
                    pass
            self._context = None
            self._browser = None
            raise WebCrawlerDMError(self._playwright_error) from exc

    def _fetch_playwright(self, url: str) -> FetchedPage:
        self._start_browser()
        self._wait_for_host(url)
        page = self._context.new_page()
        stealth_applied = bool(self._stealth)
        if self.config.use_playwright_stealth and self._stealth is None:
            try:
                from playwright_stealth import stealth_sync

                stealth_sync(page)
                stealth_applied = True
            except ImportError:
                pass
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(self.config.timeout * 1000),
            )
            status = response.status if response is not None else 200
            if status >= 400:
                raise WebCrawlerDMError(f"HTTP {status}")
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            html = page.content()
            if len(html.encode("utf-8", "ignore")) > self.config.max_html_bytes:
                raise WebCrawlerDMError(
                    f"HTML exceeds max_html_bytes={self.config.max_html_bytes}"
                )
            final_url = canonicalize_url(page.url) or url
            parser = parse_page(html)
            title, preview, canonical_hint = parser.summary()
            headers = response.headers if response is not None else {}
            return FetchedPage(
                requested_url=url,
                final_url=final_url,
                html=html,
                title=title or page.title(),
                text_preview=preview,
                status=status,
                content_type=headers.get("content-type", "text/html"),
                headers={
                    key: headers[key]
                    for key in ("content-language", "etag", "last-modified")
                    if key in headers
                },
                fetch_mode="playwright",
                stealth_applied=stealth_applied,
                canonical_hint=urljoin(final_url, canonical_hint) if canonical_hint else "",
            )
        finally:
            page.close()


class WebCrawlerTools:
    """Tools exposed to the model, including the terminal root-URL tool."""

    tool_names = (
        "search_web", "open_page", "extract_related_urls", "submit_resource_urls"
    )

    def __init__(
        self,
        query: str,
        config: WebCrawlerDMConfig,
        search_client: WebSearchClient,
        fetcher: WebPageFetcher,
        *,
        root: str = "",
    ):
        self.query = query
        self.config = config
        self.search_client = search_client
        self.fetcher = fetcher
        self.root = root
        self.known: dict[str, dict[str, Any]] = {}
        self.opened: dict[str, FetchedPage] = {}
        self.submitted_url = ""
        self.submitted_urls: list[str] = []
        self.submission_reason = ""
        self.search_summary = {
            "enabled": bool(config.search_llm_summary),
            "attempted": 0,
            "enriched": 0,
            "model": config.model or "",
            "error": "",
        }

    def execute(self, tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        if tool == "search_web":
            return self.search_web(str(args.get("query") or self.query), args.get("max_results"))
        if tool == "open_page":
            return self.open_page(str(args.get("url") or ""))
        if tool == "extract_related_urls":
            return self.extract_related_urls(str(args.get("url") or ""))
        if tool == "submit_resource_urls":
            return self.submit_resource_urls(
                args.get("urls"), str(args.get("reason") or "")
            )
        # Compatibility for persisted traces and third-party callers.  The
        # model-facing contract advertises only the plural terminal tool.
        if tool == "submit_resource_url":
            return self.submit_resource_url(
                str(args.get("url") or ""), str(args.get("reason") or "")
            )
        return {"ok": False, "error": f"unknown tool {tool!r}", "allowed": self.tool_names}

    def search_web(self, query: str, max_results: Any = None) -> dict[str, Any]:
        try:
            limit = int(max_results) if max_results is not None else self.config.search_results
        except (TypeError, ValueError):
            limit = self.config.search_results
        rows = self.search_client.search(query, max(1, min(limit, 20)))
        provider = (
            self.search_client.primary_provider()
            if hasattr(self.search_client, "primary_provider")
            else str(rows[0].provider if rows else self.config.search_provider)
        )
        summary_reports = []
        if self.config.search_llm_summary and provider in _LLM_SUMMARY_PROVIDERS:
            self._summarize_search_results(query, rows)
            summary_reports.append(dict(self.search_summary))

        def has_rubric_inclusions(values: list[SearchResult]) -> bool:
            return any(
                row.rubric_decision in {"core", "supporting"}
                for row in values
            )

        # A non-empty provider response is not automatically useful. In auto
        # and Tavily modes, continue the direct-search chain when the LLM rubric
        # finds no core/supporting URL, while retaining every candidate and its
        # evidence for the main Agent.
        if (
            self.config.search_llm_summary
            and provider in _LLM_SUMMARY_PROVIDERS
            and self.config.search_provider in {"auto", "tavily"}
            and not has_rubric_inclusions(rows)
            and hasattr(self.search_client, "search_provider")
        ):
            attempted = {
                str(item.get("provider") or "")
                for item in getattr(self.search_client, "last_attempts", [])
            }
            seen_urls = {row.url for row in rows}
            for fallback_provider in ("bing", "baidu", "duckduckgo_html"):
                if fallback_provider in attempted:
                    continue
                try:
                    extra = self.search_client.search_provider(
                        query,
                        fallback_provider,
                        max(1, min(limit, 20)),
                    )
                    if not extra:
                        raise WebCrawlerDMError("no_results")
                    self.search_client.last_attempts.append({
                        "provider": fallback_provider,
                        "ok": True,
                        "results": len(extra),
                        "continued_by": "llm_rubric_no_inclusions",
                    })
                    self.search_client.last_provider = fallback_provider
                    provider = fallback_provider
                    self._summarize_search_results(query, extra)
                    summary_reports.append(dict(self.search_summary))
                    for row in extra:
                        if row.url not in seen_urls:
                            seen_urls.add(row.url)
                            rows.append(row)
                    if has_rubric_inclusions(rows):
                        break
                except Exception as exc:  # noqa: BLE001 - continue provider chain
                    self.search_client.last_attempts.append({
                        "provider": fallback_provider,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"[:1000],
                        "continued_by": "llm_rubric_no_inclusions",
                    })

        if summary_reports:
            errors = [
                str(report.get("error") or "")
                for report in summary_reports
                if report.get("error")
            ]
            self.search_summary = {
                "enabled": True,
                "attempted": sum(int(report.get("attempted") or 0) for report in summary_reports),
                "enriched": sum(int(report.get("enriched") or 0) for report in summary_reports),
                "model": self.config.model or "",
                "error": "; ".join(errors)[:1000],
            }
        for row in rows:
            candidate = {
                "url": row.url,
                "title": row.title[:300],
                "snippet": (row.summary or row.snippet)[:600],
                "search_snippet": row.search_snippet[:600],
                "source": "search",
                "rank": row.rank,
                "provider": row.provider,
                "llm_enriched": row.llm_enriched,
                "relevance_score": row.relevance_score,
                "relevant": row.relevant,
                "rubric_scores": dict(row.rubric_scores),
                "rubric_evidence": dict(row.rubric_evidence),
                "rubric_decision": row.rubric_decision,
                "decision_reason": row.decision_reason,
            }
            previous = self.known.get(row.url) or {}
            decision_priority = {
                "core": 0,
                "supporting": 1,
                "uncertain": 2,
                "exclude": 3,
            }
            if previous and decision_priority.get(
                str(previous.get("rubric_decision") or "uncertain"), 2
            ) < decision_priority.get(row.rubric_decision, 2):
                # Inclusion evidence from any search pass cannot be erased by
                # one later, narrower rubric assessment.
                for key in (
                    "llm_enriched", "relevance_score", "relevant",
                    "rubric_scores", "rubric_evidence", "rubric_decision",
                    "decision_reason",
                ):
                    candidate[key] = previous.get(key)
            self.known[row.url] = candidate
        return {
            "ok": True,
            "query": query,
            "provider": provider,
            "provider_attempts": list(
                getattr(self.search_client, "last_attempts", [])
            ),
            "llm_summary": dict(self.search_summary),
            "results": [row.to_dict() for row in rows],
        }

    def _summarize_search_results(
        self,
        query: str,
        rows: list[SearchResult],
    ) -> None:
        selected = rows[: self.config.search_summary_results]
        self.search_summary = {
            "enabled": True,
            "attempted": len(selected),
            "enriched": 0,
            "model": self.config.model or "",
            "error": "",
        }
        if not selected:
            return
        try:
            model_spec = (
                ModelPool(self.root).get(self.config.model)
                if self.config.model else None
            )
        except (KeyError, ValueError, OSError) as exc:
            model_spec = None
            self.search_summary["error"] = (
                f"model resolution failed: {exc}"[:500]
            )
        if model_spec is None:
            error = (
                self.search_summary["error"]
                or "no model configured for search summary"
            )
            self.search_summary["error"] = error
            for row in selected:
                row.summary_error = error
            return

        evidence_rows = []
        for index, row in enumerate(selected):
            row.search_snippet = row.search_snippet or row.snippet
            page_text = ""
            fetch_error = ""
            try:
                page = self.fetcher.fetch(row.url)
                row.fetch_mode = page.fetch_mode
                row.url = page.canonical_url or row.url
                row.title = page.title or row.title
                page_text = page.text_preview[: self.config.search_summary_chars]
            except Exception as exc:  # noqa: BLE001 - snippet-only fallback
                row.fetch_mode = "search_snippet"
                fetch_error = f"{type(exc).__name__}: {exc}"[:500]
            evidence_rows.append({
                "index": index,
                "title": row.title[:500],
                "url": row.url,
                "search_snippet": row.search_snippet[:1200],
                "page_text": page_text,
            })
            if fetch_error:
                row.summary_error = (
                    "page fetch failed; summarized search snippet: " + fetch_error
                )

        system_content = (
            "You are a grounded web-search result summarizer and URL rubric "
            "grader. Use only the supplied search snippet and webpage text. "
            "Write a concise summary in the query's language and score five "
            "independent dimensions from 1 to 5 with grounded evidence: "
            "query_coverage, source_authority, content_substance, crawl_yield, "
            "and complementary_value. Scale anchors: 1=absent or conflicting, "
            "2=weak, 3=usable, 4=strong, 5=primary or exceptional. Make a "
            "holistic decision: core, supporting, exclude, or uncertain. One "
            "low score is never sufficient for exclude; exclude requires at "
            "least two independently weak dimensions and a grounded reason. "
            "Fetch failure is not a relevance dimension. Generic homepages, "
            "entity background, lexical matches, and thin pages must be judged "
            "through all five dimensions, never by one aggregate score. Never "
            "invent facts. Return one JSON object with a results array containing "
            "exactly the supplied index."
        )
        rubric_schema = {
            dimension: {"score": 1, "evidence": "grounded evidence"}
            for dimension in _RUBRIC_DIMENSIONS
        }
        errors = []
        # Keep each response below the configured model's 1024-token cap. A
        # truncated multi-result JSON response must not erase good evidence for
        # the other candidates.
        for evidence_row, row in zip(evidence_rows, selected):
            index = int(evidence_row["index"])
            messages = [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query": query,
                            "required_schema": {
                                "results": [{
                                    "index": index,
                                    "summary": "grounded summary",
                                    "rubric": rubric_schema,
                                    "decision": "uncertain",
                                    "decision_reason": "holistic rubric reason",
                                }]
                            },
                            "evidence": [evidence_row],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            try:
                raw = llm.complete(
                    model_spec,
                    messages,
                    json_mode=True,
                    max_retries=self.config.max_retries,
                )
                payload = llm.parse_json(raw)
                summaries = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(summaries, list):
                    raise ValueError("LLM summary response has no results array")
                item = next(
                    (
                        value for value in summaries
                        if isinstance(value, dict)
                        and str(value.get("index", "")) == str(index)
                    ),
                    summaries[0] if len(summaries) == 1 and isinstance(summaries[0], dict) else None,
                )
                if not item:
                    raise ValueError("LLM omitted this result")
                summary = re.sub(
                    r"\s+", " ", str(item.get("summary") or "")
                ).strip()
                if not summary:
                    raise ValueError("LLM returned an empty summary")
                scores, rubric_evidence, decision, decision_reason = _normalize_rubric(item)
                row.summary = summary[:1600]
                row.rubric_scores = scores
                row.rubric_evidence = rubric_evidence
                row.rubric_decision = decision
                row.decision_reason = decision_reason
                row.relevant = (
                    True if decision in {"core", "supporting"}
                    else False if decision == "exclude"
                    else None
                )
                row.llm_enriched = True
                row.summary_model = model_spec.name or model_spec.model
                self.search_summary["enriched"] += 1
            except Exception as exc:  # noqa: BLE001 - isolate one candidate
                error = f"{type(exc).__name__}: {exc}"[:500]
                errors.append(f"{index}: {error}")
                row.summary_error = (
                    row.summary_error + "; " if row.summary_error else ""
                ) + error
        if errors:
            self.search_summary["error"] = "; ".join(errors)[:1000]

    def open_page(self, url: str) -> dict[str, Any]:
        canonical = canonicalize_url(url)
        if not canonical:
            return {"ok": False, "error": f"invalid URL: {url!r}"}
        try:
            page = self.fetcher.fetch(canonical)
        except Exception as exc:  # noqa: BLE001 - observation for the agent
            return {"ok": False, "url": canonical, "error": str(exc)[:800]}
        self.opened[page.canonical_url] = page
        self.known.setdefault(page.canonical_url, {
            "url": page.canonical_url,
            "title": page.title,
            "snippet": page.text_preview[:800],
            "source": "opened",
        })
        return {
            "ok": True,
            "url": page.canonical_url,
            "title": page.title,
            "status": page.status,
            "content_type": page.content_type,
            "fetch_mode": page.fetch_mode,
            "stealth_applied": page.stealth_applied,
            "text_preview": page.text_preview[:1800],
            "html_chars": len(page.html),
        }

    def extract_related_urls(self, url: str) -> dict[str, Any]:
        canonical = canonicalize_url(url)
        page = self.opened.get(canonical) or self.fetcher.cache.get(canonical)
        if page is None:
            opened = self.open_page(canonical)
            if not opened.get("ok"):
                return opened
            page = self.opened.get(opened["url"]) or self.fetcher.cache.get(canonical)
        links = extract_related_links(
            page,
            self.query,
            max_links=self.config.max_links_per_page,
            same_domain_only=self.config.same_domain_only,
        )
        for link in links:
            self.known.setdefault(link.url, {
                "url": link.url,
                "title": link.anchor,
                "snippet": "",
                "source": "crawler_tool",
                "parent_url": page.canonical_url,
                "score": link.score,
            })
        return {
            "ok": True,
            "url": page.canonical_url,
            "related_urls": [link.to_dict() for link in links],
            "count": len(links),
        }

    def submit_resource_urls(
        self,
        urls: Any,
        reason: str = "",
    ) -> dict[str, Any]:
        if not isinstance(urls, (list, tuple)) or not urls:
            return {
                "ok": False,
                "error": "urls must be a non-empty array of discovered relevant URLs",
            }
        submitted = []
        invalid = []
        undiscovered = []
        for value in urls:
            canonical = canonicalize_url(str(value or ""))
            if not canonical:
                invalid.append(str(value or ""))
                continue
            if canonical not in self.known and canonical not in self.opened:
                undiscovered.append(canonical)
                continue
            if canonical not in submitted:
                submitted.append(canonical)
        if invalid or undiscovered:
            return {
                "ok": False,
                "error": "every submitted URL must be valid and discovered by a tool",
                "invalid_urls": invalid,
                "undiscovered_urls": undiscovered,
            }

        explicitly_excluded = [
            url for url in submitted
            if bool((self.known.get(url) or {}).get("llm_enriched"))
            and (self.known.get(url) or {}).get("rubric_decision") == "exclude"
        ]
        if explicitly_excluded:
            return {
                "ok": False,
                "error": "multi-dimensional LLM rubric excludes submitted URLs",
                "excluded_urls": explicitly_excluded,
            }
        required_by_rubric = {
            url for url, row in self.known.items()
            if bool(row.get("llm_enriched"))
            and row.get("rubric_decision") in {"core", "supporting"}
        }
        missing = sorted(required_by_rubric.difference(submitted))
        if missing:
            return {
                "ok": False,
                "error": "submit every URL included by the multi-dimensional LLM rubric",
                "missing_rubric_urls": missing,
            }

        self.submitted_urls = submitted
        self.submitted_url = submitted[0]
        self.submission_reason = reason[:1000]
        return {
            "ok": True,
            "submitted": True,
            "urls": list(self.submitted_urls),
            "count": len(self.submitted_urls),
            "reason": self.submission_reason,
        }

    def submit_resource_url(self, url: str, reason: str = "") -> dict[str, Any]:
        """Backward-compatible single-root wrapper around the plural tool."""

        result = self.submit_resource_urls([url], reason)
        if result.get("submitted"):
            result["url"] = self.submitted_url
        return result

    def candidate_snapshot(self, limit: int = 80) -> list[dict[str, Any]]:
        rows = list(self.known.values())
        decision_priority = {
            "core": 0,
            "supporting": 1,
            "uncertain": 2,
            "exclude": 3,
        }
        rows.sort(key=lambda row: (
            decision_priority.get(
                str(row.get("rubric_decision") or "uncertain"), 2
            ),
            int(row.get("rank") or 9999),
            str(row.get("url") or ""),
        ))
        return [
            {
                **row,
                "title": str(row.get("title") or "")[:220],
                "snippet": str(row.get("snippet") or "")[:320],
            }
            for row in rows[:limit]
        ]


_AGENT_SYSTEM_PROMPT = """You are the Domain Data Acquisition web agent (legacy alias: webcrawler_dm), a focused resource-page discovery agent.

Your sole goal is to find ALL authoritative, content-rich resource pages that are relevant to the user's query. Prefer first-party or otherwise reputable sources appropriate to the topic. Avoid search result pages, generic home pages, login pages, tag/category pages, social media, lexical coincidences, and thin aggregators.

You have exactly four tools:
1. search_web(query, max_results): search for candidate pages.
2. open_page(url): inspect a candidate page.
3. extract_related_urls(url): the special crawler tool; extract relevant links from the current page so you can find a more specific resource page.
4. submit_resource_urls(urls, reason): the ONLY valid way to finish and return every relevant root URL.

Return only one JSON object per step:
{"tool":"<tool name>","arguments":{...},"reason":"short reason"}

Rules:
- Use only URLs discovered through the tools.
- The state includes the active search_provider. Rewrite each search query for that provider and its target ecosystem instead of copying the user's wording.
- For global code repositories and other global technical ecosystems, use concise English discovery terms. For other ecosystems, use the terminology and language that their search index is most likely to understand.
- Keep provider queries concise and search-ready; put investigation details in your internal reason, not in the query.
- The state includes remaining_search_calls. When it reaches zero, inspect or submit existing candidates instead of searching again.
- Treat every core concept in the user's query as mandatory. Judge the title, snippet, URL, five-dimensional LLM rubric, and opened-page evidence together before selecting resources.
- Reject lexical coincidences and pages whose actual purpose is unrelated, even when one or more query words appear in their metadata.
- Use provider-native search syntax or qualifiers when they help preserve all mandatory concepts.
- Never accept or reject a URL from one aggregate relevance score. Use all rubric dimensions: query_coverage, source_authority, content_substance, crawl_yield, and complementary_value. One weak dimension is not enough to reject a candidate.
- Submit every discovered candidate supported as relevant by the holistic evidence. In particular, include every LLM-enriched candidate whose rubric_decision is core or supporting; inspect uncertain candidates with page evidence when useful.
- Never submit a candidate whose rubric_decision is exclude or one that you judge unrelated merely to finish. A failed run is preferable to contaminating the dataset.
- Fetchability is not relevance. A discovered authoritative URL may still be submitted when open_page is blocked but its grounded search evidence establishes relevance; the crawler will record its fetch failure independently.
- Inspect enough evidence before submitting.
- Use extract_related_urls when the current page is an index, documentation root, or collection page.
- Never answer the user's topic question and never return prose instead of a tool call.
- Finish by calling submit_resource_urls with one deduplicated array containing all relevant roots. A plain URL, a field named `url`, or a partial relevant set is not a valid final answer.
"""


class ToolCallingWebAgentKernel:
    """Bounded browser-use-style observe/action/tool loop."""

    def __init__(self, config: WebCrawlerDMConfig, *, root: str):
        self.config = config
        self.model_spec = ModelPool(root).get(config.model) if config.model else None

    def discover(
        self,
        query: str,
        tools: WebCrawlerTools,
    ) -> tuple[list[str], list[dict[str, Any]], int]:
        if self.model_spec is None:
            raise WebCrawlerDMError(
                "LLM model is required; deterministic URL fallback is disabled"
            )
        trace: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        seen_actions: set[str] = set()
        tool_counts: dict[str, int] = {}
        terminal_tools = {"submit_resource_urls", "submit_resource_url"}
        for step in range(1, self.config.max_steps + 1):
            state = {
                "query": query,
                "search_provider": (
                    tools.search_client.primary_provider()
                    if hasattr(tools.search_client, "primary_provider")
                    else self.config.search_provider
                ),
                "search_provider_mode": self.config.search_provider,
                "remaining_search_calls": max(
                    0,
                    self.config.max_search_calls
                    - tool_counts.get("search_web", 0),
                ),
                "step": step,
                "max_steps": self.config.max_steps,
                "remaining_steps": self.config.max_steps - step + 1,
                "known_candidates": tools.candidate_snapshot(),
                "recent_tool_observations": recent[-6:],
                "must_finish_with": "submit_resource_urls",
                "must_submit_all_relevant": True,
                "soft_step_limit_reached": step >= self.config.soft_step_limit,
            }
            messages = [
                {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(state, ensure_ascii=False)},
            ]
            try:
                raw = llm.complete(
                    self.model_spec,
                    messages,
                    json_mode=True,
                    max_retries=self.config.max_retries,
                )
                action = llm.parse_json(raw)
                tool = str(action.get("tool") or "")
                arguments = action.get("arguments")
                reason = str(action.get("reason") or "")
                if not isinstance(arguments, dict):
                    arguments = {}
                if reason and tool in terminal_tools and not arguments.get("reason"):
                    arguments["reason"] = reason
                action_key = json.dumps(
                    {"tool": tool, "arguments": arguments},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if (
                    tool == "search_web"
                    and tool_counts.get("search_web", 0) >= self.config.max_search_calls
                ):
                    observation = {
                        "ok": False,
                        "error": (
                            "search call limit reached; inspect or submit an existing "
                            "candidate"
                        ),
                    }
                elif action_key in seen_actions and tool not in terminal_tools:
                    observation = {
                        "ok": False,
                        "error": "duplicate tool action; choose a new URL/tool or submit the best resource",
                    }
                else:
                    seen_actions.add(action_key)
                    observation = tools.execute(tool, arguments)
            except Exception as exc:  # noqa: BLE001 - bounded agent recovery
                tool = "agent_error"
                arguments = {}
                reason = ""
                observation = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:1000]}
            compact = _compact_observation(observation, self.config.max_observation_chars)
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            trace.append({
                "step": step,
                "tool": tool,
                "arguments": _redact_arguments(arguments),
                "reason": reason[:500],
                "observation": compact,
            })
            recent.append({"tool": tool, "observation": compact})
            if tool in terminal_tools and observation.get("submitted"):
                return list(tools.submitted_urls), trace, step
        raise WebCrawlerDMError(
            f"LLM agent exhausted max_steps={self.config.max_steps} without "
            "an explicit submit_resource_urls call; deterministic fallback is disabled"
        )


def _compact_observation(value: dict[str, Any], limit: int) -> dict[str, Any]:
    raw = json.dumps(value, ensure_ascii=False)
    if len(raw) <= limit:
        return value
    compact = dict(value)
    for key in ("text_preview", "snippet"):
        if isinstance(compact.get(key), str):
            compact[key] = compact[key][:500]
    for key in ("results", "related_urls"):
        if isinstance(compact.get(key), list):
            compact[key] = compact[key][:5]
    raw = json.dumps(compact, ensure_ascii=False)
    if len(raw) <= limit:
        return compact
    return {"ok": value.get("ok", False), "summary": raw[:limit], "truncated": True}


def _redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("***" if "key" in key.lower() or "token" in key.lower() else value)
        for key, value in arguments.items()
    }


@register("webcrawler_dm")
class WebCrawlerDMAgent(WebAgent):
    """Registered DataMixer input agent producing raw webpage L1 records."""

    spec = WebAgentSpec(
        name="webcrawler_dm",
        version="1.1.0",
        description=(
            "LLM tool-calling web agent that submits every relevant resource "
            "URL, traverses related links, and ingests raw HTML as DataMixer L1."
        ),
        input_type="query",
        output_type="resource_urls+L1_raw_html",
    )

    def __init__(
        self,
        config: WebCrawlerDMConfig | None = None,
        *,
        search_client: WebSearchClient | None = None,
        fetcher: WebPageFetcher | None = None,
    ):
        self.config = config or WebCrawlerDMConfig()
        self.search_client = search_client or WebSearchClient(self.config)
        self.fetcher = fetcher or WebPageFetcher(self.config)
        self._owns_fetcher = fetcher is None

    def close(self) -> None:
        if self._owns_fetcher:
            self.fetcher.close()

    def run(
        self,
        query: str,
        *,
        store: DataStore,
        dataset: str,
        campaign_id: str = "",
        parent_query: str = "",
        subgoal_index: int | None = None,
        subgoal_metadata: dict[str, Any] | None = None,
        **kwargs,  # noqa: ARG002 - plugin contract extension point
    ) -> WebCrawlerDMResult:
        query = str(query or "").strip()
        if not query:
            raise ValueError("webcrawler_dm query must not be empty")
        run_id = "webcrawl-" + uuid.uuid4().hex[:16]
        tools = WebCrawlerTools(
            query,
            self.config,
            self.search_client,
            self.fetcher,
            root=str(store.root),
        )
        kernel = ToolCallingWebAgentKernel(self.config, root=str(store.root))
        run_dir = Path(store.root) / "webcrawler_dm_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        progress_path = run_dir / "progress.json"
        manifest = run_dir / "pages.jsonl"
        failure_manifest = run_dir / "failures.jsonl"
        self._write_progress(progress_path, {
            "run_id": run_id,
            "status": "running",
            "phase": "discover",
            "query": query,
            "max_pages": self.config.max_pages,
            "max_depth": self.config.max_depth,
            "pages_fetched": 0,
            "pages_ingested": 0,
            "pages_failed": 0,
            "failure_manifest": str(failure_manifest),
        })
        try:
            selected_urls, trace, steps = kernel.discover(query, tools)
        except Exception as exc:
            self._write_progress(progress_path, {
                "run_id": run_id,
                "status": "failed",
                "phase": "discover",
                "query": query,
                "error": f"{type(exc).__name__}: {exc}"[:2000],
            })
            raise
        selected_url = selected_urls[0]
        dataset_id = store.catalog.resolve_dataset(dataset)
        if dataset_id is None:
            dataset_id = store.catalog.add_dataset(
                name=dataset,
                source="webcrawler_dm",
                license=self.config.license,
                description="Raw HTML discovered from queries by webcrawler_dm.",
                meta={
                    "webagent": self.spec.name,
                    "webagent_version": self.spec.version,
                    "quality_level": "L1",
                },
            )
        record_domain = str((subgoal_metadata or {}).get("domain") or "web")
        ingest_defaults = {
            "quality_level": "L1",
            "modality": "text",
            "stage": "pretrain",
            "domain": record_domain,
            "source": "webcrawler_dm",
            "license": self.config.license,
            "processing_level": "raw_html",
            "source_kind": "webpage_html",
        }
        record_batch: list[dict[str, Any]] = []
        pages_ingested = 0
        streamed_pages = 0
        progress_events = 0

        def flush_records() -> None:
            nonlocal pages_ingested
            if not record_batch:
                return
            ingest = store.ingest_records(
                dataset_id,
                list(record_batch),
                defaults=ingest_defaults,
                decontaminate=False,
            )
            pages_ingested += ingest.written
            record_batch.clear()

        with (
            manifest.open("w", encoding="utf-8") as manifest_handle,
            failure_manifest.open("w", encoding="utf-8") as failure_handle,
        ):
            def on_page(
                page_row: tuple[FetchedPage, int, str, str, str],
            ) -> None:
                nonlocal streamed_pages
                page, depth, parent_url, discovered_by, root_url = page_row
                streamed_pages += 1
                manifest_handle.write(json.dumps({
                    "url": page.canonical_url,
                    "title": page.title,
                    "depth": depth,
                    "parent_url": parent_url,
                    "root_url": root_url,
                    "discovered_by": discovered_by,
                    "http_status": page.status,
                    "fetch_mode": page.fetch_mode,
                    "playwright_stealth": page.stealth_applied,
                    "html_chars": len(page.html),
                    "content_sha256": hashlib.sha256(
                        page.html.encode("utf-8", "replace")
                    ).hexdigest(),
                }, ensure_ascii=False) + "\n")
                record_batch.extend(self._records(
                    [page_row],
                    query=query,
                    selected_urls=selected_urls,
                    run_id=run_id,
                    agent_steps=steps,
                    campaign_id=campaign_id,
                    parent_query=parent_query,
                    subgoal_index=subgoal_index,
                    subgoal_metadata=subgoal_metadata,
                ))
                if len(record_batch) >= 16:
                    flush_records()

            def on_progress(progress: dict[str, Any]) -> None:
                nonlocal progress_events
                progress_events += 1
                if progress_events % 25 and progress.get("current_depth") != 0:
                    return
                self._write_progress(progress_path, {
                    "run_id": run_id,
                    "status": "running",
                    "phase": "crawl_and_ingest_l1",
                    "query": query,
                    "selected_urls": selected_urls,
                    "max_pages": self.config.max_pages,
                    "max_depth": self.config.max_depth,
                    "pages_ingested": pages_ingested,
                    "failure_manifest": str(failure_manifest),
                    **progress,
                })

            def on_failure(failure: dict[str, str]) -> None:
                failure_handle.write(
                    json.dumps(failure, ensure_ascii=False) + "\n"
                )
                # Failure evidence must survive Ctrl-C or a dead executor so a
                # large crawl can be stopped and diagnosed without reproduction.
                failure_handle.flush()

            try:
                _, failures, pages_fetched, pages_failed = self._crawl(
                    selected_urls,
                    query,
                    root=str(store.root),
                    on_page=on_page,
                    progress_callback=on_progress,
                    failure_callback=on_failure,
                    retain_pages=False,
                )
                flush_records()
            except Exception as exc:
                self._write_progress(progress_path, {
                    "run_id": run_id,
                    "status": "failed",
                    "phase": "crawl_and_ingest_l1",
                    "query": query,
                    "selected_urls": selected_urls,
                    "pages_fetched": streamed_pages,
                    "pages_ingested": pages_ingested,
                    "failure_manifest": str(failure_manifest),
                    "error": f"{type(exc).__name__}: {exc}"[:2000],
                })
                raise
        result = WebCrawlerDMResult(
            run_id=run_id,
            query=query,
            selected_url=selected_url,
            selected_urls=selected_urls,
            submitted_by_tool=bool(tools.submitted_urls),
            agent_steps=steps,
            max_steps=self.config.max_steps,
            model=self.config.model,
            dataset=dataset,
            dataset_id=dataset_id,
            pages_fetched=pages_fetched,
            pages_ingested=pages_ingested,
            pages_failed=pages_failed,
            crawl_depth=self.config.max_depth,
            search_provider=(
                self.search_client.primary_provider()
                if hasattr(self.search_client, "primary_provider")
                else self.config.search_provider
            ),
            browser=self.fetcher.browser_status(),
            trace=trace,
            failures=failures,
            campaign_id=campaign_id,
            parent_query=parent_query,
            subgoal_index=subgoal_index,
        )
        result.lineage_path = self._write_run_artifacts(
            store,
            result,
            [],
            manifest_path=manifest,
        )
        self._write_progress(progress_path, {
            "run_id": run_id,
            "status": "completed",
            "phase": "completed",
            "query": query,
            "selected_urls": selected_urls,
            "pages_fetched": pages_fetched,
            "pages_ingested": pages_ingested,
            "pages_failed": pages_failed,
            "failure_manifest": str(failure_manifest),
            "lineage_path": result.lineage_path,
        })
        return result

    def run_many(
        self,
        queries: list[str],
        *,
        store: DataStore,
        dataset: str,
    ) -> dict[str, Any]:
        started = time.time()
        results = []
        errors = []
        try:
            for index, query in enumerate(queries):
                try:
                    result = self.run(query, store=store, dataset=dataset)
                    results.append(result.to_dict())
                except Exception as exc:  # noqa: BLE001 - isolate each input
                    errors.append({
                        "index": index,
                        "query": query,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
        finally:
            self.close()
        selected_urls = []
        for row in results:
            row_urls = row.get("selected_urls")
            if not isinstance(row_urls, list):
                row_urls = [row.get("selected_url")]
            for url in row_urls:
                if url and url not in selected_urls:
                    selected_urls.append(url)
        return {
            "webagent": self.spec.name,
            "version": self.spec.version,
            "dataset": dataset,
            "inputs": len(queries),
            "succeeded": len(results),
            "failed": len(errors),
            "selected_urls": selected_urls,
            "pages_fetched": sum(row["pages_fetched"] for row in results),
            "pages_ingested": sum(row["pages_ingested"] for row in results),
            "elapsed_s": round(time.time() - started, 3),
            "config": self.config.public_dict(),
            "results": results,
            "errors": errors,
        }

    def _crawl(
        self,
        selected_urls: list[str],
        query: str,
        *,
        root: str,
        on_page: Callable[
            [tuple[FetchedPage, int, str, str, str]], None
        ] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        failure_callback: Callable[[dict[str, str]], None] | None = None,
        retain_pages: bool = True,
    ) -> tuple[
        list[tuple[FetchedPage, int, str, str, str]],
        list[dict[str, str]],
        int,
        int,
    ]:
        del root  # Reserved for future per-lake crawl hooks.
        seeds = []
        for value in selected_urls:
            canonical = canonicalize_url(value)
            if canonical and canonical not in seeds:
                seeds.append(canonical)
        # Put every submitted root ahead of child links.  This gives each root
        # a chance to consume the shared page budget before breadth expansion.
        # If there are more successful roots than max_pages, the roots that
        # cannot be attempted are reported explicitly below instead of being
        # silently sliced out of the queue.
        queue = deque(
            (seed, 0, "", "agent_submit", seed)
            for seed in seeds
        )
        queued = set(seeds)
        visited: set[str] = set()
        materialized: set[str] = set()
        fetched_count = 0
        pages: list[tuple[FetchedPage, int, str, str, str]] = []
        failures: list[dict[str, str]] = []
        failure_count = 0
        while queue and fetched_count < self.config.max_pages:
            url, depth, parent_url, discovered_by, root_url = queue.popleft()
            if not url or url in visited:
                continue
            visited.add(url)
            try:
                page = self.fetcher.fetch(url)
            except Exception as exc:  # noqa: BLE001 - per-page isolation
                failure_count += 1
                failure = {
                    "url": url,
                    "root_url": root_url,
                    "error": str(exc)[:1000],
                }
                if len(failures) < 5000:
                    failures.append(failure)
                if failure_callback:
                    failure_callback(failure)
                if progress_callback:
                    progress_callback({
                        "pages_fetched": fetched_count,
                        "pages_failed": failure_count,
                        "queue_size": len(queue),
                        "visited": len(visited),
                        "current_url": url,
                        "current_depth": depth,
                    })
                continue
            canonical = page.canonical_url
            if canonical in materialized:
                continue
            materialized.add(canonical)
            fetched_count += 1
            page_row = (page, depth, parent_url, discovered_by, root_url)
            if retain_pages:
                pages.append(page_row)
            if on_page:
                on_page(page_row)
            if progress_callback:
                progress_callback({
                    "pages_fetched": fetched_count,
                    "pages_failed": failure_count,
                    "queue_size": len(queue),
                    "visited": len(visited),
                    "current_url": canonical,
                    "current_depth": depth,
                })
            if depth >= self.config.max_depth:
                continue
            try:
                links = self._collect_crawl_links(page, query)
            except Exception as exc:  # noqa: BLE001 - preserve page and continue
                failure_count += 1
                failure = {
                    "url": canonical,
                    "root_url": root_url,
                    "error": f"related-link extraction: {type(exc).__name__}: {exc}"[:1000],
                }
                if len(failures) < 5000:
                    failures.append(failure)
                if failure_callback:
                    failure_callback(failure)
                links = []
            for link in links:
                if fetched_count + len(queue) >= self.config.max_pages:
                    break
                if link.url in visited or link.url in queued:
                    continue
                queued.add(link.url)
                queue.append((
                    link.url,
                    depth + 1,
                    canonical,
                    "crawler_tool",
                    root_url,
                ))
        for seed in seeds:
            if seed not in visited:
                failure_count += 1
                failure = {
                    "url": seed,
                    "root_url": seed,
                    "error": (
                        "crawl page budget exhausted before submitted root "
                        "could be fetched"
                    ),
                }
                if len(failures) < 5000:
                    failures.append(failure)
                if failure_callback:
                    failure_callback(failure)
        return pages, failures, fetched_count, failure_count

    def _collect_crawl_links(
        self,
        page: FetchedPage,
        query: str,
    ) -> list[LinkCandidate]:
        """Return every bounded, crawl-safe link for downstream DataMixer grading."""

        return extract_related_links(
            page,
            query,
            max_links=self.config.max_links_per_page,
            same_domain_only=self.config.same_domain_only,
        )

    def _records(
        self,
        pages: list[tuple[FetchedPage, int, str, str, str]],
        *,
        query: str,
        selected_urls: list[str],
        run_id: str,
        agent_steps: int,
        campaign_id: str = "",
        parent_query: str = "",
        subgoal_index: int | None = None,
        subgoal_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        record_domain = str((subgoal_metadata or {}).get("domain") or "web")
        canonical_roots = [
            canonicalize_url(url) for url in selected_urls if canonicalize_url(url)
        ]
        records = []
        for page, depth, parent_url, discovered_by, root_url in pages:
            html_sha = hashlib.sha256(page.html.encode("utf-8", "replace")).hexdigest()
            record = {
                "content": {
                    "html": page.html,
                    "url": page.canonical_url,
                    "title": page.title,
                    "http_status": page.status,
                    "content_type": page.content_type,
                    "document_type": "webpage_raw_html",
                },
                "quality_level": "L1",
                "domain": record_domain,
                "source_uri": page.canonical_url,
                "query": query,
                "crawl_depth": depth,
                "parent_url": parent_url,
                "root_url": canonicalize_url(root_url),
                "selected_resource_url": canonicalize_url(root_url),
                "selected_resource_urls": canonical_roots,
                "discovered_by": discovered_by,
                "retrieved_at": retrieved_at,
                "http_status": page.status,
                "content_type": page.content_type,
                "canonical_url": page.canonical_url,
                "fetch_mode": page.fetch_mode,
                "playwright_stealth": page.stealth_applied,
                "response_headers": page.headers,
                "content_sha256": html_sha,
                "agent_run_id": run_id,
                "agent_steps": agent_steps,
                "webagent": self.spec.name,
                "webagent_version": self.spec.version,
            }
            if campaign_id:
                record.update({
                    "campaign_id": campaign_id,
                    "parent_query": parent_query,
                    "subgoal_index": subgoal_index,
                    "subgoal_metadata": subgoal_metadata or {},
                })
            records.append(record)
        return records

    @staticmethod
    def _write_progress(path: Path, payload: dict[str, Any]) -> None:
        payload = {
            **payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _write_run_artifacts(
        store: DataStore,
        result: WebCrawlerDMResult,
        pages: list[tuple[FetchedPage, int, str, str, str]],
        *,
        manifest_path: Path | None = None,
    ) -> str:
        run_dir = Path(store.root) / "webcrawler_dm_runs" / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = manifest_path or (run_dir / "pages.jsonl")
        if manifest_path is None:
            with manifest.open("w", encoding="utf-8") as handle:
                for page, depth, parent_url, discovered_by, root_url in pages:
                    handle.write(json.dumps({
                        "url": page.canonical_url,
                        "title": page.title,
                        "depth": depth,
                        "parent_url": parent_url,
                        "root_url": root_url,
                        "discovered_by": discovered_by,
                        "http_status": page.status,
                        "fetch_mode": page.fetch_mode,
                        "playwright_stealth": page.stealth_applied,
                        "html_chars": len(page.html),
                        "content_sha256": hashlib.sha256(
                            page.html.encode("utf-8", "replace")
                        ).hexdigest(),
                    }, ensure_ascii=False) + "\n")
        lineage_dir = Path(store.root) / "lineage"
        lineage_dir.mkdir(exist_ok=True)
        lineage = lineage_dir / f"{result.run_id}.json"
        result.lineage_path = str(lineage)
        doc = result.to_dict()
        doc.update({
            "kind": "webagent_crawl",
            "quality_level": "L1",
            "manifest": str(manifest),
        })
        lineage.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return str(lineage)


@register("domain_data_acquisition")
class DomainDataAcquisitionAgent(WebCrawlerDMAgent):
    """Primary data-acquisition name for the legacy ``webcrawler_dm`` agent.

    The implementation remains identical and ``webcrawler_dm`` stays
    registered for old campaigns. The new name makes the intended role clear
    to outer planners: collect authoritative vertical-domain source pages as
    L1 inputs alongside hosted-dataset discovery.
    """

    spec = WebAgentSpec(
        name="domain_data_acquisition",
        version="1.1.0",
        description=(
            "Domain data-acquisition web agent: submits all LLM-relevant vertical "
            "resource pages, traverses related links, and ingests raw HTML as "
            "DataMixer L1."
        ),
        input_type="domain_query",
        output_type="resource_urls+L1_raw_html",
    )
