# ObtainerCLI/DataMixer 详细指南

ObtainerCLI/DataMixer 是当前唯一可用的数据工作流。它负责从数据需求到最终训练数据的完整链路，不需要在中间切换到其他数据 Agent。

## 核心职责

Obtainer 不只是“下载数据”，而是训练数据进入 Trainer 前的治理边界。它需要把多源原始数据变成可解释、可复现、可发布的训练快照，并为每个决定留下证据：

- 根据 Analyzer 报告或用户目标确定来源、许可证、规模和能力覆盖；
- 将异构数据保留在数据湖中，逐层规范化而不破坏原始字段；
- 通过 DataMixer 完成去重、质量门、去污染、配平、采样和格式映射；
- 运行并评审 DataFlowAgent pipeline，确认字段保真、筛选比例和模型调用行为；
- 生成 manifest、snapshot、recipe、lineage 和导出报告，交给 Trainer 消费。

## 进入它之前通常要准备什么

启动前应把以下信息写入任务配置或运行目录，而不是只放在聊天上下文中：

| 输入 | 至少包含 |
| --- | --- |
| 数据目标 | 任务类型、目标能力、期望数据形态、规模或 token 预算 |
| 分析依据 | Analyzer 报告、失败样例、保留/排除条件 |
| 数据源 | 数据集/网页 URI、版本、许可证、抓取时间和访问凭据 |
| Benchmark | 名称、版本、样例、评测格式及污染 guard（如适用） |
| 配方 | recipe、bucket 维度、采样比例、随机种子、每源上限 |
| 运行环境 | lake/warehouse 路径、模型池、DataFlow serving、trial 行数 |

如果这些字段尚未确定，应先生成 `planned_only` 计划并补齐配置，不要直接导出一个看似完整但无法复现的数据集。

## 输入和输出

Obtainer 的输入可以是 L1/L2 原始或规范化记录，也可以是已存在的 L3 候选集；同时接收 benchmark 注册信息、recipe、`mix_plan` 和质量门配置。输出按用途分为三类：

1. **数据产物**：L4 发布数据、`manifest`、`snapshot`、`dataset card` 和稳定导出路径；
2. **过程证据**：采集 run、pipeline、每步 cache/funnel、拒绝原因、字段保真审计和 benchmark 命中记录；
3. **交接信息**：`final_report.json`、recipe fingerprint、lineage、trial/full-run 统计及 blocker 状态。

下游只应消费带版本的 L4 snapshot 和报告，不应从临时 worker 目录或未评审的 trial 文件取数。

## 执行流程

一次完整运行通常经过以下阶段，阶段之间以状态文件和版本化产物衔接：

```text
acquisition -> ingest/lake-load -> schema-normalization
  -> dedup/quality -> benchmark-registration/decontamination
  -> recipe/bucketing/multi-dimensional-sampling
  -> DataFlowAgent trial -> independent review (D1-D6)
  -> rework/review loop -> sft-export -> Trainer handoff
```

先用受限 trial 验证 schema、字段和 funnel，再用同一 recipe 执行全量；全量任务应由 chunked runner 分块运行并合并 manifest。每个阶段都要记录输入 fingerprint、代码/skill 版本和输出计数，避免“试跑通过后实际使用了另一套过滤逻辑”。

## 运行状态和失败处理

状态文件至少应区分 `running`、`completed`、`completed_with_errors` 和 `failed`。`completed` 也必须检查 blocker 为空、输出非零、manifest/snapshot 可读且 schema 校验通过。

- trial 输出为零、淘汰率异常或来源集中到单一数据集：暂停导出，查看 funnel 和拒绝样本；可将格式问题分流修复后重跑。
- DataFlow serving 超时或模型池不可用：保留失败 chunk、请求和重试记录，修复资源/timeout 后 `resume`，不要伪造完成状态。
- 评审返回 `rework`：按 `required_next_repairs` 修改 pipeline，重新 trial 和 D1-D6；只有 `release` 才能发布。
- 仅在输入已固定但外部资源暂不可用时使用 `planned_only`，并明确标注没有运行证据。

失败运行的日志、评审 JSON 和中间快照应保留，便于定位是采集、规范化、算子还是导出阶段出错。

## 使用时最该关注什么

- 抽查真实记录，不要只看 schema 或总条数；确认问题、答案、对话角色和来源字段没有错位。
- 对照 funnel 检查淘汰率、来源/能力分布和修复分支，防止配平变成隐式删数。
- 查看 benchmark contamination 命中样本和 guard 版本，确认去污染没有误删目标能力。
- 检查 evaluator 输入包装与训练字段分离，避免把评测答案或提示泄漏进生成算子。
- 确认 full run 使用已评审 pipeline、固定 recipe fingerprint 和 chunked runner，并能由 lineage 回溯到 L1。

