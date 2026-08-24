# Dataflow-LoopAI

简体中文 | [English](./Dev_README_en.md)

Dataflow-LoopAI 是一个具备**自演化**能力的智能系统，围绕 Starter、Looper 与各类节点 / Skill，完成评测、分析、数据获取、训练和持续迭代。

```text
用户  ⇄  Starter（Codex SDK）  ⇄  Node（Skill）
                    │
                    ├── 普通问答：直接返回
                    └── 复杂任务：闭环执行
                               （评测 → 分析 → 数据获取 → 训练）
```

---

## 📂 项目结构说明

下面的结构说明以**当前仓库实际内容**为准，同时标明推荐的新开发位置：

```text
Dataflow-LoopAI/
├── api/                       # WebUI 后端，FastAPI 服务、任务接口、responseProxy、数据库接入
│   ├── app/controllers/       # starter / task / config 等路由
│   ├── app/services/          # starter session、looper、任务运行服务
│   ├── app/utils/             # 配置、监控、凭据迁移等后端工具
│   ├── db/                    # SQLite 数据库目录
│   └── dist/                  # 发布版前端产物，生产环境由 FastAPI 直接托管
│
├── codex-runner/              # Codex runner，负责对接本地 codex 运行时与事件流
│
├── docs/                      # 项目文档与图片资源
│   └── assets/                # 图片与素材
│
├── examples/                  # 示例脚本与运行用例
│   └── scripts/               # 启动、测试、独立运行脚本
│
├── loopai/                    # 项目核心 Python 代码
│   ├── agents/                # 历史/兼容目录；当前仍保留 BaseAgent 和 Obtainer
│   │   ├── BaseAgent/         # 基础 Agent / 节点能力封装
│   │   └── Obtainer/          # 旧数据获取实现
│   │
│   ├── common/                # 通用工具、事件流、异常、Prompt 等
│   ├── mcp/                   # MCP 服务与工具
│   ├── schema/                # 状态、模型池、事件、系统配置 schema
│   ├── skills/                # 当前推荐的新能力实现目录
│   │   ├── Analyzer/
│   │   ├── Configer/
│   │   ├── Judger/
│   │   ├── Looper/
│   │   ├── ObtainerCLI/
│   │   └── Trainer/
│   └── utils/                 # 通用辅助代码
│
├── skills/                    # 实际给系统消费的技能说明 Markdown（SKILL.md）
│   ├── Analyzer/
│   ├── Configer/
│   ├── Judger/
│   ├── Trainer/
│   └── obtainer/
│
├── scripts/                   # 项目脚本，例如 UI 发布、代理启动等
│
├── tui/                       # 终端 UI（tasks 管理、主界面对话、节点状态查看）
│
└── ui/                        # Vue 3 + Vite WebUI 前端源码
```

### 当前推荐的开发位置

当前开发结构和早期版本相比已经发生变化：

1. 新的 Sub-Agent / Skill 不再优先放在 `loopai/agents` 下开发。
2. 新的能力实现统一优先放在 `loopai/skills` 下。
3. 根目录的 `skills` 目录用于定义实际给系统消费的 `SKILL.md`。
4. `loopai/agents` 里仍保留部分历史实现与兼容代码，文档描述必须以仓库当前内容为准，不要假设它已经完全清空。

可以简单理解为：

- `loopai/skills`：放 Python 侧的技能实现、工具代码、运行逻辑
- `skills`：放技能定义文件，主要是实际使用的 `SKILL.md`
- `loopai/agents`：历史实现、兼容层，以及仍未完全迁移的部分模块

---

## 🧩 核心 Skills / Nodes

当前开发文档统一使用 **Skill / Node** 口径，不再单独强调 Core Agents。

### Starter

- 基于 `codex-sdk` 的系统入口
- 负责用户对话、意图识别、节点调度和整体闭环推进

### Looper

- 替代用户维护与 Starter 的连续对话
- 结合 conversation 自动总结上下文、补齐参数、推进下一步
- 避免因为缺少人工接话导致 loop 中断

### Judger

- 执行评测、生成结果、输出评测指标与日志

### Analyzer

- 分析评测结果，抽取 failure pattern、insight 和结构化结论

### ObtainerCLI / DataMixer 网页采集

- 负责数据搜索、下载、入湖、导出以及网页采集相关流程

### Trainer

- 负责训练任务编排、配置生成、执行、日志回传和结果管理

说明：`Configer` 当前仍然存在并用于配置读写与运行态更新，但这里不再把它单独列为核心 Skill。

---

## 📦 安装与开发启动

### Python 依赖

