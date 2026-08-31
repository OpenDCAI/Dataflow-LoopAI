# Analyzer Agent 详细指南

> Dataflow-LoopAI v2  

`Analyzer` 负责读取已经完成的评测结果，进一步解释模型为什么失败、失败集中在哪些能力，以及下一轮应该补什么数据。它不重新生成被测模型回答，而是位于“评测之后、数据动作之前”的诊断与决策层。

当前 Analyzer 支持四类任务：

- `code`
- `text2sql`
- `general_text`
- `math`

相比“模型得了多少分”，Analyzer 更关注：

> 模型为什么失败、失败在哪里、哪些缺陷值得优先投入训练数据，以及下一轮如何验证改进是否有效。

## 1. 核心职责

- 读取 `Judger` 评测结果；
- 统计通过率、失败阶段、错误标签和 metric；
- 分析失败样例并归纳错误模式；
- 将可验证证据归入领域专属能力桶；
- 生成 summary、report、final report 和优化建议；
- 结合历史评测记录生成对比分析；
- 为下一轮数据获取、构造和训练生成分桶与比例建议。

在完整闭环中的位置为：

```text
Judger 执行与评分
        ↓
Analyzer 诊断、分桶与数据决策
        ↓
Obtainer / Constructor / Trainer
```

## 2. v2 架构定位

v2 将技能说明和 Python 实现分开维护：

| 位置 | 职责 |
| --- | --- |
| `skills/Analyzer/SKILL.md` | 给 Codex/Agent 阅读的能力说明与调用契约 |
| `loopai/skills/Analyzer/` | Analyzer 的 Python 入口、运行控制、节点、指标、分桶和报告逻辑 |
| `loopai/agents/Analyzer` | v2 不再依赖旧 agents 目录，不作为 Analyzer 主入口 |

Codex、WebUI 和 Python 调用方应直接使用 `loopai.skills.Analyzer`。人工调试可以使用 CLI，但系统编排不应通过拼接调试脚本命令来间接调用 Analyzer。

Analyzer 的 MCP 暴露目前保持关闭，暂不注册 `analyzer_run` 或 `analyzer_load_events`。

## 3. 输入与预检查

### 3.1 最小输入

| 字段 | 用途 | 说明 |
| --- | --- | --- |
| `task_id` | 任务追踪、状态同步和产物归档 | CLI 的 `--thread-id` 优先，其次为 `TASK_ID` |
| `analyzer.eval_result_path` | 当前评测结果 | 支持单一路径，也支持同任务类型的路径列表 |
| `analyzer.analyze_task_type` | 选择分析路线 | 常用值为 `code`、`text2sql`、`general_text`、`math` |
| `output_dir` | 报告和状态根目录 | 未指定时默认使用 `./outputs` |
| 模型运行配置 | 样本判因和报告生成 | 应优先从 system runtime 或环境变量注入 |

现有 state 结构保持兼容：

```json
{
  "task_id": "task-001",
  "output_dir": "./outputs",
  "eval": {},
  "analyzer": {
    "analyze_task_type": "math",
    "eval_result_path": "./outputs/math_result.jsonl",
    "baseline_result_path": "./outputs/math_previous.jsonl"
  }
}
```

### 3.2 运行时配置

常用环境变量包括：

- `ANALYZER_API_KEY`
- `ANALYZER_MODEL`
- `ANALYZER_BASE_URL`
- `TASK_ID`
- `DB_PATH`
- `ANALYZER_CHECKPOINT_PATH`
- `ANALYZER_VERSION_ID` / `VERSION_ID`
- `ANALYZER_REQUEST_TIMEOUT_SECONDS`

当前主要优先级为：

```text
显式 kwargs > system runtime / model pool > env > legacy state
```

旧字段 `analyzer.analyze_api_key` 仅作为兼容回退。配置解析后，密钥不会继续保存在可视化 Analyzer state 中；stdout、StreamEvent 和 `--print-result` 也会对 `api_key`、`token`、`*_key` 等字段脱敏。

### 3.3 执行前检查

