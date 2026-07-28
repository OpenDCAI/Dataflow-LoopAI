"""Query -> resource URL -> depth-two raw HTML -> DataMixer L1.

The small observe/action/tool loop is inspired by the MIT-licensed
``browser-use`` project (https://github.com/browser-use/browser-use), but is
implemented locally so DataMixer does not inherit that project's large runtime
surface.  The terminal action is deliberately a tool call:
``submit_resource_url``.  A prose answer from the model can never complete a
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
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
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
        self.request_delay = max(0.0, float(self.request_delay))
        self.timeout = max(1.0, float(self.timeout))
        self.max_retries = max(0, int(self.max_retries))
        self.max_html_bytes = max(16_384, int(self.max_html_bytes))
        if self.search_provider not in {
            "auto", "bing", "github", "tavily", "duckduckgo_html"
        }:
            raise ValueError(
                "search_provider must be auto, bing, github, tavily, or duckduckgo_html"
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        limit = max_results or self.config.search_results
        provider = self.primary_provider()
        errors = []
        providers = [provider]
        if self.config.search_provider == "auto":
            providers.extend(
                p for p in ("bing", "duckduckgo_html") if p != provider
            )
        for item in providers:
            try:
                if item == "tavily":
                    rows = self._tavily(query, limit)
                elif item == "bing":
                    rows = self._bing(query, limit)
                elif item == "github":
                    rows = self._github(query, limit)
                else:
                    rows = self._duckduckgo_html(query, limit)
                if rows:
                    deduped = self._dedup(rows, limit)
                    if deduped:
                        return deduped
            except Exception as exc:  # noqa: BLE001 - provider fallback
                errors.append(f"{item}: {type(exc).__name__}: {exc}")
        detail = "; ".join(errors) if errors else f"no results from {', '.join(providers)}"
        raise WebCrawlerDMError("web search failed: " + detail)

    def primary_provider(self) -> str:
        if self.config.search_provider != "auto":
            return self.config.search_provider
        return "tavily" if self.config.tavily_api_key else "bing"

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
        response.raise_for_status()
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
        self.cache: dict[str, FetchedPage] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}
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
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
        except socket.gaierror as exc:
            raise WebCrawlerDMError(f"cannot resolve {host}: {exc}") from None
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise WebCrawlerDMError(f"non-public address is not allowed: {host} -> {ip}")

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
            return self.cache[canonical]
        self._validate_public_url(canonical)
        if not self._robots_allowed(canonical):
            raise WebCrawlerDMError(f"robots.txt disallows crawling: {canonical}")

        backend = self.config.browser_backend
        errors = []
        if backend != "playwright":
            try:
                page = self._fetch_http(canonical)
                if backend == "httpx" or not self._needs_browser(page):
                    self.cache[canonical] = page
                    self.cache[page.canonical_url] = page
                    return page
                errors.append("HTTP page looked blocked or required JavaScript")
            except Exception as exc:  # noqa: BLE001 - browser fallback
                errors.append(f"httpx: {type(exc).__name__}: {exc}")
        if backend in {"auto", "playwright"}:
            try:
                page = self._fetch_playwright(canonical)
                self.cache[canonical] = page
                self.cache[page.canonical_url] = page
                return page
            except Exception as exc:  # noqa: BLE001
                self._playwright_error = f"{type(exc).__name__}: {exc}"[:500]
                errors.append(f"playwright: {self._playwright_error}")
        raise WebCrawlerDMError(f"failed to fetch {canonical}: " + "; ".join(errors))

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
        lowered = f"{page.title} {page.text_preview} {page.html[:5000]}".lower()
        if any(marker in lowered for marker in _BOT_MARKERS):
            return True
        visible = len(re.sub(r"\s+", " ", page.text_preview))
        return visible < 180 and "<script" in page.html.lower()

    def _start_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise WebCrawlerDMError(
                "Playwright is not installed; install playwright and run "
                "`playwright install chromium`"
            ) from None
        self._playwright = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": self.config.headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        proxy_settings = playwright_proxy_settings(self._proxy)
        if proxy_settings:
            launch_kwargs["proxy"] = proxy_settings
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
            status = response.status if response is not None else 200
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
    """Tools exposed to the model, including the unique terminal URL tool."""

    tool_names = (
        "search_web", "open_page", "extract_related_urls", "submit_resource_url"
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
        self.submission_reason = ""

    def execute(self, tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        if tool == "search_web":
            return self.search_web(str(args.get("query") or self.query), args.get("max_results"))
        if tool == "open_page":
            return self.open_page(str(args.get("url") or ""))
        if tool == "extract_related_urls":
            return self.extract_related_urls(str(args.get("url") or ""))
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
        for row in rows:
            self.known[row.url] = {
                "url": row.url,
                "title": row.title[:300],
                "snippet": row.snippet[:600],
                "source": "search",
                "rank": row.rank,
                "provider": row.provider,
            }
        return {"ok": True, "query": query, "results": [row.to_dict() for row in rows]}

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

    def submit_resource_url(self, url: str, reason: str = "") -> dict[str, Any]:
        canonical = canonicalize_url(url)
        if not canonical:
            return {"ok": False, "error": f"invalid URL: {url!r}"}
        if canonical not in self.known and canonical not in self.opened:
            return {
                "ok": False,
                "error": "URL was not discovered by search/open/crawler tools",
                "url": canonical,
            }
        opened = self.open_page(canonical)
        if not opened.get("ok"):
            return {"ok": False, "error": "submitted URL could not be fetched", "detail": opened}
        self.submitted_url = str(opened["url"])
        self.submission_reason = reason[:1000]
        return {
            "ok": True,
            "submitted": True,
            "url": self.submitted_url,
            "reason": self.submission_reason,
        }

    def candidate_snapshot(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = list(self.known.values())
        rows.sort(key=lambda row: (
            int(row.get("rank") or 9999),
            -float(row.get("score") or 0.0),
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

Your sole goal is to find ONE primary, authoritative, content-rich resource page that best answers the user's query. Prefer first-party or otherwise reputable sources appropriate to the topic. Avoid search result pages, home pages, login pages, tag/category pages, social media, and thin aggregators.

You have exactly four tools:
1. search_web(query, max_results): search for candidate pages.
2. open_page(url): inspect a candidate page.
3. extract_related_urls(url): the special crawler tool; extract relevant links from the current page so you can find a more specific resource page.
4. submit_resource_url(url, reason): the ONLY valid way to finish and return the URL.

Return only one JSON object per step:
{"tool":"<tool name>","arguments":{...},"reason":"short reason"}

Rules:
- Use only URLs discovered through the tools.
- The state includes the active search_provider. Rewrite each search query for that provider and its target ecosystem instead of copying the user's wording.
- For global code repositories and other global technical ecosystems, use concise English discovery terms. For other ecosystems, use the terminology and language that their search index is most likely to understand.
- Keep provider queries concise and search-ready; put investigation details in your internal reason, not in the query.
- The state includes remaining_search_calls. When it reaches zero, inspect or submit existing candidates instead of searching again.
- Treat every core concept in the user's query as mandatory. Judge the title, snippet, URL, and opened-page evidence together before selecting a resource.
- Reject lexical coincidences and pages whose actual purpose is unrelated, even when one or more query words appear in their metadata.
- Use provider-native search syntax or qualifiers when they help preserve all mandatory concepts.
- Never submit a candidate that you judge unrelated merely to finish. A failed run is preferable to contaminating the dataset.
- Inspect enough evidence before submitting.
- Use extract_related_urls when the current page is an index, documentation root, or collection page.
- Never answer the user's topic question and never return prose instead of a tool call.
- Finish by calling submit_resource_url. A plain URL or a field named `url` is not a valid final answer.
"""


