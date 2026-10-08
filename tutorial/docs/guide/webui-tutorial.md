# WebUI 教程

这一页按最新 README 的运行方式介绍如何通过 WebUI 使用 LoopAI。WebUI 由 `api/start.py` 启动的后端直接提供，适合配置资源、创建任务、通过对话驱动闭环，并查看各 node 的运行状态。

## 顺序

推荐顺序是：

1. 安装 LoopAI、Codex 和 `codex-runner`
2. 准备 `starter.yaml`，配置可用的模型池
3. 下载已发布的 WebUI 前端并启动后端
4. 在 WebUI 的 Config 和资源池中补齐运行所需的配置
5. 创建任务，通过对话让 Starter 调度 node
6. 使用状态、事件和产物检查执行结果

## 第一步：安装运行环境

在仓库根目录安装核心运行时：

```bash
conda create -n loopai python=3.12
conda activate loopai

pip install uv
uv pip install -e .
```

Starter 通过 `codex-sdk` 工作，因此还需要已登录的 `codex` 命令。macOS / Linux 可以使用：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex
```

首次运行按提示登录后，安装并检查 `codex-runner`：

```bash
cd codex-runner
yarn
yarn build
cd ..
```

Windows、Homebrew 和 npm 的替代安装方式见根目录 `README.md`；完整安装说明见[快速开始](/guide/quick-start)。

## 第二步：准备 `starter.yaml`

在启动 WebUI 之前，先在仓库根目录放好 `starter.yaml`：

```bash
cp examples/config/starter.yaml ./starter.yaml
```

编辑复制出的模板，在模型配置区的 `pool` 中配置至少一个可用模型，并使 `default_model`、`codex_model` 与 `looper_model` 引用相应的 pool 名称。还应确认：

```yaml
system:
  api_port: 8855
  codex_workspace: "<项目根目录>"
  codex_home: "<项目根目录>/codex_home"
```

Tavily、Kaggle 等可选凭据应通过模板中的 `env:变量名` 引用环境变量；不要把真实密钥提交到仓库。

## 第三步：下载前端并启动 WebUI

如果还没有准备前端发布产物，先执行：

```bash
python scripts/download_ui_release.py
```

然后启动后端：

```bash
python api/start.py
```

默认地址：

```text
http://localhost:8855
```

API 文档位于 `http://localhost:8855/docs`。后端会直接服务 `api/dist`，因此正常使用 WebUI 时不需要启动前端开发服务器；若自动下载失败，请手动下载前端 dist 压缩包并解压至 `api/dist`。

![LoopAI WebUI 主界面：任务、流程图、对话与运行信息](/images/webui/UI.png)

## 第四步：先配置全局参数

第一次上手时，不建议一打开界面就立刻执行评测或分析。更稳妥的顺序，是先去 UI 里的 `Config` 面板把全局参数补齐，再开始真正的任务流。

这样做的原因很简单：

- 后续很多 node 都依赖这些全局参数
- 如果基础配置没补齐，系统很快就会跳去 Configer node 让你补字段
- 先把全局参数整理好，后面的操作会顺很多

这里有两个非常重要的使用规则：

### 保存配置

在 `Config` 里填完全局参数之后，需要点击右上角的 `Update`，否则配置不会正式写回当前任务上下文。正在执行的任务不应假设会即时读取新值；修改关键模型、路径或运行时参数后，建议在下一次任务或明确的后续步骤中使用。

另外，每个参数项右上角通常还有一个 `?`，里面会给出该参数的说明和使用提示。第一次配置时，建议优先点开查看，能减少很多试错成本。

![全局配置参数面板](/images/webui/s3.png)

## 第五步：使用资源池管理维护路径类参数

针对模型、数据集、配置文件等路径类信息，推荐优先使用“资源池管理”。

这样做的好处是：

- 后续配置路径时可以快速选择
- 减少手工输入路径出错
- 便于统一管理模型和数据资源

这一步不是强制的，但非常建议在正式开始任务前先整理好。

先在资源池中选择或新增预设的模型、数据集和配置文件路径：

![选择预设资源路径](/images/webui/s4.1.png)

