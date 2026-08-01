# Trainer Skill 使用指南

Trainer Skill 是 Dataflow-LoopAI 中负责模型训练的技能实现，支持两条互不混用的路径：

- `sft + llamafactory`：保留原有监督微调流程。
- `grpo + verl`：使用 Verl 执行 GRPO 强化学习，初版正式支持 FSDP 与 vLLM/SGLang rollout。

Trainer 不依赖 MCP。对调用方仍保持同步返回语义，但训练、进度持久化和结果收尾由独立本地 Worker 持有；Codex/API 会话提前结束不会中断 Worker。两条路径都先生成完整 YAML，用户确认后才启动训练。

## Verl GRPO 最小配置

```python
from loopai.skills.Trainer import prepare, run_prepared

state = {
    "task_id": "my-grpo-task",
    "output_dir": "./outputs",
    "trainer": {
        "train_framework": "verl",
        "train_stage": "grpo",
        "verl_dir": "/path/to/verl",
        "verl_env_path": "verl",  # Conda 环境名；默认就是 verl
        "train_input_dataset_path": "/data/train.parquet",
        "train_input_eval_dataset_path": "/data/validation.parquet",
        "train_input_model_name": "/models/base-model",
        "train_input_task_description": "Use GRPO to optimize Text2SQL accuracy.",
        "verl_reward_mode": "custom",
        "verl_reward_function_path": "/data/text2sql_reward.py",
        "verl_reward_function_name": "compute_score",
        "CUDA_VISIBLE_DEVICES": "0,1,2,3",
    },
}

prepared = prepare(state=state, thread_id=state["task_id"])
approval = prepared["trainer"]["trainer_result"]["data"]
# 先向用户展示 approval["config_yaml"]；用户明确确认后：
result = run_prepared(
    approval["config_path"],
    approval["config_sha256"],
    state=prepared,
    thread_id=state["task_id"],
    version_id=approval["trainer_version_id"],
)
```

GRPO 训练/验证 Parquet 至少需要 `prompt`、`data_source`、`reward_model` 三列。训练通过 `conda run --no-capture-output -n verl python -m verl.trainer.main_ppo ...` 启动；指标写入 Trainer 的 `metrics` 目录，checkpoint 按 `global_step_N` 识别，根据 YAML 中的验证指标选择最佳项，再把选中的 FSDP actor 合并为 Hugging Face 模型。

## 生成数据与多轮 GRPO

Constructor 仍负责生成和清洗数据；当 `train_framework=verl` 时，Trainer 在每轮 `prepare()` 中负责最后一段训练协议适配：

1. 优先读取本轮 `constructor.mapping_results.output_file`，也可用 `verl_source_dataset_path` 显式覆盖。
2. 自动识别原生 Verl、messages/ShareGPT、Alpaca 或普通 QA，转换为本轮目录下的 `prepared_data/train.parquet`。
3. 若未提供验证数据，按 `verl_validation_ratio` 和 `verl_split_seed` 确定性切分；多轮仅在 reward 协议兼容时复用上一轮验证 Parquet。
4. 生成 `dataset_manifest.json` 和 `rejected_rows.jsonl`，记录来源、去重、拒绝、切分、哈希和 reward 决策。
5. 以上一轮已确认的 Verl YAML 为超参基线，只刷新本轮数据、最佳模型、reward、GPU、输出目录和 version；然后仍向用户展示整份新 YAML，等待本轮确认。

生成数据的最小增量配置如下；通常不需要用户手写 Verl Parquet：

```yaml
trainer:
  train_framework: verl
  train_stage: grpo
  verl_dir: /path/to/verl
  verl_env_path: verl
  train_input_model_name: /path/to/base-model
  train_input_task_description: Mathematics reasoning with GRPO
  verl_source_dataset_path: ""       # 留空时使用本轮 Constructor 输出
  verl_data_adapter: auto
  verl_validation_ratio: 0.05
  verl_reuse_previous_validation: true
  verl_inherit_previous_config: true
  verl_use_previous_best_model: true
  verl_multi_round_enabled: true
  verl_reward_mode: auto
```

