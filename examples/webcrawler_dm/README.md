# webcrawler_dm

`webcrawler_dm` is a formally registered DataMixer web-agent plugin. It turns
one or more raw queries into a selected resource URL and then materializes raw
HTML as L1 data:

```text
query
  -> search_web
  -> open_page / extract_related_urls
  -> submit_resource_url (required terminal tool)
  -> BFS over related links, depth 0..2
  -> raw HTML in DataMixer CAS + L1 catalog rows + lineage
```

The agent kernel is a lightweight, local refactor of the observe/action/tool
pattern popularized by the MIT-licensed
[`browser-use`](https://github.com/browser-use/browser-use) project. It does
not vendor or require the full `browser-use` package. Page retrieval is
HTTP-first and can fall back to Playwright. When installed,
`playwright-stealth` is applied to the browser context before navigation.

HTTPX and Playwright read the same proxy by default from
`WEBCRAWLER_DM_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY`, or `ALL_PROXY`. An explicit
`--proxy` overrides environment discovery; `--no-env-proxy` disables it.

## Registration

The plugin is registered by import side effect in
`loopai.agents.Obtainer.datamixer.webagents`:

```python
from loopai.agents.Obtainer.datamixer.webagents import create

agent = create("webcrawler_dm", config=config)
```

List registered plugins:

```bash
python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse webagent list
```

## Run the ten-query example

Register a model in the warehouse model pool first, then run:

```bash
python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse \
  webagent run webcrawler_dm \
  --query-file examples/webcrawler_dm/queries.jsonl \
  --dataset webcrawler_dm_l1 \
  --model deepseek-proxy \
  --max-steps 30 \
  --max-depth 2 \
  --max-pages 4 \
  --max-links-per-page 2 \
  --search-provider github \
  --browser-backend auto
```

`--query` is repeatable and can be used instead of `--query-file`.

## Expanded-query campaign

For a broad query that should fan out into many independent resource goals:

```bash
python3 -m loopai.agents.Obtainer.datamixer \
  --root /path/to/warehouse \
  webagent campaign start webcrawler_dm \
  --query "collect authoritative resources about hypertension" \
  --model deepseek-proxy \
  --subquery-count 24 \
  --workers 4 \
  --batch-size 8
```

Expanded tasks are saved under
`<warehouse>/webagent_campaigns/<run_id>/expanded_queries.jsonl`; persistent
queue state lives in `<warehouse>/webagent_queue.sqlite`. Use
`webagent campaign status` to inspect progress and `webagent campaign resume`
to process the next batch.

To automatically materialize the campaign's raw HTML through L2 pretraining
text and L3 grounded QA, add `--auto-process` and point `--pipeline` at
`examples/datamixer_l1_l3_pipeline/pipeline.yaml`.

## Output contract

Every successful input contains exactly one `selected_url`, and
`submitted_by_tool=true` proves that the URL was returned through
`submit_resource_url`. A model response containing only prose or a plain URL is
treated as an invalid action and cannot end the run.

Each crawled L1 sample stores:

- `content.html`: unprocessed raw HTML;
- `content.url`, `content.title`, HTTP status and content type;
- query, selected root URL, parent URL and crawl depth;
- discovery method, fetch backend and stealth flag;
- retrieval time, content hash and agent run/version lineage.

Run artifacts are written under:

```text
<warehouse>/webcrawler_dm_runs/<run_id>/pages.jsonl
<warehouse>/lineage/<run_id>.json
```

The crawler respects `robots.txt`, rate-limits requests per host, rejects
private-network URLs by default, restricts related-link traversal to the same
site by default, and bounds steps/pages/depth/HTML size.
