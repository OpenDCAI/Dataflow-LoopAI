from __future__ import annotations

import asyncio
import json

import pytest

from loopai.agents.Obtainer.datamixer import llm
from loopai.agents.Obtainer.datamixer.cli import build_parser
from loopai.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from loopai.agents.Obtainer.datamixer.store import DataStore
from loopai.agents.Obtainer.datamixer.webagents import is_registered
from loopai.agents.Obtainer.datamixer.webagents.webcrawler_dm import (
    FetchedPage,
    SearchResult,
    WebCrawlerDMConfig,
    WebCrawlerDMError,
    WebCrawlerDMAgent,
    WebCrawlerTools,
    WebPageFetcher,
    WebSearchClient,
    _AGENT_SYSTEM_PROMPT,
    _normalize_rubric,
    canonicalize_url,
    mask_proxy_url,
    playwright_proxy_settings,
    resolve_network_proxy,
    playwright_process_env,
)


def test_agent_prompt_filters_only_by_domain_relevance() -> None:
    assert (
        "Reject a URL only when its actual subject matter is outside"
        in _AGENT_SYSTEM_PROMPT
    )
    assert "PHI must not cause a domain-relevant page" in _AGENT_SYSTEM_PROMPT
    assert "authoritative, content-rich" not in _AGENT_SYSTEM_PROMPT
    assert "Avoid search result pages" not in _AGENT_SYSTEM_PROMPT
    assert "page type, content quality, fetchability" in _AGENT_SYSTEM_PROMPT


def rubric_payload(
    decision: str = "supporting",
    *,
    weak_dimensions: tuple[str, ...] = (),
) -> dict:
    dimensions = (
        "query_coverage",
        "source_authority",
        "content_substance",
        "crawl_yield",
        "complementary_value",
    )
    return {
        "rubric": {
            dimension: {
                "score": 2 if dimension in weak_dimensions else 4,
                "evidence": f"grounded {dimension} evidence",
            }
            for dimension in dimensions
        },
        "decision": decision,
        "decision_reason": "holistic grounded rubric decision",
    }


class FakeSearch:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def search(self, query, max_results=None):
        self.queries.append((query, max_results))
        return self.rows[:max_results]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = {canonicalize_url(url): html for url, html in pages.items()}
        self.cache = {}
        self.calls = []

    def fetch(self, url):
        from loopai.agents.Obtainer.datamixer.webagents.webcrawler_dm import parse_page

        url = canonicalize_url(url)
        self.calls.append(url)
        if url in self.cache:
            return self.cache[url]
        if url not in self.pages:
            raise RuntimeError(f"missing fake page: {url}")
        html = self.pages[url]
        parser = parse_page(html)
        title, preview, canonical_hint = parser.summary()
        page = FetchedPage(
            requested_url=url,
            final_url=url,
            html=html,
            title=title,
            text_preview=preview,
            status=200,
            content_type="text/html",
            headers={},
            fetch_mode="fake",
            canonical_hint=canonical_hint,
        )
        self.cache[url] = page
        return page

    def browser_status(self):
        return {
            "backend": "fake",
            "playwright_available": False,
            "stealth_requested": True,
            "stealth_available": False,
            "playwright_started": False,
            "playwright_error": "",
        }

    def close(self):
        pass


def test_webcrawler_dm_is_registered_and_defaults_to_30_steps() -> None:
    assert is_registered("webcrawler_dm")
    args = build_parser().parse_args([
        "webagent", "run", "webcrawler_dm", "--query", "python docs"
    ])
    assert args.max_steps == 30
    assert args.soft_step_limit == 16
    assert args.max_search_calls == 4
    assert args.search_timeout == 15.0
    assert args.max_pages == 1000
    assert args.max_depth == 2
    assert args.max_links_per_page == 1000
    assert args.page_cache_entries == 64
    assert args.proxy == ""
    assert args.no_env_proxy is False
    assert args.no_search_llm_summary is False
    assert args.search_summary_results == 5
    assert args.search_summary_chars == 4000


