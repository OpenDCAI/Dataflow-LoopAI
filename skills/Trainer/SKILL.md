---
name: trainer
description: Use this skill when the user wants LoopAI to validate training data, generate LLaMA-Factory training configs, start SFT/training through TrainerAgent, monitor Trainer progress events, or inspect Trainer failures from starter.yaml or runtime state.
---

# Trainer Skill

## Purpose

Trainer Skill is the Codex-facing entry point for LoopAI model training. It wraps the existing `loopai/agents/Trainer` implementation instead of duplicating training logic.

Use this skill for:

- Starting SFT or TrainerAgent training from `starter.yaml`
- Validating Trainer configuration and dataset paths
- Generating LLaMA-Factory training YAML
- Running TrainerAgent and reading structured StreamEvent progress
- Returning structured Trainer errors

Do not use this skill for Judger, Analyzer, data crawling, or broad project refactors.

## Python Implementation

```text
loopai/skills/Trainer/
├── __init__.py        # run() / load_events() / analyze_results() / prefill_guide()
├── results.py         # parses metrics and selects the best checkpoint
├── runner.py          # skill entry that calls TrainerAgent
└── runtime_config.py  # resolves kwargs/env/state/starter.yaml

loopai/agents/Trainer/
└── ...                # existing TrainerAgent implementation
```

The root skill description lives at:

```text
skills/Trainer/SKILL.md
```

## Quick Start

### Python API

```python
from loopai.skills.Trainer import run

result = run(
    config_path="starter.yaml",
    thread_id="trainer_task_001",
)
print(result["trainer"].get("trainer_result"))
```

### Explicit State

```python
from loopai.skills.Trainer import run

result = run(
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

By default, `run()` generates a fresh UUID. You can override it with:

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

TrainerAgent emits structured `StreamEvent` entries with:

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

## Invocation Guidance

Prefer calling the local Trainer skill or its script/runner entrypoints directly.

When you need to inspect or confirm task config first, read the task-scoped trainer section through Configer before launching training.

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
