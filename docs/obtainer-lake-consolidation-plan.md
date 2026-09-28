# Obtainer 数据湖整合计划

lite 版本的数据获取边界是 Hugging Face Hub。主 agent 直接控制
`dataset-acquisition-agent` 和 DataFlowAgent。

## 目标布局

```text
.datamixer/
  lake.yaml
  warehouse/
outputs/obtainer/
  runs/<run-id>/
  dataflow_work/
  events/
  logs/
  .codex/worker/
```

`.datamixer/lake.yaml` 只保存当前 warehouse 指针和不含凭据的运行上下文。
worker run 目录保存候选清单、过滤结果、每个数据集的下载与规范化结果、入湖
记录、统一索引结果和 `final_report.json`。运行时状态和日志不进入 lake bundle。

## 获取与入湖契约

1. `dataset-acquisition-agent` 使用 Hugging Face Hub 搜索，按更新时间排序并
   优先 2025/2026 创建或更新的数据集。
2. 每个选中的数据集独立下载、规范化为 JSONL，并保留 `source_dataset`、
   `source_uri`、`split` 等来源字段。
3. 每个 JSONL 独立注册到 DataMixer；全部数据集入湖后只执行一次共享索引构建。
4. 后续质量处理通过 `dm dataflow agent-run`，recipe 校验和出湖由主 agent
   直接调用 DataMixer 命令完成。

## 导出 / 导入

`dm lake export-bundle` 默认只打包 warehouse 的数字资产（catalog、blobs、
index、exports、snapshots、lineage 和审计记录），排除 worker 运行时状态、
日志、缓存和临时下载文件。`dm lake import-bundle` 解包后重写 lake 指针并
   校验 catalog、索引和 manifest 一致性。

增量数据通过 `dm lake import-data` 或标准 `ingest` 进入现有 warehouse；导入后
按需运行 `index build`，不绕过 DataMixer catalog。

## 验证

- 下载单元测试覆盖 Hugging Face 多数据集、JSONL 规范化、行数/字节上限和来源字段。
- monitor 测试覆盖 worker 状态、lake 指针和索引结果。
- 发布前运行 `python -m compileall`、相关 pytest 和 `git diff --check`。