def test_canonicalize_url_removes_tracking_and_fragments() -> None:
    assert canonicalize_url(
        "HTTPS://Example.COM:443//docs/?utm_source=x&b=2&a=1#intro"
    ) == "https://example.com/docs/?a=1&b=2"
    assert canonicalize_url("http://example.com:bad/") == ""
    assert canonicalize_url("file:///etc/passwd") == ""


def test_proxy_defaults_to_environment_and_masks_credentials(monkeypatch) -> None:
    for key in (
        "WEBCRAWLER_DM_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
        "http_proxy", "ALL_PROXY", "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://alice:secret@127.0.0.1:7890")
    proxy, source = resolve_network_proxy()
    assert proxy == "http://alice:secret@127.0.0.1:7890"
    assert source == "env:HTTP_PROXY"
    assert mask_proxy_url(proxy) == "http://***@127.0.0.1:7890"
    assert playwright_proxy_settings(proxy) == {
        "server": "http://127.0.0.1:7890",
        "username": "alice",
        "password": "secret",
    }
    assert WebCrawlerDMConfig().resolved_proxy() == (proxy, source)
    assert WebCrawlerDMConfig(use_env_proxy=False).resolved_proxy() == ("", "")
    assert resolve_network_proxy("socks5://127.0.0.1:1080") == (
        "socks5://127.0.0.1:1080", "config"
    )


def test_proxy_public_host_validation_is_cached_after_public_dns_answer(
    monkeypatch,
) -> None:
    calls = []

    def proxy_doh(host):
        calls.append(host)
        return {"93.184.216.34"}
    fetcher = WebPageFetcher(WebCrawlerDMConfig(
        proxy="http://127.0.0.1:7890",
        use_env_proxy=False,
    ))
    try:
        monkeypatch.setattr(fetcher, "_resolve_addresses_through_proxy", proxy_doh)
        fetcher._validate_public_url("https://example.test/first")
        fetcher._validate_public_url("https://example.test/second")
    finally:
        fetcher.close()
    assert calls == ["example.test"]


def test_playwright_process_env_uses_user_runtime(monkeypatch, tmp_path) -> None:
    prefix = tmp_path / "browser-runtime"
    (prefix / "lib").mkdir(parents=True)
    (prefix / "etc" / "fonts").mkdir(parents=True)
    (prefix / "share").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_RUNTIME_PREFIX", str(prefix))
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/lib")
    monkeypatch.setenv("XDG_DATA_DIRS", "/existing/share")

    env = playwright_process_env()

    assert env is not None
    assert env["LD_LIBRARY_PATH"] == f"{prefix / 'lib'}:/existing/lib"
    assert env["FONTCONFIG_PATH"] == str(prefix / "etc" / "fonts")
    assert env["XDG_DATA_DIRS"] == f"{prefix / 'share'}:/existing/share"

def test_proxy_validation_does_not_cache_non_public_first_answer(
    monkeypatch,
) -> None:
    calls = []

    def private_proxy_doh(host):
        calls.append(host)
        return {"2001::1"}
    fetcher = WebPageFetcher(WebCrawlerDMConfig(
        proxy="http://127.0.0.1:7890",
        use_env_proxy=False,
    ))
    try:
        monkeypatch.setattr(fetcher, "_resolve_addresses_through_proxy", private_proxy_doh)
        with pytest.raises(WebCrawlerDMError, match="non-public address"):
            fetcher._validate_public_url("https://blocked.test/first")
        with pytest.raises(WebCrawlerDMError, match="non-public address"):
            fetcher._validate_public_url("https://blocked.test/second")
    finally:
        fetcher.close()

    assert calls == ["blocked.test", "blocked.test"]


def test_direct_public_host_validation_is_not_cached(monkeypatch) -> None:
    calls = []

    def alternating_getaddrinfo(host, port):  # noqa: ARG001
        calls.append(host)
        if len(calls) == 1:
            return [(2, 1, 6, "", ("93.184.216.34", 0))]
        return [(10, 1, 6, "", ("2001::1", 0, 0, 0))]

    monkeypatch.setattr(
        "loopai.agents.Obtainer.datamixer.webagents.webcrawler_dm.socket.getaddrinfo",
        alternating_getaddrinfo,
    )
    fetcher = WebPageFetcher(WebCrawlerDMConfig(use_env_proxy=False))
    try:
        fetcher._validate_public_url("https://example.test/first")
        with pytest.raises(WebCrawlerDMError, match="non-public address"):
            fetcher._validate_public_url("https://example.test/second")
    finally:
        fetcher.close()

    assert calls == ["example.test", "example.test"]


