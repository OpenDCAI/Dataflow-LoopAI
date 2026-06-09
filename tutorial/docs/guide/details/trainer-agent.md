# Trainer Agent 详细指南

`TrainerAgent` 是 LoopAI 闭环里负责模型更新的节点。它会把前面 `Analyzer`、`Obtainer`、`Constructor` 准备好的训练数据，转化为一次完整的微调任务，并将训练日志、checkpoint、SwanLab 监控数据等写回 `state`，供下一轮 `Judger` 使用。

当前主路径基于 [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) 执行 SFT。虽然代码中为其他训练路径预留了扩展分支，但当前文档仍以 LLaMA-Factory 为主进行说明。

## 在闭环中的位置

```text
Judger -> Analyzer -> Obtainer / WebCrawler -> Constructor -> Trainer -> Judger（下一轮）
```

Trainer 的输入通常包括：

- 上游 `Constructor` 或 `Obtainer` 的 `mapping_results.output_file`，或显式给定的 `train_input_dataset_path`
- 一份训练任务描述：`train_input_task_description`
- 一份 LLaMA-Factory 训练配置模板：`train_input_config_template_path`
- 一个基础模型路径：`train_input_model_name`

Trainer 的输出通常包括：

- 数据检查报告
- 自动生成的 YAML 训练配置和配置说明文本
- 训练日志、训练报告、`trainer_log.jsonl` 解析出的 step-loss
- SwanLab 本地日志路径
- 训练产生的 `checkpoint-*` 目录列表

## 执行流程

`TrainerAgent` 内部主要由三个顺序节点组成，任一步失败都会直接结束当前训练流程：

```text
check_required_fields
        |
        v
   data_check ------失败------> end
        | 通过
        v
config_generation --失败-----> end
        | 成功
        v
training_execution ---------> end
```

### 0. 前置字段检查：`check_required_fields`

进入正式流程前，Trainer 会先校验 `state.trainer` 中的必填字段。

只要任意必填字段缺失，Trainer 不会直接开始训练，而是把控制权交给 `ConfigerAgent` 子图，由它尝试从对话中补全配置。

LLaMA-Factory 模式下必填字段包括：

- `train_framework`，当前固定为 `llamafactory`
- `train_input_dataset_path`
- `train_input_task_description`
- `train_input_config_template_path`
- `train_input_model_name`
- `llamafactory_dir`

特别说明：

如果没有显式提供 `train_input_dataset_path`，Trainer 会优先尝试从 `state.obtainer.mapping_results.output_file` 或 `state.constructor.mapping_results.output_file` 中读取上游产物。

### 1. 数据检查节点：`data_check`

该节点会使用 `loopai/agents/Trainer/utils/data_checker.py` 验证数据集是否符合 LLaMA-Factory 支持的格式。

支持的主要格式包括：

1. Alpaca / Instruction 格式，推荐用于 SFT

```json
{
  "instruction": "请计算 2 + 2 的结果",
  "input": "",
  "output": "2 + 2 = 4"
}
```

2. 多轮对话格式（ShareGPT 风格）

```json
{
  "conversations": [
    {"from": "human", "value": "你好"},
    {"from": "gpt", "value": "你好！我是 AI 助手"}
  ]
}
```

其中 `from` 仅支持 `human`、`gpt`、`system` 三种取值。

输入要求包括：

- 文件后缀必须是 `.json` 或 `.jsonl`
- `.json` 顶层必须是 `list`，不能是单个 `dict`
- `.jsonl` 的每一行都必须是合法 JSON

输出包括：

- `train_output_data_check_report_path`：人类可读的数据检查报告
- `trainer_data_check_passed`：是否通过校验

### 2. 配置生成节点：`config_generation`

该节点会以 `train_input_config_template_path` 指向的 YAML 模板为基础，根据任务描述自适应调整关键参数，最终生成一份可供 LLaMA-Factory 直接使用的 YAML 配置。

