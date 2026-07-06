# Judger Skill

## Purpose

Judger Skill 用于在无 LangGraph（独立模式）下运行 LoopAI 评测流水线。支持三种任务类型：

- **code** — 代码生成评测（human-eval / mbpp 格式），计算 pass@k
- **text2sql** — SQL 生成评测，在 SQLite 数据库上执行校验
- **general_text** — 通用文本评测（One-Eval DataFlowEvalTool）

评测结果写入文件系统，进度事件持久化到 pickle，state 通过 Configer 读写 TaskModel.state。

## How to Invoke（强制）

**Codex 必须通过以下方式调用 Judger，禁止进程内直接导入：**

| 方式 | 入口 | 适用场景 |
|---|---|---|
| **CLI 子进程（推荐）** | `python examples/scripts/run_judger_standalone.py` | Codex 编排、手动调试、脚本 |
| **受控 Python 封装** | `loopai.skills.Judger.run(...)` 外包一层子进程/保护层 | 需要代码内集成时 |

**禁止使用的路径：**
- ❌ `loopai.agents.Judger.JudgerAgent` — **已硬拦截**，`import JudgerAgent` 会直接抛 `RuntimeError`。旧 LangGraph 实现不会产生 `judger.pkl` 事件流，state 不写入 Configer，Codex 无法读取评测指标。Codex 看到此错误不应尝试绕过，必须使用上方 CLI 或受控封装路径
- ❌ Codex 进程内直接 `import loopai.skills.Judger.run()` — pipeline 内 `emit_error` 会 `sys.exit(1)`，杀死 Codex 自身进程

**正确调用后的产物（用于判断是否走了正确路径）：**
- `outputs/<task_id>/judger.pkl` — 事件流（含 `metrics`）
- `outputs/<task_id>/judger/` — 评测结果文件
- Configer `state.judger` — 流水线进度和产出路径

如果没有 `judger.pkl`，说明没有走正确路径。

## When to Use

当 Codex 或用户要执行以下操作时使用本 skill：

- 评测模型生成的代码 / SQL / 文本
- 计算 pass@k、accuracy 等指标
- 从 Configer（DB）或 starter.yaml 配置启动评测流水线
- 断点续跑中断的评测任务
- 查看评测进度事件及 pass@k/stats

不要用它处理：

- 训练、数据爬取、数据构造（走对应的 Agent/Skill）
- 全局 `system` 配置修改（走 Configer）

## Configuration

Codex 启动评测前，必须通过 **Configer skill** 检查并预填配置。**不要手动拼字段**——用 `configer_get_task` 查缺，用 `configer_update_task` 补齐。

### 预填写流程

```
1. configer_get_task(schema="states", section="judger", task_id="<task_id>")
     → 查看字段 schema 和当前 value，找出 value 为 null 的必填字段
2. 将缺失字段列表和待写入的值告知用户，**必须征得用户确认后才能写入**
3. configer_update_task("judger", {用户确认的字段}, task_id="<task_id>")
     → Configer 会校验字段名是否合法，不存在的字段直接报错
```

**⚠️ 修改 state 前必须询问用户。** 不要自动覆盖已有配置字段，不要猜测 model_path、problem_path 等路径值。

### 运行环境

| 条件 | 说明 |
|---|---|
| `DB_PATH` | Configer SQLite 数据库（MCP 自动注入） |
| `TASK_ID` | 任务唯一标识（MCP 参数传入） |
| GPU | 至少一张 CUDA GPU |
| Port 8911 | code/text2sql 的 vLLM HTTP 端口 |

### 必填字段

以下字段必须在 `state["judger"]` 中有非空值：

| 字段 | 适用 task_type | 示例 |
|---|---|---|
| `eval_task_type` | 全部 | `"code"` / `"text2sql"` / `"general_text"` |
| `eval_model_path` | 全部 | `"/data/models/Qwen2.5-7B-Instruct/"` |
| `eval_problem_path` | 全部 | `"/data/.../dev_bird_for_oj_sampled.jsonl"` |
| `eval_text2sql_dir` | text2sql | `"/data/.../dev_databases/"` |
| `bench_dataflow_eval_type` | general_text | `"key2_qa"` |