def test_bot_marker_in_script_config_does_not_reject_substantive_html() -> None:
    page = FetchedPage(
        requested_url="https://example.test/article",
        final_url="https://example.test/article",
        html=(
            "<html><head><title>Complete reference article</title>"
            "<script>window.config = {captcha: 'only-for-editing'};</script>"
            "</head><body>" + ("Substantive visible reference content. " * 30)
            + "</body></html>"
        ),
        title="Complete reference article",
        text_preview="Substantive visible reference content. " * 30,
        status=200,
        content_type="text/html",
        headers={},
        fetch_mode="httpx",
    )

    assert WebPageFetcher._needs_browser(page) is False


def test_visible_bot_challenge_still_requires_browser() -> None:
    page = FetchedPage(
        requested_url="https://example.test/challenge",
        final_url="https://example.test/challenge",
        html=(
            "<html><title>Attention required</title>"
            "<body>Verify you are human</body></html>"
        ),
        title="Attention required",
        text_preview="Verify you are human to continue.",
        status=200,
        content_type="text/html",
        headers={},
        fetch_mode="httpx",
    )

    assert WebPageFetcher._needs_browser(page) is True


def test_page_cache_is_bounded_lru() -> None:
    fetcher = WebPageFetcher(WebCrawlerDMConfig(
        use_env_proxy=False,
        page_cache_entries=2,
    ))
    try:
        for index in range(3):
            url = f"https://example.test/{index}"
            page = FetchedPage(
                requested_url=url,
                final_url=url,
                html=f"<html><body>page {index}</body></html>",
                title=f"Page {index}",
                text_preview=f"page {index}",
                status=200,
                content_type="text/html",
                headers={},
                fetch_mode="httpx",
            )
            fetcher._cache_page(url, page)

        assert list(fetcher.cache) == [
            "https://example.test/1",
            "https://example.test/2",
        ]
    finally:
        fetcher.close()


def test_search_dedup_is_provider_agnostic_and_canonical() -> None:
    rows = WebSearchClient._dedup([
        SearchResult(
            url="HTTPS://Example.COM:443/docs/?utm_source=x&a=1#intro",
            title="First",
            provider="one",
        ),
        SearchResult(
            url="https://example.com/docs/?a=1",
            title="Duplicate",
            provider="two",
        ),
        SearchResult(url="file:///tmp/nope", title="Invalid", provider="two"),
        SearchResult(url="https://example.org/resource", title="Second", provider="two"),
    ], 5)
    assert [row.url for row in rows] == [
        "https://example.com/docs/?a=1",
        "https://example.org/resource",
    ]
    assert [row.rank for row in rows] == [1, 2]


def test_baidu_direct_search_parses_external_result_without_optional_bs4(
    monkeypatch
) -> None:
    import httpx

    class FakeResponse:
        text = (
            "<html><body><div class='result'><h3>"
            "<a href='https://example.test/finance'>金融监管指南</a>"
            "</h3></div></body></html>"
        )
        url = "https://www.baidu.com/s?wd=finance"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: FakeResponse())
    client = WebSearchClient(WebCrawlerDMConfig(
        search_provider="baidu", search_llm_summary=False
    ))

    rows = client._baidu("金融监管", 3)

    assert len(rows) == 1
    assert rows[0].url == "https://example.test/finance"
    assert rows[0].title == "金融监管指南"
    assert rows[0].provider == "baidu"


