# Judger Skill

## Purpose

Judger Skill 用于在无 LangGraph（独立模式）下运行 LoopAI 评测流水线。支持三种任务类型：

- **code** — 代码生成评测（human-eval / mbpp 格式），计算 pass@k
- **text2sql** — SQL 生成评测，在 SQLite 数据库上执行校验
- **general_text** — 通用文本评测（One-Eval DataFlowEvalTool）

所有评测结果写入文件系统，进度事件持久化到 pickle，state 持久化到 SQLite checkpoint。

## When to Use

当 Codex 或用户要执行以下操作时使用本 skill：

- 评测模型生成的代码 / SQL / 文本
- 计算 pass@k、accuracy 等指标
- 从 starter.yaml 配置启动评测流水线
- 断点续跑中断的评测任务
- 查看评测进度事件

不要用它处理：

- 训练、数据爬取、数据构造（走对应的 Agent/Skill）
- 全局 `system` 配置修改（走 Configer 或直接改 starter.yaml）

## Python Implementation

```
loopai/skills/Judger/          ← Skill 层（独立模式，无 LangGraph）
├── __init__.py                # run() / load_events()
├── runner.py                  # 流水线主逻辑
├── runtime_config.py          # 配置解析（kwargs > env > YAML > defaults）
└── utils/
    ├── eval_general_text.py   # general_text 评测
    ├── generate.py            # code/text2sql 样本生成
    ├── evaluate.py            # code/text2sql 评测
    └── format.py              # 数据格式转换

loopai/agents/Judger/          ← Agent 层（LangGraph，供 Starter 用）
    └── ...                     # 保持不变
```

## Quick Start

### 方式 1: CLI（推荐）

```bash
# general_text 评测
python examples/scripts/run_judger_standalone.py \
    --config-path starter.yaml \
    --task-id my_eval_001 \
    --print-result

# code 评测（human-eval 格式）
python examples/scripts/run_judger_standalone.py \
    --config-path starter.yaml \
    --task-id my_code_eval \
    --print-result

# 查看流水线步骤
python examples/scripts/run_judger_standalone.py --list-steps

# 断点续跑
python examples/scripts/run_judger_standalone.py \
    --task-id my_eval_001 \
    --resume
```

### 方式 2: Python API

```python
from loopai.skills.Judger import run

result = run(
    state={
        "judger": {
            "eval_model_path": "/data/models/Qwen2.5-7B-Instruct",
            "eval_task_type": "general_text",
            "eval_problem_path": "/data/problems/test.jsonl",
            "bench_dataflow_eval_type": "key2_qa",
        },
        "task_id": "my_task",
        "output_dir": "./outputs",
    },
    thread_id="my_task",
)
print(result["judger"]["output_result_path"])
```

### 方式 3: Codex 子进程

```bash
timeout 600 python3 -u <<'PY'
import json
from loopai.skills.Judger import run
result = run(
    state={"judger": {...}, "task_id": "task_001"},
    thread_id="task_001",
)
print(json.dumps({"ok": True, "data": result["judger"]}, ensure_ascii=False))
PY
```

## Configuration

### starter.yaml 结构

```yaml
default_states:
  task_id: "my_task"          # 必填，通过 --task-id 覆盖
  output_dir: "./outputs"     # 输出根目录

  judger:
    # --- 必填字段 ---
    eval_model_path: "/data/models/Qwen2.5-7B-Instruct"  # 模型路径
    eval_task_type: "general_text"     # code / text2sql / general_text
    eval_problem_path: "/data/test.jsonl"  # 问题文件路径

    # --- 模型参数 ---
    eval_temperature: 0               # 温度（默认 0）
    eval_top_p: 0.95                  # top_p（默认 0.95）

    # --- 样本参数 ---
    eval_batch_size: 10               # 批处理大小（默认 10）
    eval_case_num: 10                 # 每问题样本数（默认 10）

    # --- vLLM 参数 ---
    eval_vllm_tensor_parallel_size: 1 # 张量并行数（默认 2）
    eval_vllm_gpu_memory_utilization: 0.9  # GPU 显存利用率
    cuda_visible_devices: "5"         # 可见 GPU

    # --- general_text 专用 ---
    bench_name: "gsm8k"
    bench_dataflow_eval_type: "key2_qa"  # 评测类型，见下表

  # YAML 中可用短键名（兼容旧配置）：
  #   tensor_parallel_size → eval_vllm_tensor_parallel_size
```