### 可选字段（有默认值，通常不需要改）

| 字段 | 默认值 | 何时需要修改 |
|---|---|---|
| `eval_temperature` | `0` | 调整生成随机性 |
| `eval_top_p` | `0.95` | 调整采样策略 |
| `eval_batch_size` | `10` | GPU 显存不足时调小 |
| `eval_case_num` | `10` | 提高 pass@k 精度 |
| `eval_vllm_tensor_parallel_size` | `1` | 多 GPU 时调整 |
| `eval_vllm_gpu_memory_utilization` | `0.9` | GPU 显存不足时调小 |
| `cuda_visible_devices` | `"0"` | 指定 GPU |
| `output_dir` | `"./outputs"` | 自定义输出路径 |
| `bench_name` | `"general_text_eval"` | general_text 基准名 |
| `key_mapping` | `{}` | general_text 字段映射（可自动推断） |

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
kwargs > 环境变量 > state["judger"]（DB）> schema 默认值
```

### validate 自动检查

- 必填字段是否缺失 → `CONFIG_ERROR`，detail 列出 `missing_fields`
- `eval_problem_path` 文件是否存在 → `NOT_FOUND`
- JSONL 字段结构是否匹配 task_type → `INVALID_INPUT`

```
loopai/skills/Judger/          ← Skill 层（独立模式，无 LangGraph）
├── __init__.py                # run() / load_events()
├── runner.py                  # 流水线主逻辑 + _load_task_state / _save_task_progress
├── runtime_config.py          # 配置解析（kwargs > env > state["judger"] > schema defaults）
└── utils/
    ├── eval_general_text.py   # general_text 评测（One-Eval DataFlowEvalTool）
    ├── generate.py            # code/text2sql 样本生成
    ├── evaluate.py            # code/text2sql 评测（含 pass@k 计算）
    └── format.py              # 数据格式转换
```

## Quick Start

### 前提条件

**必须设置 `DB_PATH` 和 `TASK_ID` 环境变量**，Configer 通过它们读写 TaskModel.state：

```bash
export DB_PATH=api/db/db.sqlite3
export TASK_ID=<your-task-uuid>
```

如果 task 在 DB 中已有配置（通过 Codex/Starter 预先写入），直接运行即可：

```bash
python examples/scripts/run_judger_standalone.py --print-result
```

### 方式 1: CLI（推荐）

```bash
# 从 DB 读取 task 配置运行（需要 DB_PATH + TASK_ID）
DB_PATH=api/db/db.sqlite3 TASK_ID=a4341a82-... \
python examples/scripts/run_judger_standalone.py --print-result

# 从 starter.yaml 读取配置运行
python examples/scripts/run_judger_standalone.py \
    --config-path examples/config/starter.yaml \
    --print-result

# 断点续跑
python examples/scripts/run_judger_standalone.py --resume

# 从指定步骤强制执行
python examples/scripts/run_judger_standalone.py --from-step evaluate

# 查看流水线步骤
python examples/scripts/run_judger_standalone.py --list-steps
```

> **注意**：CLI 脚本会自动将项目根目录加入 `sys.path`，无需设置 `PYTHONPATH`。

## Configuration

### 配置来源优先级

```
CLI --task-id / kwargs > 环境变量 > state["judger"]（DB / YAML）> schema 默认值
```

Judger 支持三种配置来源：

1. **Configer（DB）** — task 已在 TaskModel.state 中有 judger 配置时直接读取
2. **starter.yaml** — 通过 `--config-path` 传入，从 `default_states.judger` 提取
3. **环境变量** — 可覆盖上述两种来源的任意字段

> **重要**：没有 DB 配置也没有 YAML 时，`eval_model_path` 和 `eval_problem_path` 必须通过环境变量传入，否则 validate 步骤会报 `CONFIG_ERROR`。

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
    eval_vllm_tensor_parallel_size: 1 # 张量并行数（默认 1）
    eval_vllm_gpu_memory_utilization: 0.9  # GPU 显存利用率
    cuda_visible_devices: "5"         # 可见 GPU

    # --- general_text 专用 ---
    bench_name: "gsm8k"
    bench_dataflow_eval_type: "key2_qa"  # 评测类型，见下表

  # YAML 中可用短键名（兼容旧配置）：
  #   tensor_parallel_size → eval_vllm_tensor_parallel_size
```

