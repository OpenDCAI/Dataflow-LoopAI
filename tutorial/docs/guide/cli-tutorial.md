# 终端教程

WebUI 更适合第一次完整体验，而终端模式更适合开发、调试和脚本化使用。

这一页后续主要承载两类内容：

1. 整个 LoopAI 的终端启动教程
2. 各个子 Agent 的单独启动教程

## 整体启动

如果你想直接启动主流程，可以运行：

```bash
python examples/scripts/run_starter.py
```

这条命令更适合：

- 验证主流程配置
- 调试 Starter 调度逻辑
- 在没有 WebUI 的场景下直接体验任务流

## 子 Agent 单独启动

各个子 Agent 都可以脱离 Starter 主流程单独跑，常用于开发联调、复跑某一步、或离线生成中间产物。

### TrainerAgent

入口脚本：`examples/scripts/run_trainer.py`。它会构建一个独立的 `TrainerAgent` 图并调用 `graph.invoke`，完整跑一遍「数据检查 → 配置生成 → 训练执行」三个节点。

#### 运行方式

```bash
python examples/scripts/run_trainer.py
```

> 脚本顶部以 `# %%` 分块，可以直接在 VSCode / Jupyter 里按 cell 跑，方便定位某一阶段失败。

#### 必填参数（state.trainer）

不经 Starter 调度时，必须自己在 `training_state["trainer"]` 里填齐 LlamaFactory 模式的全部必填字段，否则 `check_required_fields` 节点会判失败并尝试转去 `ConfigerAgent` 子图，单独跑场景下会直接报错退出：

| 字段 | 说明 |
| --- | --- |
| `train_framework` | 固定写 `"llamafactory"` |
| `llamafactory_dir` | 本地 LLaMA-Factory 仓库根目录，用于注册 dataset_info.json |
| `train_input_dataset_path` | 训练数据集（`.json` / `.jsonl`）。单独运行时**不要依赖** obtainer/constructor 的 `mapping_results`，必须显式给出 |
| `train_input_task_description` | 任务描述，决定规则模式下学习率 / epoch / LoRA 参数 |
| `train_input_config_template_path` | YAML 模板路径，可直接用 `loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml` |
| `train_input_model_name` | 基础模型名或本地权重目录，会写入 `model_name_or_path` |

可选但强烈建议给到的字段：

- `output_dir`：Trainer 的工作目录，默认会落到 `./output/trainer`
- `train_input_use_swanlab` / `train_input_swanlab_project`：开启 SwanLab 监控
- `swanlab_api_key` / `llamafactory_env_path` / `CUDA_VISIBLE_DEVICES`：未在 `state.system` 中提供时需在 `state.trainer` 直接给出

> 完整字段含义见 [Trainer Agent 详细指南](/guide/details/trainer-agent#输入字段表-state-trainer)。

#### 改成自己的参数

直接复制 `run_trainer.py` 后改 `training_state` 即可：

```python
training_state = {
    "trainer": {
        "train_framework": "llamafactory",
        "llamafactory_dir": "/your/path/to/LLaMA-Factory",
        "train_input_dataset_path": "/your/path/to/train.json",
        "train_input_task_description": "训练一个 SQL 代码生成模型",
        "train_input_config_template_path":
            "loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
        "train_input_model_name": "/your/path/to/Qwen2.5-Coder-7B",
        "output_dir": "./output/trainer_demo",

        "train_input_use_swanlab": True,
        "train_input_swanlab_project": "sql_sft",
        # 单独运行时如果 starter.yaml 没加载到 system，可在这里直接补
        # "swanlab_api_key": "xxx",
        # "llamafactory_env_path": "/opt/conda/envs/llamafactory",
        # "CUDA_VISIBLE_DEVICES": "0,1",
    }
}
```

#### 调试小技巧

- **只想验证模板与字段，不真训**：注释掉 `graph.invoke(...)` 那段，直接调用 `from loopai.agents.Trainer.utils.config_generator import ConfigGenerator`，单独跑配置生成，几秒内就能产出 YAML，方便检查参数是否合理。
- **只想跑数据检查**：`from loopai.agents.Trainer.utils.data_checker import check_data_format, generate_format_report`，传入数据集路径即可，不依赖 LangGraph。
- **复跑某次任务**：`thread_id` 用同一个值（脚本里默认是 `"trainer_test_1"`），LangGraph 会从 checkpointer 里恢复上次状态；想要从 0 开始就换一个新的 `thread_id`。
- **想看到完整 SSE 流**：把 `graph.invoke(...)` 换成 `for event in graph.stream(training_state, config=config, stream_mode="custom"): print(event)`，会逐条打出 `StreamEvent`。
- **训练超时（默认 1 小时）**：`training_execution_node` 内 `max_wait_time = 3600` 是写死的，长任务调试时可以临时改大，或者把训练命令拷出来手动跑，再让 Trainer 只做配置生成。
- **训练失败定位**：先看 `result["trainer"]["train_output_training_log_path"]` 指向的日志文件（LlamaFactory 子进程原始 stdout/stderr），再看 `train_output_training_report_path` 中的汇总报告。
- **SwanLab 日志看不到**：确认模板 `report_to` 没被覆盖成 `none`，并且 `swanlab_api_key` 能从 `state.trainer` 或 `state.system` 之一读到。

#### 输出位置

成功跑完后，下面这些文件会按 `{output_dir}/{global_task_id}/trainer/{trainer_task_id}/` 的层级生成：

- `data_check_report.txt`：数据检查报告
- `training_config.yaml` + `config_explanation.txt`：自动生成的 LlamaFactory 配置与说明
- `configs/{trainer_task_id}.yaml`：实际提交给训练子进程的配置副本
- `logs/`、`runs/`：TaskManager 维护的训练日志与运行记录
- `training_log_{task_id}.txt`、`training_report_{task_id}.txt`：最终汇总日志与报告
- 训练产物（`checkpoint-*`、`trainer_log.jsonl`）写在配置里的 `output_dir`，可在 `result["trainer"]["training_checkpoints"]` 里拿到列表
