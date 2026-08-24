# ObtainerCLI 网页采集说明

网页采集属于 ObtainerCLI 托管 `dataset-acquisition-agent` 的内部数据源，不再作为独立 Agent 调度。外层只负责传入结构化数据需求、启动 worker 并轮询结果。

Worker 会并行执行：

- SearchAgent：发现可下载的 hosted dataset。
- WebAgent：采集垂直领域网页，形成独立的 DataMixer L1 数据集。

两个数据源会保留各自的状态和产物，并统一写入 `final_report.json`。任一数据源失败时，worker 会保留另一侧的证据，但不会绕过失败继续执行下载或入湖。

网页数据入湖后，清洗、去重、质量处理、格式映射、recipe 规划和最终导出仍在同一个 ObtainerCLI/DataMixer 链路中完成：

```text
Analyzer -> ObtainerCLI/DataMixer -> Trainer
```

详细启动命令、模型解析和失败处理规则见 [ObtainerCLI/DataMixer 详细指南](./obtainer-agent.md) 与仓库中的 `skills/obtainer/SKILL.md`。
