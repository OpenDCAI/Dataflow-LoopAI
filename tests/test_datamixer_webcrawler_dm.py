from __future__ import annotations

import json

from loopai.agents.Obtainer.datamixer import llm
from loopai.agents.Obtainer.datamixer.cli import build_parser
from loopai.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from loopai.agents.Obtainer.datamixer.store import DataStore
from loopai.agents.Obtainer.datamixer.webagents import is_registered
from loopai.agents.Obtainer.datamixer.webagents.webcrawler_dm import (
    FetchedPage,
    SearchResult,
    WebCrawlerDMConfig,
    WebCrawlerDMAgent,
    WebCrawlerTools,
    WebSearchClient,
    canonicalize_url,
    mask_proxy_url,
    playwright_proxy_settings,
    resolve_network_proxy,
)


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
    assert args.proxy == ""
    assert args.no_env_proxy is False


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


def test_submit_resource_url_accepts_discovered_page_without_relevance_gate(
    monkeypatch, tmp_path
) -> None:
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="fake", api_url="mock://llm"))
    unrelated = "https://example.test/robotics"
    search = FakeSearch([
        SearchResult(
            url=unrelated,
            title="Robotics SDK",
            snippet="Android robot controller source code",
            rank=1,
            provider="fake",
        )
    ])
    fetcher = FakeFetcher({
        unrelated: (
            "<html><title>Robotics SDK</title><body>Android robot controller "
            "software for a competition.</body></html>"
        )
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
    result = tools.submit_resource_url(unrelated, "only candidate")
    assert result["ok"] is True
    assert result["submitted"] is True
    assert result["url"] == unrelated
    assert "relevance" not in result
    assert tools.submitted_url == unrelated
    store.close()


def test_agent_must_finish_through_submit_resource_url(monkeypatch, tmp_path) -> None:
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
        {"tool": "submit_resource_url", "arguments": {"url": better, "reason": "specific reference"}},
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
    assert result.submitted_by_tool is True
    assert result.trace[-1]["tool"] == "submit_resource_url"
    assert result.trace[-1]["observation"]["submitted"] is True
    assert any(item["tool"] == "" for item in result.trace)
    state = json.loads(prompts[0][1]["content"])
    assert state["search_provider"] == "auto"
    assert state["remaining_search_calls"] == 1
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
        {"tool": "submit_resource_url", "arguments": {"url": root, "reason": "best resource"}},
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
    store.close()


def test_max_steps_is_a_hard_total_tool_budget(monkeypatch, tmp_path) -> None:
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
    result = agent.run("topic guide", store=store, dataset="web_l1")
    assert result.agent_steps <= 5
    assert result.trace[-1]["tool"] == "submit_resource_url"
    assert result.submitted_by_tool is True
    store.close()
