# Trainer Agent 详细指南

`TrainerAgent` 是 LoopAI 闭环里负责"模型更新"的节点。它把前面 Analyzer / Obtainer / Constructor 准备好的训练数据，真正转换为一次完整的微调任务，并把训练日志、checkpoint、SwanLab 监控数据写回到 state，供下一轮 Judger 使用。

当前主路径基于 [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) 做 SFT；`verl`（RL 训练）已在代码中预留分支但尚未作为推荐主线，本指南以 LlamaFactory 为主进行说明。

## 在闭环中的位置

```text
Judger ──▶ Analyzer ──▶ Obtainer / WebCrawler ──▶ Constructor ──▶ Trainer ──▶ Judger（下一轮）
```

Trainer 的输入通常是：

- 上游 Constructor / Obtainer 的 `mapping_results.output_file`（自动注入）
  或显式给定的 `train_input_dataset_path`
- 一份训练任务描述（`train_input_task_description`）
- 一份 LlamaFactory 训练配置模板（`train_input_config_template_path`）
- 基础模型路径（`train_input_model_name`）

输出通常是：

- 校验后的数据检查报告
- 自动生成的 YAML 训练配置 + 配置说明文本
- 训练日志、训练报告、`trainer_log.jsonl` 解析得到的 step-loss
- SwanLab 本地日志路径
- 训练产生的所有 `checkpoint-*` 目录列表

## 三阶段执行流程

`TrainerAgent` 内部由三个顺序节点构成，任何一步失败都会直接进入 `end`：

```text
check_required_fields
        │
        ▼
   data_check ──失败──▶ end
        │ 通过
        ▼
config_generation ──失败──▶ end
        │ 成功
        ▼
training_execution ──▶ end
```

### 0. 前置字段检查（check_required_fields）

进入正式流程前，Agent 会先校验 `state.trainer` 中的必填字段。**只要任意必填字段缺失，Trainer 不会自己执行训练**，而是把控制权交给 `ConfigerAgent` 子图，让它从对话里把字段补全。

LlamaFactory 模式下必须存在的字段为：

- `train_framework`（当前固定为 `"llamafactory"`）
- `train_input_dataset_path`
- `train_input_task_description`
- `train_input_config_template_path`
- `train_input_model_name`
- `llamafactory_dir`

特别说明：如果 `train_input_dataset_path` 没有显式给出，Trainer 会先尝试自动从 `state.obtainer.mapping_results.output_file` 或 `state.constructor.mapping_results.output_file` 中读取上游产物。这也是 LoopAI 闭环里 Obtainer / Constructor → Trainer 的衔接方式。

### 1. 数据检查节点（data_check）

**做的事情**：用 `loopai/agents/Trainer/utils/data_checker.py` 验证数据集是否符合 LlamaFactory 的两类支持格式。

**支持的格式**：

1. Alpaca / Instruction 格式（推荐用于 SFT）：

```json
{
  "instruction": "请计算 2 + 2 的结果",
  "input": "",
  "output": "2 + 2 = 4"
}
```

2. 多轮对话格式（ShareGPT 风格）：

```json
{
  "conversations": [
    {"from": "human", "value": "你好"},
    {"from": "gpt",   "value": "你好！我是 AI 助手"}
  ]
}
```

`from` 仅接受 `human` / `gpt` / `system` 三种值。

**输入要求**：

- 文件后缀必须是 `.json` 或 `.jsonl`
- `.json` 必须是 list，不能是单个 dict
- `.jsonl` 每行必须是合法 JSON

**产出**：

- `train_output_data_check_report_path`：人类可读的数据检查报告
- `trainer_data_check_passed`：是否通过校验，决定是否进入下一步

### 2. 配置生成节点（config_generation）

**做的事情**：以 `train_input_config_template_path` 指向的 YAML 模板为基础，根据任务描述自适应调整关键参数，最终生成一份 LlamaFactory 可直接消费的 YAML 配置。

**两种生成模式**：

- **规则模式（默认）**：根据 `train_input_task_description` 中的关键词调整参数。
  - 含「数学/推理/复杂/困难」→ `learning_rate=1e-5`
  - 含「对话/聊天/简单」→ `learning_rate=5e-5`
  - 含「微调/适应/few-shot」→ `num_train_epochs=1.0`
  - 含「从头/完整/全面」→ `num_train_epochs=5.0`
  - LoRA 任务下含「代码/编程/code」→ `lora_r=16, lora_alpha=32, lora_target=all`
  - LoRA 任务下含「对话/聊天/chat」→ `lora_r=8, lora_alpha=16, lora_target=q_proj,v_proj`
