# DataMixer Domain Data Acquisition Agent (`domain_data_acquisition`)

`domain_data_acquisition` provides DataMixer's query-to-L1 vertical-domain
data-acquisition stage. `webcrawler_dm` remains a compatibility alias. Its only
responsibility is resource discovery and raw-page collection. Main-content
extraction remains the L1 -> L2 `webpage_to_pt` operator, and QA generation
remains the L2 -> L3 `pt_to_sft_qa` operator.

## Agent tools

| Tool | Purpose |
| --- | --- |
| `search_web` | Search through Tavily, Bing, Baidu, GitHub, or DuckDuckGo HTML with fallback diagnostics. |
| `open_page` | Fetch and inspect a candidate without exposing full HTML to the model. |
| `extract_related_urls` | Extract all bounded, crawl-safe same-site URLs from the current page. |
| `submit_resource_urls` | Required terminal tool; submits every URL included by the LLM's multi-dimensional rubric. |

The default maximum is 30 model/tool steps. The terminal call must include every
candidate whose rubric decision is `core` or `supporting`, and cannot include a
candidate whose decision is `exclude`. The rubric scores `query_coverage`,
`source_authority`, `content_substance`, `crawl_yield`, and
`complementary_value` independently on a grounded 1-5 scale. One weak dimension
can never exclude a URL: an exclusion is trusted only with at least two weak
dimensions and a grounded reason. Partial or invalid rubrics remain `uncertain`
for the main Agent to inspect. Legacy `relevance_score`/`relevant` fields are
compatibility metadata and never act as gates.

There is no deterministic URL-selection fallback: if the model never explicitly
submits a complete rubric-included set, the run fails. Fetchability is recorded
independently, so an authoritative URL blocked by an origin can remain a root
without being replaced by a fetchable lexical match.

After submission, every selected root is depth 0. All roots share one page and
depth budget, and every crawl-safe same-site HTML link is enqueued under the
root that discovered it. Each subgoal is bounded by at most 1,000 materialized
pages. Submitted roots are attempted before child links. If the number of
successfully fetched roots alone exhausts the page budget, each unattempted root
is retained in `selected_urls` and emitted as an explicit crawl-budget failure.
Canonical-URL and content-addressed deduplication remain deterministic safety
mechanisms; they do not select or replace resource roots.

Search-query rewriting is prompt-driven. Production code does not contain
domain translation tables or math/code/medical/financial keyword filters. The
LLM selects all relevant search roots; downloaded child links remain bounded,
same-site raw L1 inputs for downstream DataMixer quality grading. Provider
queries are rewritten for the active ecosystem, while URL canonicalization,
size limits, timeouts, and tool budgets stay provider-agnostic.

With `--search-provider auto`, the provider order is Tavily (when a key is
present), Bing, Baidu, then DuckDuckGo HTML. Explicit Tavily mode uses the same
direct-search fallback chain when Tavily errors or returns no rows. The chain
also continues when a provider returns rows but its completed LLM rubrics have
no `core` or `supporting` candidate; non-empty lexical noise is not treated as a
successful search. Candidates and rubrics from every attempted provider are
retained. For Bing, a blocked/empty HTML result page is retried through Bing's
RSS output.

For every real provider, the crawler fetches the configured top pages and asks
the DataMixer model for grounded summaries and five-dimensional rubrics. Each
candidate is graded in its own model call so a response-token truncation or
malformed JSON cannot discard the rest of the batch. The tool response exposes
the actual provider, `provider_attempts`, and `llm_summary`. Page or per-candidate
model failures preserve the original search snippet and remain `uncertain`.

LLM rubric enrichment is enabled by default and bounded to five results and
4,000 page characters per result. Tune it with `--search-summary-results` and
`--search-summary-chars`, or disable it with `--no-search-llm-summary`.

## Browser behavior

`browser_backend=auto` first performs a normal HTTP fetch. A blocked page or a
JavaScript shell triggers Playwright fallback. `playwright-stealth>=2.0.3` is
applied to the browser context when available. This reduces common automation
fingerprints; it is not a promise to bypass access control or a site's policy.
The crawler still respects robots.txt and per-host delays by default.
HTTP 4xx/5xx responses from Playwright are rejected just like HTTPX responses;
an access-denied page is never materialized as L1 content. Per-page navigation
errors are not cached as browser startup failures, so one origin's 403 cannot
disable later Playwright fallbacks.

When the host lacks Playwright's system libraries and sudo is unavailable, put
a user-space runtime under `.cache/playwright-runtime` or set
`PLAYWRIGHT_RUNTIME_PREFIX` to another prefix containing `lib`, `etc/fonts`,
and `share`. The crawler passes that runtime only to the Chromium child through
`LD_LIBRARY_PATH`, `FONTCONFIG_PATH`, and `XDG_DATA_DIRS`; it does not modify the
host. `browser_status.playwright_started=true` and an empty
`browser_status.playwright_error` are the runtime acceptance evidence.

Network proxy discovery is enabled by default and shared by HTTPX and
Playwright. Resolution order is `WEBCRAWLER_DM_PROXY`, `HTTPS_PROXY`,
`HTTP_PROXY`, then `ALL_PROXY` (uppercase and lowercase variants are accepted).
Use `--proxy <url>` to override it or `--no-env-proxy` to disable environment
proxy discovery. Proxy credentials are masked in CLI status and lineage output.
In proxy mode, SSRF validation resolves A and AAAA records through public DNS
over HTTPS using the same proxy path, then caches only hosts whose complete
answer is public. This avoids treating a local DNS interceptor's `2001::1` sink
as the proxy's actual destination while retaining the public-address gate.
Private/reserved answers are rejected and not cached. Direct connections still
use local DNS and revalidate every request.

