# Judger Agent 详细指南

`JudgerAgent` 是 LoopAI 闭环中的评测节点，负责把“当前模型表现如何”这件事测清楚。

当前实现是一套**独立的函数流水线**（不依赖 LangGraph），通过评测集列表（`benchlist` / `extra_benchlist`）在一次运行里评测一个或多个 bench。

## 核心职责

- 执行评测任务（`code` / `text2sql` / `general_text`）
- 调用本地 vLLM 服务完成样本生成
- 执行代码 / SQL 计算通过率（pass@k）；通用文本走 One-Eval DataFlowEvalTool
- 产出样例输出、分数与评测结果，供 Analyzer 使用

## 运行方式

`Judger` 是独立命令行工具 `loopai-judger`（入口 `loopai.skills.Judger.cli:main`），也会作为子进程被 Codex / 后端调用（`loopai.skills.Judger.run`）。

运行前需要两个环境变量：

| 环境变量 | 说明 |
| --- | --- |
| `DB_PATH` | SQLite 数据库路径（任务状态与配置存储） |
| `TASK_ID` | 任务 ID |

```bash
loopai-judger                        # 从头运行
loopai-judger --resume               # 从上次 checkpoint 恢复
loopai-judger --from-step generate   # 从指定步骤开始
```

## 配置模型：benchlist / extra_benchlist

新版不再用单一的 `eval_problem_path` 配置，而是通过**评测集列表**组织：

- `benchlist`（主任务评测集）：某个 bench 失败会终止整个流水线
- `extra_benchlist`（附加任务评测集）：失败只记录、不影响主任务

每个 bench 是一个 dict，字段如下：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `str` | 评测集名称，同时作为输出目录名 |
| `task_type` | `str` | `code` / `text2sql` / `general_text` |
| `problem_path` | `str` | `jsonl` 格式的问题集文件路径 |
| `case_num` | `int` | 每条问题生成样例数（可选，默认取全局 `eval_case_num`） |
| `batch_size` | `int` | 批大小（可选，默认取全局 `eval_batch_size`） |
| `temperature` | `float` | 覆盖全局 `eval_temperature`（可选） |
| `top_p` | `float` | 覆盖全局 `eval_top_p`（可选） |
| `max_tokens` | `int` | 覆盖全局 `eval_max_tokens`（可选） |
| `enable_thinking` | `bool` | 覆盖全局 `eval_enable_thinking`（可选，`false` 强制关闭思考） |
| `text2sql_dir` | `str` | `text2sql` 任务的数据库文件夹（可选） |
| `eval_type` | `str` | `general_text` 任务的评测类型（可选） |
| `key_mapping` | `dict` | `general_text` 任务的字段映射（可选） |

`general_text` 支持的 `eval_type`：`key2_qa`、`key2_q_ma`、`key3_q_choices_a`、`key3_q_choices_as`、`key3_q_a_rejected`、`key1_text_score`。

### Per-bench 可选覆盖（特殊测试）

`case_num` / `batch_size` / `temperature` / `top_p` / `max_tokens` / `enable_thinking` 这 6 个字段，**既可以在全局设置，也可以在单个 bench 里单独设置**：

- bench 里设置了 → 覆盖全局值，只对这一个 bench 生效
- bench 里没设置 → 回落到全局默认

用于「某个评测集需要特殊生成参数」的场景，例如某个 code 评测集想用更低温度、某个 text2sql 评测集要关闭思考模式：

```json
{
  "benchlist": [
    {
      "name": "human_eval_default",
      "task_type": "code",
      "problem_path": "/data/humaneval.jsonl"
    },
    {
      "name": "human_eval_cold",
      "task_type": "code",
      "problem_path": "/data/humaneval.jsonl",
      "temperature": 0.3,
      "enable_thinking": false,
      "max_tokens": 4096
    }
  ]
}
```

上面的 `human_eval_cold` 覆盖了全局的 `eval_temperature` / `eval_enable_thinking` / `eval_max_tokens`，而 `human_eval_default` 用全局默认。

## 全局配置字段

以下字段对整个任务生效，均支持环境变量覆盖：

| 字段 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- |
| `eval_model_path` | - | 被评测模型路径；为空时尝试从 trainer checkpoint 推断 | `JUDGER_MODEL_PATH` |
| `eval_temperature` | `0` | 模型温度 | `JUDGER_TEMPERATURE` |
| `eval_top_p` | `0.95` | top-p 采样累计概率阈值 | `JUDGER_TOP_P` |
| `eval_enable_thinking` | 不设置 | 是否开启评估模型的思考模式（如 Qwen3 的 `enable_thinking`）；`true`/`false` 会通过 `chat_template_kwargs` 显式开关，不设置则跟随模型默认 | `JUDGER_ENABLE_THINKING` |
| `eval_max_tokens` | `16384` | 最大输出 token 数（含思考模式的推理 token） | `JUDGER_MAX_TOKENS` |
| `eval_batch_size` | `10` | 批大小 | `JUDGER_BATCH_SIZE` |
| `eval_case_num` | `10` | 每条问题样例数 | `JUDGER_CASE_NUM` |
| `eval_vllm_tensor_parallel_size` | `1` | vLLM 张量并行大小 | `JUDGER_TENSOR_PARALLEL_SIZE` |
| `eval_vllm_gpu_memory_utilization` | `0.9` | vLLM GPU 显存利用率 | `JUDGER_GPU_MEMORY_UTILIZATION` |
| `cuda_visible_devices` | `0` | 可见 GPU 编号 | `CUDA_VISIBLE_DEVICES` |

