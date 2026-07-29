# DataMixer Domain Data Acquisition Agent (`domain_data_acquisition`)

`domain_data_acquisition` provides DataMixer's query-to-L1 vertical-domain
data-acquisition stage. `webcrawler_dm` remains a compatibility alias. Its only
responsibility is resource discovery and raw-page collection. Main-content
extraction remains the L1 -> L2 `webpage_to_pt` operator, and QA generation
remains the L2 -> L3 `pt_to_sft_qa` operator.

## Agent tools

| Tool | Purpose |
| --- | --- |
| `search_web` | Search through Bing, GitHub, Tavily, or the DuckDuckGo HTML fallback. |
| `open_page` | Fetch and inspect a candidate without exposing full HTML to the model. |
| `extract_related_urls` | Extract all bounded, crawl-safe same-site URLs from the current page. |
| `submit_resource_url` | Required terminal tool; the fetched selected URL becomes the crawl root. |

The default maximum is 30 model/tool steps. After submission, the selected page
is depth 0 and every crawl-safe same-site HTML link is enqueued through depth 2.
Each subgoal is bounded by at most 1,000 materialized pages. Canonical-URL and
content-addressed deduplication remain deterministic safety mechanisms.

Search-query rewriting is prompt-driven. Production code does not contain
domain translation tables or math/code/medical/financial keyword filters, and
does not reject or LLM-select downloaded links; DataMixer handles downstream
quality grading. Provider queries are rewritten for the active ecosystem, while
URL canonicalization, size limits, timeouts, and tool budgets stay
provider-agnostic.

## Browser behavior

`browser_backend=auto` first performs a normal HTTP fetch. A blocked page or a
JavaScript shell triggers Playwright fallback. `playwright-stealth>=2.0.3` is
applied to the browser context when available. This reduces common automation
fingerprints; it is not a promise to bypass access control or a site's policy.
The crawler still respects robots.txt and per-host delays by default.

Network proxy discovery is enabled by default and shared by HTTPX and
Playwright. Resolution order is `WEBCRAWLER_DM_PROXY`, `HTTPS_PROXY`,
`HTTP_PROXY`, then `ALL_PROXY` (uppercase and lowercase variants are accepted).
Use `--proxy <url>` to override it or `--no-env-proxy` to disable environment
proxy discovery. Proxy credentials are masked in CLI status and lineage output.

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
If a retry adds new L1 pages, the campaign rebuilds only that campaign's L2/L3
derived records before rerunning the automatic pipeline.

### Automatic L1 -> L2 -> L3 processing

Add `--auto-process` to run DataMixer's registered webpage pipeline after the
queue has no pending/running tasks. The campaign dynamically binds the L1
source dataset, L2/L3 output datasets, extractor, and model-pool name:

```bash
python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse \
  webagent campaign start domain_data_acquisition \
  --query "收集数学方向的code数据" \
  --model deepseek-proxy \
  --subquery-count 12 \
  --workers 4 \
  --auto-process \
  --pipeline examples/datamixer_l1_l3_pipeline/pipeline.yaml \
  --l2-dataset math_code_l2_pt \
  --l3-dataset math_code_l3_sft \
  --pipeline-extractor mineru \
  --pipeline-mineru-gpu 0
```

The campaign report contains `pipeline.levels.L1/L2/L3` counts and the normal
DataMixer pipeline lineage. A partial batch does not trigger processing; the
pipeline starts only after the persistent queue reaches a terminal state. Its
source filter and reported counts are isolated by `campaign_id`, so reuse of an
L1 dataset does not reprocess historical campaigns.

At the L2 boundary the default webpage pipeline invokes the registered
`domain_classify` LLM operator. It classifies PT text against persistent broad
domains and the current lake's registered/observed domain classes, then writes
the primary class to `domain` and all selected classes to `domain_labels`.

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
