# Trainer node 详细指南

Trainer node 是 LoopAI 闭环里负责模型更新的节点。它会把 ObtainerCLI/DataMixer 导出的最终训练数据转化为一次完整的微调任务，并将本地训练指标、checkpoint 等写回 `state`，供下一轮 `Judger` 使用。

当前支持两条严格配对的训练路径：使用 [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) 执行 SFT，以及使用 Verl 执行 GRPO。两条路径共享配置审批、版本化状态、持久化 Worker 和本地指标接口，但数据格式、训练 YAML、checkpoint 形式与结果选择规则分别处理。

## 在闭环中的位置

```text
Judger -> Analyzer -> ObtainerCLI/DataMixer -> Trainer -> Judger（下一轮）
```

Trainer 的输入通常包括：

- 上游 `Obtainer` / `Constructor` 的 `mapping_results.output_file`，或显式给定的数据路径
- 一份训练任务描述：`train_input_task_description`
- 一份对应后端的训练配置模板：`train_input_config_template_path`；留空时自动选择内置模板
- 一个基础模型路径：`train_input_model_name`
- SFT 使用 `llamafactory_dir`，GRPO 使用 `verl_dir`

Trainer 的输出通常包括：

- 数据检查报告
- 自动生成的 YAML 训练配置和配置说明文本
- 训练日志、训练报告和规范化指标
- `metrics/metrics.json` 等本地指标文件
- SFT 的 `checkpoint-*` 或 GRPO 的 `global_step_*` checkpoint，以及选出的最佳模型路径

## 执行流程

Trainer node 内部仍由三个顺序节点组成。推荐通过公开入口先准备并展示配置，再执行获批版本：

```text
prepare(): resolve runtime config / check_required_fields
        |
        v
   data_check ------失败------> end
        | 通过
        v
config_generation --失败-----> end
        | 成功
        v
prepare() 返回 YAML + SHA256
        |
        v 用户确认
run_prepared(): 校验摘要并回填配置
        |
        v
check_required_fields
        |
        v
   data_check ------失败------> end
        |
        v
training_execution ---------> end
```

`run_prepared()` 必须原样传回 `prepare()` 返回的 `trainer_version_id`、`config_path` 和 `config_sha256`。不要直接改动已准备的 YAML；需要调整时先修改模板或输入参数，再重新 `prepare()` 并审批新版本。

SHA256 锁定的是 YAML 内容，不是数据文件快照。SFT 在 `prepare()` 到 `run_prepared()` 之间不应更换上游 mapping 或修改数据文件；数据有变化时也应重新准备和审批。

### 0. 前置字段检查：`check_required_fields`

公开 API/CLI 会先合并调用参数、环境变量、当前 state、持久化配置和 `state.system`，再校验后端与阶段。默认使用 `llamafactory+sft`；也支持 `verl+grpo`，其他交叉组合会被拒绝。

共同需要的核心字段包括：

- `train_framework` 和 `train_stage`
- `train_input_dataset_path`，GRPO 也可以提供 `verl_source_dataset_path`
- `train_input_task_description`
- `train_input_config_template_path`
- `train_input_model_name`
- SFT 的 `llamafactory_dir`，或 GRPO 的 `verl_dir`
- custom reward 模式下的 `verl_reward_function_path`

特别说明：

模板未填写时会根据后端自动补齐。完整 LoopAI 父图中的基础字段缺失时可以路由到 `Configer node`；独立调用 `Trainer.prepare()` 或 CLI 时则会记录 `trainer_missing_fields` 和 `trainer_prefill_guide`，并抛出结构化配置错误，不会直接开始训练。

### 1. 数据检查节点：`data_check`

该节点根据 `train_framework` 进入 SFT 或 GRPO 分支。

#### LLaMA-Factory SFT

SFT 分支会使用 `loopai/skills/Trainer/utils/data_checker.py` 验证数据集格式。节点内部的数据来源优先级为：存在的 Obtainer 输出、存在的 Constructor 输出、`trainer.train_input_dataset_path`。

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

其中 `from` 仅支持 `human`、`gpt`、`system` 三种取值。这里表示数据检查支持 ShareGPT；自动注册 `dataset_info.json` 时仍需确认 LLaMA-Factory 的 formatting/columns 配置符合实际数据。