当前主要有两种生成模式：

- 规则模式（默认）
- LLM 辅助模式

规则模式会根据 `train_input_task_description` 中的关键词调整参数。例如：

- 包含“数学 / 推理 / 复杂 / 困难”时，倾向使用 `learning_rate=1e-5`
- 包含“对话 / 聊天 / 简单”时，倾向使用 `learning_rate=5e-5`
- 包含“微调 / 适应 / few-shot”时，倾向使用 `num_train_epochs=1.0`
- 包含“从头 / 完整 / 全面”时，倾向使用 `num_train_epochs=5.0`
- LoRA 任务中包含“代码 / 编程 / code”时，倾向使用 `lora_r=16, lora_alpha=32, lora_target=all`
- LoRA 任务中包含“对话 / 聊天 / chat”时，倾向使用 `lora_r=8, lora_alpha=16, lora_target=q_proj,v_proj`

LLM 辅助模式下，如果 `ConfigGenerator` 初始化时提供了 `model_path`、`base_url`、`api_key`，则会调用 LLM 直接产出 JSON 配置补丁，并经过 `_validate_llm_config` 进行范围校验，例如：

- 学习率范围：`1e-6 ~ 1e-3`
- `batch_size` 范围：`1 ~ 16`
- `lora_r` 范围：`1 ~ 64`

输出包括：

- `train_output_config_path`：最终 YAML 配置文件路径
- `trainer_config_explanation_path`：人类可读的配置说明
- `train_config`：内存中的完整配置字典

### 3. 训练执行节点：`training_execution`

该节点通过 `TaskManager` 启动本地训练子进程，不依赖远程训练 API。

主要步骤如下：

1. 将确认后的数据集注册到 `{llamafactory_dir}/data/dataset_info.json`
2. 将生成的 YAML 配置复制到 `{output_dir}/configs/{trainer_task_id}.yaml`
3. 通过 `TaskManager.start_training` 启动训练子进程，并每 30 秒轮询一次任务状态，最长等待 1 小时
4. 训练过程中实时解析 LLaMA-Factory 的日志，将 step、总 step、训练时间等信息写回 `state` 和 SSE 流
5. 训练结束后扫描 `output_dir`，汇总 `checkpoint-*` 目录、解析 `trainer_log.jsonl` 中的 step-loss、获取 SwanLab 本地日志路径，并生成训练报告

## 输入字段表：`state.trainer`

> 字段定义来源：`loopai/schema/states.py` 中的 `TrainerState`

### 必填字段（LLaMA-Factory 模式）

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `train_framework` | `str` | 训练框架。目前 UI 只暴露 `llamafactory`。 |
| `llamafactory_dir` | `str` | LLaMA-Factory 仓库根目录，用于注册数据集。 |
| `train_input_dataset_path` | `str` | 训练数据集路径，支持 `.json` / `.jsonl`。如果未提供，会尝试使用 `obtainer/constructor.mapping_results.output_file`。 |
| `train_input_task_description` | `str` | 任务描述，用于决定规则模式下的学习率、epoch、LoRA 参数等。 |
| `train_input_config_template_path` | `str` | YAML 模板路径，例如 `loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml`。 |
| `train_input_model_name` | `str` | 基础模型名称或本地路径，最终写入 `model_name_or_path`。 |

### 可选字段

| 字段名 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `output_dir` | `str` | `./output/trainer` | Trainer 工作目录。最终路径通常为 `{global_output_dir}/{global_task_id}/trainer/{trainer_task_id}`。 |
| `train_input_use_swanlab` | `bool` | `True` | 是否启用 SwanLab 监控，对应 `report_to: swanlab`。 |
| `train_input_swanlab_project` | `str` | `llamafactory_training` | SwanLab 项目名。 |
| `swanlab_api_key` | `str` | 来自 `state.system` | SwanLab 鉴权 key。 |
| `llamafactory_env_path` | `str` | 来自 `state.system` | LLaMA-Factory 所在 Conda / venv 路径。 |
| `CUDA_VISIBLE_DEVICES` | `str` | `0,1` | 训练进程可见 GPU。 |

