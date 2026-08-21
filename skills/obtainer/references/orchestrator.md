# Obtainer Orchestrator Contract

Read this when you are the outer Codex agent delegating an obtain task: parsing
the intent, starting the Obtainer Orchestrator, polling its structured status,
and handling terminal states. The orchestrator owns lake bootstrap, sub-agent
dispatch, progress gating, and the final deliverable report; the policy in
SKILL.md is its domain policy.

## Parse the data need into an intent

Extract from the Analyzer report / user request / recipe:

- `--objective`: the sample shape needed, not only error keywords
- `--keywords`: search / domain hints
- `--target-datasets`: how many buckets/datasets
- `--message`: compact failure taxonomy, quality gates, proportions

## Start the orchestrator

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm \
  obtainer-orchestrator start \
  --run ./outputs/obtainer_run_<timestamp> \
  --objective "buggy and fixed Python code pairs for syntax repair SFT" \
  --keywords "python syntax error, code repair dataset" \
  --target-datasets 2 \
  --message "Analyzer report: ...; require license=unknown and quality>=0.8" \
  --python-executable /path/to/loopai-env/bin/python
```

`start` launches the orchestrator's inner Codex SDK worker in the background
and returns the run directory. Use `--foreground` only when you intend to
block.

## Poll the orchestrator

A full obtainer orchestration runs for roughly 3-4 hours (acquisition +
DataFlow L4 + export). Poll no more often than every 5 minutes
(`sleep 300 && ... status ...`); faster polling wastes tokens and does not
speed up the run. `updated_at` / `stale` tell you whether the orchestrator is
alive far better than polling frequency does.

```bash
${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm \
  obtainer-orchestrator status --run ./outputs/obtainer_run_<timestamp> --json
```

Read the machine-readable contract (`schema_version: 1`):

- `state`: `idle | running | completed | completed_with_errors | failed | interrupted | stopped`
- `phase`: `bootstrap | acquiring | gating | dataflow | exporting | finalizing`
- `progress` (0..1), `message`, `updated_at` (heartbeat), `stale`
- `next_action`:
  - `poll`: keep polling
  - `start_dataflow`: the lake volume gate already passed while acquisition is
    still running; the orchestrator should dispatch DataFlow L4 in parallel,
    keep polling
  - `report`: read `final_report.json` and report
  - `resume`: the orchestrator concluded while sub-agents were still running or
    returned no valid result; run `resume` to continue
  - `blocked`: surface error + gates to the user
- `subtasks[]`: each managed sub-agent (state / progress / message / run_dir)
- `gates[]`: e.g. `lake_volume`, `dataflow_l4` with `ok` + `detail`
- `lake`: warehouse, dataset / record counts, quality_levels

Never judge progress from `message` alone; use the structured fields.

## Terminal handling

- `completed` / `next_action=report`: read `final_report.json` in the run dir
  and report warehouse, datasets, record counts, recipe / export artifacts,
  lineage, manifests, and snapshots.
- `interrupted` / `next_action=resume`: the orchestrator concluded while a
  sub-agent was still running or returned no valid result - run
  `${LOOPAI_PYTHON_EXECUTABLE:-python} -m loopai.skills.ObtainerCLI.cli dm \
  obtainer-orchestrator resume --run <dir> --message "<why / resume from where>"`;
  do NOT take over its sub-agents.
- `failed` / `next_action=blocked`: read `error` + failing `gates`, tell the
  user, and offer `resume` once the blocker is addressed.
- `stale=true` while `state=running`: warn that the orchestrator may be hung
  and offer `stop` or `resume`.

## Resume vs fresh start

- Use `resume` when the same worker understood the target but needs a bounded
  correction to recipe mapping, bucket filters, normalization, or validation.
- Use a fresh `start` when the worker context is polluted, picked the wrong
  task, or needs a different high-level strategy.

## Hard constraints for the main agent

- Never run `dm lake ...`, `dataset-acquisition-agent`, `sft-export-agent`,
  `dataflow agent-run`, `searchagent`, `webagent`, or `download manifest`
  yourself for a normal obtain task - the orchestrator owns those.
- Never `kill` / `pkill` the orchestrator's worker processes. The worker is
  managed by the CLI (`start` / `resume` / `stop`); raw process kills leave it
  in a stuck `running` state and break the run. If the status looks stuck,
  first check `updated_at` / `stale`; only then use
  `dm obtainer-orchestrator stop --run <dir>` followed by `resume` (never raw
  `kill`), and keep polling otherwise.
- Never claim obtainer completion without a `final_report.json` reported by
  the orchestrator.