输入要求包括：

- 文件后缀必须是 `.json` 或 `.jsonl`
- `.json` 顶层必须是 `list`，不能是单个 `dict`
- `.jsonl` 的每一行都必须是合法 JSON

输出包括：

- `train_output_data_check_report_path`：人类可读的数据检查报告
- `trainer_data_check_passed`：是否通过校验
- `trainer_data_check_result` / `trainer_data_check_error`：结构化检查结果或失败原因

#### Verl GRPO

GRPO 分支可以原地复用完整的原生 Verl 训练/验证 Parquet 对（包含 `prompt`、`data_source`、`reward_model`），也可以把 JSON、JSONL、Parquet 文件或目录转换成本轮数据。通过 `prepare(..., verl_source_dataset_path=...)` 或环境变量传入的本轮覆盖值优先；否则依次读取本轮 Constructor、Obtainer 和 state 中已保存的数据来源。

常用数据参数如下：

| 字段名 | 默认值 | 说明 |
| --- | --- | --- |
| `train_input_eval_dataset_path` / `verl_source_eval_dataset_path` | 空 | 已准备好的原生验证 Parquet，或待适配的验证数据。 |
| `verl_data_adapter` | `auto` | `auto`、`native`、`messages`、`alpaca` 或 `qa`。 |
| `verl_validation_ratio` / `verl_split_seed` | `0.05` / `42` | 未提供验证集时的确定性切分参数。 |
| `verl_reuse_previous_validation` | `true` | reward 合约兼容时复用上一轮验证集。 |
| `verl_reward_mode` / `verl_reward_preset` | `auto` / `auto` | 可选 auto/preset/custom；custom 必须提供 Python 文件。 |

非原生数据会自动适配字段、拒绝坏样本、去重，并在没有验证集时按 ratio/seed 切分；产物及统计写入 `prepared_data`、`rejected_rows.jsonl` 和 `dataset_manifest.json`。答案只从受支持字段或最后一条 assistant 回复中提取；`auto` 无法可靠判断 reward preset/语义时会停止并要求显式选择 preset/custom。

### 2. 配置生成节点：`config_generation`

该节点根据训练后端生成最终 YAML；模板未填写时会自动选择 SFT 或 GRPO 内置模板。

#### LLaMA-Factory SFT

Trainer 节点当前使用确定性的规则模式，根据 `train_input_task_description` 中的关键词调整参数。例如：

- 包含“数学 / 推理 / 复杂 / 困难”时，倾向使用 `learning_rate=1e-5`
- 包含“对话 / 聊天 / 简单”时，倾向使用 `learning_rate=5e-5`
- 包含“微调 / 适应 / few-shot”时，倾向使用 `num_train_epochs=1.0`
- 包含“从头 / 完整 / 全面”时，倾向使用 `num_train_epochs=5.0`
- LoRA 任务中包含“代码 / 编程 / code”时，倾向使用 `lora_r=16, lora_alpha=32, lora_target=all`
- LoRA 任务中包含“对话 / 聊天 / chat”时，倾向使用 `lora_r=8, lora_alpha=16, lora_target=q_proj,v_proj`

节点还会把本轮模型、数据文件和输出目录写入 `model_name_or_path`、`dataset`、`output_dir`，并关闭外部 tracker。模板中的 Deepspeed 路径无效时，会尝试在 `{llamafactory_dir}/examples/deepspeed/` 中按文件名修复，仍找不到则移除该配置。LLM 辅助生成仅属于文末单独调用 `ConfigGenerator` 的进阶能力，不是 Trainer 节点的默认路径。

#### Verl GRPO

GRPO 会校验模板中的 `framework: verl` 和 `stage: grpo`，并自动刷新本轮数据、模型、reward、GPU、rollout 后端、checkpoint 目录和结果选择字段。主要参数包括：

