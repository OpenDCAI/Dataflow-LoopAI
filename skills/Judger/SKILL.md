# Judger Skill

## Purpose

无 LangGraph 的独立评测流水线。支持三种任务类型：

- **code** — 代码生成评测（human-eval / mbpp），计算 pass@k
- **text2sql** — SQL 生成评测，SQLite 执行校验
- **general_text** — 通用文本评测（One-Eval DataFlowEvalTool）

## How to Invoke

**唯一入口：`loopai.skills.Judger.run()`**

`DB_PATH` 和 `TASK_ID` 从环境变量自动获取：

```bash
DB_PATH=api/db/db.sqlite3 TASK_ID=<task_id> \
python -c "from loopai.skills.Judger import run; run()"
```

或通过 CLI：

```bash
DB_PATH=api/db/db.sqlite3 TASK_ID=<task_id> loopai-judger
```

## Configuration

配置通过 **Configer skill** 写入 `TaskModel.state`，分两部分：

### 全局字段（state["judger"] 顶层，所有 bench 共享）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `eval_model_path` | 无 | 模型路径（必填） |
| `eval_temperature` | `0` | 采样温度 |
| `eval_top_p` | `0.95` | Top-P 采样 |
| `eval_batch_size` | `10` | 批处理大小 |
| `eval_case_num` | `10` | 每问题样本数 |
| `eval_vllm_tensor_parallel_size` | `1` | vLLM 张量并行数 |
| `eval_vllm_gpu_memory_utilization` | `0.9` | vLLM GPU 显存利用率 |
| `cuda_visible_devices` | `"0"` | 指定 GPU |
| `output_dir` | `"./outputs"` | 输出根目录 |

### Bench 配置（state["judger"]）

所有评测集通过 `benchlist` 和 `extra_benchlist` 列表配置。**格式必须是 JSON 数组**（`[{...},{...}]`），**不是** JSONL（每行一个对象）：

```json
[{"name":"gsm8k","task_type":"general_text","problem_path":"/data/gsm8k/test.jsonl","eval_type":"key2_qa"},{"name":"human_eval","task_type":"code","problem_path":"/data/humaneval.jsonl","case_num":10}]
```

```json
{
  "benchlist": [
    {
      "name": "gsm8k",
      "task_type": "general_text",
      "problem_path": "/data/gsm8k/test.jsonl",
      "eval_type": "key2_qa",
      "key_mapping": {}
    },
    {
      "name": "human_eval",
      "task_type": "code",
      "problem_path": "/data/humaneval.jsonl",
      "case_num": 10,
      "batch_size": 10,
      "format_type": ""
    },
    {
      "name": "bird_dev",
      "task_type": "text2sql",
      "problem_path": "/data/bird/dev.jsonl",
      "text2sql_dir": "/data/bird/dev_databases",
      "case_num": 10,
      "batch_size": 10
    }
  ],
  "extra_benchlist": []
}
```

**bench entry 字段：**

| 字段 | code | text2sql | general_text | 说明 |
|---|---|---|---|---|
| `name` | ✅ 必填 | ✅ 必填 | ✅ 必填 | bench 标识 |
| `task_type` | ✅ 必填 | ✅ 必填 | ✅ 必填 | `code` / `text2sql` / `general_text` |
| `problem_path` | ✅ 必填 | ✅ 必填 | ✅ 必填 | 问题文件路径 |
| `case_num` | 可选 10 | 可选 10 | — | 每问题样本数，bench 设了覆盖全局 |
| `batch_size` | 可选 10 | 可选 10 | — | 批处理大小，bench 设了覆盖全局 |
| `format_type` | 可选 | — | — | `human-eval` / `mbpp`，不设走默认 |
| `text2sql_dir` | — | ✅ 必填 | — | SQLite 数据库目录 |
| `eval_type` | — | — | ✅ 必填 | `key2_qa` / `key1_text_score` 等 |
| `key_mapping` | — | — | 可选 | 字段映射，可自动推断 |

**主/附加区别：**

| | 主任务 | 附加任务 |
|---|---|---|
| 执行顺序 | 先 | 后 |
| 失败策略 | 记录失败 + `_save_task_progress` + 退出 | 记录失败，继续 |

### 预填写流程

```
1. configer_get_task(schema="states", section="judger", task_id="<task_id>")
2. 将缺失字段告知用户，征得确认后写入
3. configer_update_task("judger", {"benchlist": [...], "eval_model_path": "..."}, task_id="<task_id>")
```

## Pipeline

每个 bench entry 独立跑一遍完整流水线：

```
对每个 bench:
  _apply_bench_to_state → 注入 bench 字段到 state["judger"]
  → 按 task_type 选流水线:
    code/text2sql: validate → kill_vllm → start_vllm → format_data → generate → evaluate → kill_vllm_cleanup → finish
    general_text:  validate → eval_general_text → finish
  → 收集结果到 bench_result / extra_bench_result
```

## Output

### stdout（emit_success）

```json
{
  "ok": true,
  "data": {
    "bench_result": [
      {"bench_name": "gsm8k", "task_type": "general_text",
       "output_result_path": "...", "metrics": {"accuracy": 0.94}}
    ],
    "extra_bench_result": [
      {"bench_name": "human_eval", "task_type": "code",
       "output_result_path": "...", "metrics": {"pass@1": 0.85}}
    ],
    "metrics": {"gsm8k": {"accuracy": 0.94}, "human_eval": {"pass@1": 0.85}}
  }
}
```

### 目录结构

```
outputs/<task_id>/
├── judger/
│   └── <version_id>/
│       ├── gsm8k/                      ← bench_name 子目录
│       │   ├── text_eval_summary_*.json
│       │   └── gsm8k_*_steps/
│       ├── human_eval/
│       │   ├── human_eval_sample.jsonl
│       │   ├── human_eval_result.jsonl
│       │   └── log.txt
│       └── bird_dev/
└── judger.pkl
```

### Configer 持久化

`_save_task_progress` 写入 `state.judger.bench_result` 和 `state.judger.extra_bench_result`，Analyzer 从中读取。

## Error Handling

每个步骤 `emit_error(exc, stream_writer=writer)`：
- stdout 输出 `{"ok": false, ...}` 
- judger.pkl 写入 `status=failed`
- taskruntime 表标记失败

所有 error `recoverable=true`，Codex 可引导用户修复后重试。

## Environment Variables

| 变量 | 来源 | 默认值 |
|---|---|---|
| `DB_PATH` | 环境变量 | 必填 |
| `TASK_ID` | 环境变量 | 必填 |
| `OUTPUT_DIR` | 环境变量 | `./outputs` |
| `CUDA_VISIBLE_DEVICES` | 环境变量 | `"0"` |
