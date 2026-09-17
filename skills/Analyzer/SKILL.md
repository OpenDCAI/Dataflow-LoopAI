# Analyzer Skill

## Purpose
Analyzer Skill is the Codex/Agent-facing capability for running LoopAI Analyzer independently. It analyzes evaluation outputs, writes Analyzer reports, emits stream events, returns unified success/error payloads, and compares completed versions of the same task and benchmark. Code, Text2SQL and Math Rollout share seven text reports, a training-plan JSON and a new annotated OJ copy.

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
- `--thread-id`
- `--version-id`
- `--resume`
- `--new-version`
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

`metric_recommend -> metric_score -> math_llmaj_label -> analyze_metric_report -> finish`

Math does not reuse the Code/Text2SQL OJ evidence parser. It uses
`numerical_match`, `math_verify`, or `choice_accuracy` for deterministic answer
scoring, then applies a Math-specific capability taxonomy to structured
step-level error evidence.

For evaluated Math JSON containing `eval[].results[].generations[]`, the metric
nodes reuse each generation's explicit Judger `correct` verdict. The report
adds five rollout bands and an evidence-based SFT/RL readiness assessment.
The SFT stage decision is binary and scoped to configurable engineering gates,
not a claim about training history. `08_training_plan.json` includes
`sft_completed`, `is_sft` (continue SFT), `is_rl` (RL pilot), and collection
domains with actual question-type tags, SFT/RL routing and source question IDs.
It reads all failure critiques for these new sections, regardless of the normal
per-tag sample limit. See [Math Rollout Input and Reports](MATH_ROLLOUT.md).

The in-memory state still carries:

- `state["current"]`
- `state["last_completed"]`

`--from-node` forces a specific Analyzer step. `--resume` loads the matching version-scoped checkpoint first, then falls back to Configer task state if no checkpoint exists.

## Historical Comparison
Code, Text2SQL and Math automatically search sibling version directories under
the same task's `analyzer/` directory for completed reports of the same task
type and Bench. The second version compares against the previous completed
version; later versions also compare against the first. Resuming the same
version is not a new round and retains its original baseline window. Incomplete
versions are not baselines. Legacy bundles without an index are imported only
when all seven reports, the training plan and matching OJ counts are available.

The new contract is `analyzer.historical_comparisons[Bench]`, also written into
reports 02/03 and `08_training_plan.json.historical_comparison`. It includes
full-population correctness and error-count changes, matched-question pass-rate
changes, improved/regressed counts and up to 20 examples of each. Match stable
question identity and content, not row order or random generation index. Metric,
question-set and sampling changes are audited; unavailable or incompatible
evidence must not be presented as verified training gains.

Set `analyzer.baseline_result_paths` per Bench or `baseline_result_path` for an
explicit baseline. A flat Math baseline without metric metadata also needs
`baseline_metric` to establish comparability. Unreadable explicit baselines
produce a notice, not silent substitution. The old `historical_comparison`
field remains compatible; General Text is outside this automatic-history extension.
Keep historical report directories and `.analyzer_report_history` even when
old resume checkpoints are cleaned up.

## Multiple Benches

Analyzer can consume two or more Judger results from `judger.bench_result` and
`judger.extra_bench_result`. Results with the same `task_type` are merged into
one Code/Text2SQL analysis run while `summary["bench_summaries"]` preserves per-bench sample
counts, pass rates, and failure distributions. Each Bench receives a separate
delivery directory and manifest entry. A single string
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

Bucket counts and construction counts have different meanings:

- `count` / `observed_count` counts every failed case exactly once, including
  cases that require review.
- For Code, Text2SQL, and General Text, `actionable_count` counts cases that
  pass their evidence gates. For Math, every resolved model error is actionable:
  strong step evidence produces step-level repair data, while weaker evidence
  produces whole-case contrastive data.
- `critique_samples_per_tag` controls only how many one-line critiques are read
  for semantic profiling. It never changes either population count.

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
inside each capability. Each metric failure must resolve to either a concrete
model-error bucket or `评测异常`. Exact grounded evidence produces step-level
repair data; weaker evidence keeps the concrete tag and produces whole-case
contrastive data. `评测异常` enters Metric regression with zero model-training
budget. If labeling retries still cannot produce a concrete route, Analyzer
stops report generation and resumes the same version instead of publishing a
`待诊断` result.