## 完整链路

1. 解析 Analyzer 报告或用户的数据需求。
2. 通过托管 `dataset-acquisition-agent` 并行执行 hosted dataset 检索和垂直领域网页采集。
3. 在 worker 内完成候选筛选、下载、规范化和 DataMixer 入湖。
4. 使用 DataMixer operator 执行清洗、去重、质量处理和格式映射。
5. 根据当前数据需求规划 recipe，并由 `sft-export-agent` 导出最终训练数据。
6. 保留 dataset card、lineage、manifest、snapshot、recipe fingerprint 和导出报告。

## 数据湖分层：L1 到 L4

数据湖中的 L1-L4 不是四份相同数据的副本，而是逐步增加可用性和可审计性的内容层：

| 层级 | 内容与目标 | 典型处理 |
| --- | --- | --- |
| L1 | 原始采集内容，保留来源原貌 | 下载、解压、记录 URI、许可证和原始字段 |
| L2 | 可解析的规范化记录 | 统一 JSON/JSONL、字段类型、语言、domain、`sample_id`，修复明显结构错误 |
| L3 | 面向任务的候选训练数据 | 去重、质量筛选、去污染、答案/对话结构检查，保留可追溯的原始字段 |
| L4 | 经过 recipe 和质量门的发布数据 | DataFlow pipeline、试跑证据、质量评审、schema/lineage 校验和稳定导出 |

每一层都应保留 `sample_id`、来源和版本信息，禁止用下游结果覆盖 L1 原文。这样可以回溯某条 L4 记录来自哪些数据集、经过哪些算子，以及为什么被过滤。

## 多源异构数据入湖

一个任务经常同时包含问答、对话、代码、证明、表格和网页段落。入湖时不要假设所有来源共享同一字段。先保留原始字段，再建立最小公共字段，例如：

```json
{
  "sample_id": "stable-id",
  "dataset_id": "source-dataset",
  "raw_content": "original record or serialized object",
  "source_uri": "hf://...",
  "license": "...",
  "domain": "math",
  "lang": "en"
}
```

DataFlow pipeline 再根据实际记录选择 `question`、`instruction`、`problem`、`answer`、`solution` 等候选字段，生成内部规范字段。字段缺失、答案表示不同或 evaluator-facing 包装应进入带计数的修复分支；只有损坏、重复、污染或无法恢复的记录才直接淘汰。

## 配平、分桶和多维采样

配平不是简单按数据集等比例抽样。先定义 bucket，再为每个 bucket 设置目标数量或比例。常用维度包括：

- 来源数据集、许可证和语言；
- 任务类型（问答、证明、代码、分类、推理）；
- 难度、答案类型和推理长度；
- benchmark 能力标签、领域和去污染状态；
- 是否来自修复分支、是否通过 LLM 质量门。

`mix_plan` 应记录每个 bucket 的目标、候选池大小、实际采样数和随机种子。试跑时可使用每个数据集最多 N 条的上限检查异构 schema；完整输入则使用同一 recipe 生成，不能在试跑通过后悄悄改变过滤阈值或 prompt。

建议用“多维约束 + 最小保留量”的策略：先保证每个重要能力/来源至少有可评估样本，再按 token 预算和目标比例扩展。若筛选后几乎没有记录或只剩单一来源，必须查看拒绝样本；格式、schema 或答案表示问题可单独修复或分流，不应静默删除。

## Benchmark 注册与去污染

当任务涉及 benchmark 时，先确认它是否已注册。注册内容至少包括 benchmark 名称、版本、官方样例、schema、评测格式和下载来源，并生成污染防护集合（通常为规范化文本的 n-gram 集）。

处理顺序应为：

1. 读取已注册 benchmark 元数据和代表性样例，提炼能力与答案约束。
2. 将 benchmark 样例和污染 guard 固化到当前 lake 的可追溯目录。
3. 对候选题目做去污染检查；命中时记录 `sample_id`、匹配片段和 guard 版本。
4. 将“训练内容契约”和“benchmark 原生评测格式”分开记录。训练数据不应机械复制 evaluator prompt 或答案槽位。

去污染是排除 benchmark 泄漏，不等同于排除所有非 benchmark 格式数据。证明题、表达式答案或不同对话 schema 是否保留，应根据目标能力和可修复性决定。

## DataFlowAgent 的迭代与评审

`dataflow agent-run` 先在 trial 输入上生成 pipeline，之后由 `reviewing-dataflow-pipeline` 执行 D1-D6 独立评审。一个可靠的循环是：