- **LLM 辅助模式**：当 `ConfigGenerator` 初始化时传入了 `model_path / base_url / api_key`，会调用 LLM 直接产出 JSON 配置补丁，并落到 `_validate_llm_config` 中做范围校验（学习率 1e-6 ~ 1e-3、batch_size 1-16、`lora_r` 1-64 等）。

**产出**：

- `train_output_config_path`：最终 YAML 配置文件路径
- `trainer_config_explanation_path`：人类可读的配置说明
- `train_config`：内存中的完整配置 dict

### 3. 训练执行节点（training_execution）

**做的事情**：本地直接通过 `TaskManager` 拉起训练子进程，不再依赖远程 API。

主要步骤：

1. 把上游确认的数据集注册到 `{llamafactory_dir}/data/dataset_info.json`（自动以文件名 stem 为数据集名）。
2. 把生成的 YAML 配置拷贝到 `{output_dir}/configs/{trainer_task_id}.yaml`。
3. 通过 `TaskManager.start_training` 启动子进程，每 30 秒轮询一次任务状态，最多等待 1 小时。
4. 训练过程中实时解析 LlamaFactory 的训练日志，把当前 step、总 step、训练时间写回 `state` 与 SSE 流。
5. 训练结束后扫描 `output_dir`，汇总 `checkpoint-*` 目录、解析 `trainer_log.jsonl` 的 step-loss、获取 SwanLab 本地日志路径，并生成训练报告。

## 输入字段表（state.trainer）

> 字段定义来源：`loopai/schema/states.py` 的 `TrainerState`。

### 必填字段（LlamaFactory 模式）

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `train_framework` | str | 训练框架，目前 UI 仅暴露 `llamafactory` |
| `llamafactory_dir` | str | LlamaFactory 仓库根目录，用于注册数据集 |
| `train_input_dataset_path` | str | 训练数据集路径（`.json` / `.jsonl`）。若未提供，会尝试用 `obtainer/constructor.mapping_results.output_file` |
| `train_input_task_description` | str | 任务描述，决定规则模式下学习率 / epoch / LoRA 参数 |
| `train_input_config_template_path` | str | YAML 模板路径，参考 `loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml` |
| `train_input_model_name` | str | 基础模型名称或本地路径，会写入 `model_name_or_path` |

### 可选字段

| 字段名 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `output_dir` | str | `./output/trainer` | Trainer 自身的工作目录，最终路径为 `{global_output_dir}/{global_task_id}/trainer/{trainer_task_id}` |
| `train_input_use_swanlab` | bool | `True` | 是否启用 SwanLab 监控，对应 `report_to: swanlab` |
| `train_input_swanlab_project` | str | `llamafactory_training` | SwanLab 项目名 |
| `swanlab_api_key` | str | 取自 `state.system` | SwanLab 鉴权 key |
| `llamafactory_env_path` | str | 取自 `state.system` | LlamaFactory 所在 Conda / venv 路径 |
| `CUDA_VISIBLE_DEVICES` | str | `0,1` | 训练进程可见 GPU |

> 这些可选字段如果在 `state.trainer` 没有，会从 `state.system`（即 `starter.yaml` 中的全局配置）读取。

## 输出字段表

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `trainer_task_id` | str | Trainer 内部生成的子任务 ID |
| `trainer_data_check_passed` | bool | 数据检查是否通过 |
| `train_output_data_check_report_path` | str | 数据检查报告路径 |
| `trainer_config_generation_success` | bool | 配置生成是否成功 |
| `train_output_config_path` | str | 最终 YAML 配置路径 |
| `trainer_config_explanation_path` | str | 配置说明文本路径 |
| `trainer_training_success` | bool | 训练是否成功 |
| `trainer_training_task_id` | str | 训练子进程 ID（本地 TaskManager 维护） |
| `trainer_training_execution_time` | float | 训练耗时（秒） |
| `trainer_training_final_status` | dict | `{status, created_at, started_at, completed_at, error_message}` |
| `train_output_training_log_path` | str | 训练日志保存路径 |
| `train_output_training_report_path` | str | 训练报告路径 |
| `train_output_swanlab_log_path` | str | SwanLab 本地日志路径 |
| `training_checkpoints` | List[str] | 产出的 checkpoint 目录名 |
| `training_step_losses` | List[Dict] | 从 `trainer_log.jsonl` 解析的 step-loss |

## 不同任务模式下要重点填什么

### 模式 A：通用对话 / 问答 SFT（默认推荐）

- `train_framework`: `llamafactory`
- `train_input_task_description` 包含「对话 / 聊天 / chat」字样
- 模板：`templates/qwen2_5_coder_bird_full_sft.yaml`（`finetuning_type: full`），如要走 LoRA 自行将 `finetuning_type` 改为 `lora`
- 数据格式：建议 ShareGPT `conversations` 或 Alpaca