```bash
conda create -n loopai python=3.12
conda activate loopai

pip install uv
uv pip install -e .
```

### WebUI 前端开发

生产环境或正常使用时，优先直接下载已发布的前端 dist：

```bash
python scripts/download_ui_release.py
```

仅在需要修改或调试 `ui/` 源码时，再执行下面步骤。

#### 1. 安装 NVM

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc  # 或 ~/.zshrc
```

#### 2. 安装 Node.js 20 与 Yarn

```bash
nvm install 20
nvm use 20
nvm alias default 20

corepack enable
corepack prepare yarn@stable --activate
```

#### 3. 启动前端开发环境

```bash
cd ui
yarn
yarn dev
```

如果后端不在 `127.0.0.1:8855`，请修改 `ui/vite.config.js` 里的代理配置。

### TUI 开发

`tui/` 是终端 UI，适用于无法方便访问网页的环境。

```bash
cd tui
yarn
```

开发模式：

```bash
yarn dev
```

构建并启动：

```bash
yarn build
yarn start
```

### codex-runner 开发

`codex-runner/` 负责与本地 Codex 运行时衔接。

```bash
cd codex-runner
yarn
yarn build
```

---

## 🧭 新 Skill / Node 的开发约定

旧文档中关于“如何定义 Agent”的部分已经不再适用，当前统一替换为 Node / Skill 开发约定。推荐按下面顺序理解一个新节点的开发逻辑：

### 1. 推荐目录结构

后续新增能力时，建议按下面结构组织：

```text
loopai/skills/<SkillName>/
├── __init__.py
├── runner.py
├── cli.py              # 如有必要再加
├── utils/
├── nodes/              # 如有复杂节点逻辑
└── ...

skills/<SkillName>/SKILL.md
```

约定说明：

1. `loopai/skills/<SkillName>/__init__.py` 作为统一入口。
2. `runner.py` 一般作为主要执行入口。
3. 如果需要独立命令行调用，可以增加 `cli.py`。
4. 如果新增了 CLI，记得同步更新 [setup.py](/home/lpc/repos/Dataflow-LoopAI/setup.py) 的 `entry_points`。
5. 实际供系统消费的技能定义放在根目录 `skills/<SkillName>/SKILL.md`。

当前 `setup.py` 中已经存在的 `console_scripts` 示例：

```python
entry_points={
    "console_scripts": [
        "loopai-obtainercli=loopai.skills.ObtainerCLI.cli:main",
        "loopai-judger=loopai.skills.Judger.cli:main",
        "loopai-analyzer=loopai.skills.Analyzer.cli:main",
    ],
}
```

### 2. 入口函数与运行时参数约定

Node 开发里一个很重要的约定是：`task_id`、`DB_PATH` 等运行时参数应由**入口函数主动从环境变量读取**；如果缺失，入口函数需要**立即报错并退出**，不要静默继续执行。

推荐做法：

1. 入口函数先读取环境变量，例如 `TASK_ID`、`DB_PATH`。
2. 如果缺失必填项，直接抛错或通过统一异常结构返回失败。
3. 当存在 `task_id` 时，优先通过配置接口读取数据库中的运行态配置。
4. 参数校验、数据库访问、缺参报错，最好统一收敛到一层接口里处理。

### 3. State 继承与运行时注入

Sub-Agent / Node 一般继承 `LoopAIState` 中对应的子状态，例如 `JudgerState`。

运行时注入参数示例：

```python
import os

from loopai.skills.Configer import (
    get_configer_task_state_config,
    update_configer_task_state_config,
)

# 入口函数中应主动读取环境变量
DB_PATH = os.environ.get("DB_PATH")
TASK_ID = os.environ.get("TASK_ID")

if not DB_PATH:
    raise ValueError("missing required env: DB_PATH")
if not TASK_ID:
    raise ValueError("missing required env: TASK_ID")

# 读取当前任务的 judger 运行态配置
judger_cfg = get_configer_task_state_config(
    section_name="judger",
)

if not judger_cfg.get("ok"):
    raise ValueError(judger_cfg.get("message", "failed to load judger config"))

judger_config = judger_cfg["data"]["config"]
print(judger_config)

eval_api_key = judger_config.get("eval_api_key", {}).get("value")
eval_temperature = judger_config.get("eval_temperature", {}).get("value")

if not eval_api_key:
    raise ValueError("missing required config: judger.eval_api_key")
if eval_temperature is None:
    raise ValueError("missing required config: judger.eval_temperature")

# 更新当前任务的 judger 运行态配置
update_result = update_configer_task_state_config(
    "judger",
    {
        "eval_api_key": {"value": "xxx", "type": "str"},
        "eval_temperature": 0.2,
    },
)