### general_text 评测类型


| `bench_dataflow_eval_type` | 说明    |
| -------------------------- | ----- |
| `key1_text_score`          | 文本评分  |
| `key2_qa`                  | 问答评测  |
| `key2_q_ma`                | 多答案评测 |
| `key3_q_choices_a`         | 选择题评测 |
| `key3_q_choices_as`        | 多选评测  |
| `key3_q_a_rejected`        | 对比评测  |


## CLI Reference

```
python examples/scripts/run_judger_standalone.py [OPTIONS]

Options:
  --config-path PATH    配置文件路径（starter.yaml 或 JSON）
  --task-id ID          任务 ID（必填，可用 TASK_ID 环境变量替代）
  --output-dir DIR      输出目录（默认 ./outputs）
  --resume              从上次 checkpoint 恢复
  --from-step STEP      从指定步骤开始执行
  --print-result        打印结果摘要
  --print-events        打印事件列表
  --list-steps          列出流水线步骤
```

`**--task-id` 是必填的**，不传会报 `CONFIG_ERROR`。可通过 `TASK_ID` 环境变量替代。

`**--config-path` 和 `--resume` 互斥**：resume 时 state 从 Configer（DB）加载，不需要 `--config-path`。但可通过环境变量覆盖部分字段。

## Python API

