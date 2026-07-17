---
name: trainer
description: Use this skill when the user wants LoopAI to validate training data, generate and approve LLaMA-Factory training YAML, start SFT/training, monitor Trainer progress events, or inspect Trainer failures from starter.yaml or runtime state.
---

# Trainer Skill

## Purpose

Trainer Skill is the Codex-facing entry point and the canonical Python implementation for LoopAI model training.

Use this skill for:

- Starting SFT or Trainer training from `starter.yaml`
- Validating Trainer configuration and dataset paths
- Generating LLaMA-Factory training YAML
- Running Trainer and reading structured StreamEvent progress
- Returning structured Trainer errors

Do not use this skill for Judger, Analyzer, data crawling, or broad project refactors.

## Python Implementation

```text
loopai/skills/Trainer/
├── __init__.py        # prepare() / run_prepared() / run() / result helpers
├── trainer_agent.py   # LangGraph subgraph used by Starter and the skill runner
├── nodes/             # data validation, config generation, training execution
├── utils/             # events, task manager, parsers, training utilities
├── templates/         # bundled training templates
├── results.py         # parses metrics and selects the best checkpoint
├── runner.py          # skill entry that runs the Trainer subgraph
└── runtime_config.py  # resolves kwargs/env/state/starter.yaml
```

The root skill description lives at:

```text
skills/Trainer/SKILL.md
```

## Mandatory YAML Approval

For every user-initiated training round, use this two-stage workflow:

1. Call `prepare()` to validate the data and generate the final training YAML. This stage must not start training.
2. Read `result["trainer"]["trainer_result"]["data"]` and show the user:
   - `config_path`
   - the complete `config_yaml` in a YAML code block
   - the important values such as dataset/model paths, learning rate, epochs, batch size, LoRA fields, devices, output directory, and `save_total_limit`
3. Stop and wait for explicit user approval. Do not treat a previous round's approval as approval for a new round.
4. If the user requests edits, update the generated YAML, call `inspect_prepared_config()`, show the complete updated YAML, and wait for approval again.
5. Only after approval, call `run_prepared()` with the displayed `config_path`, its displayed `config_sha256`, and the `trainer_version_id` returned by `prepare()`.
6. Keep `run_prepared()` in the foreground until training reaches `completed`, `failed`, or `cancelled`.

Never call `run()` directly for an interactive, user-initiated training request. Keep `run()` only as a backward-compatible entry point for explicitly non-interactive callers that intentionally opt out of human approval.

The SHA-256 check is part of the approval boundary. If the YAML changes after it is shown, `run_prepared()` must reject it; show the changed YAML and request approval again.

## Quick Start

### Python API

```python
from loopai.skills.Trainer import prepare, run_prepared

prepared = prepare(
    config_path="starter.yaml",
    thread_id="trainer_task_001",
)
approval = prepared["trainer"]["trainer_result"]["data"]
print(approval["config_yaml"])

# Stop here. Ask the user to approve the complete YAML above.

result = run_prepared(
    prepared_config_path=approval["config_path"],
    expected_config_sha256=approval["config_sha256"],
    thread_id="trainer_task_001",
    version_id=approval["trainer_version_id"],
)
```

### Explicit State

```python
from loopai.skills.Trainer import prepare

prepared = prepare(
    state={
        "task_id": "trainer_task_001",
        "output_dir": "./outputs",
        "trainer": {
            "train_framework": "llamafactory",
            "llamafactory_dir": "/path/to/LLaMA-Factory",
            "llamafactory_env_path": "/path/to/env/bin",
            "CUDA_VISIBLE_DEVICES": "0",
            "train_input_dataset_path": "/path/to/data.json",
            "train_input_model_name": "/path/to/model",
            "train_input_task_description": "SFT a chat assistant",
            "train_input_config_template_path": "/path/to/template.yaml",
        },
    },
    thread_id="trainer_task_001",
)
```

## Runtime Configuration

Priority:

```text
kwargs > environment variables > state["trainer"] > state["system"] > defaults
```

When `TASK_ID` is set, Trainer Skill loads the task-scoped `trainer` section through Configer before running:

```python
from loopai.skills.Configer import get_configer_task_state_config

cfg = get_configer_task_state_config("trainer", task_id=TASK_ID)
```

`DB_PATH` must also be set for task-scoped loading. After the run completes or fails, Trainer Skill writes structured Trainer result fields back through Configer:

```python
from loopai.skills.Configer import update_configer_task_state_config

update_configer_task_state_config("trainer", updates, task_id=TASK_ID)
```

Useful environment variables:

- `TASK_ID`
- `DB_PATH`
- `VERSION_ID`
- `OUTPUT_DIR`
- `TRAINER_OUTPUT_DIR`
- `TRAIN_FRAMEWORK`
- `TRAIN_DATASET_PATH`
- `TRAIN_MODEL_PATH`
- `TRAIN_TASK_DESCRIPTION`
- `TRAIN_CONFIG_TEMPLATE_PATH`
- `LLAMAFACTORY_DIR`
- `LLAMAFACTORY_ENV_PATH`
- `CUDA_VISIBLE_DEVICES`
- `SWANLAB_API_KEY`
- `TRAIN_USE_SWANLAB`

Required Trainer fields:

- `train_framework`
- `train_input_dataset_path`
- `train_input_task_description`
- `train_input_config_template_path`
- `train_input_model_name`
- `llamafactory_dir` when `train_framework` is `llamafactory`

## Versioned Runtime

Each Trainer run owns one `version_id`.

By default, `prepare()` or `run()` asks its event writer to generate a fresh `version_id`. Pass the ID returned by `prepare()` into `run_prepared()` so configuration preparation and training remain one logical Trainer run. You can override it with:

- `version_id=...`
- `trainer_version_id=...`
- `VERSION_ID`

Runtime status is synchronized through `TaskRuntimeItem` with:

```text
task_id = task_id
node_name = trainer
version = trainer_version_id
status = running | completed | failed
```

Trainer static files are written under:

```text
{output_dir}/{task_id}/trainer/{trainer_version_id}/
```

Important state fields:

- `trainer_version_id`
- `trainer_output_dir`
- `trainer_event_log_path`
- `trainer_training_task_id`

Keep `context_id` as the task id when reading events or runtime state. Use `trainer_version_id` only to distinguish a specific run.

## Prefill Guidance

Use `prefill_guide()` before launching Trainer when the task may not have enough config:

```python
from loopai.skills.Trainer import prefill_guide

guide = prefill_guide(state, task_type="sft")
if not guide["ready"]:
    print(guide["user_required_fields"])
```

`trainer_prefill_guide` is also written to `state["trainer"]` during runtime resolution.

Fields that usually require user or task-specific input:

- `train_input_dataset_path`
- `train_input_model_name`
- `train_input_task_description`
- `llamafactory_dir`

Fields that Trainer can usually prefill:

- `train_framework`: defaults to `llamafactory`
- `train_input_config_template_path`: defaults to the bundled SFT YAML template
- `CUDA_VISIBLE_DEVICES`: defaults to `0`
- `train_input_use_swanlab`: defaults to `false`

If `guide["user_required_fields"]` is non-empty, ask the user or Configer to fill those fields before starting training. Do not start Trainer only with placeholder paths.

## Events

Trainer Skill emits structured `StreamEvent` entries with:

- `current`
- `node`
- `status`
- `progress`
- `message`
- `data`
- `context_id`
- `error`

Read persisted events:

```python
from loopai.skills.Trainer import load_events

events = load_events(
    task_id="trainer_task_001",
    output_dir="./outputs",
    version_id="trainer_version_uuid",
)
```

## Result Analysis

Trainer Skill writes core training results back to `state["trainer"]` after a run:

- `trainer_result`
- `trainer_last_error`
- `trainer_result_analysis`
- `trainer_result_summary`
- `trainer_best_checkpoint`
- `trainer_best_metric`
- `trainer_best_checkpoint_path`
- `update_model_path`
- `training_checkpoints`
- `training_step_losses`

Use `analyze_results()` when you need to inspect training artifacts without starting a new training run:

```python
from loopai.skills.Trainer import analyze_results

analysis = analyze_results(
    task_id="trainer_task_001",
    output_dir="./outputs",
)

best_checkpoint = analysis["data"]["best_checkpoint"]
summary = analysis["data"]["summary"]
```

The analyzer reads, when available:

- `trainer_log.jsonl`
- `metrics/metrics.json`
- `checkpoint-*` directories

Best checkpoint selection rule:

1. Prefer the checkpoint aligned with the lowest `eval_loss`.
2. If no `eval_loss` is available, use the lowest training `loss`.
3. If no loss metric is available, choose the latest checkpoint.

When comparing multiple Trainer runs, call `analyze_results()` for each run and compare:

- `summary["best_metric"]`
- `summary["best_checkpoint_name"]`
- `summary["checkpoint_count"]`
- `summary["metric_count"]`
- `trainer_result.status`
- `trainer_last_error`

Prefer reporting the selected checkpoint path from `trainer_best_checkpoint_path` or `update_model_path` as the model candidate for the next Judger or Analyzer step.

## Execution Lifetime

Trainer runs synchronously in the foreground. A Trainer invocation is not
complete when the training process has merely started; it is complete only
after the training status becomes `completed`, `failed`, or `cancelled` and
the local Trainer runner exits.

- Do not launch the Trainer runner with `&`, `nohup`, or a detached shell.
- If a command execution yields a running cell/session id, keep waiting on
  that same execution until it exits.
- Do not finish the Codex turn while the Trainer command is still running.
- Progress events with status `running` are intermediate updates, not a final
  tool result.

## MCP Status (Disabled)

The local LoopAI MCP route is currently disabled. Do not call or register
`trainer_run` / `trainer_load_events`, and do not start `loopai.mcp.server`.
Call the local Trainer skill or its script/runner entrypoints directly.

Read the task-scoped trainer section through Configer before preparing the YAML. Treat task-state confirmation and final YAML approval as separate gates: the first confirms the task inputs; the second approves the exact executable training configuration.

## Errors

Trainer failures should use the common structured shape:

```json
{
  "ok": false,
  "status": "failed",
  "message": "Trainer failed.",
  "data": null,
  "error": {
    "type": "RuntimeError",
    "code": "UNHANDLED_EXCEPTION",
    "detail": "...",
    "recoverable": true
  }
}
```

Prefer reporting this structured error over only returning raw traceback text.