`auto` 会针对每轮新的上游数据重新判断，只在任务/数据来源或答案标记能可靠对应已有 preset 时采用建议；无法判断时 `prepare()` 会停止并要求设置 `verl_reward_mode=preset`/`verl_reward_preset` 或 custom reward，不会猜测 ground truth 或 reward 语义。上一轮模型只有在训练成功、导出无错误且目录包含可加载的 Hugging Face 配置和权重时才会自动传给下一轮；若要改用指定模型，可在本轮调用显式传 `train_input_model_name`，或关闭 `verl_use_previous_best_model`。

## Verl Reward 预设

LoopAI 通过 `loopai/skills/Trainer/rewards/router.py` 提供稳定入口，预设只调用当前 Verl 环境中的实现，不复制 Verl 源码。

```yaml
trainer:
  verl_reward_mode: preset       # auto | preset | custom
  verl_reward_preset: math_boxed
  verl_reward_kwargs: {}
```

可用预设：

- `auto` / `verl_builtin`：根据 Parquet 的 `data_source` 使用 Verl 默认路由；LoopAI 环境安装了 `pyarrow` 时会在准备阶段拒绝未知来源，否则会明确告警并交给 Verl 启动时校验。
- `gsm8k_exact`：要求输出 `#### answer`，与 `ground_truth` 精确比较。
- `math_boxed`：提取最后一个 `\\boxed{}` 并进行 MATH 风格等价比较。
- `math_dapo`：DAPO 数学验证，返回 reward、accuracy 和预测答案。
- `prime_math`：Numina/PRIME 数学验证。
- `geometry`：Geometry3K 正确性与格式联合打分。
- `qa_exact_match`：提取 `<answer>...</answer>` 并执行规范化精确匹配。

`custom` 模式保持原有方式，必须配置 `verl_reward_function_path`；`verl_reward_kwargs` 会传给自定义函数。Trainer 在生成 YAML 前检查训练/验证 Parquet、抽样检查 `prompt`、`data_source`、`reward_model.ground_truth`，并校验预设或自定义函数入口。

## 🏗️ 架构设计

Trainer Skill 内部采用三阶段顺序执行架构：

```
数据检查 → 配置生成 → 独立 Trainer Worker
                         ├─ 启动 LLaMAFactory / Verl
                         ├─ 更新 trainer.pkl / run_state.json
                         └─ 结果分析、最佳 checkpoint 与模型导出
```

默认 `trainer_persistent_worker: true`。调用方存活时会同步等待 Worker 并返回原有结果结构；调用方断开后，Worker 继续运行。每次运行在 `outputs/{task_id}/trainer/{version_id}/` 下新增：

- `run_state.json`：Worker、训练 PID、当前 step、总 step 和最终状态。
- `worker.log`：独立 Worker 自身日志。
- `worker_result.pkl`：用于调用方重连和恢复最终 state，仅本机用户可读。

Verl 实时进度优先读取 `metrics/verl_metrics.jsonl` 的 `training/global_step`，不再依赖 tqdm 的回车刷新文本。若需调试时临时恢复旧的会话内执行方式，可显式设置 `trainer_persistent_worker: false`。

### 1. 数据检查节点 (Data Check Node)

**功能：** 验证 SFT 数据；对 Verl 则先适配生成数据，再验证训练/验证 Parquet 与 reward 协议

**输入：**
- `train_input_dataset_path`: SFT 数据或原生 Verl Parquet
- `verl_source_dataset_path`: 可选的生成数据路径；未配置时读取 Constructor 输出

**输出：**
- 数据格式验证报告
- 数据样本统计信息
- 格式错误和警告列表

**支持的数据格式：**