如果这些可选字段没有在 `state.trainer` 中提供，Trainer 会尝试从 `state.system`，也就是 `starter.yaml` 的全局配置中读取。

## 输出字段表

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `trainer_task_id` | `str` | Trainer 内部生成的子任务 ID。 |
| `trainer_data_check_passed` | `bool` | 数据检查是否通过。 |
| `train_output_data_check_report_path` | `str` | 数据检查报告路径。 |
| `trainer_config_generation_success` | `bool` | 配置生成是否成功。 |
| `train_output_config_path` | `str` | 最终 YAML 配置路径。 |
| `trainer_config_explanation_path` | `str` | 配置说明文本路径。 |
| `trainer_training_success` | `bool` | 训练是否成功。 |
| `trainer_training_task_id` | `str` | 训练子进程 ID，由本地 `TaskManager` 维护。 |
| `trainer_training_execution_time` | `float` | 训练耗时，单位为秒。 |
| `trainer_training_final_status` | `dict` | 形如 `{status, created_at, started_at, completed_at, error_message}` 的最终状态字典。 |
| `train_output_training_log_path` | `str` | 训练日志保存路径。 |
| `train_output_training_report_path` | `str` | 训练报告路径。 |
| `train_output_swanlab_log_path` | `str` | SwanLab 本地日志路径。 |
| `training_checkpoints` | `List[str]` | 产出的 checkpoint 目录列表。 |
| `training_step_losses` | `List[Dict]` | 从 `trainer_log.jsonl` 解析出的 step-loss。 |

## 不同任务模式下重点填写什么

### 模式 A：通用对话 / 问答 SFT

- `train_framework` 设为 `llamafactory`
- `train_input_task_description` 中包含“对话 / 聊天 / chat”等关键词
- 模板通常使用 `templates/qwen2_5_coder_bird_full_sft.yaml`
- 如果要使用 LoRA，可将 `finetuning_type` 从 `full` 改为 `lora`
- 数据格式建议使用 ShareGPT `conversations` 或 Alpaca

规则模式下，通常会自动给出：

- `lora_r=8`
- `lora_alpha=16`
- `lora_target=q_proj,v_proj`
- `learning_rate=5e-5`

### 模式 B：代码 / 编程类 SFT

- `train_input_task_description` 中包含“代码 / 编程 / code”等关键词
- 数据格式通常为 Alpaca，`output` 中写入代码片段

规则模式下，通常会自动给出：

- `lora_r=16`
- `lora_alpha=32`
- `lora_target=all`

### 模式 C：数学 / 推理 SFT

- `train_input_task_description` 中包含“数学 / 推理 / 复杂 / 困难”等关键词
- 学习率通常会被压低到 `1e-5`
- 如果样本较长，建议在模板中显式提高 `cutoff_len`

### 模式 D：从上游 `mapping_results` 接力

如果 Trainer 是在 `Constructor` 或 `Obtainer` 之后被串联调用的，可以省略 `train_input_dataset_path`。

Trainer 会按优先级自动尝试：

- `obtainer.mapping_results.output_file`
- `constructor.mapping_results.output_file`

但以下字段仍然必须提供：

- `train_framework`
- `llamafactory_dir`
- `train_input_task_description`
- `train_input_config_template_path`
- `train_input_model_name`

## 最小可用示例

