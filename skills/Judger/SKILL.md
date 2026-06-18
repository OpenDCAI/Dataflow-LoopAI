# Judger Skill

## Purpose
Judger Skill is the Codex/Agent-facing capability for running LoopAI Judger independently. It evaluates model outputs (code generation, text-to-SQL, general text) and produces pass@k scores, execution results, and evaluation reports.

## Python Implementation
The Python skill layer lives in:

`loopai/skills/Judger`

Core Judger business logic remains in:

`loopai/agents/Judger`

Do not move or rewrite `generate.py`, `evaluate.py`, `execution.py`, or `eval_general_text_node.py` for normal skill usage. The skill layer is a standalone pipeline wrapper.

## Runtime Entry
```python
from loopai.skills.Judger import run

result = run(
    state=state,
    resume=False,
    from_step=None,
)
```

Direct runner:

```python
from loopai.skills.Judger.runner import run_judger_pipeline
```

Legacy import (LangGraph-based, via Starter) remains valid:

```python
from loopai.agents import JudgerAgent
```

## CLI
```bash
python examples/scripts/run_judger_standalone.py \
    --config-path /tmp/judger_config.json \
    --print-result
```

Supported options:

- `--config-path` — JSON config file with judger fields
- `--resume` — resume from last checkpoint
- `--from-step` — force start from a specific step
- `--checkpoint-path` — sqlite checkpoint file path
- `--task-id` — override task id
- `--output-dir` — override output directory
- `--print-result` — print final result paths
- `--list-steps` — print available pipeline steps

## Pipeline Steps

### code / text2sql pipeline
```
validate -> kill_vllm -> start_vllm -> format_data -> generate -> evaluate -> kill_vllm_cleanup -> finish
```

### general_text pipeline
```
validate -> eval_general_text -> finish
```

Step descriptions:

| Step | Description |
|------|-------------|
| `validate` | Check required fields, file existence, JSONL field structure |
| `kill_vllm` | Kill any existing local vLLM process on default port 8911 |
| `start_vllm` | Start local vLLM with configured model_path, tensor_parallel_size, gpu_memory_utilization |
| `format_data` | Optional data format conversion (human-eval, mbpp) |
| `generate` | Generate code/text2sql samples via vLLM batch inference |
| `evaluate` | Evaluate generated samples, produce pass@k results |
| `kill_vllm_cleanup` | Kill local vLLM after evaluation |
| `eval_general_text` | Run One-Eval DataFlowEvalTool for general text evaluation |
| `finish` | Mark pipeline complete |

## Environment Variables
Runtime configuration should come from environment/system runtime where possible:

- `JUDGER_MODEL_PATH` — model path for vLLM
- `JUDGER_TASK_TYPE` — evaluation task type (code / text2sql / general_text)
- `JUDGER_TEMPERATURE` — model temperature (default: 0)
- `JUDGER_TOP_P` — model top_p (default: 0.95)
- `JUDGER_PROBLEM_PATH` — path to evaluation problem JSONL file
- `JUDGER_BATCH_SIZE` — batch size for sample generation (default: 10)
- `JUDGER_CASE_NUM` — number of samples per problem (default: 10)
- `JUDGER_FORMAT_TYPE` — optional data format type (human-eval, mbpp)
- `JUDGER_TEXT2SQL_DIR` — database directory for text2sql tasks
- `JUDGER_TENSOR_PARALLEL_SIZE` — vLLM tensor parallel size (default: 2)
- `JUDGER_GPU_MEMORY_UTILIZATION` — vLLM GPU memory utilization (default: 0.9)
- `CUDA_VISIBLE_DEVICES` — GPU device ids
- `JUDGER_BENCH_NAME` — bench name for general_text (default: general_text_eval)
- `JUDGER_BENCH_DATAFLOW_EVAL_TYPE` — One-Eval eval type
- `JUDGER_CHECKPOINT_PATH` — sqlite checkpoint path
- `TASK_ID` — task id
- `OUTPUT_DIR` — output directory

## Priority
General runtime priority:

`kwargs > env > state["judger"] > schema defaults`

Example: `JUDGER_TASK_TYPE` env var overrides `state["judger"]["eval_task_type"]`, but a `judger_task_type="code"` kwarg takes priority over both.

## vLLM Management
Judger skill **only supports local vLLM startup**. Remote API (`eval_base_url`) is not supported in standalone mode.

The pipeline automatically:
1. Kills any existing vLLM on port 8911
2. Starts vLLM with the configured model and GPU settings
3. Kills vLLM after evaluation completes (for code/text2sql) or leaves it for the One-Eval tool to manage (for general_text)

## Checkpoint And Resume
Standalone Judger uses a function-level pipeline with sqlite checkpoints.

The runner stores:
- `state["current"]`
- `state["last_completed"]`

`--resume` continues from the checkpoint step and does not rerun completed steps. `--from-step` forces a specific Judger step.

## Supported Task Types

### code
Generates code samples from prompts, executes against test suites, computes pass@k scores.
Required JSONL fields: `task_id`, `prompt`, `entry_point`, `canonical_solution`, `test_list`
Optional format types: `human-eval`, `mbpp`

### text2sql
Generates SQL queries, executes against SQLite databases, compares results.
Required JSONL fields: `task_id`, `prompt`, `db_id`, `question`, `ground_truth`
Additional config: `eval_text2sql_dir`

### general_text
Runs One-Eval DataFlowEvalTool in a subprocess for configurable evaluation types.
Required config: `bench_dataflow_eval_type`
Supported eval types: `key1_text_score`, `key2_qa`, `key2_q_ma`, `key3_q_choices_a`, `key3_q_choices_as`, `key3_q_a_rejected`

## Success Response
Return the Judger final state/result directly. Key output fields:

```json
{
  "ok": true,
  "status": "completed",
  "message": "Judger pipeline completed.",
  "data": {
    "task_type": "code",
    "output_result_path": "/path/to/xxx_result.jsonl",
    "output_case_path": "/path/to/xxx_sample.jsonl",
    "output_problem_path": "/path/to/xxx_format.jsonl"
  },
  "error": null
}
```

For general_text tasks, `data` also includes `bench` with eval stats.

## Error Response
Validation failures and runtime errors:

```json
{
  "ok": false,
  "status": "failed",
  "message": "Judger pipeline failed.",
  "data": null,
  "error": {
    "type": "ValueError",
    "code": "JUDGER_ERROR",
    "detail": "Missing required fields: ...",
    "traceback": "...",
    "recoverable": true
  }
}
```

## Stream Runtime
Judger standalone must not require LangGraph runtime. Stream writer calls use a safe fallback: outside LangGraph they emit nothing and continue running.

## Config Via Configer
Judger state config fields can be read/modified via the Configer skill:

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    get_configer_state_config,
    update_configer_state_config,
)

# View judger schema
schema = get_configer_state_schema(section_name="judger")

# View current judger config
config = get_configer_state_config(section_name="judger")

# Update judger config
update_configer_state_config("judger", {"eval_task_type": "code"})
```