### general_text 评测类型

| `bench_dataflow_eval_type` | 说明 |
|---|---|
| `key1_text_score` | 文本评分 |
| `key2_qa` | 问答评测 |
| `key2_q_ma` | 多答案评测 |
| `key3_q_choices_a` | 选择题评测 |
| `key3_q_choices_as` | 多选评测 |
| `key3_q_a_rejected` | 对比评测 |

### 配置优先级

```
CLI --task-id / --output-dir > 环境变量 > YAML default_states.judger > schema 默认值
```

## CLI Reference

```
python examples/scripts/run_judger_standalone.py [OPTIONS]

Options:
  --config-path PATH    配置文件路径（starter.yaml 或 JSON）
  --task-id ID          任务 ID（必填）
  --output-dir DIR      输出目录（默认 ./outputs）
  --resume              从上次 checkpoint 恢复
  --from-step STEP      从指定步骤开始执行
  --checkpoint-path PATH SQLite checkpoint 路径
  --print-result        打印结果摘要
  --print-events        打印事件列表
  --list-steps          列出流水线步骤
```

**`--task-id` 是必填的**，不传会报 `CONFIG_ERROR`。可通过 `TASK_ID` 环境变量替代。

**`--config-path` 和 `--resume` 互斥**：resume 时 state 从 checkpoint 加载（不含 config-path 也不会报错），但可通过环境变量覆盖部分字段。

## Python API

```python
from loopai.skills.Judger import run, load_events
from loopai.skills.Judger.runner import (
    run_judger_pipeline,
    save_judger_checkpoint,
    load_judger_checkpoint,
)

# 运行流水线
result = run(
    state=None,             # dict with state["judger"] fields
    thread_id="task_001",   # 必填，= task_id
    resume=False,           # True = 从 checkpoint 恢复
    from_step=None,         # 强制起始步骤名
    checkpoint_path=None,   # 自定义 checkpoint 路径
    **kwargs,               # 运行时覆盖（优先于 state）
)

# 读取事件
events = load_events(task_id="task_001", output_dir="./outputs")

# 读取 checkpoint
state = load_judger_checkpoint("task_001", "outputs/judger_checkpoints.sqlite")
```

## Pipeline Steps

### code / text2sql pipeline

```
validate → kill_vllm → start_vllm → format_data → generate → evaluate → kill_vllm_cleanup → finish
```

### general_text pipeline

```
validate → eval_general_text → finish
```

| Step | 功能 |
|---|---|
| `validate` | 校验必填字段、文件存在性、JSONL 字段结构 |
| `kill_vllm` | 关闭端口 8911 上的 vLLM 进程 |
| `start_vllm` | 启动本地 vLLM 服务 |
| `format_data` | 数据格式转换（human-eval / mbpp），可选 |
| `generate` | vLLM 批量生成 code/text2sql 样本 |
| `evaluate` | 执行代码/执行 SQL，计算 pass@k |
| `kill_vllm_cleanup` | 评测后关闭 vLLM |
| `eval_general_text` | One-Eval DataFlowEvalTool 子进程评测 |
| `finish` | 流水线完成 |

## Environment Variables

