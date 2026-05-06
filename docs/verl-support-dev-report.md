# Trainer 模块 verl 框架支持 — 开发报告

> 分支: `feature/verl-support`  
> 日期: 2026-04-07  
> 变更: 20 个文件, +1663 / -133 行

---

## 一、背景

loopai 的 Trainer 模块此前仅完整支持 LlamaFactory（SFT 微调）。虽然已预留了 verl 框架的分支入口（`train_framework == "verl"`），但实际实现非常简陋：

- 数据检查节点：verl 直接跳过，不做任何校验
- 配置生成节点：verl 要求用户手动提供完整的 `.sh` 脚本，不提供则报错
- 训练执行节点：verl 的 Python 环境路径硬编码为 `python3.10`
- 状态定义中缺少 `verl_dir`、`verl_env_path` 等字段
- 前置校验不区分 verl 和 LlamaFactory 的必需字段
- 日志解析和训练结果收集完全按 LlamaFactory 格式设计

本次开发按 P0（基础设施）→ P1（核心功能）→ P2（文档示例）→ 测试 的顺序，系统性地补全了所有缺失项。

---

## 二、开发内容详述

### 2.1 P0: 状态字段、前置校验、环境配置

**commit**: `bbb5559`

#### 2.1.1 TrainerState 新增字段

**文件**: `loopai/schema/states.py`

在 `TrainerState` 中新增三个 verl 专用字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `verl_dir` | str | `""` | verl 项目安装目录，作为训练进程的 `cwd` |
| `verl_env_path` | str | `""` | verl 虚拟环境路径（包含 `bin/python` 的目录） |
| `verl_train_mode` | str | `"grpo"` | 训练模式，允许值: `["grpo", "ppo", "sft"]` |

三个字段均携带 `json_schema_extra` 元信息，前端 UI 可自动渲染为对应的输入控件。

**实现逻辑**：在 `CUDA_VISIBLE_DEVICES` 字段之前插入新字段定义，保持字段分组的逻辑顺序（框架配置 → 环境配置 → GPU 配置）。

#### 2.1.2 前置字段校验

**文件**: `loopai/agents/Trainer/trainer_agent.py`

原有逻辑只在 `framework == 'llamafactory'` 时追加 `llamafactory_dir` 到必需字段列表。

修改后的逻辑：

```python
framework = state.get('trainer', {}).get('train_framework')
if framework == 'llamafactory':
    required_fields["trainer"].append('llamafactory_dir')
elif framework == 'verl':
    required_fields["trainer"].append('verl_dir')
```

当用户选择 verl 框架但未填写 `verl_dir` 时，系统会触发 Configer 子图自动补全配置，而不是在训练执行阶段才报错。

#### 2.1.3 环境配置自动推断

**文件**: `loopai/agents/Trainer/utils/task_manager.py`

**问题**：原 `_run_verl_training()` 硬编码 `lib/python3.10/site-packages`，若用户使用 Python 3.11/3.12 则失败。

**修复方案**：复用 LlamaFactory 分支已有的自动推断逻辑：

```python
# 遍历 lib/ 目录下的 pythonX.Y 子目录，取最新版本的 site-packages
lib_dir = os.path.join(env_root, "lib")
candidates = [d for d in os.listdir(lib_dir) if d.startswith("python")]
candidates.sort()
for py_dir in reversed(candidates):
    candidate = os.path.join(lib_dir, py_dir, "site-packages")
    if os.path.isdir(candidate):
        python_site_packages = candidate
        break
```

同时补充了 `bin` 目录的 `PATH` 注入和异常处理日志。

---

### 2.2 P1-1: verl 数据格式检查器

**commit**: `fb13685`

#### 核心文件

- `loopai/agents/Trainer/utils/data_checker.py` — 新增 188 行
- `loopai/agents/Trainer/nodes/data_check_node.py` — verl 分支重写

#### 设计依据

通过阅读 verl 源码 `verl/utils/dataset/rl_dataset.py` 和 `verl/utils/dataset/multiturn_sft_dataset.py`，确定了 verl 的数据格式规范：

- **RL 模式 (GRPO/PPO)**：核心字段 `prompt`，类型为 `list[dict]`，每个 dict 包含 `role`（user/assistant/system）和 `content`。可选字段 `data_source`。
- **SFT 模式**：核心字段 `messages`，类型为 `list[dict]`，格式同上，需包含至少一轮 user+assistant 对话。
- 支持 `.parquet` / `.json` / `.jsonl` 三种文件格式。

