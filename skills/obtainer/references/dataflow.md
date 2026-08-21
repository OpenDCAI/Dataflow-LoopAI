# DataFlowAgent (L4) Post-Processing

Read this when running the mandatory DataFlowAgent post-processing stage
(`dm dataflow agent-run`) or executing its delivered pipeline over the full
input.

Post-processing must use DataFlowAgent; do not hand-pick individual DataFlow
operators. `agent-run` has the Codex SDK export a trial sample, plan the
operator chain per DataFlow-Skills rules, generate and trial-run the pipeline.
A successful trial run is the deliverable (`mode=trial_run`; deliverables =
`pipeline.py` + trial output `trial_processed.jsonl`).

## Commands

```bash
# 1) dataflowagent delivers a trial-verified pipeline (no full run, no merge)
loopai-obtainercli dm --root /path/to/warehouse dataflow agent-run \
  --target "score GSM8K answer-focused SFT rows and keep high-quality rows" \
  --dataset math_sft \
  --trial-rows 20 \
  --expected-outputs math_answer_quality \
  --recipe /path/to/recipe.yaml \
  --json

# 2) upper-layer Codex runs the delivered pipeline over the chunked full input
python -m loopai.agents.Obtainer.datamixer.dataflow_chunked_runner \
  --input /path/to/full_input.jsonl \
  --pipeline /path/to/pipeline.py \
  --output /path/to/full_processed.jsonl --chunk-size 10000

# 3) merge the completed L4 output back into the lake
loopai-obtainercli dm --root /path/to/warehouse apply-jsonl \
  --file /path/to/full_processed.jsonl --field content --json
```

## Rules

- **Trial -> deliver -> upstream full is the contract.** The agent must
  trial-run the pipeline and deliver it (`mode=trial_run`, `pipeline_path` +
  `processed_jsonl`); it must NOT launch the full processing or write
  `full_processed.jsonl` itself. The upper-layer Codex runs the delivered
  pipeline over the exported full input and only treats L4 as complete when
  `full_processed.jsonl` exists and is verified.
- **Export the 5x bucket buffer, not the whole lake.** Pass `--recipe`
  (recipe.yaml) or `--mix-plan` (mix_plan.json) so the full input is sampled
  per bucket to `ceil(bucket_target * 5)` rows (fixed seed; short buckets
  export everything available). The processing scope is exactly
  `full_input.jsonl`; never re-export or widen it.
- **LLM quality-evaluation operators are mandatory.** Use DataFlow LLM
  scoring/filter operators (`PromptedEvaluator`, `PromptedFilter`, ...) for
  quality scoring. Cost/latency is NOT a valid reason to fall back to pure
  heuristic rules - a slow LLM pass just takes longer. Rule operators are
  allowed only when the task has no LLM-scoring semantics or the LLM serving is
  unavailable; say so in the summary. Never let the LLM rewrite row text -
  score and filter only.
- **Full run is streaming, chunked, and executed by the upper layer.** The
  outer Codex drives the full scale through
  `loopai.agents.Obtainer.datamixer.dataflow_chunked_runner`
  (`--chunk-size 10000`, one chunk per pipeline launch, ordered merge) and must
  never load the whole export into a single DataFrame. The delivered pipeline
  must follow the `DATAFLOW_INPUT` / `DATAFLOW_CACHE_DIR` / `DATAFLOW_PREFIX`
  env-var convention so the scaffold can run it per chunk.
- **Never wrap agent-run or the chunked full run in a shell `timeout`**
  (e.g. `timeout 60 ...`). A shell timeout kills the inner Codex session or the
  chunked runner mid-flight and leaves the lake in a half-processed state. The
  1-hour budget applies only to the agent-run Codex session (trial delivery);
  the upper-layer full run has no time budget and may take many hours when LLM
  quality-evaluation operators score every row - let it finish.
- The agent runs with its own Codex home
  (`outputs/obtainer/.codex/dataflow/AGENTS.md`, legacy
  `codex_home_dataflow/AGENTS.md`), whose rules require it to deliver the
  trial-verified pipeline (never launch the full run itself), to gate export
  on the 5x L4 redundancy floor (skipped when the user explicitly specifies
  an L3 export), and to reuse the internalized pipeline skills under
  `$CODEX_HOME/skills/` when they match the target.
