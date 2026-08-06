# ObtainerCLI/DataMixer 详细指南

ObtainerCLI/DataMixer 是当前唯一可用的数据工作流。它负责从数据需求到最终训练数据的完整链路，不需要在中间切换到其他数据 Agent。

## 完整链路

1. 解析 Analyzer 报告或用户的数据需求。
2. 通过托管 `dataset-acquisition-agent` 并行执行 hosted dataset 检索和垂直领域网页采集。
3. 在 worker 内完成候选筛选、下载、规范化和 DataMixer 入湖。
4. 使用 DataMixer operator 执行清洗、去重、质量处理和格式映射。
5. 根据当前数据需求规划 recipe，并由 `sft-export-agent` 导出最终训练数据。
6. 保留 dataset card、lineage、manifest、snapshot、recipe fingerprint 和导出报告。

```text
Judger -> Analyzer -> ObtainerCLI/DataMixer -> Trainer
```

## 启动数据获取

外层 Agent 必须读取 `skills/obtainer/SKILL.md`，再通过 CLI wrapper 启动托管 worker。不要在外层直接调用 SearchAgent、WebAgent、download manifest 或入湖命令。

```bash
python -m loopai.skills.ObtainerCLI.cli dm --lake .loopai/lake.yaml \
  dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --objective "collect code-repair instruction pairs" \
  --keywords "buggy fixed Python code pairs" \
  --target-datasets 20
```

使用 `dataset-acquisition-agent status` 轮询结果；同一目标的可恢复问题使用 `resume`，目标或策略发生变化时重新 `start`。

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
