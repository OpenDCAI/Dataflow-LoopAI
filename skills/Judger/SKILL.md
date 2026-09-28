# Judger Skill

## Purpose

无 LangGraph 的独立评测技能。默认通过 Codex SDK 调度可插拔 benchmark；

Judger 的 agent/评测编排模型固定解析 Starter 模型池的 `codex` 角色，不再读取
独立厂商模型默认值。被测模型的 vLLM 属于评测对象而不是 Judger agent 模型：
评测结束后默认保活，并以 `judger.vllm` entry 注册回 Starter 模型池，供后续
Obtainer DataFlow rollout/难度筛选使用。
旧的步骤实现仅作为兼容组件。支持三种任务类型：

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

For a benchmark-skill run, pass the benchmark directly. This invokes the
Codex SDK worker without constructing a task-type-specific pipeline:

```bash
loopai-judger start --benchmark humaneval \
  --predictions outputs/predictions.jsonl \
  --lake .datamixer/lake.yaml \
  --run outputs/judger-sdk
```

`loopai-judger list` (or `--list-benchmarks`) lists all built-in, legacy, and
user-provided benchmark skills. `start`/`resume` also accept the legacy
`--resume`/`--from-step` flags for command-line compatibility; benchmark runs
themselves are always dispatched through the SDK worker.

Skills are discovered from `loopai/skills/benchmarks/<name>/`. Existing
Configer `benchlist` entries are adapted into independent workers, so adding a
benchmark only requires a skill directory. Each run records `thread.json`,
`status.json`, and `final_report.json`; DataMixer benchmark data is mounted
read-only and its guard/lineage metadata is preserved.

## Configuration

配置通过 **Configer skill** 写入 `TaskModel.state`，分两部分：

### 全局字段（state["judger"] 顶层，所有 bench 共享）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `eval_model_path` | 无 | 模型路径（必填） |
| `eval_temperature` | `0` | 采样温度 |
| `eval_top_p` | `0.95` | Top-P 采样 |
| `eval_batch_size` | `10` | 批处理大小，bench 可覆盖 |
| `eval_case_num` | `10` | 每问题样本数，bench 可覆盖 |
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

## Worker contract

每个 bench entry 都解析为一个独立 benchmark skill，并由 Codex SDK 执行。
Judger 不根据 `task_type` 拼接固定步骤；skill 的 `manifest.json`、`SKILL.md`
和可选 `benchmark.py` 提供输入契约、评估能力与失败分析维度。Configer
中的 `benchlist` / `extra_benchlist` 只是 worker 请求清单，执行结果仍按主/附加
任务分别收集。

每个 worker 至少写入：

```
<run>/
  thread.json
  worker_prompt.md
  status.json
  final_report.json
```

如果传入 DataMixer lake，benchmark 记录以只读方式挂载，并将 dataset id、
snapshot、contamination guard 和 lineage 写入 `final_report.json`。

旧的 `loopai.skills.Judger.runner` 步骤函数仅为已有集成保留，不是主入口。

## Output

### stdout（SDK worker）

```json
{
  "ok": true,
  "status": "completed",
  "bench_result": [{"bench_name": "humaneval", "metrics": {"pass@1": 0.85}}],
  "extra_bench_result": [],
  "metrics": {"humaneval": {"pass@1": 0.85}},
  "benchmark_guard": {"name": "humaneval"},
  "lineage": {"warehouse": "..."}
}
```

### 目录结构

```
outputs/judger-sdk/
├── final_report.json
├── status.json
├── humaneval/
│   ├── thread.json
│   ├── worker_prompt.md
│   └── final_report.json
└── mbpp/
```

### Configer / state 传递

SDK worker 返回的 aggregate report 同时包含 `bench_result` 和
`extra_bench_result`，并在进程内 state 中写回同名字段，Analyzer 可直接读取。
需要写回任务库时，由上层编排器用 Configer 持久化这两个结构化字段。

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