#### 实现的函数

| 函数 | 功能 |
|------|------|
| `check_verl_data_format(path, mode)` | 入口函数，根据模式分发到 RL 或 SFT 验证 |
| `_load_parquet_samples(path, max)` | Parquet 文件加载（优先 pyarrow，降级 pandas） |
| `_validate_verl_rl_format(data)` | RL 格式验证：检查 prompt 字段存在性、列表类型、消息 role/content |
| `_validate_verl_sft_format(data)` | SFT 格式验证：检查 messages 字段、消息完整性、对话轮数 |
| `_safe_preview(sample)` | 将样本转为可 JSON 序列化格式（处理 parquet 复杂类型） |
| `generate_verl_format_report(result)` | 生成可读的检查报告，包含错误/警告/修改建议 |

#### 错误控制策略

为避免大数据集下报告过长，采用阈值控制：前 5 个错误详细报告，超过 10 个后截断并汇总。区分 errors（阻断性）和 warnings（建议性），例如缺少 `data_source` 只产生 warning 不阻断。

#### data_check_node 修改

原有的 `elif framework == "verl": pass` 替换为完整的验证流程：获取 `verl_train_mode` → 调用 `check_verl_data_format` → 保存报告 → 更新 state。

---

### 2.3 P1-2: verl 智能配置生成器

**commit**: `31da264`

#### 核心文件

- `loopai/agents/Trainer/utils/verl_config_generator.py` — 新增 246 行
- `loopai/agents/Trainer/templates/verl_grpo.sh` — GRPO 模板
- `loopai/agents/Trainer/templates/verl_ppo.sh` — PPO 模板
- `loopai/agents/Trainer/templates/verl_sft.sh` — SFT 模板
- `loopai/agents/Trainer/nodes/config_generation_node.py` — verl 分支重写

#### 设计思路

verl 通过 Hydra + 命令行参数覆盖 YAML 配置，训练入口是 shell 脚本。因此配置生成器的任务是：

1. 根据 `verl_train_mode` 选择对应的脚本模板
2. 根据用户输入（模型路径、数据路径等）和任务描述自动填充参数
3. 生成包含环境变量导出 + 训练命令的完整 bash 脚本

#### 三个模板的差异

| 模板 | 入口命令 | 关键差异 |
|------|---------|---------|
| `verl_grpo.sh` | `python3 -m verl.trainer.main_ppo` | `algorithm.adv_estimator=grpo`，无 critic |
| `verl_ppo.sh` | `python3 -m verl.trainer.main_ppo` | `algorithm.adv_estimator=gae`，需要 critic 模型配置 |
| `verl_sft.sh` | `torchrun -m verl.trainer.sft_trainer` | 使用 torch distributed，不需要 Ray |

模板中使用 `${VAR:-default}` 的 bash 语法引用环境变量，生成器在脚本头部通过 `export` 语句设置所有参数。

#### 自动调参逻辑 (`_auto_tune_params`)

基于任务描述中的关键词检测，自动调整训练参数：

| 场景 | 检测关键词 | 调整参数 |
|------|-----------|---------|
| 数学/推理 | 数学, math, 推理, reasoning, 代码, code | `MAX_RESPONSE_LENGTH=2048`, `LEARNING_RATE=5e-7`, `TOTAL_EPOCHS=20` |
| 对话 | 对话, chat, 聊天 | `MAX_RESPONSE_LENGTH=512`, `ROLLOUT_N=3` |
| 长文本/代码 (SFT) | 长文本, long, 代码, code | `MAX_LENGTH=4096`, `MAX_TOKEN_LEN_PER_GPU=16384` |

#### config_generation_node 修改

verl 分支支持两种工作模式：

1. **自动生成模式**（推荐）：用户不提供 `train_input_config_template_path`，系统调用 `VerlConfigGenerator.generate_script()` 自动生成脚本
2. **自定义模板模式**：用户提供 `.sh` 脚本路径，直接使用

---

### 2.4 P1-3: verl 日志解析和训练结果收集

**commit**: `f0b67cc`

#### 核心文件

