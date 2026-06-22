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
├── __init__.py        # run() / load_events()
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

Useful environment variables:

- `TASK_ID`
- `OUTPUT_DIR`
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

events = load_events(task_id="trainer_task_001", output_dir="./outputs")
```

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