- result 文件存在且可读；
- result 的任务类型与 Analyzer 路线一致；
- prediction、reference、执行结果或 metric 字段可以完成映射；
- 需要 LLM 时，模型、base URL 和 API key 可用；
- 需要 WebUI 状态同步时，`DB_PATH` 和 `TASK_ID` 指向真实任务。

## 4. 四类任务与两条流水线

Analyzer v2 根据任务类型选择两类分析链，而不是让四类任务共享一套判因规则。

### 4.1 Code / Text2SQL：执行证据链

```text
eval_model -> analyze_result -> draw_conclusion -> finish
```

这条链读取 OJ、SQL 执行、parser 和日志证据，先完成失败样本判因，再生成整体结论和数据建议。

### 4.2 General Text / Math：Metric 分析链

```text
metric_recommend -> metric_score -> analyze_metric_report -> finish
```

General Text 和 Math 都先选择合适的 metric，再完成样本/整体评分与报告分析，但两者使用独立能力分桶。Math 不会被归入 General Text 的通用文本能力桶。

## 5. 不同任务下重点分析什么

### 5.1 Code

Code 路线优先读取编译、运行、断言、stdout/stderr 和 completion 等证据。

| 能力桶 | 典型问题 | 推荐数据方向 |
| --- | --- | --- |
| 代码补全输出契约 | 输出解释、Markdown 围栏、缺少完整函数 | 函数签名或 Docstring 到纯可执行代码的严格格式样本 |
| Python 语法与补全完整性 | 缩进、括号、字符串、`return` 或函数体不完整 | 短 Python 语法修复和代码补全样本 |
| 函数接口、作用域与依赖 | 函数名/签名错误、变量未定义、缺少导入 | 接口保持、局部作用域和标准库样本 |
| 语义逻辑与断言 | 代码可执行但结果错误 | 带正常断言和反例断言的短算法样本 |
| 边界条件与鲁棒性 | 空输入、单元素、重复值或极值处理错误 | 边界条件与对比样本 |
| 运行时与效率 | 超时、递归过深或复杂度退化 | 低效与高效实现的成对优化样本 |

当前置输出契约问题遮蔽大量后续能力时，Analyzer 会给语法、接口和语义能力保留少量探索预算。

### 5.2 Text2SQL

Text2SQL 同样依赖可执行证据，但归因对象变为 SQL 结构、Schema 和查询语义。SQL 可执行不代表结果正确。

| 能力桶 | 典型问题 | 推荐数据方向 |
| --- | --- | --- |
| SQL 输出契约 | 输出解释、Markdown 或不可直接执行的 SQL | 问题与 Schema 到纯 SQL 的严格输出样本 |
| SQL 语法与结构 | `SELECT`、`JOIN`、聚合或子查询结构错误 | 短 SQL 修复与结构组合样本 |
| Schema Linking | 表、列、实体或外键映射错误 | 问题实体与 Schema 显式对齐样本 |
| SQL 语义与结果正确性 | 可执行但过滤、聚合、排序或去重错误 | 可执行但结果错误的查询纠正样本 |
| 类型、值与条件表达 | 日期、`NULL`、字符串或类型转换错误 | 值匹配、条件表达和类型边界样本 |
| SQL 运行时与效率 | 查询超时或不必要的高复杂度 | 等价 SQL 的低效与高效实现对比样本 |

### 5.3 General Text

General Text 先根据 bench 和 eval type 推荐 metric，再根据结构化 evaluator 标签、评分理由和可验证规则进行能力归因。

主要能力桶包括：

- 指令与输出格式遵循；
- 相关性与意图理解；
- 事实性与知识依据；
- 推理与一致性；
- 完整性与要点覆盖；
- 表达与语言质量；
- 安全与拒答边界。

普通 exact-match 失败不会被强行猜测为事实性或推理错误。证据不足的样本进入待诊断池。

General Text 采用“能力主桶 + 任务领域”的两级统计。例如写作、摘要、知识问答和对话属于领域分布，不直接等同于能力缺陷。

### 5.4 Math

Math 走 Metric 分析链，而不是 Code/Text2SQL 的 OJ 解析链。数学结果首先需要处理数值、符号表达式或选项的等价性，再结合步骤级错误证据判断具体能力缺陷。

#### 指标选择