- `loopai/agents/Trainer/utils/realtime_log_parser.py` — MetricsExtractor 增强
- `loopai/agents/Trainer/nodes/training_execution_node.py` — 结果收集适配

#### verl 日志格式

通过阅读 `verl/utils/tracking.py`，确认 verl 的 `file` 日志后端输出 JSONL 格式：

```json
{"step": 100, "data": {"train/loss": 0.5, "train/lr": 1e-6, "train/grad_norm": 1.23}}
```

key 格式为 `前缀/指标名`，如 `train/loss`、`val/loss`、`train/grad_norm`。

#### MetricsExtractor 改造

在 `extract_metrics()` 方法顶部增加 verl JSONL 格式的优先检测：

```python
# 判断条件: 行以 { 开头，且包含 "step" 和 "data" 两个键
if line_stripped.startswith('{') and '"step"' in line_stripped and '"data"' in line_stripped:
    parsed = json.loads(line_stripped)
    metrics["step"] = parsed["step"]
    for key, value in parsed["data"].items():
        if isinstance(value, (int, float)):
            short_key = key.split("/")[-1]  # "train/loss" -> "loss"
            metrics[short_key] = value
            metrics[key] = value  # 保留原始 key
    return metrics  # 提前返回，不再走正则解析
```

如果不匹配 verl 格式，继续走原有的 LlamaFactory JSON/正则解析逻辑，确保完全向后兼容。

#### 训练结果收集适配

checkpoint 扫描逻辑增加 verl 格式：

```python
# LlamaFactory: checkpoint-XXX
# verl: global_step_XXX / epoch_XXX
if entry.startswith('checkpoint-') or entry.startswith('global_step_') or entry.startswith('epoch_'):
    checkpoints.append(entry)
```

训练指标收集按框架分流：
- **verl**：从 `metrics/metrics.json` 文件读取（由 RealTimeLogParser 实时写入），提取 loss/grad_norm/lr 等指标
- **LlamaFactory**：从 `trainer_log.jsonl` 读取（原有逻辑不变）

---

### 2.5 P2: 文档和示例

**commit**: `3d22a5e`

#### README 重写

`loopai/agents/Trainer/README.md` 从纯 LlamaFactory 文档重写为双框架文档，包含：

- 框架对比表
- verl 数据格式要求（RL 和 SFT 两种格式的 JSON 示例）
- verl 使用示例代码（GRPO 训练完整 state 配置）
- 三种训练模式说明（grpo/ppo/sft）
- 通用字段 / verl 专用字段 / LlamaFactory 专用字段的完整列表

#### 示例脚本

`examples/scripts/run_verl_trainer.py` 提供三种使用模式的示例：

1. `run_verl_grpo()` — GRPO 强化学习训练
2. `run_verl_sft()` — SFT 微调
3. `run_verl_with_custom_script()` — 自定义训练脚本

---

### 2.6 测试

**commit**: `737f476`

#### 测试环境

```bash
conda create -n loopai-test python=3.12
pip install -e .
pip install pytest
```

#### 测试结果: 53 passed

| 测试文件 | 用例数 | 覆盖模块 |
|---------|-------|---------|
| `test_verl_data_checker.py` | 17 | `check_verl_data_format` + `generate_verl_format_report` |
| `test_verl_config_generator.py` | 14 | `VerlConfigGenerator` + `generate_verl_config_explanation` |
| `test_verl_log_parser.py` | 13 | `MetricsExtractor` verl JSONL 解析 + LlamaFactory 回归 |
| `test_verl_state_fields.py` | 9 | `TrainerState` 新字段 + 前置校验逻辑 |

#### 测试设计覆盖矩阵

**数据检查器** (17 cases):

| 类别 | 用例 |
|------|------|
| RL 正常路径 | JSON 格式通过 / JSONL 格式通过 |
| RL 异常路径 | 缺 prompt / prompt 类型错误 / 消息缺 role / 消息缺 content |
| RL 警告 | 缺 data_source（不阻断） |
| SFT 正常路径 | 多轮对话通过 |
| SFT 异常路径 | 缺 messages / messages 类型错误 |
| SFT 警告 | 单条消息 |
| 通用异常 | 空文件 / 文件不存在 / 不支持的格式 / 未知训练模式 |
| 报告 | 通过报告 / 失败报告含修改建议 |

