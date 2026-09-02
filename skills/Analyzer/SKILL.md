# Analyzer Skill

## Purpose
Analyzer Skill is the Codex/Agent-facing capability for running LoopAI Analyzer independently. It analyzes evaluation outputs, writes Analyzer reports, emits stream events, returns unified success/error payloads, and can compare current results with a historical baseline.

## Python Implementation
The Python skill layer and Analyzer business implementation live in:

`loopai/skills/Analyzer`

Analyzer no longer depends on `loopai/agents/Analyzer`. The legacy agents-side
Analyzer directory has been removed; Codex and WebUI should call the skill
entry directly.

## Runtime Entry
```python
from loopai.skills.Analyzer import run, resume_run

run(state=None, resume=False, from_node=None, baseline_result_path=None)
resume_run(state=None, from_node=None, baseline_result_path=None)
```

`run(...)` is the Codex/sub-agent process entry. It emits the unified LoopAI
payload to stdout and exits, matching the latest Judger skill pattern. For
in-process calls use `run_analyzer_standalone(...)`.

Use `resume_run(...)` for continuation. It always passes `resume=True` and
selects the latest incomplete version checkpoint for the task. A normal
`run(...)` also resumes the latest incomplete version by default. To explicitly
start a new run, pass `new_version=True` (or use the CLI `--new-version`).

```json
{
  "ok": true,
  "status": "completed",
  "message": "Analyzer completed.",
  "data": {},
  "error": null
}
```

Direct runner:

```python
from loopai.skills.Analyzer.runner import run_analyzer_standalone
```

`run_analyzer_standalone(...)` keeps the legacy behavior and returns the final state directly.

LangGraph-compatible class import:

```python
from loopai.skills.Analyzer.analyzer_agent import AnalyzerAgent
```

## CLI
```bash
python examples/scripts/run_analyzer_standalone.py   --config-path /tmp/analyzer_full_demo.json   --baseline-result-path /tmp/analyzer_demo_baseline.jsonl   --print-result
```

Supported options:

- `--config-path`
- `--resume`
- `--from-node`
- `--checkpoint-path`
- `--baseline-result-path`
- `--print-result`
- `--list-nodes`
- `--stream-stdout`
- `--request-timeout-seconds` (default: `300`)

## Environment Variables
Runtime configuration should come from environment/system runtime where possible:

- `ANALYZER_API_KEY`
- `ANALYZER_MODEL`
- `ANALYZER_BASE_URL`
- `TASK_ID`
- `DB_PATH`
- `ANALYZER_CHECKPOINT_PATH`
- `ANALYZER_VERSION_ID` / `VERSION_ID`
- `ANALYZER_REQUEST_TIMEOUT_SECONDS`

Config JSON should not store API keys. Runtime API key/model/base URL should be placed under the task/system config when available:

- `system.analyzer_api_key`
- `system.analyzer_model`
- `system.analyzer_base_url`

If those are absent, Analyzer can fall back to:

- `system.starter_api_key`
- `system.starter_model_name` / `system.starter_model_path`
- `system.starter_base_url`
- `ANALYZER_API_KEY` / `ANALYZER_MODEL` / `ANALYZER_BASE_URL`
- legacy `state["analyzer"]["analyze_api_key"]`

## Priority
General runtime priority:

`kwargs > system runtime > env > state["analyzer"] > default`

API key priority:

`kwargs > system analyzer/starter key > ANALYZER_API_KEY > legacy analyzer.analyze_api_key`

Thread id priority:

`CLI --thread-id > TASK_ID env > state/default`

## State And Configer

Analyzer runtime state should be read and updated through Configer when `DB_PATH` and `TASK_ID` are available:

```python
from loopai.skills.Configer import (
    get_configer_task_state_config,
    update_configer_task_state_config,
)
```

Analyzer keeps a lightweight version-scoped checkpoint for standalone resume. The checkpoint key is `(task_id/thread_id, version_id)`, so a completed version1 run will not cause version2 of the same task to be skipped.

Use a new version id for a new attempt:

```bash
python examples/scripts/run_analyzer_standalone.py \
  --config-path examples/config/starter.yaml \
  --thread-id task-001 \
  --version-id version2
```

Analyzer output files and event files are version-scoped:

```text
<output_dir>/<task_id>/analyzer/<version_id>/
```

Standalone selects the pipeline from `analyzer.analyze_task_type`:

`eval_model -> analyze_result -> draw_conclusion -> finish`

General text and Math use the metric pipeline:

`metric_recommend -> metric_score -> analyze_metric_report -> finish`

Math does not reuse the Code/Text2SQL OJ evidence parser. It uses
`numerical_match`, `math_verify`, or `choice_accuracy` for deterministic answer
scoring, then applies a Math-specific capability taxonomy to structured
step-level error evidence.

The in-memory state still carries:

- `state["current"]`
- `state["last_completed"]`

`--from-node` forces a specific Analyzer step. `--resume` loads the matching version-scoped checkpoint first, then falls back to Configer task state if no checkpoint exists.

## Historical Comparison
Set `baseline_result_path` to enable Historical Comparison. Current results come from `analyzer.eval_result_path`; baseline results come from `baseline_result_path`.

Analyzer preserves the `historical_comparison` field and appends a `Historical Comparison` section to report/final_report outputs when available. Missing or unreadable baseline files produce a warning instead of failing the main flow.

## Multiple Benches