资源池统一维护可复用的路径类资源，供后续 node 配置时选择：

![资源池管理](/images/webui/s4.2.png)

## 第六步：创建任务并进入任务面板

完成全局参数和资源池整理之后，再打开任务面板，创建一个新任务，然后点击“运行”。

这时可以把任务理解成“已经具备了进入执行流的基础条件”，接下来再开始通过对话驱动具体 node，会比一上来就边跑边补配置更清晰。

点击启动后可以稍作等待。通常要等到节点上开始出现状态字段，才表示这次任务启动成功，系统已经进入可继续交互的状态。

![创建并启动任务](/images/webui/s5.1.png)

### 启动后通过 States 修改参数

任务启动后，也可以在执行面板的 **States** 中直接编辑可修改字段并保存。这适合在已经确定任务上下文后微调某个 node 的参数；修改是否立即被当前步骤读取取决于该 node 的执行边界，因此关键参数修改后应观察后续状态、事件和产物是否已使用新值。

![在执行面板的 States 中修改参数](/images/webui/revise_states.png)

## 第七步：通过对话驱动流程

完成全局参数和资源池整理之后，可以选择手动对话驱动 LoopAI，或让 Looper 自动推进后续流程。

这时任务启动后，首先真正与你交互和调度流程的是 Starter node。它是整个系统的编排 node，主要负责：

- 与你对话
- 理解当前任务意图
- 决定接下来要跳转到哪个 node
- 在不同 node 之间衔接流程

你已经先配好了全局条件，Starter 随后会把任务送往具体执行 node。

### 手动对话

未由 Looper 接管时，你可以继续通过对话提供目标、确认选择或修改配置。例如：

```text
请修改 judger 的 eval_task_type 为 code
```

Starter 会根据当前任务状态尝试更新该配置，并在需要时要求你补充无法推断的值。

### Looper 如何保持流程连续

Looper node 位于用户对话和 Starter 之间：它维护近期上下文，并在被启用后自动推进已由任务目标和已有状态确定的后续步骤。Looper 接管期间，用户通常不需要逐轮输入；评测、分析、数据处理与训练仍由相应的 node / skill 执行。

只有遇到无法从当前状态安全推断、必须由用户决定的事项时，例如模型或数据路径的选择、凭据、预算或高影响训练配置，流程才会请求你确认或补充信息。