- 数值题优先使用 `numerical_match`；
- 符号题优先使用 `math_verify`；
- 选择题使用 `choice_accuracy`；
- `extraction_rate` 用于诊断最终答案能否被可靠提取。

#### Math 能力主桶

| 能力主桶 | 典型问题 | 推荐数据方向 |
| --- | --- | --- |
| 答案提取与格式遵循 | 空答案、boxed/选项/多问格式错误、答案无法提取 | 完整过程加可验证最终答案格式 |
| 基础计算与数值精度 | 分数、小数、比例、符号、单位或代入计算错误 | 短步骤计算纠错和显式验算 |
| 代数与符号变换 | 展开、化简、因式分解、方程求解或等价变换错误 | 逐步等价变换与符号验证 |
| 题意理解与数学建模 | 变量、方程、约束或目标建立错误 | 自然语言条件到数学表达的建模对比 |
| 解题策略与定理选择 | 方法、公式、概念或定理适用条件错误 | 同题多策略和错误路线修正 |
| 多步推理与过程一致性 | 无效推断、前后矛盾、答案与过程不符 | 步骤级验证、反例检查和过程一致性样本 |
| 验证、约束与完整性 | 定义域、边界、增根漏解、证明未闭合或多问漏答 | 回代验证、约束检查和完整证明样本 |

Math 采用两级结构：

1. 上述能力主桶决定训练数据分配；
2. 代数、几何、概率统计、微积分、数论、组合数学和算术作为 `domain_breakdown`，用于在能力桶内部选择数据来源。

最终答案不匹配只能确认样本失败，不能证明失败来自计算、建模或推理。没有可靠步骤级证据、也无法确认答案提取失败的样本进入 `diagnostic_unknown`，训练预算为 0。

## 6. 数据分桶与训练比例

Analyzer 不再把“错误占比”直接当作“训练数据占比”。首轮有效能力桶的基础权重为：

```text
weight_i = error_share_i^alpha
           * confidence_i
           * severity_i
           * transfer_i
           * learnability_i
           / data_cost_i
```

其中：

- `error_share`：该能力缺陷被观察到的频率；
- `confidence`：错误归因证据的可信度；
- `severity`：该缺陷对任务成功的影响；
- `transfer`：修复后能否迁移到更多样本或子任务；
- `learnability`：少量数据能否较快改善的先验；
- `data_cost`：样本获取、标注和训练成本。

当前默认 `alpha=1.0`，有效桶通常设置 5% 的下限和 50% 的上限，避免单一错误完全挤占训练数据。

### `other` 的处理

1. 先根据执行日志、parser 结果、结构化标签和评分理由重新分类；
2. 仍无法可靠归因的样本进入诊断队列；
3. 诊断队列不参与训练预算，避免不可操作的 `other` 挤占数据。

### Math 分配示例

下表仅用于解释机制，不是固定配方：

| 能力 | 观察错误占比 | 建议训练占比 | 调整原因 |
| --- | ---: | ---: | --- |
| 答案提取与格式 | 24% | 18% | 重要但通常少量严格格式样本即可改善 |
| 基础计算 | 22% | 26% | 高频、可学习且迁移面较广 |
| 代数与符号变换 | 15% | 21% | 对多类数学任务具有较高迁移价值 |
| 数学建模 | 12% | 15% | 难度较高，但对复杂题成功率影响大 |
| 多步推理 | 10% | 12% | 需要连续推导和步骤校验样本 |
| 验证与完整性 | 7% | 8% | 用于减少漏解、增根和未闭合证明 |
| 待诊断 `other` | 10% | 0% | 证据不足，不直接进入训练数据 |

首轮后应通过小规模试训测量“目标指标增量 / 新增样本数”，再更新下一轮 `learnability` 和分配比例。

最终计划写入：

```text
state["analyzer"]["allocation_plan"]
```

## 7. 历史评测对比

设置 `baseline_result_path` 后，Analyzer 会比较当前 `eval_result_path` 和历史评测，生成 `historical_comparison`，并在 report/final_report 中加入 `Historical Comparison` 小节。