if not update_result.get("ok"):
    raise ValueError(update_result.get("message", "failed to update judger config"))

print(update_result["data"]["config"])
```

### 4. 成功 / 失败返回格式

建议统一使用下面的返回结构。

成功：

```json
{
  "ok": true,
  "status": "completed",
  "message": "Sub-agent completed.",
  "data": {},
  "error": null
}
```

失败：

```json
{
  "ok": false,
  "status": "failed",
  "message": "Sub-agent crashed with an unhandled exception.",
  "data": null,
  "error": {
    "type": "RuntimeError",
    "code": "UNHANDLED_EXCEPTION",
    "detail": "vector index not found",
    "traceback": "...",
    "recoverable": true,
    "time": "2026-06-01T00:00:00Z"
  }
}
```

建议统一通过 `emit_error` / `emit_success` 输出：

```python
from loopai.common.exception import emit_error, emit_success, ErrorCode

try:
    raise ValueError("missing codex_api_key")
except Exception as e:
    emit_error(
        e,
        code=ErrorCode.CONFIG_ERROR,
        recoverable=True,
        message="Codex runtime config is incomplete.",
    )
```

### 5. 实时事件流与节点状态

实时事件流示例：

```python
from loopai.common.event_tool import StreamEvent, get_event_writer

writer = get_event_writer(name="judger", context_id="task_001")

writer(StreamEvent(
    current="judger",
    progress=0.2,
    message="loading dataset",
    data={"rows": 128},
))

writer(StreamEvent(
    current="judger",
    progress=1.0,
    message="finished",
))
```

建议在子节点一开始执行时就触发 `writer`，这样会把节点状态更新为 `running`。当运行结束或报错时，节点运行状态需要使用 `writer.set_failed` / `writer.set_completed` 手动告知。

当前这些方法已经封装进 `emit_error` 和 `emit_success`，因此执行过程中只需传入 `stream_writer`：

```python
from loopai.common.exception import emit_error, emit_success, ErrorCode
from loopai.common.event_tool import StreamEvent, get_event_writer

writer = get_event_writer(name="judger", context_id="task_001")

try:
    raise ValueError("missing codex_api_key")
except Exception as e:
    emit_error(
        e,
        code=ErrorCode.CONFIG_ERROR,
        recoverable=True,
        stream_writer=writer,
        message="Codex runtime config is incomplete.",
    )