## Report Delivery Contract

Code/Text2SQL write a human-readable delivery bundle under:

```text
<runtime_output_dir>/评测最终报告/<code-or-text2sql>/<Bench>/
```

Math keeps its existing directory:

```text
<runtime_output_dir>/数学评测最终报告/<dataset_name>/
```

Code, Text2SQL and Math Rollout produce these seven text reports:

- `01_数据集背景与评测概览.txt`: dataset background, field mapping, and metric overview.
- `02_完整分析与审计报告.txt`: full bad-case audit followed by the same five-part analysis
  contract as Code/Text2SQL: failure taxonomy, data acquisition, training
  recipe, evaluation improvements, and next-iteration priorities.
- `03_最终报告.txt`: concise background, evaluation result, major failure
  modes, and recommended bucket allocation.
- `04_模型改进建议.txt`: prioritized model and Metric improvements.
- `05_数据爬取与构造建议.txt`: detailed acquisition and construction instructions.
- `06_Rollout五档能力分析.txt`: question types and all available failed critiques
  grouped by observed same-question success fractions.
- `07_SFT与RL训练阶段评估.txt`: binary SFT gate decision, RL pilot evidence,
  reasons, limitations and next validation steps.

The same directory also contains `08_training_plan.json` and
`09_oj_enriched.jsonl` (or `.json` for nested Math input). The training plan uses
common `task_type`, `sft_completed`, `is_sft`, `is_rl`, `domains`,
`question_refs` and `historical_comparison` fields. Missing evidence remains
unknown; single-sample questions populate only all-correct/all-wrong bands and
cannot establish rollout stability. Code/SQL questions without a topic or
usable question text may supply a capability tag, explicitly distinguished
from a question-topic tag.

Read `state["analyzer"]["report_artifacts"][Bench]["files"]`, with keys
`summary`, `report`, `final_report`, `suggestions`, `obtainer`, `rollout`,
`training`, `training_plan`, `enriched_oj`. Do not guess timestamped filenames.
`enriched_oj_paths` maps Bench names to new OJ files; `enriched_oj_path` is a
single-Bench alias. `run_analyzer_standalone_payload(...)` additionally exposes
the manifest at `data.result.report_artifacts`. CLI `--print-result` prints the
final state; read its `analyzer.report_artifacts` instead.

The parent bundle also contains `总览.txt`. Plain non-Rollout Math keeps its
five text reports plus annotated OJ; General Text keeps its existing report
route. The delivery bundle is not disabled by legacy Code/Text2SQL suggestion
toggles. `report_bundle_root` customizes Code/Text2SQL delivery;
`math_report_bundle_root` customizes Math delivery.

Reports use UTF-8 BOM and CRLF for Windows text readers and must not embed raw
JSON or internal action payloads. The new public OJ copies every original
Judger row and adds only `overall_error_tag` and `short_critique` to failures;
successful rows remain unchanged. Nested Math preserves `data`, `eval`, run
metadata and generation structure, annotating failed generations only. Never
overwrite or delete the source OJ. Code/Text2SQL request the critique in the
existing diagnosis call, not an extra per-case call. Before export, validate
source hashes and row correspondence; legacy checkpoints without source
provenance are explicitly marked `original_source_verified=false`.

Full error counts do not depend on the critique sample limit. Reports 06/07
read all available failed critiques. Runtime confidence, evidence and cached
model stages stay outside the delivery bundle. A direct-repair adapter may use
OJ annotations to design independent training data, but must verify answers,
deduplicate and prevent benchmark contamination.

Report stages cache by input, model and prompt; failed stages must not be
recorded as successful model review. `report_quick=true` (Code/Text2SQL) and
`metric_report_quick=true` (Math) are explicitly marked rules-only previews,
not substitutes for real model analysis. This report alignment does not add
an output-token cap. See [the output contract](../../docs/analyzer-report-output-contract.md).

## General Text Evidence

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