| 字段名 | 默认值与限制 |
| --- | --- |
| `verl_env_path` | `verl`。 |
| `verl_algorithm` / `verl_entrypoint` | `grpo`（当前唯一值）/ `verl.trainer.main_ppo`。 |
| `verl_rollout_backend` / `verl_model_backend` | `vllm`（也支持 `sglang`）/ `fsdp`（当前唯一值）。 |
| `verl_selection_metric` / `verl_selection_mode` / `verl_max_actor_ckpt_to_keep` | `val-core/*/acc/mean@*` / `max` / `10`。 |
| `verl_inherit_previous_config` / `verl_use_previous_best_model` / `verl_multi_round_enabled` | 默认均为 `true`。 |

多轮继承不会直接沿用旧路径：Trainer 会重新写入本轮数据、模型、reward 和运行目录。上一轮验证集仅在 reward 合约兼容时复用；上一轮模型还必须训练成功、没有导出错误并具有可加载的 Hugging Face 配置和权重。

输出包括：

- `train_output_config_path`：最终 YAML 配置文件路径
- `trainer_config_explanation_path`：人类可读的配置说明
- `train_config`：内存中的完整配置字典
- `prepare()` 还会返回 `trainer_version_id`、`config_yaml`、`config_sha256` 和 `approval_required`

### 3. 训练执行节点：`training_execution`

该节点通过 `TaskManager` 启动本地训练子进程，不依赖远程训练 API。默认 `trainer_persistent_worker=true`，训练由独立 Worker 持有；调用方断开不会自动取消任务，同一版本可重新读取状态。

主要步骤如下：

1. 重新校验获批 YAML 的 SHA256，并原子写入本版本的 `configs` 目录；持久 Worker 登记摘要后，同一 `trainer_version_id` 不能再接入另一份配置摘要
2. SFT 注册 `{llamafactory_dir}/data/dataset_info.json` 并调用 `llamafactory-cli train`；GRPO 校验 Verl YAML 后通过 Python module entrypoint 启动
3. 轮询任务并持续写入本地事件、日志与指标：SFT 解析 `trainer_log.jsonl` / `metrics/metrics.json`，GRPO 解析 `verl_metrics.jsonl`
4. SFT 收集 `checkpoint-N`，按 `eval_loss`、`loss` 等指标选优；GRPO 收集 `global_step_N`，按获批 YAML 的 selection metric/mode 选优；没有可用指标时回退到最新项
5. GRPO 最佳 actor 尚不是 Hugging Face 格式且配置允许时，自动导出 `merged_huggingface`；成功后写入 `trainer_best_checkpoint_path` 和 `update_model_path`，失败会记录显式导出错误

每轮目录为 `{output_root}/{task_id}/trainer/{trainer_version_id}`。`trainer_version_id` 是规范运行 ID；`trainer_task_id`、`trainer_training_task_id` 和 `training_task_id` 是兼容别名，不表示系统进程 PID。

## 自适应能力

| 触发条件 | 自动行为 | 用户控制方式 |
| --- | --- | --- |
| 未指定 framework/stage 或模板 | 默认 SFT，或根据 Verl 推断 GRPO；选择对应内置模板并校验严格配对 | 显式设置 framework、stage、template |
| SFT 任务描述命中关键词 | 调整学习率、epoch；仅 LoRA 模板调整 LoRA 参数 | 调整模板或输入参数后重新 `prepare()` |
| SFT Deepspeed 路径无效 | 尝试按文件名修复，失败则移除 | 在模板中提供有效路径 |
| GRPO 输入不是完整原生 train/validation 对 | 自动适配、拒绝坏样本、去重并确定性切分 | 设置 adapter、ratio、seed 或提供原生数据对 |
| `verl_reward_mode=auto` | 能可靠判断时选择 preset；不能判断时阻断 | 显式设置 preset 或 custom reward |
| 上一轮 GRPO 成功 | 条件式继承已审批 YAML、验证集和可加载 HF 模型 | 使用三个 inherit/reuse 开关或显式覆盖模型 |
| 调用方中断 | 持久 Worker 继续训练和收尾 | 调试时关闭 `trainer_persistent_worker` |
| YAML 与审批摘要不一致 | SHA256 校验失败 | 重新 `prepare()` 并审批新版本 |

## 输入字段表：`state.trainer`

> 字段定义来源：`loopai/schema/states.py` 中的 `TrainerState`

