# Analyzer

## Purpose
Analyze existing evaluation outputs and generate metric summaries, reports, and recommendations without rewriting Analyzer agent logic.

## Runtime Entry
`from loopai.skills.Analyzer import run`

The wrapper calls Analyzer standalone execution:

```python
run(
    state=state,
    thread_id="analyzer-demo",
    checkpoint_path="outputs/analyzer_checkpoints.sqlite",
    resume=False,
    from_node=None,
    baseline_result_path=None,
)
```

Standalone CLI:

```bash
python examples/scripts/run_analyzer_standalone.py \
  --config-path /tmp/analyzer_full_demo.json \
  --thread-id analyzer-demo \
  --checkpoint-path outputs/analyzer_checkpoints.sqlite \
  --baseline-result-path /path/to/previous.jsonl \
  --print-result
```

Resume:

```bash
python examples/scripts/run_analyzer_standalone.py \
  --config-path /tmp/analyzer_full_demo.json \
  --thread-id analyzer-demo \
  --checkpoint-path outputs/analyzer_checkpoints.sqlite \
  --resume \
  --print-result
```

Force a resume step:

```bash
python examples/scripts/run_analyzer_standalone.py \
  --config-path /tmp/analyzer_full_demo.json \
  --thread-id analyzer-demo \
  --checkpoint-path outputs/analyzer_checkpoints.sqlite \
  --resume \
  --from-node draw_conclusion \
  --print-result
```

## Required State
`task_id`, `output_dir`, and `analyzer` config. Existing Analyzer state fields remain unchanged.

Optional historical comparison config:

```json
{
  "analyzer": {
    "eval_result_path": "current.jsonl",
    "baseline_result_path": "previous.jsonl"
  }
}
```

When `baseline_result_path` is present, Analyzer appends a `Historical Comparison` section to report outputs.

## StreamEvent
Use Analyzer's existing StreamEvent emissions and state fields. This skill does not add or replace shared event infrastructure.

## Checkpoint
Standalone Analyzer uses `checkpoint_path` to save SQLite checkpoints. Each step saves `current` before execution and `last_completed` after success.

## Historical Comparison
Analyzer compares current and baseline jsonl files when `baseline_result_path` is set. It reports pass-rate change, score gap change when available, error distribution changes, improved cases, and regressed cases. Missing or unreadable baseline files produce a warning instead of failing the main Analyzer flow.

## Success Response
Return the Analyzer runner result/state directly.

## Error Response
Let the Analyzer runner surface existing errors unchanged.
