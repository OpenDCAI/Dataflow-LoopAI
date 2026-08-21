# Production SFT Export Worker

Read this when starting, polling, or resuming `sft-export-agent`, or when the
export worker needs its injected recipe/schema policy.

For production SFT outflow, use the managed export worker wrapper instead of
manually driving `recipe validate/plan/preview/export`. The wrapper starts an
isolated Codex SDK worker and injects the detailed DataMixer recipe, schema,
validation, snapshot, and failure-handling policy into that worker's context.

## Schema mapping

Schema mapping must be dataset/bucket-aware. Do not use one global
`output.sources` fallback order across datasets whose fields have different
semantics. Prefer bucket-level schema blocks such as
`recipe.buckets[].schema.fields` or `recipe.buckets[].export.schema.fields`.
Fields may be composed with templates when the final training row needs several
source fields, for example `output.template: "<think>{chain}</think>{answer}"`
for reasoning + answer, or for text2sql:
`instruction.template: "{question}"` and
`input.template: "{evidence}\n{sql_schema}\n{sql_block}"`.

## Start, check, resume

```bash
loopai-obtainercli dm --root /path/to/warehouse sft-export-agent start \
  --run ./outputs/sft_export_run \
  --analysis-report ./outputs/analyzer_report.md \
  --format alpaca \
  --target-records 100000 \
  --out ./outputs/sft_export_run/export

loopai-obtainercli dm --root /path/to/warehouse sft-export-agent status \
  --run ./outputs/sft_export_run

loopai-obtainercli dm --root /path/to/warehouse sft-export-agent resume \
  --run ./outputs/sft_export_run \
  --message "Exclude buckets whose output field falls back to text, then re-export."
```

`start` and `resume` run in the background by default and return a PID plus log
paths; poll with `status`. Use `--foreground` only when the caller intends to
block until the inner Codex SDK worker finishes.

Do not pass `--model` to `sft-export-agent` unless the user explicitly requests
a one-off override. The worker should use Starter's configured Codex model by
default.

## When to start and how to recover

Start the export worker only after the DataFlowAgent post-processing stage has
completed and the final L4 dataset scale meets the recipe target; the lake must
hold at least 5x the target volume per bucket and overall before export is
allowed. WebAgent and the acquisition worker may still be active and continue
adding data; their terminal states are not export prerequisites.

Decide between `resume` and a fresh `start` (see `orchestrator.md`):

- Use `resume` when the same worker understood the target but needs a bounded
  correction to recipe mapping, bucket filters, normalization, or validation.
- Use a fresh `start` when the worker context is polluted, picked the wrong
  task, or needs a different high-level strategy.

## Injected worker constraints

The worker wrapper owns the detailed constraints. For Alpaca SFT it requires
final rows to contain exactly `instruction`, `input`, and `output`, forbids
`output` fallback to whole-record text fields, rejects `instruction == output`,
requires DataMixer recipe export with snapshot, and writes `final_report.json`
with manifest, snapshot, digest, planned-versus-actual bucket mix, validation
evidence, and blockers. For datasets where a field like `output` is a noisy
trace and `answer` is the gold label, the worker must define that bucket's
schema explicitly instead of letting a global mapping choose the wrong source.