emit_success(data={...}, stream_writer=writer)
```

### 6. 开发备注

1. `python examples/scripts/run_judger.py` 相关旧说明已经移除，不再作为当前推荐开发路径。
2. 新功能优先放在 `loopai/skills` 下，除非你明确是在维护 `loopai/agents` 里的历史模块。
3. 文档规范和实际项目结构如果有出入，始终以仓库当前代码为准。

---

## 📐 LoopAI Sub-Agent Skill 补充规范

### 1. 先决条件（Prerequisites / Input Contract）

定义执行该 sub-agent 的最小必要输入集合，用于保证任务可调度与可复现。

#### 1.1 必填参数（Required）

- `task_id`：任务唯一标识（用于 trace / retry / logging）
- `input`：核心输入数据（字符串 / JSON / 结构化对象）
- `context`：上下文信息（可选但推荐，如历史状态 / embedding / external memory）
- `config`：运行配置（如 model、temperature、top_k、timeout 等）
- `callback`：回调或结果写入方式（stream / webhook / queue）

#### 1.2 可选参数（Optional）

- `trace_id`：链路追踪 ID
- `priority`：任务优先级（用于 scheduler）
- `resource_limit`：资源限制（CPU / GPU / time / tokens）

#### 1.3 执行前校验（Pre-check）

- 参数完整性检查（required fields）
- schema validation（JSON schema / pydantic）
- 依赖资源可用性（index / model / db / cache）
- 权限校验（是否允许调用 external tool）

---

### 2. 错误体系（Error Model & Recovery Strategy）

统一 sub-agent 错误返回结构，并定义可恢复策略与建议处理方式。

#### 2.1 标准错误结构

```json
{
  "ok": false,
  "status": "failed | partial_failed | timeout",
  "message": "Human readable error summary",
  "data": null,
  "error": {
    "type": "RuntimeError | ValidationError | ResourceError | ExternalServiceError | TimeoutError",
    "code": "MACHINE_READABLE_CODE",
    "detail": "Specific failure context",
    "traceback": "...",
    "recoverable": true,
    "retry_after": 3,
    "time": "ISO-8601"
  }
}
```

#### 2.2 主要错误类型分类与处理建议

##### （1）ValidationError（输入非法）

- 典型原因：
  - 参数缺失
  - schema 不匹配
  - 类型错误
- 处理建议：
  - 返回字段级错误（field-level error）
  - 前端/上游修正输入
  - 不建议自动 retry

##### （2）RuntimeError（执行异常）

- 典型原因：
  - null pointer / undefined state
  - pipeline stage error
- 处理建议：
  - 自动 fallback（如降级模型 / 简化流程）
  - retry ≤ 2 次
  - 打印完整 trace

##### （3）ResourceError（资源不足）

- 典型原因：
  - vector index / model 未加载
  - GPU / memory 不足
- 处理建议：
  - 切换 backup resource
  - queue 等待重试
  - 触发 autoscaling（如有）

##### （4）ExternalServiceError（外部依赖失败）

- 典型原因：
  - embedding API failed
  - DB / vector DB unreachable
- 处理建议：
  - retry with exponential backoff
  - fallback cache
  - degrade to offline mode

##### （5）TimeoutError（超时）

- 典型原因：
  - 长链路推理
  - IO 卡住
- 处理建议：
  - checkpoint resume
  - reduce max_tokens / batch size
  - task split

---

### 3. 输出契约（Output Contract / Result Spec）

定义 sub-agent 成功执行后必须返回的结构，确保可被上层（trainer / orchestrator / analyzer）消费。

#### 3.1 标准输出结构

```json
{
  "ok": true,
  "status": "success",
  "result": {},
  "metrics": {},
  "artifacts": [],
  "logs": [],
  "trace_id": "",
  "time_cost_ms": 0
}
```

#### 3.2 `result`（核心结果）

不同 sub-agent 类型需定义各自 result。

**Trainer Agent / Node**

- `model`: 模型路径 / checkpoint
- `loss_curve`: training loss array
- `eval_metrics`: validation metrics
- 用途：
  - checkpoint selection
  - early stopping
  - model ranking

**Judger / Evaluator Agent / Node**

- `pass_at_k`
- `accuracy / f1 / reward_score`
- `ranking_scores`
- 用途：
  - model selection
  - RL reward shaping
  - benchmark report

**Analyzer Agent / Node**

- `insights`: 结构化分析结果
- `clusters / topics`
- `error_patterns`
- 用途：
  - 数据清洗
  - failure diagnosis
  - dataset iteration

**Tool / Executor Agent / Node**

- `execution_result`
- `side_effects`
- `output_files`
- 用途：
  - pipeline chaining
  - artifact storage

#### 3.3 `metrics`（过程指标）

统一用于监控与调优：

- `latency_ms`
- `token_usage`
- `memory_peak`
- `gpu_utilization`
- `retry_count`

#### 3.4 `artifacts`（产物）

- 模型文件
- index / embedding store
- jsonl / dataset dump
- report / visualization

---

## 🔌 Codex 接入补充

### 1. 模型与代理方式

#### 使用 DeepSeek API（推荐）

**方式一：Rust 代理脚本**

先安装 Rust：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

然后启动代理：

```bash
LOOPAI_CODEX_PROXY_UPSTREAM_API_KEY=<YOUR_API_KEY> ./scripts/start_codex_deepseek_proxy.sh
```

**方式二：基于 model pool 的后端原生代理**

项目已经支持基于 model pool 的后端原生转发。启动前需要先在 `starter.yaml` 中配置模型池与代理地址；如果数据库已初始化且旧配置已经落库，通常需要删除数据库后重新初始化，或在 WebUI 中重新更新对应配置。

```yaml
system:
  api_port: 8855

model:
  proxy_base_url: "http://127.0.0.1:8855/responseProxy/v1"
  proxy_api_key: "loopai-local-proxy"
  default_model: "default"
  codex_model: "default"
  looper_model: "default"
  default_tier: "medium"
  pool:
    - tier: "medium"
      name: "default"
      api_key: "<YOUR_DEEPSEEK_API_KEY>"
      base_url: "https://api.deepseek.com"
      model_name: "deepseek-v4-flash"
      maxworker: 1
      wire_api: "chat"
      response_format: ""
      enabled: true
```

然后在 WebUI 中把 Codex / Starter 请求地址配置为：

```text
http://127.0.0.1:8855/responseProxy/v1
```

这里真正的上游供应商地址应放在 `model.pool[*].base_url` 中，例如 `https://api.deepseek.com`，而不是再单独写一个旧的 `codex_chat_proxy_url`。

### 2. 启动顺序

完成 DeepSeek 转发或其它 Codex 接入后，另起终端启动后端：

```bash
python api/start.py
```

然后在 WebUI 中配置对应请求地址，点击 `Update` 即可。

---
