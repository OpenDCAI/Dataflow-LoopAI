# Analyzer Skill

## Purpose
Analyzer Skill is the Codex/Agent-facing capability for running LoopAI Analyzer independently. It analyzes evaluation outputs, writes Analyzer reports, supports checkpoint resume, and can compare current results with a historical baseline.

## Python Implementation
The Python skill layer lives in:

`loopai/skills/Analyzer`

Core Analyzer business nodes remain in:

`loopai/agents/Analyzer`

Do not move or rewrite `eval_model.py`, `analyze_result.py`, or `draw_conclusion.py` for normal skill usage. The agent-side standalone files are compatibility wrappers.

## Runtime Entry
```python
from loopai.skills.Analyzer import run

result = run(
    state=state,
    resume=False,
    from_node=None,
    baseline_result_path=None,
)
```

Direct runner:

```python
from loopai.skills.Analyzer.runner import run_analyzer_standalone
```

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

Config JSON should not store API keys. If legacy config contains `analyze_api_key`, it is only a fallback and print-result must redact it.

## Priority
General runtime priority:

`kwargs > env > state["analyzer"] > default`

API key priority is env-first by design:

`ANALYZER_API_KEY > legacy state["analyzer"]["analyze_api_key"]`

Thread id priority:

`CLI --thread-id > TASK_ID env > state/default`

Checkpoint path priority:

`CLI --checkpoint-path > ANALYZER_CHECKPOINT_PATH env > outputs/analyzer_checkpoints.sqlite`

## Checkpoint And Resume
Standalone Analyzer uses a function-level pipeline:

`eval_model -> analyze_result -> draw_conclusion -> finish`

The runner stores:

- `state["current"]`
- `state["last_completed"]`

`--resume` continues from the checkpoint step and does not rerun completed steps. `--from-node` forces a specific Analyzer step.

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
<output_dir>/<TASK_ID or thread_id>/analyzer.pkl
```

The Analyzer skill writer supports `--stream-stdout` for JSONL stdout output and appends events to `state["messages"]`.

Analyzer node-level `StreamEvent` calls are still compatible with LangGraph runtime. In standalone/Codex mode they are routed through the Analyzer skill writer via safe fallback, so missing LangGraph runtime does not crash execution.

Analyzer event payloads are JSON serializable and redact sensitive keys such as `api_key`, `analyze_api_key`, `token`, and `*_key`.

## Success Response
Return the Analyzer final state/result directly.

## Error Response
Runtime configuration errors should be clear. If Analyzer needs an LLM key and no key is available, raise:

`missing required env: ANALYZER_API_KEY`
