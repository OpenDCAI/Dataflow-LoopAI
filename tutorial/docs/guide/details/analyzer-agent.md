# Analyzer Agent 详细指南

`AnalyzerAgent` 负责对评测结果做进一步分析，并归纳模型当前存在的问题模式。它主要读取 `Judger` 已经生成的评测结果，而不是重新生成模型回答。

Analyzer 在 Dataflow-LoopAI 中主要承担以下工作：

- 模型评测结果分析
- 指标计算
- 失败样例归纳
- 报告生成
- 优化建议总结

相比“模型得了多少分”，Analyzer 更关注的是：

> 模型为什么失败、失败在哪里、后续应该如何优化。

## 核心职责

- 读取评测结果
- 分析失败样例
- 归纳错误模式
- 统计评测表现
- 计算评测指标
- 生成分析报告
- 输出优化建议

Analyzer 主要面向两类任务：

- `code` / `text2sql`
- `general_text`

其中：

- `code` / `text2sql` 更关注失败阶段与错误原因分析
- `general_text` 更关注 metric 指标分析与报告生成

## 进入它之前通常要准备什么

Analyzer 通常依赖以下输入：

- 已完成的评测结果
- `Judger` 输出的 result 文件
- bench 信息（适用于通用文本任务）
- 分析模型服务配置
- 对应的输出目录

### `code` / `text2sql`

通常需要：

- `eval_result_path`
- `analyze_model_path`
- `analyze_base_url`
- `analyze_api_key`

Analyzer 会直接读取已经生成的 OJ 或 SQL 执行结果，再基于这些结果做原因分析。

### `general_text`

通常需要：

- `bench_name`
- `bench_dataflow_eval_type`
- `key_mapping`
- metric 配置
- benchmark detail records

Analyzer 会自动推荐 metric，并执行对应的指标计算。

## 它的输入和输出可以怎么理解

输入通常包括：

- `Judger` 的评测结果
- 模型生成样例
- benchmark records
- bench 信息
- metric 配置
- 分析模型配置
- 日志或执行信息

输出通常包括：

- 问题模式总结
- 失败案例分析
- metric 评测结果
- 分析报告
- 数据构造建议
- 模型优化建议
- 可供 `Obtainer` 或 `WebCrawler` 使用的后续优化方向

## 不同任务下重点分析什么

### `code` / `text2sql`

Analyzer 通常会重点分析：

- 哪一步失败
- 为什么失败
- 属于什么错误类型
- 模型能力短板在哪里

常见分类示例：

- reasoning error
- execution error
- format error
- hallucination
- SQL schema misunderstanding

### `general_text`

Analyzer 通常会重点分析：

- metric 表现
- 指标之间是否存在冲突
- 哪类样本最容易失败
- 当前数据集是否存在覆盖问题

常见观察示例：

- EM 高但 GPT-Score 低
- ROUGE 高但语义质量差
- 格式正确但事实错误

## 在闭环中的位置

Analyzer 位于：

> “评测之后，数据动作之前”。

它主要回答这些问题：

- 模型为什么失败
- 哪类问题最容易失败
- 当前模型的能力短板是什么
- 数据集是否存在问题
- 当前评测是否可靠
- 后续应该往哪里优化

在完整闭环中，它通常处在下面这个位置：

```text
模型生成 -> Judger 执行评测 -> Analyzer 分析结果 -> Obtainer / WebCrawler 获取新数据 -> 再次训练与评测
```

因此，Analyzer 更像是整个闭环中的“诊断与决策层”。

## 使用时最该关注什么

- 评测结果路径是否正确
- 分析样本是否足够具有代表性
- 分析模型是否足够稳定
- metric 是否适合当前任务
- 输出报告是否具有可执行性
- 优化建议是否真的能够指导后续闭环

### 对于 `code` / `text2sql`

尤其需要关注：

- failure cases 是否真实有效
- OJ / SQL 执行结果是否完整
- `analyze_sampling_top_k` 是否合理
- `quick_brief` 是否导致关键信息丢失

### 对于 `general_text`

尤其需要关注：

- metric recommendation 是否正确
- `key_mapping` 是否正确
- prediction / answer 字段是否对齐
- metric 是否真的反映模型质量

Analyzer 的重点并不只是输出一个“分数”，而是解释：

> 为什么会得到这个分数。