```python
from loopai.skills.Judger import run, load_events

# 运行流水线
result = run(
    state=None,             # dict with state["judger"] fields，None 时从 Configer 加载
    task_id="task_001",   # 必填，= task_id
    resume=False,           # True = 从 Configer 恢复上次进度
    from_step=None,         # 强制起始步骤名
    **kwargs,               # 运行时覆盖（优先于 state）
)

# 读取事件（含 pass@k / stats）
events = load_events(task_id="task_001", output_dir="./outputs")
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


| Step                | 功能                              | 完成事件 data                                             |
| ------------------- | ------------------------------- | ----------------------------------------------------- |
| `validate`          | 校验必填字段、文件存在性、JSONL 字段结构         | `task_type`, `problem_path`                           |
| `kill_vllm`         | 关闭端口 8911 上的 vLLM 进程            | —                                                     |
| `start_vllm`        | 启动本地 vLLM 服务                    | `base_url`                                            |
| `format_data`       | 数据格式转换（human-eval / mbpp），可选    | `target`                                              |
| `generate`          | vLLM 批量生成 code/text2sql 样本      | `output_case_path`                                    |
| `evaluate`          | 执行代码/执行 SQL，计算 pass@k           | `output_result_path`, `**metrics**`                   |
| `kill_vllm_cleanup` | 评测后关闭 vLLM                      | —                                                     |
| `eval_general_text` | One-Eval DataFlowEvalTool 子进程评测 | `output_result_path`, `output_pred_path`, `**metrics**` |
| `finish`            | 流水线完成                           | —                                                     |


## Output Metrics

Judger 在不同任务类型下产出的评测指标，均写入事件流（`judger.pkl`）和输出文件。

### code / text2sql — pass@k

由 Judger 直接计算（`evaluate.py` → `_calculate_pass_at_k`），不需要 One-Eval。

| 指标 | 说明 | 计算方式 |
|---|---|---|
| `pass@1` | 1 次采样通过率 | `estimate_pass_at_k(n, c, 1)` |
| `pass@10` | 10 次采样通过率（需 `eval_case_num ≥ 10`） | `estimate_pass_at_k(n, c, 10)` |
| `pass@100` | 100 次采样通过率（需 `eval_case_num ≥ 100`） | `estimate_pass_at_k(n, c, 100)` |

- 输出位置：事件流 `data.metrics` + `outputs/<task_id>/judger/log.txt`
- k 值列表：`[1, 10, 100]`，仅当 `total_samples ≥ k` 时对应 k 才会出现在结果中

**事件示例：**

```json
{
  "current": "judger.evaluate",
  "progress": 1.0,
  "message": "评测完成",
  "data": {
    "output_result_path": "outputs/.../result.jsonl",
    "metrics": {
      "pass@1": 0.3125
    }
  }
}
```

### general_text — One-Eval stats

由 One-Eval `DataFlowEvalTool` 计算并返回 `stats` 字典。具体包含哪些指标取决于 `bench_dataflow_eval_type`。

**所有 eval_type 通用的 stat 键：**

| 键 | 类型 | 说明 |
|---|---|---|
| `accuracy` | `float` | 综合准确率（0~1） |
| `score` | `float` | 综合得分（通常 = accuracy） |
| `total_samples` | `int` | 总样本数 |
| `valid_samples` | `int` | 有效样本数 |

**按 eval_type 的专属指标：**

| `bench_dataflow_eval_type` | 典型产出指标 |
|---|---|
| `key1_text_score` | `bleu`, `rouge`, `chrf`, `ter`, `token_f1`, `exact_match`, `containment_match` |
| `key2_qa` | `exact_match`, `containment_match`, `numerical_match`, `token_f1` |
| `key2_q_ma` | `exact_match`, `token_f1` |
| `key3_q_choices_a` | `choice_accuracy`, `exact_match` |
| `key3_q_choices_as` | `exact_match`, `token_f1` |
| `key3_q_a_rejected` | 对比评测指标（pairwise comparison） |

**One-Eval 支持的完整指标集：**

| 指标名 | 类别 | 说明 |
|---|---|---|
| `pass_at_k` | code | 代码 pass@k（One-Eval 版本） |
| `code_similarity` | code | 代码相似度 |
| `soft_code_execution` | code | 软代码执行评测 |
| `exact_match` | general | 精确匹配 |
| `containment_match` | general | 包含匹配 |
| `strict_match` | general | 严格匹配 |
| `numerical_match` | general | 数值匹配 |
| `choice_accuracy` | general | 选择题准确率 |
| `bleu` | text_gen | BLEU 机器翻译评测 |
| `rouge` | text_gen | ROUGE 摘要评测 |
| `chrf` | text_gen | 字符级 n-gram F-score |
| `ter` | text_gen | 翻译错误率 |
| `token_f1` | text_gen | Token 级 F1 |
| `math_verify` | math | 数学表达式验证 |
| `symbolic_match` | symbolic | 符号匹配 |
| `spearman` | classification | Spearman 排名相关系数 |
| `pearson` | classification | Pearson 相关系数 |
| `mcc` | classification | Matthews 相关系数 |
| `auc_roc` | classification | AUC-ROC |
| `gini_index` | classification | Gini 系数 |

> **注意**：上表为 One-Eval 的完整能力。实际 stats 中出现的指标由 One-Eval 根据 task_type 和数据特征自动选择。不是所有指标都会同时出现。

**事件示例：**

```json
{
  "current": "judger.eval_general_text",
  "progress": 1.0,
  "message": "通用文本评测完成",
  "data": {
    "output_result_path": "outputs/.../text_eval_summary_20240625_120000.json",
    "output_pred_path": "outputs/.../text_eval_scored_20240625_120000.json",
    "metrics": {
      "accuracy": 0.94,
      "score": 0.94,
      "total_samples": 100,
      "valid_samples": 95,
      "token_f1": 0.89
    }
  }
}
```

### 指标产出总结

| 指标类型 | code/text2sql | general_text | 输出位置 |
|---|---|---|---|
| `pass_at_k` | ✅ | — | stdout (metrics) + 事件流 + log.txt |
| `accuracy` | — | ✅ | stdout (metrics) + 事件流 + summary JSON |
| `score` | — | ✅ | stdout (metrics) + 事件流 + summary JSON |
| `token_f1` | — | ✅ | stdout (metrics) + 事件流 + summary JSON |
| `exact_match` | — | ✅ | stdout (metrics) + 事件流 + summary JSON |
| `bleu` / `rouge` / `chrf` | — | ✅ (text_score) | stdout (metrics) + 事件流 + summary JSON |
| `spearman` (ranking) | — | ✅ (classification) | stdout (metrics) + 事件流 + summary JSON |
| `reward_score` | — | ❌ (One-Eval 不直接产出) | — |

### 如何读取指标

```python
from loopai.skills.Judger import load_events