def test_tavily_failure_falls_back_to_bing_and_llm_summarizes(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    url = "https://example.test/finance"
    config = WebCrawlerDMConfig(
        model="fake",
        search_provider="tavily",
        tavily_api_key="invalid-test-key",
        search_results=3,
        search_summary_results=1,
        request_delay=0,
    )
    client = WebSearchClient(config)

    def fail_tavily(query, limit):  # noqa: ARG001
        raise RuntimeError("Tavily HTTP 401: invalid API key")

    monkeypatch.setattr(client, "_tavily", fail_tavily)
    monkeypatch.setattr(
        client,
        "_bing",
        lambda query, limit: [
            SearchResult(
                url=url,
                title="Finance guide",
                snippet="A search-engine snippet about audited financial statements.",
                provider="bing",
            )
        ],
    )
    monkeypatch.setattr(
        llm,
        "complete",
        lambda *args, **kwargs: json.dumps({
            "results": [{
                "index": 0,
                "summary": "该页面解释了经审计财务报表及其主要用途。",
                **rubric_payload("core"),
            }]
        }, ensure_ascii=False),
    )
    tools = WebCrawlerTools(
        "经审计财务报表怎么解读",
        config,
        client,
        FakeFetcher({
            url: (
                "<html><title>Audited financial statements</title><body>"
                "Audited balance sheets, income statements, and cash flow "
                "statements help readers assess a company's finances."
                "</body></html>"
            )
        }),
        root=str(store.root),
    )

    result = tools.search_web("经审计财务报表怎么解读", 3)

    assert result["provider"] == "bing"
    assert [row["provider"] for row in result["provider_attempts"]] == [
        "tavily", "bing"
    ]
    assert result["provider_attempts"][0]["ok"] is False
    assert result["llm_summary"]["enriched"] == 1
    assert result["results"][0]["llm_enriched"] is True
    assert result["results"][0]["rubric_decision"] == "core"
    assert set(result["results"][0]["rubric_scores"]) == {
        "query_coverage",
        "source_authority",
        "content_substance",
        "crawl_yield",
        "complementary_value",
    }
    assert result["results"][0]["content"].startswith("该页面解释")
    assert result["results"][0]["search_snippet"].startswith(
        "A search-engine snippet"
    )
    store.close()


def test_direct_search_keeps_original_snippet_when_llm_fails(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    url = "https://example.test/reference"
    config = WebCrawlerDMConfig(
        model="fake", search_provider="bing", request_delay=0
    )
    client = WebSearchClient(config)
    monkeypatch.setattr(
        client,
        "_bing",
        lambda query, limit: [
            SearchResult(
                url=url,
                title="Reference",
                snippet="Original search snippet remains usable.",
                provider="bing",
            )
        ],
    )
    monkeypatch.setattr(
        llm,
        "complete",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("LLM down")),
    )
    tools = WebCrawlerTools(
        "reference query",
        config,
        client,
        FakeFetcher({url: "<html><title>Reference</title><body>Reference text.</body></html>"}),
        root=str(store.root),
    )

    result = tools.search_web("reference query", 1)

    assert result["ok"] is True
    assert result["results"][0]["content"] == "Original search snippet remains usable."
    assert result["results"][0]["llm_enriched"] is False
    assert "LLM down" in result["results"][0]["summary_error"]
    store.close()


def test_auto_search_continues_when_bing_has_only_rubric_exclusions(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    bing_url = "https://example.test/bing-noise"
    baidu_url = "https://example.test/baidu-noise"
    ddg_url = "https://example.test/financial-statements"
    config = WebCrawlerDMConfig(
        model="fake",
        search_provider="auto",
        search_results=3,
        search_summary_results=3,
        request_delay=0,
    )
    client = WebSearchClient(config)
    monkeypatch.setattr(
        client,
        "_bing",
        lambda query, limit: [SearchResult(
            url=bing_url, title="Bing noise", snippet="lexical noise", provider="bing"
        )],
    )
    monkeypatch.setattr(
        client,
        "_baidu",
        lambda query, limit: [SearchResult(
            url=baidu_url, title="Baidu noise", snippet="lexical noise", provider="baidu"
        )],
    )
    monkeypatch.setattr(
        client,
        "_duckduckgo_html",
        lambda query, limit: [SearchResult(
            url=ddg_url,
            title="Three financial statements guide",
            snippet="Income statement, balance sheet, and cash flow statement guide.",
            provider="duckduckgo_html",
        )],
    )
    fetcher = FakeFetcher({
        bing_url: "<html><title>Bing noise</title><body>Noise.</body></html>",
        baidu_url: "<html><title>Baidu noise</title><body>Noise.</body></html>",
        ddg_url: (
            "<html><title>Three financial statements guide</title><body>"
            "Income statement, balance sheet, and cash flow statement guide."
            "</body></html>"
        ),
    })

    def complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        request = json.loads(messages[1]["content"])
        evidence = request["evidence"][0]
        url = evidence["url"]
        if url == ddg_url:
            payload = rubric_payload("core")
        else:
            payload = rubric_payload(
                "exclude",
                weak_dimensions=("query_coverage", "content_substance"),
            )
        # rubric_payload is the compact rubric object; wrap it in the API schema.
        result = {
            "index": evidence["index"],
            "summary": "grounded candidate summary",
            **payload,
        }
        return json.dumps({"results": [result]})

    monkeypatch.setattr(llm, "complete", complete)
    tools = WebCrawlerTools(
        "financial statements guide",
        config,
        client,
        fetcher,
        root=str(store.root),
    )

    result = tools.search_web("financial statements guide", 3)

    assert result["provider"] == "duckduckgo_html"
    assert [item["provider"] for item in result["provider_attempts"]] == [
        "bing", "baidu", "duckduckgo_html"
    ]
    assert result["llm_summary"]["enriched"] == 3
    assert result["results"][-1]["rubric_decision"] == "core"
    assert {row["url"] for row in result["results"]} == {
        bing_url, baidu_url, ddg_url
    }
    store.close()


def test_shared_webtools_exposes_actual_fallback_provider(monkeypatch) -> None:
    from loopai.agents.Obtainer.utils.web_tools import WebTools

    monkeypatch.setattr(
        WebSearchClient,
        "_tavily",
        lambda self, query, limit: (_ for _ in ()).throw(
            RuntimeError("Tavily HTTP 432: quota exhausted")
        ),
    )
    monkeypatch.setattr(
        WebSearchClient,
        "_bing",
        lambda self, query, limit: [
            SearchResult(
                url="https://example.test/fallback",
                title="Fallback result",
                snippet="Structured fallback snippet.",
                provider="bing",
            )
        ],
    )

    details = asyncio.run(WebTools.search_web_detailed(
        "fallback test",
        search_engine="tavily",
        tavily_api_key="test-key",
        max_results=3,
    ))
    formatted = asyncio.run(WebTools.search_web(
        "fallback test",
        search_engine="tavily",
        tavily_api_key="test-key",
        max_results=3,
    ))

    assert details["provider"] == "bing"
    assert [item["provider"] for item in details["attempts"]] == [
        "tavily", "bing"
    ]
    assert details["results"][0]["content"] == "Structured fallback snippet."
    assert "URL: https://example.test/fallback" in formatted


def test_one_low_score_cannot_exclude_a_url() -> None:
    payload = rubric_payload(
        "exclude",
        weak_dimensions=("query_coverage",),
    )

    scores, evidence, decision, reason = _normalize_rubric(payload)

    assert len(scores) == 5
    assert len(evidence) == 5
    assert decision == "uncertain"
    assert "fewer than two" in reason


def test_two_independent_weak_rubric_dimensions_can_exclude_a_url() -> None:
    payload = rubric_payload(
        "exclude",
        weak_dimensions=("query_coverage", "content_substance"),
    )

    _, _, decision, _ = _normalize_rubric(payload)

    assert decision == "exclude"


def test_submit_resource_urls_requires_every_rubric_included_candidate(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    first = "https://example.test/finance/filings"
    second = "https://example.test/finance/reports"
    unrelated = "https://example.test/robotics"
    search = FakeSearch([
        SearchResult(
            url=first,
            title="Corporate filings",
            rank=1,
            provider="fake",
            # Legacy single-score fields cannot veto a holistic inclusion.
            relevant=False,
            relevance_score=0.0,
            llm_enriched=True,
            rubric_decision="core",
        ),
        SearchResult(
            url=second,
            title="Annual reports",
            rank=2,
            provider="fake",
            llm_enriched=True,
            rubric_decision="supporting",
        ),
        SearchResult(
            url=unrelated,
            title="Robotics SDK",
            snippet="Android robot controller source code",
            rank=3,
            provider="fake",
            llm_enriched=True,
            rubric_decision="exclude",
        )
    ])
    fetcher = FakeFetcher({
        first: "<html><title>Corporate filings</title></html>",
        second: "<html><title>Annual reports</title></html>",
        unrelated: "<html><title>Robotics SDK</title></html>",
    })

    config = WebCrawlerDMConfig(model="fake", request_delay=0)
    tools = WebCrawlerTools(
        "open source mathematics library",
        config,
        search,
        fetcher,
        root=str(store.root),
    )
    assert tools.search_web("open source mathematics library")["ok"] is True
    partial = tools.submit_resource_urls([first], "partial")
    assert partial["ok"] is False
    assert partial["missing_rubric_urls"] == [second]
    contaminated = tools.submit_resource_urls(
        [first, second, unrelated], "contains lexical coincidence"
    )
    assert contaminated["ok"] is False
    assert contaminated["excluded_urls"] == [unrelated]

    result = tools.submit_resource_urls([first, second], "all relevant roots")
    assert result["ok"] is True
    assert result["submitted"] is True
    assert result["urls"] == [first, second]
    assert result["count"] == 2
    assert tools.submitted_urls == [first, second]
    assert fetcher.calls == []
    store.close()


def test_agent_must_finish_through_submit_resource_urls(monkeypatch, tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    root = "https://example.test/docs/"
    better = "https://example.test/docs/reference"
    search = FakeSearch([
        SearchResult(url=root, title="Python docs", snippet="reference", rank=1, provider="fake")
    ])
    fetcher = FakeFetcher({
        root: (
            "<html><title>Python documentation</title><body>"
            "<a href='/docs/reference'>Python API reference</a></body></html>"
        ),
        better: "<html><title>Python API Reference</title><body>Complete API details.</body></html>",
    })
    actions = iter([
        {"tool": "search_web", "arguments": {"query": "Python API"}},
        {"url": root},  # plain URL is deliberately invalid and cannot finish
        {"tool": "open_page", "arguments": {"url": root}},
        {"tool": "extract_related_urls", "arguments": {"url": root}},
        {
            "tool": "submit_resource_urls",
            "arguments": {"urls": [better], "reason": "specific reference"},
        },
    ])
    prompts = []

    def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        prompts.append(messages)
        return json.dumps(next(actions))

    monkeypatch.setattr(llm, "complete", fake_complete)
    agent = WebCrawlerDMAgent(
        WebCrawlerDMConfig(
            model="fake",
            max_steps=30,
            max_search_calls=1,
            max_depth=0,
            max_pages=1,
        ),
        search_client=search,
        fetcher=fetcher,
    )
    result = agent.run("Python API", store=store, dataset="web_l1")
    assert result.selected_url == better
    assert result.selected_urls == [better]
    assert result.submitted_by_tool is True
    assert result.trace[-1]["tool"] == "submit_resource_urls"
    assert result.trace[-1]["observation"]["submitted"] is True
    assert any(item["tool"] == "" for item in result.trace)
    state = json.loads(prompts[0][1]["content"])
    assert state["search_provider"] == "auto"
    assert state["remaining_search_calls"] == 1
    assert state["must_finish_with"] == "submit_resource_urls"
    assert "Treat every core concept" in prompts[0][0]["content"]
    assert search.queries == [("Python API", 8)]
    assert store.catalog.count(dataset_id=result.dataset_id) == 1
    store.close()


def test_depth_two_crawl_materializes_all_crawlable_raw_l1(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    root = "https://example.test/python/"
    level1 = "https://example.test/python/tutorial"
    level2 = "https://example.test/python/reference"
    level3 = "https://example.test/python/deep/internal"
    distractor = "https://example.test/account/settings"
    search = FakeSearch([
        SearchResult(url=root, title="Python guide", snippet="tutorial reference", rank=1, provider="fake")
    ])
    fetcher = FakeFetcher({
        root: (
            "<html><title>Python Guide</title><body>Useful Python guide content."
            "<a href='/python/tutorial'>Python tutorial</a>"
            "<a href='/account/settings'>Account settings</a></body></html>"
        ),
        level1: (
            "<html><title>Python Tutorial</title><body>Detailed Python tutorial."
            "<a href='/python/reference'>Python reference</a></body></html>"
        ),
        level2: (
            "<html><title>Python Reference</title><body>Complete Python reference."
            "<a href='/python/deep/internal'>Python internal reference</a></body></html>"
        ),
        level3: "<html><title>Too Deep</title><body>Must not be fetched.</body></html>",
        distractor: "<html><title>Account</title><body>Unrelated settings.</body></html>",
    })
    actions = iter([
        {"tool": "search_web", "arguments": {"query": "Python programming reference"}},
        {
            "tool": "submit_resource_urls",
            "arguments": {"urls": [root], "reason": "best resource"},
        },
    ])

    def fake_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        return json.dumps(next(actions))

    monkeypatch.setattr(llm, "complete", fake_complete)
    agent = WebCrawlerDMAgent(
        WebCrawlerDMConfig(
            model="fake",
            max_search_calls=1,
            max_depth=2,
            max_pages=10,
            request_delay=0,
        ),
        search_client=search,
        fetcher=fetcher,
    )
    result = agent.run("Python programming tutorial reference", store=store, dataset="web_l1")
    assert result.selected_url == root
    assert result.selected_urls == [root]
    assert result.pages_fetched == 4
    assert result.pages_ingested == 4
    assert result.pages_failed == 0
    assert level3 not in fetcher.calls
    assert distractor in fetcher.calls
    assert result.lineage_path

    rows = store.catalog.query(dataset_id=result.dataset_id)
    assert {row["quality_level"] for row in rows} == {"L1"}
    assert {row["tags"]["crawl_depth"] for row in rows} == {0, 1, 2}
    assert {store.get_content(row["cid"])["document_type"] for row in rows} == {
        "webpage_raw_html"
    }
    assert all("<html>" in store.get_content(row["cid"])["html"] for row in rows)
    assert all(row["tags"]["selected_resource_url"] == root for row in rows)
    assert all(row["tags"]["selected_resource_urls"] == [root] for row in rows)
    store.close()


def test_multiple_llm_roots_share_one_crawl_budget(monkeypatch, tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    first = "https://one.example.test/filings"
    first_child = "https://one.example.test/filings/archive"
    second = "https://two.example.test/reports"
    second_child = "https://two.example.test/reports/annual"
    search = FakeSearch([
        SearchResult(
            url=first,
            title="Filings",
            rank=1,
            provider="fake",
            llm_enriched=True,
            rubric_decision="core",
        ),
        SearchResult(
            url=second,
            title="Reports",
            rank=2,
            provider="fake",
            llm_enriched=True,
            rubric_decision="supporting",
        ),
    ])
    fetcher = FakeFetcher({
        first: (
            "<html><title>Filings</title><body>"
            "<a href='/filings/archive'>Archive</a></body></html>"
        ),
        first_child: "<html><title>Filing archive</title></html>",
        second: (
            "<html><title>Reports</title><body>"
            "<a href='/reports/annual'>Annual</a></body></html>"
        ),
        second_child: "<html><title>Annual reports</title></html>",
    })
    actions = iter([
        {"tool": "search_web", "arguments": {"query": "filings reports"}},
        {
            "tool": "submit_resource_urls",
            "arguments": {"urls": [first, second], "reason": "both relevant"},
        },
    ])
    monkeypatch.setattr(
        llm,
        "complete",
        lambda *args, **kwargs: json.dumps(next(actions)),
    )
    agent = WebCrawlerDMAgent(
        WebCrawlerDMConfig(
            model="fake",
            max_search_calls=1,
            max_depth=1,
            max_pages=4,
            request_delay=0,
        ),
        search_client=search,
        fetcher=fetcher,
    )

    result = agent.run("filings reports", store=store, dataset="multi_root_l1")

    assert result.selected_url == first
    assert result.selected_urls == [first, second]
    assert result.pages_fetched == 4
    rows = store.catalog.query(dataset_id=result.dataset_id)
    assert {row["tags"]["crawl_depth"] for row in rows} == {0, 1}
    by_url = {row["tags"]["canonical_url"]: row["tags"] for row in rows}
    assert by_url[first]["root_url"] == first
    assert by_url[first_child]["root_url"] == first
    assert by_url[second]["root_url"] == second
    assert by_url[second_child]["root_url"] == second
    assert all(
        row["tags"]["selected_resource_urls"] == [first, second]
        for row in rows
    )
    manifest_rows = [
        json.loads(line)
        for line in (tmp_path / "warehouse" / "webcrawler_dm_runs"
                     / result.run_id / "pages.jsonl").read_text().splitlines()
    ]
    assert {row["root_url"] for row in manifest_rows} == {first, second}
    store.close()


def test_submitted_roots_are_prioritized_and_budget_skips_are_reported(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    roots = [
        "https://one.example.test/root",
        "https://two.example.test/root",
        "https://three.example.test/root",
    ]
    search = FakeSearch([
        SearchResult(
            url=url,
            title=f"Relevant root {index}",
            rank=index,
            provider="fake",
            llm_enriched=True,
            rubric_decision="supporting",
        )
        for index, url in enumerate(roots, 1)
    ])
    fetcher = FakeFetcher({
        url: (
            f"<html><title>Root {index}</title><body>"
            f"<a href='/child'>child {index}</a></body></html>"
        )
        for index, url in enumerate(roots, 1)
    })
    actions = iter([
        {"tool": "search_web", "arguments": {"query": "three relevant roots"}},
        {
            "tool": "submit_resource_urls",
            "arguments": {"urls": roots, "reason": "all three are relevant"},
        },
    ])
    monkeypatch.setattr(
        llm,
        "complete",
        lambda *args, **kwargs: json.dumps(next(actions)),
    )
    agent = WebCrawlerDMAgent(
        WebCrawlerDMConfig(
            model="fake",
            max_search_calls=1,
            max_depth=1,
            max_pages=2,
            request_delay=0,
        ),
        search_client=search,
        fetcher=fetcher,
    )

    result = agent.run(
        "three relevant roots",
        store=store,
        dataset="budgeted_multi_root_l1",
    )

    assert result.selected_urls == roots
    assert result.pages_fetched == 2
    assert fetcher.calls == roots[:2]
    assert result.pages_failed == 1
    assert result.failures == [{
        "url": roots[2],
        "root_url": roots[2],
        "error": "crawl page budget exhausted before submitted root could be fetched",
    }]
    failure_rows = [
        json.loads(line)
        for line in (
            tmp_path / "warehouse" / "webcrawler_dm_runs"
            / result.run_id / "failures.jsonl"
        ).read_text().splitlines()
    ]
    assert failure_rows == result.failures
    progress = json.loads(
        (
            tmp_path / "warehouse" / "webcrawler_dm_runs"
            / result.run_id / "progress.json"
        ).read_text()
    )
    assert progress["failure_manifest"].endswith("/failures.jsonl")
    rows = store.catalog.query(dataset_id=result.dataset_id)
    assert {
        row["tags"]["root_url"] for row in rows
    } == set(roots[:2])
    assert all(
        row["tags"]["selected_resource_urls"] == roots
        for row in rows
    )
    store.close()


def test_max_steps_fails_without_explicit_llm_submission(monkeypatch, tmp_path) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    root = "https://example.test/resource"
    search = FakeSearch([
        SearchResult(url=root, title="Specific resource", snippet="topic guide", rank=1, provider="fake")
    ])
    fetcher = FakeFetcher({
        root: "<html><title>Specific resource</title><body>Substantive topic guide.</body></html>"
    })

    def looping_complete(spec, messages, json_mode=True, max_retries=0):  # noqa: ARG001
        return json.dumps({
            "tool": "search_web",
            "arguments": {"query": "topic guide"},
        })

    monkeypatch.setattr(llm, "complete", looping_complete)
    agent = WebCrawlerDMAgent(
        WebCrawlerDMConfig(model="fake", max_steps=5, max_depth=0, max_pages=1),
        search_client=search,
        fetcher=fetcher,
    )
    with pytest.raises(
        WebCrawlerDMError,
        match="deterministic fallback is disabled",
    ):
        agent.run("topic guide", store=store, dataset="web_l1")
    assert store.catalog.resolve_dataset("web_l1") is None
    store.close()