class ToolCallingWebAgentKernel:
    """Bounded browser-use-style observe/action/tool loop."""

    def __init__(self, config: WebCrawlerDMConfig, *, root: str):
        self.config = config
        self.model_spec = ModelPool(root).get(config.model) if config.model else None

    def discover(self, query: str, tools: WebCrawlerTools) -> tuple[str, list[dict[str, Any]], int]:
        if self.model_spec is None:
            return self._fallback(query, tools, [], "no model configured")
        trace: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        seen_actions: set[str] = set()
        tool_counts: dict[str, int] = {}
        # Reserve the final action for deterministic submit_resource_url. This
        # makes max_steps a hard total-tool budget, not only an LLM-loop budget.
        for step in range(1, self.config.max_steps):
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
                "must_finish_with": "submit_resource_url",
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
                if reason and tool == "submit_resource_url" and not arguments.get("reason"):
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
                elif action_key in seen_actions and tool != "submit_resource_url":
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
            if tool == "submit_resource_url" and observation.get("submitted"):
                return tools.submitted_url, trace, step
            if tools.known and step >= self.config.soft_step_limit:
                reason = (
                    f"soft limit reached: step={step}, "
                    f"search_calls={tool_counts.get('search_web', 0)}"
                )
                return self._fallback(query, tools, trace, reason)
            errors = sum(1 for item in trace if not item["observation"].get("ok", False))
            if errors >= 4:
                return self._fallback(query, tools, trace, "repeated tool/model errors")
        return self._fallback(query, tools, trace, "max_steps reached")

    def _fallback(
        self,
        query: str,
        tools: WebCrawlerTools,
        trace: list[dict[str, Any]],
        reason: str,
    ) -> tuple[str, list[dict[str, Any]], int]:
        remaining = self.config.max_steps - len(trace)
        if remaining <= 0:
            raise WebCrawlerDMError(
                f"agent exhausted max_steps={self.config.max_steps} without a URL"
            )
        if not tools.known:
            if remaining < 2:
                raise WebCrawlerDMError(
                    "no URL candidates and insufficient step budget for search + submit"
                )
            observation = tools.search_web(query)
            trace.append({
                "step": len(trace) + 1,
                "tool": "search_web",
                "arguments": {"query": query},
                "reason": f"deterministic fallback: {reason}",
                "observation": _compact_observation(observation, self.config.max_observation_chars),
                "fallback": True,
            })
            remaining -= 1
        candidates = tools.candidate_snapshot(limit=30)
        scored = self._score_candidates(query, candidates, tools)
        # Verify only as many top candidates as fit while always reserving one
        # final submit step. Already-open pages cost no extra action.
        verify_budget = min(4, max(0, remaining - 1))
        for _, url in sorted(scored, reverse=True)[:verify_budget]:
            if url in tools.opened or url in tools.fetcher.cache:
                continue
            opened = tools.open_page(url)
            trace.append({
                "step": len(trace) + 1,
                "tool": "open_page",
                "arguments": {"url": url},
                "reason": "deterministic candidate verification",
                "observation": _compact_observation(opened, self.config.max_observation_chars),
                "fallback": True,
            })
            remaining -= 1
            if remaining <= 1:
                break
        scored = self._score_candidates(query, candidates, tools)
        if not scored:
            raise WebCrawlerDMError(f"agent could not find a fetchable resource URL ({reason})")
        selected = max(scored)[1]
        submitted = tools.submit_resource_url(selected, f"deterministic fallback: {reason}")
        trace.append({
            "step": len(trace) + 1,
            "tool": "submit_resource_url",
            "arguments": {"url": selected, "reason": reason},
            "reason": "required terminal tool",
            "observation": _compact_observation(submitted, self.config.max_observation_chars),
            "fallback": True,
        })
        if not submitted.get("submitted"):
            raise WebCrawlerDMError(f"fallback URL submission failed: {submitted}")
        return tools.submitted_url, trace, len(trace)

    @staticmethod
    def _score_candidates(
        query: str,
        candidates: list[dict[str, Any]],
        tools: WebCrawlerTools,
    ) -> list[tuple[float, str]]:
        del query  # The deterministic fallback must not imitate semantic judgment.
        scored = []
        for candidate in candidates:
            url = canonicalize_url(str(candidate.get("url") or ""))
            if not url or not _looks_like_html_url(url):
                continue
            page = tools.opened.get(url) or tools.fetcher.cache.get(url)
            score = -float(candidate.get("rank") or 10) * 0.05
            if page is not None:
                score += 3.0 + min(4.0, len(page.html) / 50_000.0)
            scored.append((score, url))
        return scored


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
        version="1.0.0",
        description=(
            "Tool-calling web agent that returns one resource URL, traverses "
            "related links to depth two, and ingests raw HTML as DataMixer L1."
        ),
        input_type="query",
        output_type="resource_url+L1_raw_html",
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
        selected_url, trace, steps = kernel.discover(query, tools)
        pages, failures = self._crawl(selected_url, query, root=str(store.root))
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
        records = self._records(
            pages,
            query=query,
            root_url=selected_url,
            run_id=run_id,
            agent_steps=steps,
            campaign_id=campaign_id,
            parent_query=parent_query,
            subgoal_index=subgoal_index,
            subgoal_metadata=subgoal_metadata,
        )
        record_domain = str((subgoal_metadata or {}).get("domain") or "web")
        ingest = store.ingest_records(
            dataset_id,
            records,
            defaults={
                "quality_level": "L1",
                "modality": "text",
                "stage": "pretrain",
                "domain": record_domain,
                "source": "webcrawler_dm",
                "license": self.config.license,
                "processing_level": "raw_html",
                "source_kind": "webpage_html",
            },
            decontaminate=False,
        )
        result = WebCrawlerDMResult(
            run_id=run_id,
            query=query,
            selected_url=selected_url,
            submitted_by_tool=bool(tools.submitted_url),
            agent_steps=steps,
            max_steps=self.config.max_steps,
            model=self.config.model,
            dataset=dataset,
            dataset_id=dataset_id,
            pages_fetched=len(pages),
            pages_ingested=ingest.written,
            pages_failed=len(failures),
            crawl_depth=self.config.max_depth,
            search_provider=self.config.search_provider,
            browser=self.fetcher.browser_status(),
            trace=trace,
            failures=failures,
            campaign_id=campaign_id,
            parent_query=parent_query,
            subgoal_index=subgoal_index,
        )
        result.lineage_path = self._write_run_artifacts(store, result, pages)
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
        return {
            "webagent": self.spec.name,
            "version": self.spec.version,
            "dataset": dataset,
            "inputs": len(queries),
            "succeeded": len(results),
            "failed": len(errors),
            "selected_urls": [row["selected_url"] for row in results],
            "pages_fetched": sum(row["pages_fetched"] for row in results),
            "pages_ingested": sum(row["pages_ingested"] for row in results),
            "elapsed_s": round(time.time() - started, 3),
            "config": self.config.public_dict(),
            "results": results,
            "errors": errors,
        }

    def _crawl(
        self,
        selected_url: str,
        query: str,
        *,
        root: str,
    ) -> tuple[list[tuple[FetchedPage, int, str, str]], list[dict[str, str]]]:
        queue = deque([(canonicalize_url(selected_url), 0, "", "agent_submit")])
        queued = {canonicalize_url(selected_url)}
        visited: set[str] = set()
        pages: list[tuple[FetchedPage, int, str, str]] = []
        failures: list[dict[str, str]] = []
        while queue and len(pages) < self.config.max_pages:
            url, depth, parent_url, discovered_by = queue.popleft()
            if not url or url in visited:
                continue
            visited.add(url)
            try:
                page = self.fetcher.fetch(url)
            except Exception as exc:  # noqa: BLE001 - per-page isolation
                failures.append({"url": url, "error": str(exc)[:1000]})
                continue
            canonical = page.canonical_url
            if canonical in {item[0].canonical_url for item in pages}:
                continue
            pages.append((page, depth, parent_url, discovered_by))
            if depth >= self.config.max_depth:
                continue
            try:
                links = self._collect_crawl_links(page, query)
            except Exception as exc:  # noqa: BLE001 - preserve page and continue
                failures.append({
                    "url": canonical,
                    "error": f"related-link extraction: {type(exc).__name__}: {exc}"[:1000],
                })
                links = []
            for link in links:
                if len(pages) + len(queue) >= self.config.max_pages:
                    break
                if link.url in visited or link.url in queued:
                    continue
                queued.add(link.url)
                queue.append((link.url, depth + 1, canonical, "crawler_tool"))
        return pages, failures

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
        pages: list[tuple[FetchedPage, int, str, str]],
        *,
        query: str,
        root_url: str,
        run_id: str,
        agent_steps: int,
        campaign_id: str = "",
        parent_query: str = "",
        subgoal_index: int | None = None,
        subgoal_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        record_domain = str((subgoal_metadata or {}).get("domain") or "web")
        records = []
        for page, depth, parent_url, discovered_by in pages:
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
    def _write_run_artifacts(
        store: DataStore,
        result: WebCrawlerDMResult,
        pages: list[tuple[FetchedPage, int, str, str]],
    ) -> str:
        run_dir = Path(store.root) / "webcrawler_dm_runs" / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = run_dir / "pages.jsonl"
        with manifest.open("w", encoding="utf-8") as handle:
            for page, depth, parent_url, discovered_by in pages:
                handle.write(json.dumps({
                    "url": page.canonical_url,
                    "title": page.title,
                    "depth": depth,
                    "parent_url": parent_url,
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
        version="1.0.0",
        description=(
            "Domain data-acquisition web agent: discovers authoritative vertical "
            "resource pages, traverses related links to depth two, and ingests "
            "raw HTML as DataMixer L1."
        ),
        input_type="domain_query",
        output_type="resource_url+L1_raw_html",
    )