events = load_events(task_id="my_task")
for e in events:
    data = e.get("data") or {}

    # 统一指标：code/text2sql → pass@k，general_text → accuracy/score/f1 等
    if "metrics" in data:
        for k, v in data["metrics"].items():
            print(f"{k}: {v:.4f}")
```

## Environment Variables


| 变量                                | 对应字段                               | 默认值                 |
| --------------------------------- | ---------------------------------- | ------------------- |
| `DB_PATH`                         | Configer 数据库路径                     | 必填（Configer 模式）     |
| `TASK_ID`                         | `task_id`                          | 必填，无默认              |
| `OUTPUT_DIR`                      | `output_dir`                       | `./outputs`         |
| `JUDGER_MODEL_PATH`               | `eval_model_path`                  | 必填（无 DB/YAML 时）     |
| `JUDGER_TASK_TYPE`                | `eval_task_type`                   | `code`              |
| `JUDGER_TEMPERATURE`              | `eval_temperature`                 | `0`                 |
| `JUDGER_TOP_P`                    | `eval_top_p`                       | `0.95`              |
| `JUDGER_PROBLEM_PATH`             | `eval_problem_path`                | 必填（无 DB/YAML 时）     |
| `JUDGER_BATCH_SIZE`               | `eval_batch_size`                  | `10`                |
| `JUDGER_CASE_NUM`                 | `eval_case_num`                    | `10`                |
| `JUDGER_FORMAT_TYPE`              | `eval_format_type`                 | 可选                  |
| `JUDGER_TEXT2SQL_DIR`             | `eval_text2sql_dir`                | text2sql 必填         |
| `JUDGER_TENSOR_PARALLEL_SIZE`     | `eval_vllm_tensor_parallel_size`   | `1`                 |
| `JUDGER_GPU_MEMORY_UTILIZATION`   | `eval_vllm_gpu_memory_utilization` | `0.9`               |
| `CUDA_VISIBLE_DEVICES`            | `cuda_visible_devices`             | `0`                 |
| `JUDGER_BENCH_NAME`               | `bench_name`                       | `general_text_eval` |
| `JUDGER_BENCH_DATAFLOW_EVAL_TYPE` | `bench_dataflow_eval_type`         | 空（general_text 必填）  |


## Output & Artifacts

```
outputs/<task_id>/
├── judger/
│   ├── <name>_format.jsonl           # 格式化后的问题文件
│   ├── <name>_sample.jsonl           # 生成的样本
│   ├── <name>_result.jsonl           # 评测结果
│   ├── log.txt                       # 评测日志（含 pass@k）
│   ├── text_eval_summary_*.json      # general_text 摘要
│   ├── general_text_dataset_cache_*.jsonl  # 缓存
│   └── gsm8k_*_steps/               # One-Eval 中间产物
└── judger.pkl                        # 事件 pickle（load_events 读取）
```

- **stdout** — 最终结果 JSON payload（`emit_success` / `emit_error`，Codex 消费）
- **stderr** — `--print-result` / `--print-events` 的输出
- **judger.pkl** — 所有进度事件，含步骤完成时的 `metrics`（pass@k 或 stats）
- **log.txt** — 评测日志（`outputs/<task_id>/judger/log.txt`），含 pass@k 数值

## State & Resume（Configer）

### 工作原理

Judger 通过 Configer 读写 TaskModel.state：

- **读取**：`_load_task_state(task_id)` 调用 `get_configer_task_state_config` 从 DB 读取 `state.judger`
- **写入**：每步执行前后调用 `_save_task_progress` → `update_configer_task_state_config` 写入进度

state 中的关键字段：


| 字段                                | 用途                        |
| --------------------------------- | ------------------------- |
| `state.judger._last_completed`    | 最后完成的步骤名（如 `evaluate`）    |
| `state.judger._current`           | 当前步骤（如 `judger.generate`） |
| `state.judger.output_result_path` | 评测结果路径                    |
| `state.judger.output_case_path`   | 样本路径                      |


### 断点续跑

```bash
# 从上次中断处继续（DB_PATH + TASK_ID 指向已有进度的 task）
python examples/scripts/run_judger_standalone.py --resume