**配置生成器** (14 cases):

| 类别 | 用例 |
|------|------|
| 脚本生成 | GRPO 含关键参数 / PPO 含 critic / SFT 含 torchrun / 无效模式抛异常 |
| 参数覆盖 | extra_params 生效 / project_name 写入 |
| 自动调参 | 数学任务 / 对话任务 / 代码SFT / 无描述用默认值 |
| 文件操作 | 保存为可执行文件 / 自定义模板加载 |
| 说明文档 | GRPO 说明 / SFT 说明 |

**日志解析器** (13 cases):

| 类别 | 用例 |
|------|------|
| verl JSONL | 基本解析 / grad_norm / val 指标 / 空 data / 非数值跳过 / 带空白 |
| LlamaFactory 回归 | JSON 格式 / key=value 格式 / 普通文本无指标 / 畸形 JSON 降级 |
| 边界 | 空行 / 纯空白 / 缺 data 键 |

**状态字段** (9 cases):

| 类别 | 用例 |
|------|------|
| 字段存在性 | verl_dir / verl_env_path / verl_train_mode 存在且有正确默认值 |
| allowed_values | train_framework 含 verl / verl_train_mode 含 grpo/ppo/sft |
| 赋值 | 三个字段均可正常赋值 |
| 回归 | LlamaFactory 原有字段不受影响 |
| 前置校验 | verl 要求 verl_dir / llamafactory 要求 llamafactory_dir |

---

## 三、文件变更汇总

### 新增文件 (10)

| 文件 | 行数 | 说明 |
|------|------|------|
| `loopai/agents/Trainer/utils/verl_config_generator.py` | 246 | verl 配置生成器 |
| `loopai/agents/Trainer/templates/verl_grpo.sh` | 40 | GRPO 训练模板 |
| `loopai/agents/Trainer/templates/verl_ppo.sh` | 41 | PPO 训练模板 |
| `loopai/agents/Trainer/templates/verl_sft.sh` | 34 | SFT 训练模板 |
| `examples/scripts/run_verl_trainer.py` | 102 | verl 训练示例 |
| `tests/__init__.py` | 0 | 测试包标识 |
| `tests/test_verl_data_checker.py` | 233 | 数据检查测试 |
| `tests/test_verl_config_generator.py` | 205 | 配置生成测试 |
| `tests/test_verl_log_parser.py` | 134 | 日志解析测试 |
| `tests/test_verl_state_fields.py` | 110 | 状态字段测试 |

### 修改文件 (10)

| 文件 | 变更量 | 说明 |
|------|--------|------|
| `loopai/schema/states.py` | +19 | 新增 verl_dir/verl_env_path/verl_train_mode |
| `loopai/agents/Trainer/trainer_agent.py` | +3/-1 | verl 前置校验 |
| `loopai/agents/Trainer/utils/task_manager.py` | +35/-6 | verl 环境自动推断 |
| `loopai/agents/Trainer/utils/data_checker.py` | +188 | verl 数据格式检查函数 |
| `loopai/agents/Trainer/utils/realtime_log_parser.py` | +24 | verl JSONL 日志解析 |
| `loopai/agents/Trainer/utils/__init__.py` | +2/-1 | 导出新增函数 |
| `loopai/agents/Trainer/nodes/data_check_node.py` | +26/-2 | verl 分支实际验证 |
| `loopai/agents/Trainer/nodes/config_generation_node.py` | +42/-8 | verl 自动配置生成 |
| `loopai/agents/Trainer/nodes/training_execution_node.py` | +65/-27 | verl 结果收集适配 |
| `loopai/agents/Trainer/README.md` | 重写 | 双框架文档 |

---

## 四、后续可优化方向

| 方向 | 说明 | 优先级 |
|------|------|--------|
| LLM 智能调参 | 在 VerlConfigGenerator 中接入大模型，根据任务描述和数据特征生成更精准的参数 | 中 |
| SwanLab 集成 | verl 原生支持 swanlab 后端，可在生成的脚本中自动启用 | 中 |
| 分布式配置 | 根据可用 GPU 数量自动计算 TP/PP 并行度和 batch size | 低 |
| Reward Function | 支持在配置中指定自定义 reward function 路径 | 低 |
| Parquet 预览 | data_checker 的 parquet 预览增加嵌套结构展开 | 低 |