| 变量 | 对应字段 | 默认值 |
|---|---|---|
| `TASK_ID` | `task_id` | 必填，无默认 |
| `OUTPUT_DIR` | `output_dir` | `./outputs` |
| `JUDGER_MODEL_PATH` | `eval_model_path` | 必填 |
| `JUDGER_TASK_TYPE` | `eval_task_type` | `code` |
| `JUDGER_TEMPERATURE` | `eval_temperature` | `0` |
| `JUDGER_TOP_P` | `eval_top_p` | `0.95` |
| `JUDGER_PROBLEM_PATH` | `eval_problem_path` | 必填 |
| `JUDGER_BATCH_SIZE` | `eval_batch_size` | `10` |
| `JUDGER_CASE_NUM` | `eval_case_num` | `10` |
| `JUDGER_FORMAT_TYPE` | `eval_format_type` | 可选 |
| `JUDGER_TEXT2SQL_DIR` | `eval_text2sql_dir` | text2sql 必填 |
| `JUDGER_TENSOR_PARALLEL_SIZE` | `eval_vllm_tensor_parallel_size` | `2` |
| `JUDGER_GPU_MEMORY_UTILIZATION` | `eval_vllm_gpu_memory_utilization` | `0.9` |
| `CUDA_VISIBLE_DEVICES` | `cuda_visible_devices` | `0` |
| `JUDGER_BENCH_NAME` | `bench_name` | `general_text_eval` |
| `JUDGER_BENCH_DATAFLOW_EVAL_TYPE` | `bench_dataflow_eval_type` | 空（general_text 必填） |
| `JUDGER_CHECKPOINT_PATH` | checkpoint 路径 | `outputs/judger_checkpoints.sqlite` |

## Output & Artifacts

```
outputs/<task_id>/
├── judger/
│   ├── <name>_format.jsonl           # 格式化后的问题文件
│   ├── <name>_sample.jsonl           # 生成的样本
│   ├── <name>_result.jsonl           # 评测结果
│   ├── log.txt                       # 评测日志
│   ├── text_eval_summary_*.json      # general_text 摘要
│   ├── general_text_dataset_cache_*.jsonl  # 缓存
│   └── gsm8k_*_steps/               # One-Eval 中间产物
├── judger.pkl                        # 事件 pickle（load_events 读取）
└── judger_checkpoints.sqlite         # state checkpoint（全局共享）
```

- **stdout** — 最终结果 JSON payload（Codex 消费）
- **stderr** — `--print-result` / `--print-events` 的输出
- **judger.pkl** — 所有进度事件，`load_events(task_id)` 读取
- **checkpoint** — 每步前后自动保存，`load_judger_checkpoint(task_id)` 读取

## Checkpoint & Resume

### 工作原理

每一步执行前后自动保存 state 到 SQLite：

```
outputs/judger_checkpoints.sqlite
  ┌──────────────┬──────────────────────┬──────────────────────┐
  │ thread_id    │ state_json           │ updated_at           │
  ├──────────────┼──────────────────────┼──────────────────────┤
  │ my_task      │ {"last_completed":   │ 2026-06-18T12:00:00Z │
  │              │  "generate", ...}    │                      │
  └──────────────┴──────────────────────┴──────────────────────┘
```

### 断点续跑

```bash
# 从上次中断处继续（跳过已完成步骤）
python examples/scripts/run_judger_standalone.py --task-id my_task --resume

# 从指定步骤强制执行（跳过之前所有步骤）
python examples/scripts/run_judger_standalone.py \
    --task-id my_task --from-step evaluate

# resume + 覆盖部分配置
CUDA_VISIBLE_DEVICES=6 python examples/scripts/run_judger_standalone.py \
    --task-id my_task --resume
```

**注意**：`--resume` 时 state 从 checkpoint 加载，不需要 `--config-path`。但可通过环境变量覆盖字段（如换 GPU、改温度）。

### 查看 checkpoint

```python
from loopai.skills.Judger.runner import load_judger_checkpoint

state = load_judger_checkpoint("my_task", "outputs/judger_checkpoints.sqlite")
print(state["last_completed"])  # 最后完成的步骤
print(state["judger"]["output_result_path"])
```

## Event System

### 事件写入

流水线运行时自动持久化到 `<output_dir>/<task_id>/judger.pkl`：

```python
from loopai.common.event_tool import get_event_writer, StreamEvent

writer = get_event_writer(name="judger", context_id="task_001", log_file_path="./outputs")
writer(StreamEvent(current="judger", progress=0.5, message="样本生成中"))
```