<video controls preload="metadata" style="width: 100%; max-width: 960px">
  <source src="https://github.com/OpenDCAI/Dataflow-LoopAI/releases/download/res-v1.0.0/Looper.mp4" type="video/mp4" />
  你的浏览器不支持视频播放；请[下载 Looper 演示视频](https://github.com/OpenDCAI/Dataflow-LoopAI/releases/download/res-v1.0.0/Looper.mp4)。
</video>

## 如何判断当前正在谁的节点里

- 查看 flow 图中正在执行或刚完成的 node
- 查看该 node 的 State 与 Custom Info 中是否产生新的状态、进度或产物路径
- 结合对话中的工具事件与回复判断下一步需要补充的信息


## 如果缺参数，LoopAI 会怎么处理

Starter 会依据当前任务状态和你的对话补齐可推断的信息；无法安全推断的模型路径、数据路径、凭据或训练参数会要求你确认或补充。先在 Config 与资源池中准备常用资源，能显著减少执行中断。

这也是为什么 Starter 更像主管，而不是固定执行器。

## 第八步：观察节点状态、消息与结果

执行某个 node 任务时，右侧 `Custom Info` 往往会更接近实时信息源，你可以在那里看到：

- 当前进度
- 实时消息
- 执行中的关键状态

而左侧 `States` 并不一定实时更新。很多字段会在该阶段执行完成后才集中刷新。

对于路径类更新字段，如果界面支持点击预览，可以直接点击查看对应内容。

![查看运行详情：Custom Info、States 与产物信息](/images/webui/s7.png)

## 示例一：启动评测与分析

### 1. 创建任务并点击运行

运行后可以看到 state 已经加载完成。

### 2. 发起评测任务

如果 `Judger` 所需的模型、评测集或推理服务尚未配置，不需要立刻退出页面手动修改文件。说明你的评测目标，Starter 会利用当前状态与对话收集缺失信息；无法推断的路径和资源仍需要你确认。

### 3. 执行评测

参数齐全后，就可以开始评测。

在 `judger.eval_base_url` 为空且已提供 vLLM 环境时，Judger 可以拉起本地 OpenAI-compatible vLLM 服务；若已运行兼容服务，则配置该地址即可复用。

评测过程中你可以查看：

- 左上角面板中的 GPU 使用情况
- 节点详情里的监控界面

### 4. 查看评测结果

评测完成后，在左侧面板中通常可以查看：

- 模型样例输出
- 对应评测结果

### 5. 进入分析阶段

接下来可以对话启动分析任务。

如果刚完成评测，Analyzer 可使用本任务的评测产物；若没有上游结果，则需要通过对话或配置提供可访问的评测结果路径。

### 6. 执行分析

分析阶段通常更适合使用更强的模型，因此你可能需要：

- 提前启动外部 vLLM 服务
- 或配置其他模型 API

分析完成后，系统会生成分析报告，你可以在界面中查看详细结果。

## 示例二：数据获取、后处理与训练

### 1. 启动 ObtainerCLI/DataMixer

基于分析报告，可以进一步使用 ObtainerCLI/DataMixer 做数据获取、入湖和训练数据导出。旧的 LangGraph Obtainer 实现已退役，不应再作为独立 node 调度。

DataMixer 是 WebUI 中的数据湖工作台：它以数据湖为中心展示数据状态、缓存、索引、评测集，以及 L1 原始数据、L2 预处理数据、L3 训练数据等分层产物。开始新任务前，先确认当前数据湖路径和状态；运行中可在同一工作台查看每层的记录数与处理进度。

![DataMixer 数据湖工作台：状态概览、分层数据与处理进度](/images/webui/datamixer.png)

ObtainerCLI 的托管 acquisition worker 会同时处理 hosted dataset 检索和垂直领域网页采集。获取完成后继续在同一个 ObtainerCLI/DataMixer 链路中执行：

- 数据清洗
- 去重与筛选
- 质量处理和格式映射
- recipe 规划和最终训练数据导出

目标是减少无效样本和潜在数据泄露风险。每次获取、处理和导出都应保留来源、许可证、manifest、snapshot 与 lineage；只有最终导出的数据路径和报告均有效时，才应将其交给 Trainer node。

### 2. 查看处理进度

ObtainerCLI worker 执行过程中，右侧聊天框通常会展示：

- 数据处理进度
- 数据获取与导出过程
- 结果概览

任务流程图中的 Obtainer 卡片会汇总数据湖状态、分层记录数、Web 采集子任务、DataFlow 处理进度和最终导出路径。优先根据具体阶段的状态、错误信息和产物路径判断结果，而不要只依据对话文本或进程是否结束。

![任务面板中的 Obtainer 状态卡片与会话进度](/images/webui/obtainer.png)

### 3. 启动 Trainer node

数据准备完成后，就可以执行 `Trainer` 做训练。

Trainer 支持 LLaMA-Factory SFT 与 verl GRPO。先选择对应的 `train_framework` / `train_stage`，并准备 LLaMA-Factory 或 verl 的仓库目录、环境、模型与数据。训练需要先生成并审批配置，再使用获批版本执行；完整字段、审批与多轮训练说明见 [Trainer node 详细指南](/guide/details/trainer-agent)。

### 4. 观察训练过程

训练期间，节点面板和产物路径通常会展示：

- 终端日志
- 训练状态曲线

便于监控训练进展。

训练完成后，可以查看详细训练日志，并基于训练产物开展下一轮评测。

## 一页总结

第一次用 WebUI 时，最重要的不是记住所有 node 的名字，而是记住这条使用节奏：

1. 确认 Starter、模型池和 WebUI 服务均可用
2. 在 Config 与资源池中准备模型、数据和运行时路径
3. 创建任务，用对话描述优化目标
4. 由 Starter 与 Looper 协调后续 node；补充无法推断的参数
5. 用 node 状态、Custom Info、对话事件与产物路径判断进度和结果
