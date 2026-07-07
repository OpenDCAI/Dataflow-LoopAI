# Analyzer Skill

## Purpose
Analyzer Skill is the Codex/Agent-facing capability for running LoopAI Analyzer independently. It analyzes evaluation outputs, writes Analyzer reports, emits stream events, returns unified success/error payloads, and can compare current results with a historical baseline.

## Python Implementation
The Python skill layer lives in:

`loopai/skills/Analyzer`

Core Analyzer business nodes remain in:

`loopai/agents/Analyzer`

Do not move or rewrite `eval_model.py`, `analyze_result.py`, or `draw_conclusion.py` for normal skill usage. The agent-side standalone files are compatibility wrappers.

## Runtime Entry
```python
from loopai.skills.Analyzer import run

run(state=None, resume=False, from_node=None, baseline_result_path=None)
```

`run(...)` is the Codex/sub-agent process entry. It emits the unified LoopAI
payload to stdout and exits, matching the latest Judger skill pattern. For
in-process calls use `run_analyzer_standalone(...)`.

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

Legacy import remains valid:

```python
from loopai.agents.Analyzer.standalone import run_analyzer_standalone
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

## Environment Variables
Runtime configuration should come from environment/system runtime where possible:

- `ANALYZER_API_KEY`
- `ANALYZER_MODEL`
- `ANALYZER_BASE_URL`
- `TASK_ID`
- `DB_PATH`
- `ANALYZER_CHECKPOINT_PATH`
- `ANALYZER_VERSION_ID` / `VERSION_ID`

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

Standalone function pipeline remains:

`eval_model -> analyze_result -> draw_conclusion -> finish`

The in-memory state still carries:

- `state["current"]`
- `state["last_completed"]`

`--from-node` forces a specific Analyzer step. `--resume` loads the matching version-scoped checkpoint first, then falls back to Configer task state if no checkpoint exists.

## Historical Comparison
Set `baseline_result_path` to enable Historical Comparison. Current results come from `analyzer.eval_result_path`; baseline results come from `baseline_result_path`.

Analyzer preserves the `historical_comparison` field and appends a `Historical Comparison` section to report/final_report outputs when available. Missing or unreadable baseline files produce a warning instead of failing the main flow.

## Stream Runtime
Analyzer standalone follows the same base event writer style as Judger:

```python
from loopai.common.event_tool import StreamEvent, get_event_writer
```

Analyzer-specific stdout/state-message/redaction helpers live in:

```text
loopai/skills/Analyzer/event_tool.py
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
