# Nodes

LoopAI 的主要能力以可组合的 node 提供；Starter 负责理解意图并协调这些 node 与 skill。node 是面向运行时的核心概念。

## Starter node

Starter 是交互与编排入口：与用户对话并识别意图、选择执行路径，以及协调下游 node 和 skill。

## Judger node

Judger 评估当前模型质量：运行评测、比较结果、定位失败样本，并为后续分析提供证据。它可连接本地或远程的 OpenAI-compatible 推理服务。

## Analyzer node

Analyzer 将评测观察转为可操作结论：归纳失败模式、分析可能原因，并提出数据与优化建议。

## ObtainerCLI/DataMixer

数据获取、处理和导出由 ObtainerCLI/DataMixer 统一完成。它使用托管 worker 获取 hosted dataset 和网页数据，再完成清洗、去重、质量处理、格式映射与训练数据导出；已退役的独立数据 node 不应再被调度。

## Trainer node

Trainer 发起训练或微调，收集日志和指标，并将结果写回运行状态。当前支持 LLaMA-Factory SFT 与 verl GRPO；两者都需要各自准备的本地运行环境。

## 为什么拆分为 node

- 每个 node 的职责更聚焦，便于替换、测试和复用。
- 新能力可接入图执行，而无需重写整个闭环。
- 团队可选择哪些阶段自动化，哪些阶段人工复核。