```json
{
  "has_baseline": true,
  "baseline_result_path": "previous.jsonl",
  "current_result_path": "current.jsonl",
  "metric_diff": {},
  "score_distribution_diff": {},
  "error_distribution_diff": {},
  "improved_cases": [],
  "regressed_cases": [],
  "comparison_summary": "..."
}
```

case 匹配优先级：

```text
sample_id > task_id > id > 行号
```

baseline 缺失、不可读或字段不完整时只产生 warning，不中断主流程。

## 8. 多 Bench 分析

Analyzer 可以合并两个及以上同任务类型的 Judger 结果：

- 从 `judger.bench_result` 和 `judger.extra_bench_result` 聚合；
- 或向 `analyzer.eval_result_path` 传入路径列表。

```json
{
  "analyzer": {
    "analyze_task_type": "code",
    "eval_result_path": ["humaneval.jsonl", "mbpp.jsonl"]
  }
}
```

`summary["bench_summaries"]` 会保留每个 bench 的样本量、通过率和失败分布。

Code、Text2SQL、General Text 和 Math 的证据与分桶规则不同，因此多 Bench 合并仅适用于同一任务类型，不应跨路线混合。

## 9. 输出与产物

### 9.1 版本化目录

```text
<output_dir>/<task_id>/analyzer/<version_id>/
├── analyzer.pkl
├── state_checkpoint.sqlite
├── summary_*.json / summary_*.txt
├── report_*.json / report_*.txt
├── final_report_*.json / final_report_*.txt
└── final_report_*.suggestions.txt
```

具体文件会随任务路线变化。Code/Text2SQL 还可能生成增强后的失败记录；Metric 路线会保存指标明细、分析报告和数据计划。

### 9.2 核心输出

- `insights`：模型当前主要问题和结构化结论；
- `error_patterns`：错误模式、证据和分布；
- `allocation_plan`：下一轮训练数据分桶与建议比例；
- `historical_comparison`：当前版本相对历史版本的改善与退化；
- `artifacts`：summary、report、final report 和建议文件路径。

### 9.3 报告生成耗时参考

耗时主要取决于失败样本数、batch size、模型端点速度、网络和 Prompt 长度。以下仅用于排期，不是固定 SLA：

| 规模 | 样本判因与统计 | 报告生成 | 总耗时参考 |
| --- | --- | --- | --- |
| 约 80 条 | 约 40 秒至 2 分钟 | 约 30 至 120 秒 | 约 1 至 4 分钟 |
| 约 500 条 | 约 4 至 12 分钟 | 约 1 至 3 分钟 | 约 5 至 15 分钟 |
| 约 1500 条 | 约 12 至 35 分钟 | 约 1 至 5 分钟 | 约 15 至 40 分钟 |

默认模型请求 timeout 为 300 秒。首次请求保留完整证据；发生 timeout/524 时，Analyzer 记录耗时和 Prompt 长度，并用压缩后的代表证据重试一次。第三方代理仍可能有更短的网关限制。

## 10. Codex、Python 与 CLI 调用

### 10.1 Codex / Sub-Agent 入口

```python
from loopai.skills.Analyzer import run, resume_run

run(state=state)
resume_run(state=state)
```

`run(...)` 是标准 Skill 入口，默认会检查同一任务是否存在未完成版本；`resume_run(...)` 明确要求续跑。

进程内直接获取最终 state：

```python
from loopai.skills.Analyzer.runner import run_analyzer_standalone

result = run_analyzer_standalone(
    state,
    thread_id="task-001",
    resume=False,
    from_node=None,
    baseline_result_path=None,
)
```

### 10.2 CLI

安装后可使用：

```bash
loopai-analyzer \
  --config-path /tmp/analyzer_demo.json \
  --thread-id task-001 \
  --baseline-result-path /tmp/previous.jsonl \
  --print-result
```

人工调试脚本：

```bash
python examples/scripts/run_analyzer_standalone.py \
  --config-path /tmp/analyzer_demo.json \
  --thread-id task-001 \
  --print-result
```

两种入口共同支持：

- `--resume`
- `--from-node`
- `--checkpoint-path`
- `--baseline-result-path`
- `--request-timeout-seconds`
- `--new-version`
- `--list-nodes`

