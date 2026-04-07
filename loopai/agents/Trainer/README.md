# Trainer Agent 使用指南

Trainer Agent 是 Dataflow-LoopAI 框架中负责模型训练的智能代理。支持 **LlamaFactory**（SFT 微调）和 **verl**（RL/SFT 训练）两种训练框架。

## 🏗️ 架构设计

通过 `train_framework` 字段自动选择对应框架的执行逻辑：

```
数据检查 → 配置生成 → 训练执行
    ↓         ↓         ↓
   结束      结束      结束
```

## 📦 双框架对比

| 特性 | LlamaFactory | verl |
|------|-------------|------|
| `train_framework` | `"llamafactory"` | `"verl"` |
| 训练类型 | SFT 微调 | GRPO / PPO / SFT |
| 数据格式 | Alpaca/对话 (JSON/JSONL) | prompt/messages (Parquet/JSON/JSONL) |
| 配置生成 | YAML (规则式+LLM) | Shell 脚本 (模板+自动调参) |
| 训练执行 | `llamafactory-cli train` | `bash script.sh` |
| 额外必需字段 | `llamafactory_dir` | `verl_dir` |

---

## 🔧 verl 框架

### 数据格式

**RL 模式 (GRPO/PPO)：** 需要 `prompt` 字段
```json
{"prompt": [{"role": "user", "content": "请计算 2+2"}], "data_source": "math"}
```

**SFT 模式：** 需要 `messages` 字段
```json
{"messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}]}
```

支持 `.parquet` / `.json` / `.jsonl` 格式。

### 用法示例

```python
from loopai.agents import TrainerAgent
from loopai.memory import checkpointer, store

trainer = TrainerAgent(checkpointer=checkpointer, store=store)

training_state = {
    "trainer": {
        "train_framework": "verl",
        "verl_train_mode": "grpo",          # grpo / ppo / sft
        "verl_dir": "/path/to/verl",
        "verl_env_path": "/path/to/verl_env",
        "train_input_dataset_path": "/path/to/train.parquet",
        "train_input_task_description": "数学推理 GRPO 训练",
        "train_input_model_name": "Qwen/Qwen2-7B-Instruct",
        "CUDA_VISIBLE_DEVICES": "0,1,2,3,4,5,6,7",
    },
    "output_dir": "./output/verl_test",
}

graph = trainer()
result = graph.invoke(training_state, config={"configurable": {"thread_id": "verl_training"}})
```

### 训练模式

| 模式 | 说明 |
|------|------|
| `grpo` | Group Relative Policy Optimization，无需 Critic，采样 N 个响应组内对比 |
| `ppo` | Proximal Policy Optimization，需要 Critic 模型 |
| `sft` | Supervised Fine-Tuning，使用 torchrun 执行 |

### 配置生成

- **自动生成**（推荐）：不提供模板路径，系统根据 `verl_train_mode` 自动生成脚本
- **自定义模板**：提供 `train_input_config_template_path` 指向 `.sh` 脚本

自动调参规则：
- 数学/推理任务：响应长度 2048，lr=5e-7
- 对话任务：响应长度 512，rollout_n=3
- 代码/长文本（SFT）：max_length=4096

---

## 🔧 LlamaFactory 框架

### 数据格式

```json
{"instruction": "请计算 2+2", "input": "", "output": "4"}
```
或
```json
{"conversations": [{"from": "human", "value": "你好"}, {"from": "gpt", "value": "你好！"}]}
```

### 用法示例

```python
training_state = {
    "trainer": {
        "train_framework": "llamafactory",
        "llamafactory_dir": "/path/to/LLaMA-Factory",
        "train_input_dataset_path": "/path/to/data.json",
        "train_input_task_description": "训练对话AI助手",
        "train_input_config_template_path": "loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
        "train_input_model_name": "/path/to/Qwen2.5-1.5B",
    },
    "output_dir": "./output/trainer_test",
}
```

---

## 📊 状态字段

### 通用输入字段

| 字段名 | 类型 | 必需 | 说明 |
|-------|------|-----|-----|
| `train_framework` | str | ✅ | 训练框架: `llamafactory` / `verl` |
| `train_input_dataset_path` | str | ✅ | 训练数据集路径 |
| `train_input_task_description` | str | ✅ | 训练任务描述 |
| `train_input_model_name` | str | ✅ | 基础模型名称/路径 |
| `train_input_config_template_path` | str | ❌ | 配置模板路径（不提供则自动生成） |
| `CUDA_VISIBLE_DEVICES` | str | ❌ | GPU 设备号 |

### verl 专用字段

| 字段名 | 类型 | 必需 | 说明 |
|-------|------|-----|-----|
| `verl_dir` | str | ✅ | verl 项目目录 |
| `verl_env_path` | str | ❌ | verl 虚拟环境路径 |
| `verl_train_mode` | str | ❌ | 训练模式: `grpo`(默认) / `ppo` / `sft` |

### LlamaFactory 专用字段

| 字段名 | 类型 | 必需 | 说明 |
|-------|------|-----|-----|
| `llamafactory_dir` | str | ✅ | LlamaFactory 目录 |
| `llamafactory_env_path` | str | ❌ | LlamaFactory 环境路径 |
| `train_input_use_swanlab` | bool | ❌ | 是否使用 SwanLab (默认 True) |

### 输出字段

| 字段名 | 类型 | 说明 |
|-------|------|-----|
| `train_output_data_check_report_path` | str | 数据检查报告 |
| `train_output_config_path` | str | 生成的配置文件 |
| `train_output_training_log_path` | str | 训练日志 |
| `train_output_training_report_path` | str | 训练报告 |
| `training_checkpoints` | list | checkpoint 目录列表 |
| `training_step_losses` | list | 各 step loss 记录 |