### 公共字段

| 字段名 | 类型 | 默认值与说明 |
| --- | --- | --- |
| `train_framework` | `str` | 默认 `llamafactory`；可选 `llamafactory` / `verl`。 |
| `train_stage` | `str` | 根据 framework 推断为 `sft` / `grpo`，且必须严格配对。 |
| `train_input_dataset_path` | `str` | SFT 数据，或已准备好的原生 Verl 训练 Parquet。 |
| `train_input_task_description` | `str` | 任务描述，用于 SFT 规则调参和 GRPO reward 判断。 |
| `train_input_config_template_path` | `str` | YAML 模板；留空时自动选择后端默认模板。 |
| `train_input_model_name` | `str` | 基础模型名称或本地路径。 |
| `CUDA_VISIBLE_DEVICES` | `str` | 默认 `"0"`；也可从 `state.system` 读取。 |
| `trainer_persistent_worker` | `bool` | 默认 `true`。 |

顶层 `state.output_dir` 是输出根目录，默认 `./outputs`；单轮实际目录通过只读结果 `trainer_output_dir` 返回。

### 后端专用字段

| 字段名 | 类型 | 默认值与说明 |
| --- | --- | --- |
| `llamafactory_dir` | `str` | SFT 必需，LLaMA-Factory 仓库根目录。 |
| `llamafactory_env_path` | `str` | SFT 环境路径，可从 `state.system` 读取。 |
| `verl_dir` | `str` | GRPO 必需，Verl 仓库根目录。 |
| `verl_env_path` | `str` | GRPO 环境名或路径，默认 `verl`。 |
| `verl_source_dataset_path` / `verl_source_eval_dataset_path` | `str` | GRPO 训练来源和可选验证来源。 |
| `verl_data_adapter` / `verl_validation_ratio` / `verl_split_seed` | `str/float/int` | 默认 `auto` / `0.05` / `42`。 |
| `verl_reward_mode` / `verl_reward_preset` | `str` | 默认 `auto`；custom 还需 `verl_reward_function_path`。 |
| `verl_inherit_previous_config` / `verl_reuse_previous_validation` / `verl_use_previous_best_model` / `verl_multi_round_enabled` | `bool` | 默认均为 `true`。 |

## 输出字段表

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| `trainer_version_id` / `trainer_task_id` / `trainer_training_task_id` / `training_task_id` | `str` | 前者是规范版本 ID，其余是同值兼容字段。 |
| `trainer_parent_version_id` / `trainer_round_index` / `trainer_model_inheritance` | `str/int/dict` | GRPO 父版本、轮次和模型继承原因。 |
| `trainer_output_dir` | `str` | 当前版本的输出目录。 |
| `trainer_data_check_passed` / `train_output_data_check_report_path` | `bool/str` | 数据检查状态和报告。 |
| `verl_data_manifest_path` / `verl_data_prepare_result` / `verl_reward_recommendation` | `str/dict` | GRPO 数据 manifest、统计和 reward 决策。 |
| `trainer_config_generation_success` / `train_output_config_path` / `trainer_config_explanation_path` | `bool/str` | 配置状态、最终 YAML 和说明文本。 |
| `trainer_training_success` / `trainer_training_execution_time` / `trainer_training_final_status` | `bool/float/dict` | 训练状态、耗时和最终状态。 |
| `train_output_training_log_path` / `train_output_training_report_path` | `str` | 训练日志和报告。 |
| `training_checkpoints` | `List[str]` | SFT `checkpoint-N` 或 GRPO `global_step_N` 的名称列表。 |
| `training_step_losses` | `List[Dict]` | SFT step-loss，或 GRPO 的规范化 metric records。 |
| `trainer_result_analysis` / `trainer_result_summary` | `dict` | 统一训练结果分析与摘要。 |
| `trainer_best_metric` / `trainer_best_checkpoint` / `trainer_best_checkpoint_path` / `update_model_path` | `dict/str` | 选优信息、最佳 checkpoint 和下一轮模型路径。 |
| `trainer_event_log_path` / `trainer_run_state_path` / `trainer_worker_log_path` / `trainer_worker_pid` / `trainer_state_update_error` | `str/int` | 事件、Worker 状态、日志、PID 和可选回写错误。 |
| `trainer_model_export_error` / `trainer_model_export_log_path` | `str` | GRPO 模型导出失败原因和诊断日志。 |
| `trainer_result` / `trainer_last_error` | `dict` | 标准返回结果或最近一次结构化错误。 |

