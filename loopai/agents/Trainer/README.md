# Trainer Agent 使用指南

Trainer Agent 是 Dataflow-LoopAI 框架中负责模型训练的智能代理。它能够自动化完成从数据验证到模型训练的完整流程，并集成 SwanLab 监控功能。

## 🏗️ 架构设计

Trainer Agent 采用三阶段顺序执行架构：

```
数据检查 → 配置生成 → 训练执行
    ↓         ↓         ↓
   结束      结束      结束
```

### 1. 数据检查节点 (Data Check Node)

**功能：** 验证数据集格式是否符合 LlamaFactory 要求

**输入：**
- `train_input_dataset_path`: 数据集文件路径（支持 JSON/JSONL 格式）

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

**功能：** 执行 LlamaFactory 训练并提供 SwanLab 监控

**特性：**
- 自动环境验证（Python、CUDA、依赖包）
- 实时训练日志监控
- SwanLab 集成监控
- 详细的训练报告生成

## 📝 使用方法

### 基本用法

```python
from loopai.agents import TrainerAgent
from loopai.memory import checkpointer, store

# 创建 TrainerAgent 实例
trainer = TrainerAgent(checkpointer=checkpointer, store=store)

# 准备训练状态
training_state = {
    # 必需字段
    'train_input_dataset_path': "/jizhicfs/hymiezhao/lpc/repos/LLaMA-Factory/data/alpaca_en_demo.json",  # 使用 JSON 格式数据集
    'train_input_task_description': '训练一个能够回答简单问题和进行对话的AI助手模型，主要用于日常对话和基础问答任务',
    'train_input_config_template_path': "loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
    'train_input_model_name': '/jizhicfs/hymiezhao/models/Qwen2.5-1.5B',
    'train_output_dir': './output/training_test',

    # 可选字段（如果不提供将使用默认值）
    'train_input_use_swanlab': True,
    'train_input_swanlab_project': 'test_llamafactory_training',
    'training_service_url': 'http://localhost:8000',  # 远程训练服务地址
    'output_dir': './output/trainer_test'
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
| `train_input_use_swanlab` | bool | ❌ | True | 是否使用SwanLab |
| `train_input_swanlab_project` | str | ❌ | llamafactory_training | SwanLab项目名 |
| `output_dir` | str | ❌ | ./output/trainer | Agent输出目录 |

### 输出字段

| 字段名 | 类型 | 说明 |
|-------|------|-----|
`train_output_data_check_report_path` | str | 数据检查报告路径 |
| `train_output_config_path` | str | 生成的配置文件路径 |
| `train_output_training_log_path` | str | 训练日志文件路径 |
| `train_output_swanlab_log_path` | str | Swanlog日志路径 |
|`train_output_training_report_path` | str | 训练报告路径 |
