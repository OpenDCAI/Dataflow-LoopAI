---
name: trainer
description: Use this skill when the user wants LoopAI to validate training data, generate and approve training YAML, run LLaMA-Factory SFT or Verl GRPO, configure rewards, monitor or reconnect to Trainer workers, compare checkpoints, export a trained model, or inspect Trainer failures from starter.yaml or runtime state.
---

# Trainer Skill

## Purpose

Trainer Skill is the Codex-facing entry point and the canonical Python implementation for LoopAI model training.

Use this skill for:

- Running `sft + llamafactory` or `grpo + verl`; do not mix backend/stage pairs
- Validating SFT JSON/JSONL or GRPO Parquet data and reward configuration
- Generating and approving backend-specific training YAML
- Running or reconnecting to the persistent Trainer worker
- Reading local metrics, selecting checkpoints, and exporting Verl FSDP actors
- Returning structured Trainer errors without relying on SwanLab or Trainer MCP

Do not use this skill for Judger, Analyzer, data crawling, or broad project refactors.

## Python Implementation

```text
loopai/skills/Trainer/
├── __init__.py        # prepare() / run_prepared() / run() / result helpers
├── trainer_agent.py   # LangGraph subgraph used by Starter and the skill runner
├── nodes/             # data validation, config generation, training execution
├── rewards/           # stable LoopAI routing to Verl reward implementations
├── utils/             # persistent worker, Verl/SFT launchers, events, parsers
├── templates/         # bundled training templates
├── results.py         # parses metrics and selects the best checkpoint
├── runner.py          # skill entry that runs the Trainer subgraph
├── runtime_config.py  # resolves kwargs/env/state/starter.yaml
└── worker_entry.py    # independent process that owns training and finalization
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
   - the selected `train_framework` and `train_stage`
   - for SFT: dataset/model paths, learning rate, epochs, batch size, LoRA fields, devices, output directory, and `save_total_limit`
   - for GRPO: train/validation Parquet paths, model, reward mode/preset or custom function, rollout backend, GPUs, batch/token limits, save/test frequency, checkpoint directory, selection metric, and checkpoint retention
3. Stop and wait for explicit user approval. Do not treat a previous round's approval as approval for a new round.
4. If the user requests edits, update the generated YAML, call `inspect_prepared_config()`, show the complete updated YAML, and wait for approval again.
5. Only after approval, call `run_prepared()` with the displayed `config_path`, its displayed `config_sha256`, and the `trainer_version_id` returned by `prepare()`.
6. Keep `run_prepared()` in the foreground until training reaches `completed`, `failed`, or `cancelled`.

Never call `run()` directly for an interactive, user-initiated training request. Keep `run()` only as a backward-compatible entry point for explicitly non-interactive callers that intentionally opt out of human approval.

The SHA-256 check is part of the approval boundary. If the YAML changes before
`run_prepared()` validates it, reject it, show the changed YAML, and request
approval again. After validation, Trainer carries the exact approved YAML text
inside the trusted Worker request and atomically materializes that snapshot for
the launcher; it must not reread a mutable source file for execution.

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

### Verl GRPO State

Native Verl inputs may supply both training and validation Parquet files. For
historical tasks whose generated JSON/JSONL path remains in
`constructor.mapping_results.output_file`, leave `verl_source_dataset_path`
empty to use that compatibility input, or set it explicitly. Trainer converts
native/messages/Alpaca/QA records into a version-scoped Parquet pair before
generating YAML. `pyarrow` is required for conversion. Every output row contains
a non-empty chat-message-list `prompt`, a `data_source`, and
`reward_model.ground_truth`.

```python
prepared = prepare(
    state={
        "task_id": "trainer_grpo_001",
        "output_dir": "./outputs",
        "trainer": {
            "train_framework": "verl",
            "train_stage": "grpo",
            "verl_dir": "/path/to/verl",
            "verl_env_path": "verl",
            "train_input_dataset_path": "/path/to/train.parquet",
            "train_input_eval_dataset_path": "/path/to/validation.parquet",
            "train_input_model_name": "/path/to/model",
            "train_input_task_description": "Optimize task accuracy with GRPO.",
            "verl_reward_mode": "preset",
            "verl_reward_preset": "math_boxed",
            "CUDA_VISIBLE_DEVICES": "0,1,2,3,4,5,6,7",
        },
    },
    thread_id="trainer_grpo_001",
)
```

### Generated Data and Multi-Round Verl

Trainer owns Verl-only adaptation, deterministic train/validation splitting,
reward contract selection, and executable GRPO config generation. The retired
Constructor is not invoked; its task-state section is read only for historical
`mapping_results` compatibility.

On every fresh Verl `prepare()` round:

1. Prefer an explicitly supplied `verl_source_dataset_path`; otherwise use the
   current task's legacy `constructor.mapping_results` compatibility input, then
   the active Obtainer output, before any persisted older source.
2. Reuse native train/validation Parquet unchanged. Convert JSON, JSONL, or
   non-native Parquet under
   `{trainer_output_dir}/prepared_data/{train,validation}.parquet`.
3. Write `dataset_manifest.json` and `rejected_rows.jsonl`. Reject records that
   lack a reliable prompt or reference answer; never infer ground truth with an
   LLM or copy the assistant answer into the prompt.
4. If validation data is absent, split deterministically using
   `verl_validation_ratio` (default `0.05`) and `verl_split_seed` (default `42`).
   With `verl_reuse_previous_validation=true`, keep the previous validation
   Parquet stable only while the resolved reward contract remains compatible;
   otherwise split validation from the new source.
5. With `verl_inherit_previous_config=true`, use the preceding successful
   round's approved `train_config` as the hyperparameter baseline. Always
   replace train/validation/model paths, reward fields, devices, experiment and
   checkpoint directories, selection settings, and version metadata.
6. With `verl_use_previous_best_model=true`, promote `update_model_path` only
   when the preceding round completed, export did not fail, and the directory
   contains a loadable Hugging Face config plus weights. Never pass raw FSDP
   shards into the next round. To deliberately restart from another model,
   pass a current-call `train_input_model_name`/`model_path` override or set
   `verl_use_previous_best_model=false` together with the desired model path.
7. With `verl_multi_round_enabled=true`, the prepared YAML enables Hugging Face
   export and a positive checkpoint save cadence, including when the selected
   smoke template originally disabled them.
8. Show and approve the complete newly generated YAML again. Previous-round
   approval never authorizes a new round.

Minimal generated-data fields:

```python
"trainer": {
    "train_framework": "verl",
    "train_stage": "grpo",
    "verl_dir": "/path/to/verl",
    "train_input_model_name": "/path/to/base-model",
    "train_input_task_description": "Mathematics GRPO",
    "verl_data_adapter": "auto",
    "verl_reward_mode": "auto",
}
```

Use exactly one reward mode:

- `auto`: route by Parquet `data_source` through Verl's built-in router. When
  `pyarrow` is available, reject unsupported sources during preparation;
  otherwise defer that check to Verl with a validation warning.
- `preset`: set `verl_reward_preset` to `auto`, `verl_builtin`, `gsm8k_exact`, `math_boxed`, `math_dapo`, `prime_math`, `geometry`, or `qa_exact_match`.
- `custom`: set `verl_reward_function_path` and optionally `verl_reward_function_name` (default `compute_score`) and `verl_reward_kwargs`.

The preset router imports reward implementations lazily from the configured Verl
environment. Do not copy Verl reward source into LoopAI and do not silently fall
back from an unknown `data_source` or preset.

For generated non-native data in `auto` mode, Trainer may recommend an existing
preset only when task/dataset metadata or an explicit answer marker makes the
mapping reliable (for example GSM8K, MATH/boxed, DAPO/AIME, Numina/PRIME,
Geometry3K, or Search-R1-style QA). A user-specified named preset or custom
reward always wins. If the mapping is ambiguous, stop preparation and ask the
user to select a preset or custom reward; do not guess.

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

For Verl data handoff it also reads only `mapping_results` from the task-scoped
`constructor` and `obtainer` sections as optional, read-only upstream state so
a Trainer-only invocation can locate the latest output without pulling
unrelated section configuration or credentials into the worker state.

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
- `TRAIN_STAGE`
- `TRAIN_DATASET_PATH`
- `TRAIN_EVAL_DATASET_PATH`
- `VERL_SOURCE_DATASET_PATH` and `VERL_SOURCE_EVAL_DATASET_PATH`
- `VERL_DATA_ADAPTER`, `VERL_DATA_SOURCE`, `VERL_VALIDATION_RATIO`, and `VERL_SPLIT_SEED`
- `TRAIN_MODEL_PATH`
- `TRAIN_TASK_DESCRIPTION`
- `TRAIN_CONFIG_TEMPLATE_PATH`
- `LLAMAFACTORY_DIR`
- `LLAMAFACTORY_ENV_PATH`
- `VERL_DIR`
- `VERL_ENV_PATH` or `VERL_CONDA_ENV`
- `VERL_REWARD_MODE`, `VERL_REWARD_PRESET`, and `VERL_REWARD_KWARGS`
- `VERL_REWARD_FUNCTION_PATH` and `VERL_REWARD_FUNCTION_NAME` for custom reward mode
- `VERL_INHERIT_PREVIOUS_CONFIG`, `VERL_USE_PREVIOUS_BEST_MODEL`, and `VERL_MULTI_ROUND_ENABLED`
- `TRAINER_PERSISTENT_WORKER`
- `CUDA_VISIBLE_DEVICES`

Trainer metrics are driven by local files and do not require an external
experiment-tracking service. SFT reads `trainer_log.jsonl` and
`metrics/metrics.json`; Verl reads `metrics/verl_metrics.jsonl`.

Required Trainer fields:

- `train_framework`
- `train_stage`
- `train_input_dataset_path`
- `train_input_task_description`
- `train_input_config_template_path`
- `train_input_model_name`
- for SFT: `train_framework=llamafactory`, `train_stage=sft`, and `llamafactory_dir`
- for GRPO: `train_framework=verl`, `train_stage=grpo`, `verl_dir`, either a native train Parquet or generated source (including legacy Constructor task state), and a valid reward mode. Validation may be supplied, reused, or deterministically split.

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

The task-level compatibility event pickle remains at:

```text
{output_dir}/{task_id}/trainer.pkl
```

The version directory contains `run_state.json`, `worker.log`, and, after
finalization, `worker_result.pkl`. Treat pickle files as trusted local artifacts;
they are created with user-only permissions and must not be loaded from an
untrusted source.

Important state fields:

- `trainer_version_id`
- `trainer_output_dir`
- `trainer_event_log_path`
- `trainer_training_task_id`
- `trainer_run_state_path`
- `trainer_worker_log_path`

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
- `llamafactory_dir` for SFT, or `verl_dir` for GRPO. A separate validation path is optional when Trainer can split generated data.

Fields that Trainer can usually prefill:

- `train_framework` / `train_stage`: default to `llamafactory` / `sft`, or infer `verl` / `grpo` from `task_type="grpo"`
- `train_input_config_template_path`: selects the bundled SFT or GRPO YAML template
- `verl_env_path`: defaults to Conda environment `verl`
- `verl_reward_mode`: defaults to `auto`
- `verl_data_adapter`: defaults to `auto`
- `verl_validation_ratio` / `verl_split_seed`: default to `0.05` / `42`
- `verl_inherit_previous_config`, `verl_use_previous_best_model`, `verl_multi_round_enabled`: default to `true`
- `trainer_persistent_worker`: defaults to `true`
- `CUDA_VISIBLE_DEVICES`: defaults to `0`

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
- `metrics/verl_metrics.jsonl`
- SFT `checkpoint-*` and Verl `checkpoints/global_step_*` directories

Best checkpoint selection rule:

1. For Verl, use `result.selection_metric` and `result.selection_mode` from the approved YAML. Fall back to the runtime `verl_selection_metric` / `verl_selection_mode` defaults (`val-core/*/acc/mean@*`, maximize) only when the YAML omits them.
2. Otherwise prefer the lowest `eval_loss`, then the lowest training `loss`, then the highest validation score or reward.
3. Align metrics and checkpoints by numeric `step`/`global_step`, not by list index. Prefer an exact step, otherwise the nearest saved checkpoint not after the best metric step, then the nearest checkpoint.
4. Ignore non-finite metric values (`NaN` / `Inf`) and reject a selection mode other than `max` or `min`.
5. If no usable metric exists, choose the latest checkpoint.

When `result.export_huggingface=true` and the selected Verl `global_step_*`
actor is still an FSDP shard, Trainer runs `verl.model_merger` and writes a
Hugging Face model directory. Report the merged `update_model_path`; do not
pass an unmerged actor shard to Judger.

When comparing multiple Trainer runs, call `analyze_results()` for each run and compare:

- `summary["best_metric"]`
- `summary["best_checkpoint_name"]`
- `summary["checkpoint_count"]`
- `summary["metric_count"]`
- `trainer_result.status`
- `trainer_last_error`

Prefer reporting the selected checkpoint path from `trainer_best_checkpoint_path` or `update_model_path` as the model candidate for the next Judger or Analyzer step.

## Execution Lifetime

Trainer defaults to `trainer_persistent_worker=true`. An independent local
worker owns the LLaMA-Factory/Verl process, progress persistence, result
analysis, and final state update. The normal caller still waits synchronously:
a Trainer invocation is not complete when the worker has merely started; it is
complete only after the run reaches `completed`, `failed`, or `cancelled`.

- Do not launch the Trainer runner with `&`, `nohup`, or a detached shell.
- If a command execution yields a running cell/session id, keep waiting on
  that same execution until it exits.
- Do not finish the Codex turn while the Trainer command is still running.
- Progress events with status `running` are intermediate updates, not a final
  tool result.
- If the caller or API session ends unexpectedly while the worker remains
  alive, do not submit the same `version_id` as a new run. Reconnect to that
  worker and read `run_state.json` / `worker_result.pkl`; training continues.
- If the worker itself has exited while its child training process is still
  alive, Trainer refuses to launch a duplicate. Report the orphaned process and
  require explicit operator recovery instead of claiming it was reattached.
- Concurrent attach/launch attempts for the same version are serialized by a
  run-directory lock so only one Worker may be started.
- Use `trainer_persistent_worker=false` only for explicit local debugging of
  the legacy in-process execution path.

## Trainer MCP Status (Disabled)

The Trainer MCP route is disabled. Do not call or register `trainer_run` /
`trainer_load_events`, and do not start an MCP server for the purpose of running
Trainer. Call the local Trainer skill or its script/runner entrypoints directly.
This restriction does not disable or remove unrelated LoopAI MCP routes.

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