Analyzer can consume two or more Judger results from `judger.bench_result` and
`judger.extra_bench_result`. Results with the same `task_type` are merged into
one analysis run while `summary["bench_summaries"]` preserves per-bench sample
counts, pass rates, and failure distributions. A single string
`analyzer.eval_result_path` remains supported. Standalone callers may also use:

```json
{
  "analyzer": {
    "analyze_task_type": "code",
    "eval_result_path": ["humaneval.jsonl", "mbpp.jsonl"]
  }
}
```

Do not combine `code`, `text2sql`, `math`, and general-text results in one Analyzer
route; each task type keeps its own analysis rules.

## Data Bucket Strategy

The final report includes `obtainer_stats.allocation_plan`. Analyzer first
reclassifies fallback `other` records with runtime/parser evidence, then
computes a first-round data budget from observed need, classification
confidence, severity, transfer value, learnability prior, and data cost.
`other`/unresolved records receive zero training allocation and enter a
diagnostic queue. Each actionable bucket is capped by default at 50%, and the
plan explicitly requires pilot-training gains to update later rounds.

Analyzer keeps four independent bucket routes:

- Code: output contract, syntax/completion, interface/scope, semantic logic,
  boundary robustness, and runtime efficiency.
- Text2SQL: SQL output contract, syntax, schema linking, semantic correctness,
  type/value handling, and runtime efficiency.
- General Text: instruction/format following, relevance/intent, factuality and
  grounding, reasoning consistency, completeness/coverage, language quality,
  and safety/refusal boundaries.
- Math: answer extraction/format, arithmetic, algebra/symbolic manipulation,
  problem modeling, strategy/theorem selection, multi-step consistency, and
  verification/completeness.

Math uses a two-level structure. Capability buckets determine the recommended
training allocation; algebra, geometry, probability/statistics, calculus,
number theory, and combinatorics are reported as `domain_breakdown` values
inside each capability. A final-answer mismatch without trustworthy step-level
evidence stays in the zero-budget diagnostic queue instead of being guessed
into a Math capability bucket.

General Text uses structured evaluator labels and reasons first. Empty answers,
verifiable format violations, and obvious refusal patterns provide deterministic
fallback evidence. Generic exact-match failures are not guessed into factuality
or reasoning; unresolved samples enter the zero-budget diagnostic queue. The
plan allocates by capability first and reports the observed domain distribution
inside each capability bucket.

The General Text design follows these established ideas without claiming to
reimplement the full paper algorithms:

- [HELM](https://arxiv.org/abs/2211.09110) (TMLR 2023): multi-dimensional model evaluation.
- [InstructGPT](https://proceedings.neurips.cc/paper_files/paper/2022/hash/b1efde53be364a73914f58805a001731-Abstract.html) (NeurIPS 2022): user intent and instruction following.
- [TruthfulQA](https://aclanthology.org/2022.acl-long.229/) (ACL 2022): truthfulness separated from informativeness.
- [Skill-It!](https://proceedings.neurips.cc/paper_files/paper/2023/hash/70b8505ac79e3e131756f793cd80eb8d-Abstract-Conference.html) (NeurIPS 2023): prerequisite and ordered skill acquisition.
- [DoReMi](https://proceedings.neurips.cc/paper_files/paper/2023/hash/dcba6be91359358c2355cd920da3fcbd-Abstract-Conference.html) (NeurIPS 2023): adaptive data mixtures instead of raw-frequency mixing.
- [LESS](https://proceedings.mlr.press/v235/xia24c.html) (ICML 2024): targeted data selection and empirical influence/utility.

## Model Request Timeout

Analyzer model requests use a 300-second client timeout by default. Conclusion
requests stream response chunks so long output does not need to wait for the
entire completion before the connection becomes active. A provider/proxy `524`
may still enforce its own shorter gateway limit; in that case Analyzer records
the elapsed time and prompt length, then retries once with compact evidence.

## Stream Runtime
Analyzer standalone follows the same base event writer style as Judger:

```python
from loopai.common.event_tool import StreamEvent, get_event_writer
```

Events are written to:

```text
<output_dir>/<TASK_ID or thread_id>/analyzer/<version_id>/analyzer.pkl
```

The Analyzer skill writer supports `--stream-stdout` for JSONL stdout output and appends events to `state["messages"]`.

Analyzer node-level `StreamEvent` calls are still compatible with LangGraph runtime. In standalone/Codex mode they are routed through the Analyzer skill writer via safe fallback, so missing LangGraph runtime does not crash execution.

Analyzer event payloads are JSON serializable and redact sensitive keys such as `api_key`, `analyze_api_key`, `token`, and `*_key`.

Analyzer uses `emit_success(..., stream_writer=writer)` and
`emit_error(..., stream_writer=writer)` for terminal status updates. Analyzer
emits explicit terminal events:

- `analyzer.completed`
- `analyzer.failed`

## MCP Tools
Analyzer MCP exposure is currently disabled. Do not register `analyzer_run` or `analyzer_load_events` until the team re-enables Analyzer MCP routing.

## Success Response
`from loopai.skills.Analyzer import run` emits:

```json
{
  "ok": true,
  "status": "completed",
  "message": "Analyzer pipeline completed.",
  "data": {
    "task_id": "",
    "version_id": "default",
    "current": "finish",
    "last_completed": "finish",
    "output_dir": "",
    "historical_comparison": {},
    "state": {}
  },
  "error": null
}
```

## Error Response
Analyzer returns the unified error payload. Runtime configuration errors are recoverable. If Analyzer needs an LLM key and no key is available, the error detail is:

`missing required env: ANALYZER_API_KEY`