> 规则模式会自动给出 `lora_r=8, lora_alpha=16, lora_target=q_proj,v_proj`、`learning_rate=5e-5`。

### 模式 B：代码 / 编程类 SFT

- `train_input_task_description` 包含「代码 / 编程 / code」
- 数据格式：Alpaca 形式，`output` 为代码片段
- 规则模式自动给出 `lora_r=16, lora_alpha=32, lora_target=all`，更靠近代码任务的实际经验值

### 模式 C：数学 / 推理 SFT

- `train_input_task_description` 包含「数学 / 推理 / 复杂 / 困难」
- 学习率会被压低到 `1e-5`，避免推理类任务的训练发散
- 建议显式提高 `cutoff_len`（在模板里直接改），推理样本通常较长

### 模式 D：从上游 mapping_results 接力（闭环模式）

如果 Trainer 是被 `StarterAgent` 串到 Constructor / Obtainer 之后调用的，可以**省略 `train_input_dataset_path`**：

- Trainer 会按优先级 `obtainer.mapping_results.output_file` → `constructor.mapping_results.output_file` 自动取数据
- 但其余必填字段（`train_framework / llamafactory_dir / train_input_task_description / train_input_config_template_path / train_input_model_name`）**仍然不可缺**

## 最小可用示例

```python
from loopai.agents import TrainerAgent
from loopai.memory import checkpointer, store

trainer = TrainerAgent(checkpointer=checkpointer, store=store)
graph = trainer()

state = {
    "trainer": {
        # 必填
        "train_framework": "llamafactory",
        "llamafactory_dir": "/path/to/LLaMA-Factory",
        "train_input_dataset_path": "/path/to/LLaMA-Factory/data/alpaca_en_demo.json",
        "train_input_task_description": "训练一个能够回答简单问题和进行对话的 AI 助手",
        "train_input_config_template_path":
            "loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
        "train_input_model_name": "/path/to/Qwen2.5-1.5B",

        # 可选
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

## WebUI / 资源池里的填法建议

在 WebUI 里使用 Trainer 时，建议：

1. 先在 `Config` 面板把 `system` 级别的 `llamafactory_dir / llamafactory_env_path / CUDA_VISIBLE_DEVICES / swanlab_api_key` 填好，避免每个任务重复输入。
2. 在「资源池管理」里维护好以下三类路径，再在任务面板里下拉选用：
   - 训练数据集（`train_input_dataset_path`）
   - 配置模板（`train_input_config_template_path`）
   - 基础模型路径（`train_input_model_name`）
3. 通过对话给出 `train_input_task_description`，让规则模式或 LLM 模式自动决定关键超参。

## 环境与依赖

- LlamaFactory 主仓库已可正常运行 `llamafactory-cli train`
- `llamafactory_env_path` 指向的 Python 环境里安装了 LlamaFactory + 训练所需的 deepspeed / transformers
- 启用 SwanLab 时需要 `pip install swanlab` 并设置 `swanlab_api_key`
- 多卡训练通过 `CUDA_VISIBLE_DEVICES` 控制，例如 `"0,1,2,3"`

## 常见坑位

- **数据检查未通过**：`.json` 顶层必须是 list；`.jsonl` 每行都要是合法 JSON；`conversations[*].from` 必须是 `human/gpt/system` 之一。
- **配置生成失败**：通常是模板路径不存在或不是合法 YAML，落地前可以先用 `yaml.safe_load` 自查一次。
- **训练子进程失败**：优先看 `train_output_training_log_path`，里面是 LlamaFactory 子进程原始日志。常见原因：模型路径错、`llamafactory_dir` 不存在、CUDA OOM、未在 `dataset_info.json` 中注册数据集。
- **超时（默认 1 小时）**：当前 `training_execution_node` 里写死了 `max_wait_time = 3600`，长任务可在自定义场景里覆盖该节点或修改源码。
- **SwanLab 日志路径为空**：检查模板里 `report_to` 是否被覆盖成 `none`，以及 `swanlab_api_key` 是否生效。

## 进阶：直接复用配置生成器

如果只想用 LoopAI 的"任务描述 → LlamaFactory YAML"能力，而不跑训练，可以单独使用 `ConfigGenerator`：

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

- 训练前：必填字段是否齐全、数据检查是否通过
- 训练中：日志是否在持续更新、SwanLab 是否能看到 loss 曲线
- 训练后：`training_checkpoints` 是否非空、`train_output_training_report_path` 是否成功生成、新 checkpoint 是否能被下一轮 Judger 加载