人工调试脚本额外支持 `--stream-stdout`。

## 11. State、Configer 与断点恢复

Analyzer 同时维护两类不同用途的状态：

| 状态层 | 用途 | 关键点 |
| --- | --- | --- |
| Configer / DB | 系统任务运行态，供 WebUI、Starter 和其他 Sub-Agent 查询 | 有 `DB_PATH` 和真实 `TASK_ID` 时读取与更新 |
| SQLite checkpoint | Analyzer 长任务的细粒度恢复状态 | 按 `(task_id, version_id)` 隔离，不能替代系统数据库 |

运行 state 保留：

```text
state["current"]
state["last_completed"]
```

恢复规则：

- 默认运行前检查同一 task 是否存在未完成 version；
- 存在未完成 version 时继续该 version，不自动创建新 version；
- `new_version=True` 或 `--new-version` 才明确开启新一轮；
- `--resume` 读取匹配 version 的 checkpoint，跳过已完成节点和已提交 batch；
- `--from-node` 用于人工强制从指定节点开始；
- 已完成 version 不会阻止同一 task 创建下一 version。

已提交的节点或 batch 可以跳过，但一个尚未返回的外部 LLM 请求无法从 token 中间恢复，只能从该请求重新发起。真正的细粒度恢复依赖分批提交 checkpoint，而不是仅依赖进度条估算。

## 12. StreamEvent 与统一异常契约

Analyzer 直接使用公共事件接口：

```python
from loopai.common.event_tool import StreamEvent, get_event_writer

writer = get_event_writer(
    name="analyzer",
    context_id=task_id,
    version_id=version_id,
)

writer(StreamEvent(
    current="analyzer.analyze_result",
    progress=0.63,
    message="正在归纳失败样本",
    data={"completed_batches": 4, "total_batches": 8},
))
```

常见 `current` 包括：

- `analyzer.initializing`
- `analyzer.pipeline`
- `analyzer.eval_model`
- `analyzer.analyze_result`
- `analyzer.draw_conclusion`
- `analyzer.metric_recommend`
- `analyzer.metric_score`
- `analyzer.analyze_metric_report`
- `analyzer.completed`
- `analyzer.failed`

事件写入当前版本目录的 `analyzer.pkl`，也可以通过 `--stream-stdout` 输出 JSONL。事件数据必须可序列化并自动脱敏。

成功与失败都必须传入同一个 writer：

```python
emit_success(
    data=result,
    message="Analyzer pipeline completed.",
    stream_writer=writer,
)

emit_error(
    error,
    code=ErrorCode.CONFIG_ERROR,
    recoverable=True,
    stream_writer=writer,
    message="Analyzer runtime configuration is incomplete.",
)
```

这样既能返回统一 payload，也能将 WebUI 节点状态标记为 completed 或 failed。

## 13. 使用检查清单

- result 路径是否指向完整 Judger 输出，而不是旧样例或失败子集；
- `analyze_task_type` 是否与 bench 一致；
- Math 是否进入独立 Math 分桶，而不是 General Text 分桶；
- 多个 bench 是否属于同一任务路线；
- API key 是否来自 system runtime/环境变量，且未出现在 state、事件或报告中；
- `task_id`、`version_id`、writer 和 checkpoint 是否使用同一运行身份；
- 续跑时是否复用未完成 version，而不是误开新 version；
- 报告中的 `other` 是否进入诊断池，而不是直接获得训练预算；
- 分桶比例是否经过小规模试训收益校准，而不是长期固定使用首轮先验。

## 14. 参考依据

当前说明以 Dataflow-LoopAI `dev/v2` 的 Skill 架构和 Analyzer v2 实现为准。

分桶设计借鉴以下工作中的多维评估、能力依赖、数据混合和过程监督思想，但不宣称完整复现论文算法：

- HELM（TMLR 2023）
- InstructGPT（NeurIPS 2022）
- TruthfulQA（ACL 2022）
- Skill-It!（NeurIPS 2023）
- DoReMi（NeurIPS 2023）
- LESS（ICML 2024）
- MATH（NeurIPS 2021）
- Let's Verify Step by Step（ICLR 2024）