## 不同任务模式下重点填写什么

### 模式 A：通用对话 / 问答 SFT

- `train_framework` 设为 `llamafactory`
- `train_stage` 设为 `sft`
- `train_input_task_description` 中包含“对话 / 聊天 / chat”等关键词
- 模板通常使用 `templates/qwen2_5_coder_bird_full_sft.yaml`
- 如果要使用 LoRA，可在源模板中将 `finetuning_type` 从 `full` 改为 `lora`
- 数据格式建议使用 ShareGPT `conversations` 或 Alpaca

当模板本身使用 LoRA 时，规则模式通常会自动给出：

- `lora_r=8`
- `lora_alpha=16`
- `lora_target=q_proj,v_proj`
- `learning_rate=5e-5`

### 模式 B：代码 / 编程类 SFT

- `train_input_task_description` 中包含“代码 / 编程 / code”等关键词
- 数据格式通常为 Alpaca，`output` 中写入代码片段

当模板本身使用 LoRA 时，规则模式通常会自动给出：

- `lora_r=16`
- `lora_alpha=32`
- `lora_target=all`

### 模式 C：数学 / 推理 SFT

- `train_input_task_description` 中包含“数学 / 推理 / 复杂 / 困难”等关键词
- 学习率通常会被压低到 `1e-5`
- 如果样本较长，建议在模板中显式提高 `cutoff_len`

### 模式 D：从上游 `mapping_results` 接力

如果 Trainer 是在 ObtainerCLI/DataMixer 最终导出之后被串联调用的，可以省略 `train_input_dataset_path`。SFT 节点也能识别 Constructor 输出；独立调用公开 API 时，直接传入数据路径最明确。

Trainer 会按优先级自动尝试：

- SFT：Obtainer、Constructor、`train_input_dataset_path`
- GRPO：`prepare()` kwargs/环境变量、本轮 Constructor、Obtainer、已保存的数据来源

但以下字段仍然必须提供：

- `train_input_task_description`
- `train_input_model_name`
- 对应后端的 `llamafactory_dir` 或 `verl_dir`

使用非默认后端时，应显式设置 `train_framework`；`train_stage` 可由 framework 推断。

### 模式 E：Verl GRPO

- `train_framework=verl`、`train_stage=grpo`
- 提供 `verl_dir`、模型和数据；非原生数据通常保留 `verl_data_adapter=auto`
- `verl_reward_mode=auto` 不能可靠识别时，同时设置 `verl_reward_mode=preset` 和 `verl_reward_preset`，或改用 custom reward
- 多轮默认尝试继承已审批 YAML、兼容验证集和上一轮可加载 HF 模型；若要覆盖数据或模型，应通过本轮 `prepare()` kwargs 或环境变量传入

## 最小可用示例

```python
from loopai.skills.Trainer import prepare, run_prepared

state = {
    "task_id": "trainer_demo",
    "output_dir": "./outputs",
    "trainer": {
        "train_framework": "llamafactory",
        "train_stage": "sft",
        "llamafactory_dir": "/path/to/LLaMA-Factory",
        "train_input_dataset_path": "/path/to/LLaMA-Factory/data/alpaca_en_demo.json",
        "train_input_task_description": "训练一个能够回答简单问题并进行对话的 AI 助手",
        "train_input_model_name": "/path/to/Qwen2.5-1.5B",
    }
}

prepared = prepare(state=state, thread_id=state["task_id"])
approval = prepared["trainer"]["trainer_result"]["data"]
print(approval["config_yaml"])
if input("确认按以上 YAML 启动训练？输入 yes：").strip().lower() != "yes":
    raise SystemExit("训练未获批准")

result = run_prepared(
    prepared_config_path=approval["config_path"],
    expected_config_sha256=approval["config_sha256"],
    state=prepared,
    thread_id=state["task_id"],
    version_id=approval["trainer_version_id"],
)
print(result["trainer"]["trainer_result"])
```