```python
from loopai.agents import TrainerAgent
from loopai.memory import checkpointer, store

trainer = TrainerAgent(checkpointer=checkpointer, store=store)
graph = trainer()

state = {
    "trainer": {
        "train_framework": "llamafactory",
        "llamafactory_dir": "/path/to/LLaMA-Factory",
        "train_input_dataset_path": "/path/to/LLaMA-Factory/data/alpaca_en_demo.json",
        "train_input_task_description": "训练一个能够回答简单问题并进行对话的 AI 助手",
        "train_input_config_template_path":
            "loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
        "train_input_model_name": "/path/to/Qwen2.5-1.5B",
        "output_dir": "./output/trainer_demo",
        "train_input_use_swanlab": True,
        "train_input_swanlab_project": "demo_llamafactory_training",
    }
}

config = {"configurable": {"thread_id": "trainer_demo"}}
result = graph.invoke(state, config=config)

summary = trainer.get_training_summary(result)
print(summary["final_status"], summary["output_files"])
```

完整可运行脚本见 `examples/scripts/run_trainer.py`。

## WebUI / 资源池中的填写建议

在 WebUI 中使用 Trainer 时，通常建议：

1. 先在 `Config` 面板中配置 `system` 级别的 `llamafactory_dir`、`llamafactory_env_path`、`CUDA_VISIBLE_DEVICES`、`swanlab_api_key`
2. 在资源池中维护好以下三类路径，再在任务面板中下拉选用
3. 通过对话提供 `train_input_task_description`，让规则模式或 LLM 模式自动决定关键超参

资源池中建议维护的三类路径包括：

- 训练数据集：`train_input_dataset_path`
- 配置模板：`train_input_config_template_path`
- 基础模型路径：`train_input_model_name`

## 环境与依赖

- LLaMA-Factory 主仓库需要能正常运行 `llamafactory-cli train`
- `llamafactory_env_path` 指向的 Python 环境需要安装 LLaMA-Factory 及其训练依赖，如 `deepspeed`、`transformers`
- 启用 SwanLab 时，需要安装 `swanlab` 并配置 `swanlab_api_key`
- 多卡训练通过 `CUDA_VISIBLE_DEVICES` 控制，例如 `"0,1,2,3"`

## 常见问题

- 数据检查未通过：`.json` 顶层必须是 `list`，`.jsonl` 每行必须是合法 JSON，`conversations[*].from` 必须是 `human/gpt/system` 之一
- 配置生成失败：通常是模板路径不存在，或 YAML 本身不合法，落地前可以先用 `yaml.safe_load` 自查
- 训练子进程失败：优先查看 `train_output_training_log_path`，常见原因包括模型路径错误、`llamafactory_dir` 不存在、CUDA OOM、未在 `dataset_info.json` 中注册数据集
- 超时：当前 `training_execution_node` 中默认 `max_wait_time = 3600`，长任务需要在自定义场景中覆盖该节点或修改源码
- SwanLab 日志路径为空：检查模板中的 `report_to` 是否被覆盖成 `none`，以及 `swanlab_api_key` 是否生效

## 进阶：单独复用配置生成能力

如果只想使用 LoopAI 中“任务描述 -> LLaMA-Factory YAML”这部分能力，而不直接执行训练，可以单独使用 `ConfigGenerator`：

```python
from loopai.agents.Trainer.utils.config_generator import ConfigGenerator

gen = ConfigGenerator()
config = gen.generate_config(
    task_description="训练一个 SQL 代码生成模型，难度较高",
    dataset_path="/path/to/sql_train.json",
    model_name="/path/to/Qwen2.5-Coder-7B",
    output_dir="./output/sql_sft",
    template_path="loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
    use_swanlab=True,
    swanlab_project="sql_sft",
)
gen.save_config_as_yaml(config, "./output/sql_sft/training_config.yaml")
```

## 使用时最该关注什么

- 训练前：必填字段是否齐全，数据检查是否通过
- 训练中：日志是否持续更新，SwanLab 是否能看到 loss 曲线
- 训练后：`training_checkpoints` 是否非空，`train_output_training_report_path` 是否生成成功，新 checkpoint 是否能被下一轮 `Judger` 加载