### 事件读取

```python
from loopai.skills.Judger import load_events

events = load_events(task_id="my_task")
for e in events:
    print(f'[{e["time"]}] progress={e["progress"]} {e["message"]}')
# [2026-06-18T12:00:00Z] progress=0.0 Judger pipeline started
# [2026-06-18T12:00:01Z] progress=0.5 DataFlowEvalTool 子进程仍在运行
# [2026-06-18T12:05:00Z] progress=1.0 流水线完成
```

## Error Handling

所有错误通过 `loopai.common.exception.emit_error` 输出标准 JSON payload：

| ErrorCode | 触发场景 |
|---|---|
| `CONFIG_ERROR` | 缺少必填字段、模型路径未配置 |
| `INVALID_INPUT` | JSONL 字段不匹配、不支持的任务类型 |
| `NOT_FOUND` | 问题文件不存在 |
| `EXTERNAL_SERVICE_ERROR` | DataFlowEvalTool 子进程失败 |

### 错误响应格式

```json
{
    "ok": false,
    "status": "failed",
    "message": "Judger configuration is incomplete.",
    "data": null,
    "error": {
        "type": "ValueError",
        "code": "CONFIG_ERROR",
        "detail": "Missing required fields: {\"missing_fields\": {\"judger\": [\"eval_model_path\"]}}",
        "traceback": "...",
        "recoverable": true,
        "time": "2026-06-18T12:00:00Z"
    }
}
```

### 成功响应格式

```json
{
    "ok": true,
    "status": "completed",
    "message": "Judger pipeline completed.",
    "data": {
        "task_type": "general_text",
        "output_result_path": "/path/to/summary.json",
        "output_case_path": "",
        "output_problem_path": "/path/to/cache.jsonl",
        "output_pred_path": "/path/to/step2.jsonl",
        "bench": {
            "bench_name": "gsm8k",
            "eval_status": "success",
            "meta": {"eval_result": {"accuracy": 0.94}}
        }
    },
    "error": null
}
```

## vLLM Management

**仅支持本地 vLLM 启动。** 远程 API（`eval_base_url`）在独立模式下不支持。

流水线自动管理 vLLM 生命周期：

1. 关闭端口 8911 上已有的 vLLM 进程
2. 使用配置的 model_path、tensor_parallel_size、gpu_memory_utilization 启动 vLLM
3. code/text2sql 评测完成后自动关闭；general_text 由 One-Eval 自行管理

## Codex Integration

Codex 通过子进程调用 Python 函数，读取 stdout JSON：

```bash
timeout 600 python3 -u <<'PY'
import json, sys
from loopai.skills.Judger import run

try:
    result = run(
        state={
            "judger": {
                "eval_model_path": "/data/models/Qwen2.5-7B-Instruct",
                "eval_task_type": "general_text",
                "eval_problem_path": "/data/test.jsonl",
                "bench_dataflow_eval_type": "key2_qa",
                "eval_batch_size": 4,
                "cuda_visible_devices": "5",
            },
            "task_id": "codex_task_001",
            "output_dir": "./outputs",
        },
        thread_id="codex_task_001",
    )
    print(json.dumps({"ok": True, "data": result["judger"]}, ensure_ascii=False))
except Exception as e:
    # 错误已通过 emit_error 输出到 stdout，直接退出即可
    sys.exit(1)
PY
```

Codex 读到的 stdout 行：
- 成功：`{"ok": true, "data": {"output_result_path": "...", "bench": {...}}}`
- 失败：`{"ok": false, "error": {"code": "CONFIG_ERROR", "detail": "..."}}`

## Config Via Configer

Judger 配置字段可通过 Configer skill 读写：

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    get_configer_state_config,
    update_configer_state_config,
)

# 查看 schema（字段含义、允许值、默认值）
schema = get_configer_state_schema(section_name="judger")

# 读取当前实际配置
config = get_configer_state_config(section_name="judger")

# 修改配置
update_configer_state_config("judger", {
    "eval_task_type": "general_text",
    "eval_temperature": 0.2,
})
```