Each crawl run durably writes successful page metadata to `pages.jsonl` and
flushes every fetch/link failure immediately to `failures.jsonl`. `progress.json`
contains `failure_manifest`, live depth, fetched/ingested/failed counts, frontier
size, and visited count. This evidence survives Ctrl-C or a dead campaign
executor, so DNS/SSRF, robots, origin HTTP, timeout, and browser-runtime failures
can be distinguished without reproducing a large crawl.

## Expanded-query campaign and worker queue

For one broad request, the outer campaign layer first asks the configured LLM
to generate focused, deduplicated subgoals. It persists those tasks in SQLite
and drains them with four independent webagent workers by default:

```bash
python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse \
  webagent campaign start domain_data_acquisition \
  --query "collect authoritative resources about hypertension" \
  --model deepseek-proxy \
  --subquery-count 24 \
  --workers 4 \
  --batch-size 8 \
  --max-steps 30 \
  --max-depth 2
```

`--batch-size 8` processes eight queue claims in this invocation and leaves
the rest pending. Omit it (or use `0`) to drain the whole queue. Queue state is
stored at `<warehouse>/webagent_queue.sqlite`.

Inspect or resume a campaign:

```bash
python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse \
  webagent campaign status <run-id> --tasks

python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse \
  webagent campaign resume <run-id> --workers 4 --batch-size 8
```

After fixing credentials, proxy, or search configuration, add
`--retry-failed` to reset terminal failed tasks and enqueue them again.

Each task records the original root query, expanded goal, attempt count,
worker ID, result, and error. `resume` resets abandoned `running` tasks to
`pending`; task failures are requeued according to `--task-retries`.
If a retry adds new L1 pages, the persistent feeder resumes from its catalog
cursor and submits only unseen campaign samples to the first operator queue.
Existing L2/L3 descendants and completed stage jobs are left intact.

### Automatic L1 -> L2 -> L3 processing

Add `--auto-process` to run DataMixer's registered webpage pipeline concurrently
with WebAgent collection. The campaign starts one persistent queue consumer per
operator before it starts the crawler, then dynamically binds the L1 source
dataset, L2/L3 output datasets, extractor, and model-pool name:

```bash
python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse \
  webagent campaign start domain_data_acquisition \
  --query "收集数学方向的code数据" \
  --model deepseek-proxy \
  --pipeline-model qwen3-14b-fp8 \
  --subquery-count 12 \
  --workers 4 \
  --auto-process \
  --pipeline examples/datamixer_l1_l3_pipeline/pipeline.yaml \
  --l2-dataset math_code_l2_pt \
  --l3-dataset math_code_l3_sft \
  --pipeline-extractor mineru \
  --pipeline-mineru-gpu 0
```

The campaign report contains `pipeline.levels.L1/L2/L3` counts plus live queue
counters for every operator. As soon as a crawler ingest batch commits L1 rows,
the feeder submits them to `webpage_to_pt`; every successful stage persists its
CAS-backed hand-off before enqueueing the next stage. WebAgent, MinerU, filters,
classification, QA, and validation therefore advance at the same time. A
paused campaign drains the rows already produced and resumes from its durable
cursor later. Source filters, queue jobs, and reported counts are isolated by
`campaign_id`, so reuse of an L1 dataset does not process historical campaigns.

`--model` controls WebAgent planning and URL rubric calls. `--pipeline-model`
controls both `domain_classify` and `pt_to_sft_qa`; the prepared local default is
`qwen3-14b-fp8`.

For multi-hour campaigns, `scripts/datamixer_campaign_watchdog.py` records a
JSONL sample every 30 seconds. It checks the exact campaign PID, crawler failure
ledger, every persistent operator queue, Qwen/MinerU health, RSS, and free disk.
It safely interrupts only on hard conditions such as a terminal pipeline job,
the `2001::1` DNS sink returning, Playwright `TargetClosedError`, three
consecutive service-health failures, RSS above the configured bound, or unsafe
free disk. Expected per-page 403/404/405 and robots exclusions remain observable
but do not stop a bulk crawl.

At the L2 boundary the default webpage pipeline invokes the registered
`domain_classify` LLM operator one record at a time with the campaign's
exploration keywords (`--focus-keywords`), then runs `topic_quality_filter`.
The LLM judgement admits only items directly related to that focus; admission
then checks confidence and at least two grounded semantic signal categories
found in the record content, with no vertical hard-wiring and no source URL
whitelist. Rejections are retained as per-record quality findings, while
accepted PT text is materialized to L2.

### Persistent MinerU-HTML service

`webpage_to_pt` prefers the persistent MinerU-HTML REST service at
`http://127.0.0.1:7986` so campaign pipelines reuse one loaded model instead of
starting a second GPU worker. In this repository the prepared service is
managed with:

```bash
scripts/mineruhtml.sh start
scripts/mineruhtml.sh status
scripts/mineruhtml.sh stop
```

The script binds physical GPU 0 by default. Override it with
`MINERUHTML_CUDA_VISIBLE_DEVICES`, and override the operator endpoint with
`MINERU_HTML_URL`. Set `mineru_transport: worker` only when a pipeline
intentionally needs the compatible isolated subprocess path; its defaults are
the repository-local `envs/.mineruhtml` environment and `model/mineru-html`
model directory.

## DataMixer registration

Web agents use a dedicated registry analogous to the DataMixer operator
registry:

```python
from loopai.agents.Obtainer.datamixer.webagents import register

@register("my_webagent")
class MyWebAgent:
    ...
```

The CLI resolves plugins from this registry, so a plugin is not a standalone
script and can be used by DataMixer's CLI/API console command surface.