# resume + 覆盖部分配置
CUDA_VISIBLE_DEVICES=6 python examples/scripts/run_judger_standalone.py --resume

# 从指定步骤强制执行（跳过之前所有步骤）
python examples/scripts/run_judger_standalone.py --from-step evaluate
```

`**--resume` 时 state 从 Configer 加载**，不需要 `--config-path`。可通过环境变量覆盖字段（如换 GPU）。

`**_is_finished` 检查**：如果 `last_completed == "finish"`，流水线跳过所有步骤直接返回。

## Event System

### 事件写入

流水线运行时自动持久化到 `<output_dir>/<task_id>/judger.pkl`：

```python
from loopai.common.event_tool import get_event_writer, StreamEvent

writer = get_event_writer(name="judger", context_id="task_001", log_file_path="./outputs")
writer(StreamEvent(current="judger.generate", progress=0.5, message="样本生成中"))
```

### 事件格式

每个事件包含：

- `current` — 当前步骤（格式 `judger.<step_name>`）
- `progress` — 步骤内进度 0.0 ~ 1.0
- `message` — 人类可读描述
- `data` — 结构化数据（步骤完成时包含关键结果）
- `status` — 仅终态事件：`"completed"`（成功）或 `"failed"`（失败）

### 终态事件

流水线结束时自动写入：

```json
// 成功 — writer.set_completed()
{"current": "judger", "status": "completed", "message": "Sub-agent completed."}

// 失败 — emit_error(stream_writer=writer) → writer.set_failed()
{"current": "judger", "status": "failed", "message": "Sub-agent failed.", "error": {...}}
```

### 步骤完成事件 data

evaluate 步骤完成时（code/text2sql）：

```json
{
    "output_result_path": "outputs/.../result.jsonl",
    "metrics": {
        "pass@1": 0.85,
        "pass@10": 0.95,
        "pass@100": 1.0
    }
}
```

eval_general_text 步骤完成时：

```json
{
    "output_result_path": "outputs/.../summary.json",
    "output_pred_path": "outputs/.../step2.jsonl",
    "metrics": {
        "accuracy": 0.94,
        "score": 0.88,
        "total_samples": 100,
        "valid_samples": 95
    }
}
```

### 事件读取

```python
from loopai.skills.Judger import load_events

events = load_events(task_id="my_task")
for e in events:
    data = e.get("data") or {}
    if "metrics" in data:
        for k, v in data["metrics"].items():
            print(f"{k}: {v:.4f}")