`examples/scripts/run_trainer.py` 展示的是底层图直接调用；新集成建议使用上面的显式审批流程。

## WebUI / 资源池中的填写建议

在 WebUI 中使用 Trainer 时，通常建议：

1. 先选择 `llamafactory+sft` 或 `verl+grpo`，再配置对应的仓库目录、环境路径和 `CUDA_VISIBLE_DEVICES`
2. 在资源池中维护好以下四类路径，再在任务面板中下拉选用
3. 提供 `train_input_task_description`，供 SFT 规则调参或 GRPO reward 判断使用

资源池中建议维护的四类路径包括：

- 训练/验证数据集：`train_input_dataset_path`、`verl_source_dataset_path`、`verl_source_eval_dataset_path`
- 配置模板：`train_input_config_template_path`
- 基础模型路径：`train_input_model_name`
- custom reward 文件：`verl_reward_function_path`

## 环境与依赖

- LLaMA-Factory 主仓库需要能正常运行 `llamafactory-cli train`
- `llamafactory_env_path` 指向的 Python 环境需要安装 LLaMA-Factory 及其训练依赖，如 `deepspeed`、`transformers`
- GRPO 需要可用的 Verl 仓库和 `verl_env_path`，并安装所选的 vLLM 或 SGLang rollout 后端
- 当前 GRPO 数据准备和预检需要 LoopAI 环境安装 `pyarrow`
- 训练指标由本地文件驱动，不需要安装或配置外部实验跟踪服务
- 多卡训练通过 `CUDA_VISIBLE_DEVICES` 控制，例如 `"0,1,2,3"`

## 常见问题

- SFT 数据检查未通过：`.json` 顶层必须是 `list`，`.jsonl` 每行必须是合法 JSON，`conversations[*].from` 必须是 `human/gpt/system` 之一
- GRPO 数据检查未通过：检查 Parquet 的 `prompt/data_source/reward_model.ground_truth`；auto/preset 查看 smoke test，custom 检查文件、语法和目标函数
- 配置生成失败：检查 YAML 是否为 mapping、后端与阶段是否严格配对，以及模板中的模型、Deepspeed、Verl 路径是否有效
- 训练子进程失败：优先查看 `train_output_training_log_path`；SFT 检查数据注册和 `llamafactory_dir`，GRPO 检查 `verl_dir`、entrypoint、rollout 后端和 reward
- 长时间训练：当前 `training_execution_node` 没有固定等待上限；调用方正常存活时会同步等待。若调用方意外中断，持久化 Trainer Worker 会继续训练和收尾，可通过同一 `task_id`、`version_id` 及运行目录中的 `run_state.json` / `worker_result.pkl` 重新读取状态
- 曲线或指标为空：SFT 检查 `trainer_log.jsonl` / `metrics/metrics.json`，GRPO 检查 `metrics/verl_metrics.jsonl`，并优先查看原始训练日志

## 进阶：单独复用 SFT 配置生成能力

如果只想使用 LoopAI 中“任务描述 -> LLaMA-Factory YAML”这部分能力，而不直接执行训练，可以单独使用 `ConfigGenerator`：

```python
from loopai.skills.Trainer.utils.config_generator import ConfigGenerator

gen = ConfigGenerator()
config = gen.generate_config(
    task_description="训练一个 SQL 代码生成模型，难度较高",
    dataset_path="/path/to/sql_train.json",
    model_name="/path/to/Qwen2.5-Coder-7B",
    output_dir="./output/sql_sft",
    template_path="loopai/skills/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
)
gen.save_config_as_yaml(config, "./output/sql_sft/training_config.yaml")
```

## 使用时最该关注什么

- 训练前：必填字段是否齐全，数据检查是否通过
- 审批时：展示的 YAML、`trainer_version_id` 和 SHA256 是否与启动参数一致
- 训练中：对应后端的日志和本地指标文件是否持续更新，前端是否能读取曲线
- 训练后：`training_checkpoints` 和 `trainer_best_checkpoint_path` 是否有效，`update_model_path` 是否能被下一轮使用