## 流水线步骤

完整步骤列表：`validate`、`kill_vllm`、`start_vllm`、`format_data`、`generate`、`evaluate`、`kill_vllm_cleanup`、`eval_general_text`、`finish`。

按任务类型分成两条路径：

**code / text2sql**：

```
validate → kill_vllm → start_vllm → format_data → generate → evaluate → kill_vllm_cleanup → finish
```

**general_text**（无需 vLLM 生命周期管理）：

```
validate → eval_general_text → finish
```

各步骤职责：

- `validate`：校验必填字段、问题文件存在性与 JSONL 字段结构。
- `kill_vllm` / `start_vllm`：关闭 / 启动本地 vLLM 服务（端口 `8911`），启动后设置 `eval_base_url`。
- `format_data`：可选的数据格式转换（human-eval / mbpp），由 `eval_format_type` 触发；该字段当前未在 schema 中暴露，默认跳过。
- `generate`：调用 vLLM 批量生成样本，写 `<bench_name>_sample.jsonl`。
- `evaluate`：执行代码 / SQL，计算 pass@k，写 `<bench_name>_result.jsonl`。
- `kill_vllm_cleanup`：评测结束后关闭 vLLM。
- `eval_general_text`：One-Eval DataFlowEvalTool 子进程评测。

## 评测数据集字段

### `code` 任务

| 字段名 | 含义 | 说明 |
| --- | --- | --- |
| `task_id` | 题目标号 | 格式可以是 `问题集名/序号` 或 `序号`。 |
| `prompt` | 问题提示词 | 通常是函数定义加问题描述。为了减少后处理，模型生成结果应为完整函数。 |
| `entry_point` | 评测入口函数 | 例如 `return1`。 |
| `canonical_solution` | 标准程序 | 例如 `def return1():\n    return 1`，需要提供完整代码。 |
| `test_list` | 测试用例列表 | 例如 `["assert return1() == 1"]`，其中函数名应与 `entry_point` 一致。 |

> 备注：`validate` 步骤默认按上述字段校验；若设置了 `eval_format_type` 为 `human-eval` 或 `mbpp`，则对应使用 `test` / `text`、`code`、`challenge_test_list` 等字段，并先由 `format_data` 转换。

### `text2sql` 任务

| 字段名 | 含义 | 说明 |
| --- | --- | --- |
| `task_id` | 题目标号 | 格式可以是 `问题集名/序号` 或 `序号`。 |
| `prompt` | 问题提示词 | 模型的输入提示。 |
| `db_id` | 数据库名称 | 若值为 `dbName`，则 `dbName.sqlite` 应位于 `{text2sql_dir}/dbName` 目录下。 |
| `question` | 问题内容 | 例如自然语言查询问题。 |
| `ground_truth` | 标准答案 | 对应问题的标准 SQL。 |

### `general_text` 任务

`general_text` 数据集是通用 JSONL，**字段名不做强制要求**。评测前系统会通过 `key_mapping`（或扫描前几行自动推断）把字段映射到评测所需的关键字段。

- 若在 bench 里配置了 `key_mapping`，直接使用；否则由 `_generate_key_mapping` 按 `eval_type` 自动识别。
- 常用映射键：`input_question_key`、`input_target_key`、`input_pred_key`、`input_choices_key`、`input_label_key` 等（随 `eval_type` 不同而不同）。

## 输入与输出

**输入**：模型信息、评测任务定义（benchlist / extra_benchlist）、数据集与评测配置。

**输出**（均位于 `<output_dir>/<task_id>/judger/<version_id>/<bench_name>/` 下）：

| 任务类型 | 产物 | 说明 |
| --- | --- | --- |
| code / text2sql | `<bench_name>_sample.jsonl` | 生成样本（含 `task_id`、`completion`） |
| code / text2sql | `<bench_name>_result.jsonl` | 评测结果（含 `passed`、`result`） |
| general_text | summary JSON / detail | `output_result_path`（汇总）、`output_pred_path`（明细） |

评测结果聚合到 `bench_result` / `extra_bench_result`，其中 `metrics` 为 pass@k（code/text2sql）或评测统计（general_text），可直接提供给 Analyzer。

事件流在执行期间实时写入 `<output_dir>/<task_id>/judger.pkl`，事后可用 `load_events(task_id, output_dir)` 读取。

## 在闭环中的位置

Judger 通常是闭环里真正开始执行的第一层。没有这一步，后续分析、数据获取和训练都缺少可靠依据。

## 使用时最该关注什么

- 模型服务（本地 vLLM）是否可用
- 每个 bench 的 `task_type` 与 `problem_path` 是否正确
- 结果路径是否成功生成
- 输出样例是否足以支撑后续问题分析