```

## Error Handling

每个步骤失败点直接调用 `emit_error(exc, stream_writer=writer)`，**三通道同时输出**：

| 通道 | 机制 | 内容 |
|---|---|---|
| stdout | `print` error JSON | `{"ok": false, "error": {"code": "CONFIG_ERROR", ...}}` |
| judger.pkl | `stream_writer.set_failed()` → `_append_status_event` | status="failed" 事件 |
| DB | `stream_writer._sync_runtime(status="failed")` | taskruntime 表标记失败 |

成功时 pipeline 末尾调 `writer.set_completed()`，同样写 judger.pkl + DB。

| ErrorCode | 触发场景 |
|---|---|
| `CONFIG_ERROR` | 缺少必填字段、模型路径未配置、task_id 缺失 |
| `INVALID_INPUT` | JSONL 字段不匹配、不支持的任务类型、未知步骤名 |
| `NOT_FOUND` | 问题文件不存在 |
| `EXTERNAL_SERVICE_ERROR` | DataFlowEvalTool 子进程失败、vLLM 启动失败 |
| `UNHANDLED_EXCEPTION` | 意外的未分类异常 |

### 错误响应格式（stdout）

```json
{
    "ok": false,
    "status": "failed",
    "message": "Judger configuration is incomplete.",
    "data": null,
    "error": {
        "type": "ValueError",
        "code": "CONFIG_ERROR",
        "detail": "Missing required fields: ...",
        "traceback": "...",
        "recoverable": true,
        "time": "2026-06-25T12:00:00Z"
    }
}
```

### 成功响应格式

**code/text2sql：**

```json
{
    "ok": true,
    "status": "completed",
    "message": "Judger pipeline completed.",
    "data": {
        "task_type": "text2sql",
        "output_result_path": "/path/to/result.jsonl",
        "output_case_path": "/path/to/sample.jsonl",
        "output_problem_path": "/path/to/problem.jsonl",
        "output_pred_path": "",
        "bench": "",
        "metrics": {
            "pass@1": 0.3125
        }
    },
    "error": null
}
```

**general_text：**

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
        "output_pred_path": "/path/to/scored.jsonl",
        "bench": {
            "bench_name": "gsm8k",
            "eval_status": "success",
            "meta": {"eval_result": {"accuracy": 0.94}}
        },
        "metrics": {
            "accuracy": 0.94,
            "score": 0.94,
            "total_samples": 100,
            "valid_samples": 95
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
import json, os, sys
os.environ["DB_PATH"] = "api/db/db.sqlite3"
os.environ["TASK_ID"] = "codex_task_001"

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
        task_id="codex_task_001",
    )
    # 成功：emit_success 输出到 stdout
    sys.exit(0)
except Exception as e:
    # 错误：emit_error 已输出到 stdout
    sys.exit(1)
PY
```

Codex 读到的 stdout 行：

- 成功：`{"ok": true, "data": {"output_result_path": "...", "bench": {...}}}`
- 失败：`{"ok": false, "error": {"code": "CONFIG_ERROR", "detail": "..."}}`

Codex 也可以通过 `load_events` 读取 judger.pkl 获取 pass@k / stats 等评测指标。

## Config Via Configer

Judger 配置字段可通过 Configer skill 读写（按 task_id 隔离）：

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    get_configer_task_state_config,
    update_configer_task_state_config,
)

# 查看 schema（字段含义、允许值、默认值）
schema = get_configer_state_schema(section_name="judger")

# 读取某个 task 的当前配置
config = get_configer_task_state_config(
    section_name="judger",
    task_id="a4341a82-4ed4-46da-8776-d9cf45a4f50c",
)

# 修改某个 task 的配置（运行前预设参数）
update_configer_task_state_config(
    "judger",
    {"eval_temperature": 0.2, "eval_case_num": 20},
    task_id="a4341a82-4ed4-46da-8776-d9cf45a4f50c",
)
```

**流水线进度也通过 Configer 持久化**：`_last_completed` 和 `_current` 字段在每步前后自动写入 `state.judger`，resume 时从中恢复。
