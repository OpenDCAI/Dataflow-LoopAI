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
    'train_input_dataset_path': './data/my_dataset.json',
    'train_input_task_description': '训练一个能够回答编程问题的AI助手',
    
    # 可选字段
    'train_input_model_name': 'qwen2.5-7b-instruct',
    'train_output_dir': './output/training',
    'train_input_use_swanlab': True,
    'train_input_swanlab_project': 'my_training_project',
    'output_dir': './output/trainer'
}

# 构建并执行图
config = {"configurable": {"thread_id": "my_training"}}
graph = trainer()
result = graph.invoke(training_state, config=config)
```

### 高级配置

```python
# 使用自定义配置模板
training_state = {
    'train_input_dataset_path': './data/dataset.jsonl',
    'train_input_task_description': '训练数学推理模型',
    'train_input_config_template_path': './templates/math_config.json',
    'train_input_model_name': 'qwen2.5-14b-instruct',
    'train_input_use_swanlab': True,
    'train_input_swanlab_project': 'math_reasoning_model'
}
```

## 📊 状态字段说明

### 输入字段

| 字段名 | 类型 | 必需 | 默认值 | 说明 |
|-------|------|-----|--------|-----|
| `train_input_dataset_path` | str | ✅ | - | 训练数据集路径 |
| `train_input_task_description` | str | ✅ | - | 训练任务描述 |
| `train_input_model_name` | str | ❌ | qwen2.5-7b-instruct | 基础模型名称 |
| `train_input_config_template_path` | str | ❌ | - | 配置模板路径 |
| `train_output_dir` | str | ❌ | ./output/training | 训练输出目录 |
| `train_input_use_swanlab` | bool | ❌ | True | 是否使用SwanLab |
| `train_input_swanlab_project` | str | ❌ | llamafactory_training | SwanLab项目名 |
| `output_dir` | str | ❌ | ./output/trainer | Agent输出目录 |

### 输出字段

| 字段名 | 类型 | 说明 |
|-------|------|-----|
| `trainer_data_check_passed` | bool | 数据检查是否通过 |
| `train_output_data_check_report_path` | str | 数据检查报告路径 |
| `trainer_config_generation_success` | bool | 配置生成是否成功 |
| `train_output_config_path` | str | 生成的配置文件路径 |
| `trainer_training_success` | bool | 训练是否成功 |
| `train_output_training_log_path` | str | 训练日志文件路径 |
| `swanlab_url` | str | SwanLab监控链接 |

## 🛠️ 工具类

### DataChecker

```python
from loopai.agents.Trainer.utils import check_data_format

result = check_data_format('./data/dataset.json')
print(f"数据有效: {result['is_valid']}")
print(f"样本数量: {result['total_samples']}")
```

### ConfigGenerator

```python
from loopai.agents.Trainer.utils import ConfigGenerator

generator = ConfigGenerator()
config = generator.generate_config(
    task_description="训练对话模型",
    dataset_path="./data/chat_data.jsonl",
    model_name="qwen2.5-7b"
)
generator.save_config(config, "./config.json")
```

### TrainingExecutor

```python
from loopai.agents.Trainer.utils import TrainingExecutor

executor = TrainingExecutor()
result = executor.execute_training(
    config_path="./config.json",
    output_dir="./output",
    use_swanlab=True
)
```

## 🚨 故障排除

### 常见问题

1. **数据格式错误**
   - 检查 JSON/JSONL 格式是否正确
   - 确认字段名称符合 LlamaFactory 要求
   - 验证数据编码为 UTF-8

2. **环境依赖**
   ```bash
   pip install llamafactory[torch,metrics]
   pip install swanlab
   ```

3. **内存不足**
   - 减少 `per_device_train_batch_size`
   - 增加 `gradient_accumulation_steps`
   - 使用 LoRA 微调而非全参数微调

4. **CUDA 问题**
   - 检查 CUDA 驱动和 PyTorch 兼容性
   - 设置 `fp16: false` 如果遇到精度问题

### 日志分析

训练日志位于 `{output_dir}/training.log`，包含：
- 环境验证信息
- 训练进度详情
- 错误和警告信息
- 性能指标

## 📈 监控功能

### SwanLab 集成

Trainer Agent 自动集成 SwanLab 监控：

- **实时指标：** Loss、学习率、训练进度
- **系统监控：** GPU 使用率、内存占用
- **模型对比：** 不同实验结果对比
- **可视化：** 训练曲线、参数分布

访问 SwanLab 仪表盘查看训练状态：
```
https://swanlab.cn/project/{your_project_name}
```

## 🎯 最佳实践

### 数据准备

1. **数据质量**
   - 确保数据清洁，无重复样本
   - 平衡不同类型的训练样本
   - 适当的数据量（建议1000+样本）

2. **格式标准化**
   - 统一使用一种数据格式
   - 保持字段命名一致
   - 验证特殊字符编码

### 配置优化

1. **任务描述**
   - 详细描述训练目标
   - 包含任务类型关键词
   - 说明期望的模型能力

2. **参数调整**
   - 根据数据规模调整训练轮数
   - 监控训练曲线调整学习率
   - 根据硬件资源设置批次大小

### 训练监控

1. **实时监控**
   - 关注 Loss 下降趋势
   - 监控过拟合迹象
   - 检查资源使用情况

2. **结果评估**
   - 保存检查点进行对比
   - 测试集验证性能
   - 记录最佳配置参数

## 📚 扩展开发

### 自定义配置模板

创建 JSON 配置模板：

```json
{
  "model_name": "custom-model",
  "learning_rate": 1e-4,
  "num_train_epochs": 2,
  "custom_field": "custom_value"
}
```

### 添加新的数据格式支持

在 `data_checker.py` 中扩展 `_validate_llamafactory_format` 函数。

### 集成其他监控工具

在 `training_executor.py` 中添加新的监控后端支持。

---

💡 **提示：** 查看 `examples/scripts/run_trainer.py` 了解完整的使用示例。
