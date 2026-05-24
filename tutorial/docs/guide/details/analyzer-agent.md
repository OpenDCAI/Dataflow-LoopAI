# Analyzer Agent 详细指南

AnalyzerAgent 负责对评测结果进行进一步分析，并归纳模型当前存在的问题模式。

Analyzer 是 Dataflow-LoopAI 中用于：

- 模型评测结果分析
- 指标计算
- 失败样例归纳
- 报告生成
- 优化建议总结

的分析型 Agent。

它主要读取 Judger 已经生成的评测结果，而不是重新生成模型回答。

Analyzer 更关注：

> 模型为什么失败、失败在哪里、后续应该如何优化。

---

## 核心职责

- 读取评测结果
- 分析失败样例
- 归纳错误模式
- 统计评测表现
- 计算评测指标
- 生成分析报告
- 输出优化建议

Analyzer 主要面向两类场景：

- code / text2sql
- general_text

其中：

- code / text2sql 更关注失败阶段与错误原因分析
- general_text 更关注 metric 指标分析与报告生成

---

## 进入它之前通常要准备什么

Analyzer 通常依赖：

- 已完成的评测结果
- Judger 输出的 result 文件
- bench 信息（通用文本任务）
- 分析模型服务配置
- 对应的输出目录

对于：

### code / text2sql

通常需要：

- eval_result_path
- analyze_model_path
- analyze_base_url
- analyze_api_key

Analyzer 会直接读取已有 OJ / SQL 执行结果。

---

### general_text

通常需要：

- bench_name
- bench_dataflow_eval_type
- key_mapping
- metric 配置
- benchmark detail records

Analyzer 会自动推荐 metric，并进行指标计算。

---

## 它的输入和输出可以怎么理解

输入通常是：

- Judger 的评测结果
- 模型生成样例
- benchmark records
- bench 信息
- metric 配置
- 分析模型配置
- 日志或执行信息

---

输出通常是：

- 问题模式总结
- 失败案例分析
- metric 评测结果
- 分析报告
- 数据构造建议
- 模型优化建议
- 可供 Obtainer 或 WebCrawler 使用的优化方向

---

对于不同任务：

### code / text2sql

Analyzer 会重点分析：

- 哪一步失败
- 为什么失败
- 属于什么错误类型
- 模型能力短板在哪里

例如：

- reasoning error
- execution error
- format error
- hallucination
- SQL schema misunderstanding

---

### general_text

Analyzer 会重点分析：

- metric 表现
- 指标之间是否冲突
- 哪类样本最容易失败
- 当前数据集是否存在覆盖问题

例如：

- EM 高但 GPT-Score 低
- ROUGE 高但语义质量差
- 格式正确但事实错误

---

## 在闭环中的位置

Analyzer 位于：

> “评测之后，数据动作之前”。

它负责回答：

- 模型为什么失败
- 哪类问题最容易失败
- 当前模型的能力短板是什么
- 数据集是否存在问题
- 当前评测是否可靠
- 后续应该往哪里优化

在完整闭环中：

text 模型生成     ↓ Judger 执行评测     ↓ Analyzer 分析结果     ↓ Obtainer / WebCrawler 获取新数据     ↓ 再次训练与评测 

Analyzer 是整个闭环中的：

> “诊断与决策层”。

---

## 使用时最该关注什么

- 评测结果路径是否正确
- 分析样本是否足够具有代表性
- 分析模型是否足够稳定
- metric 是否适合当前任务
- 输出报告是否具有可执行性
- 优化建议是否真正能指导后续闭环

---

对于：

### code / text2sql

尤其需要关注：

- failure cases 是否真实有效
- OJ / SQL 执行结果是否完整
- analyze_sampling_top_k 是否合理
- quick_brief 是否导致信息丢失

---

对于：

### general_text

尤其需要关注：

- metric recommendation 是否正确
- key_mapping 是否正确
- prediction / answer 字段是否对齐
- metric 是否真的反映模型质量

---

Analyzer 更强调：

- 可解释性
- 系统性
- 报告自动化
- 数据闭环能力

它并不仅仅输出“分数”。

它更关注：

> 为什么得到这个分数。