1. **指令格式（Alpaca）：**
```json
{
  "instruction": "请计算 2 + 2 的结果",
  "input": "",
  "output": "2 + 2 = 4"
}
```

2. **对话格式：**
```json
{
  "conversations": [
    {"from": "human", "value": "你好"},
    {"from": "gpt", "value": "你好！我是AI助手"}
  ]
}
```

### 2. 配置生成节点 (Config Generation Node)

**功能：** 根据任务描述智能生成 LlamaFactory 训练配置

**输入：**
- `train_input_task_description`: 训练任务描述
- `train_input_model_name`: 基础模型名称（可选）
- `train_input_config_template_path`: 配置模板路径（可选）

**智能配置特性：**

- **自适应学习率：** 根据任务复杂度自动调整
  - 复杂任务（数学、推理）：`1e-5`
  - 对话任务：`5e-5`
  
- **动态训练轮数：** 
  - 微调任务：1 轮
  - 完整训练：5 轮
  
- **智能 LoRA 参数：**
  - 代码任务：`lora_r=16, lora_alpha=32, target=all`
  - 对话任务：`lora_r=8, lora_alpha=16, target=q_proj,v_proj`

### 3. 训练执行节点 (Training Execution Node)

**功能：** 执行 LlamaFactory 或 Verl 训练，并通过本地指标文件提供进度和结果

**特性：**
- 自动环境验证（Python、CUDA、依赖包）
- 实时解析本地训练日志和指标文件
- 不依赖外部实验跟踪服务
- 详细的训练报告生成

## 📝 使用方法

### 基本用法

```python
from loopai.skills.Trainer.trainer_agent import TrainerAgent
from loopai.memory import checkpointer, store

# 创建 TrainerAgent 实例
trainer = TrainerAgent(checkpointer=checkpointer, store=store)

# 准备训练状态
training_state = {
    # 必需字段
    'train_input_dataset_path': "/jizhicfs/hymiezhao/lpc/repos/LLaMA-Factory/data/alpaca_en_demo.json",  # 使用 JSON 格式数据集
    'train_input_task_description': '训练一个能够回答简单问题和进行对话的AI助手模型，主要用于日常对话和基础问答任务',
    'train_input_config_template_path': "loopai/skills/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
    'train_input_model_name': '/jizhicfs/hymiezhao/models/Qwen2.5-1.5B',
    'output_dir': './output/trainer_test',
}

# 构建并执行图
config = {"configurable": {"thread_id": "my_training"}}
graph = trainer()
result = graph.invoke(training_state, config=config)
```

## 📊 状态字段说明

### 输入字段

| 字段名 | 类型 | 必需 | 默认值 | 说明 |
|-------|------|-----|--------|-----|
| `train_input_dataset_path` | str | ✅ | - | 训练数据集路径 |
| `train_input_task_description` | str | ✅ | - | 训练任务描述 |
| `train_input_model_name` | str | ✅ | - | 基础模型名称 |
| `train_input_config_template_path` | str | ✅ | - | 配置模板路径 |
| `train_output_dir` | str | ✅ | ./output/training | 训练输出目录 |
| `output_dir` | str | ❌ | ./output/trainer | Agent输出目录 |

### 输出字段

| 字段名 | 类型 | 说明 |
|-------|------|-----|
`train_output_data_check_report_path` | str | 数据检查报告路径 |
| `train_output_config_path` | str | 生成的配置文件路径 |
| `train_output_training_log_path` | str | 训练日志文件路径 |
|`train_output_training_report_path` | str | 训练报告路径 |

训练指标保存在运行目录的本地文件中：SFT 使用 `trainer_log.jsonl` 和
`metrics/metrics.json`，Verl 使用 `metrics/verl_metrics.jsonl`。前端曲线、结果分析和
最佳 checkpoint 选择都读取这些文件，不需要外部实验跟踪服务。