```text
读取样例与 benchmark
  -> 生成/复用 pipeline
  -> trial 运行并保存每步 cache/funnel
  -> 检查输出与字段保真
  -> D1-D6 独立评审
  -> 按 required_next_repairs 修改
  -> 重新 trial 和复审
```

评审重点包括数据适配、质量门、benchmark 能力覆盖、LLM 输入字段、推理重生成和 SFT 字段完整性。`rework` 不是成功状态：必须继续修改和复审；只有明确 `release` 才能交付 `trial_processed.jsonl`。试跑输出为零、来源塌缩或质量分全部为满分但没有校准证据时，都应继续迭代。

每轮应保留 `pipeline.py`、`trial_funnel.json`、`operator_decision.json`、评审 JSON/Markdown、校准样本和字段保真审计。最终 JSON 应报告 trial 输入/输出、丢弃原因、分支计数、评审决策和 full-run 预估；全量执行由上层 chunked runner 负责。

## 固化高质量经验

只有经过独立评审达到发布门槛、证据完整且对同类任务可复用的 pipeline，才适合通过 `curating-dataflow-pipeline-skills` 固化为 pattern skill。固化时保留：适用条件、输入 schema、算子顺序、阈值依据、失败案例、评审分数和 trial 证据。

低分或 `rework` 的 pipeline 不应覆盖已有 skill；应把它作为比较案例记录，等后续版本达到门槛后再推广。固化的是可迁移的方法和约束，不是某次任务的绝对路径或单一数据集字段。

```text
Judger -> Analyzer -> ObtainerCLI/DataMixer -> Trainer
```

## 启动数据获取

数据获取阶段由托管的 `dataset-acquisition-agent` 完成。它把 Analyzer 的数据需求翻译成可执行的检索与采集计划，再并行处理 hosted dataset 检索和垂直领域网页采集。外层 Agent 必须先读取 `skills/obtainer/SKILL.md`，再通过 CLI wrapper 启动 worker；不要在外层直接调用 SearchAgent、WebAgent、download manifest 或入湖命令。

启动前要明确 objective、关键词、目标数据集数量、领域/语言、许可证约束、时间范围以及每个来源的规模上限。关键词只是检索线索，不是最终过滤条件；worker 返回候选后仍需检查数据集卡、版本、许可证、字段形态和样例质量。网页采集还应记录入口 URL、抓取时间、robots/许可判断和去重键，避免把搜索结果页面本身当作训练记录。

```bash
python -m loopai.skills.ObtainerCLI.cli dm --lake .loopai/lake.yaml \
  dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --objective "collect code-repair instruction pairs" \
  --keywords "buggy fixed Python code pairs" \
  --target-datasets 20
```

worker 的运行目录应保存任务配置、候选来源清单、下载/抓取日志、失败 URL、许可证快照和原始 manifest。使用 `dataset-acquisition-agent status` 轮询，确认各来源的 discovered、accepted、downloaded、rejected 和 failed 计数；不能只依据进程退出码判断获取成功。同一目标的网络中断、限流或单个来源失败，使用 `resume` 继续未完成部分；目标、关键词、许可证策略或采集范围变化时重新 `start`，生成新的 run 和 fingerprint。

获取完成后先做入湖验收，再进入 L2：检查文件可读性、记录数、编码、字段保真、重复来源和许可证完整性。无法下载或无法解析的来源要留在失败清单中，并在报告里说明，不得用空结果替代；原始文件和原始记录应以只读方式写入 L1，后续规范化、修复和筛选在下游层完成。

## 数据处理与导出

数据进入 warehouse 后，继续使用 `loopai-obtainercli dm ...` 完成 schema 检查、DataFlow operator 处理、索引、recipe、snapshot、lineage 和 export。生产 SFT 数据使用托管 `sft-export-agent`：

```bash
loopai-obtainercli dm --root /path/to/warehouse sft-export-agent start \
  --run ./outputs/sft_export_run \
  --analysis-report ./outputs/analyzer_report.md \
  --format alpaca \
  --target-records 100000 \
  --out ./outputs/sft_export_run/export
```

只有 `final_report.json` 没有 blocker，且 manifest、snapshot、schema 校验和导出路径均有效时，才把最终数据路径交给 Trainer。

## 重点检查

- 数据是否匹配当前失败类型和目标样本形态
- 许可证、来源和派生字段是否有完整 provenance
- 清洗、去重、质量门和格式映射是否有可复查证据
- recipe 的样本/Token 预算和 bucket 比例是否来自当前任务
- 最终导出是否包含 manifest、snapshot、lineage 和稳定的数据路径

完整命令和约束以 `skills/obtainer/SKILL.md` 与 `docs/OBTAINERCLI_USAGE.md` 为准。
