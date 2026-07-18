# ObtainerCLI DataMixer Lakehouse Design

日期：2026-06-30
状态：已收敛为 DataMixer-only 设计

## 目标

ObtainerCLI 的数据湖能力由 DataMixer 统一承载。SearchAgent 和
`download manifest` 只负责数据发现与下载桥接；从数据入湖开始，所有
storage、catalog、processing、index、recall、recipe、export、snapshot 和
lineage 操作都通过：

```bash
loopai-obtainercli dm --root /path/to/warehouse <command> --json
loopai-obtainercli dm --lake .loopai/lake.yaml <command> --json
```

## Warehouse

DataMixer warehouse 结构：

```text
warehouse/
  datamixer.toml
  catalog.db
  blobs/
  index/
  exports/
  lineage/
  snapshots/
```

`.loopai/lake.yaml` 只保存 pointer：

```yaml
root: /data/lakes/code_sft
warehouse: /data/lakes/code_sft/warehouse
catalog: datamixer
backend: datamixer
namespace: loopai
```

## Analyzer 到出湖流程

1. Codex 读取 Analyzer report，启用 Obtainer skill。
2. 解析 report，生成明确 objective、keywords 或 task-json。
3. 调用 SearchAgent，检查 `searchagent_manifest.json`。
4. 使用 `download manifest` 下载候选数据集，单数据集采集上限为 100000 行和 2GiB 本地 JSONL 输出；达到字节上限时保留部分数据并报告截断。
5. 使用 `dm ingest` 或 `dm agent-ingest` 入湖。
6. 使用 `dm op` / `dm pipeline` 做质量、去重、安全与标签补齐。
7. 使用 `dm index` / `dm recall` 做召回与覆盖检查。
8. 使用 `dm recipe plan/preview/export --snapshot` 做生产出湖。
9. 汇报 lineage、snapshot、export manifest、recipe fingerprint 与 dataset digest。

## Production SFT

生产 SFT 数据必须通过 DataMixer recipe 出湖。若没有明确规模，SFT recipe
按 `total_samples: 100000` 规划，或由用户明确给出 token budget。

failure taxonomy 配比必须依赖语义标签，例如：

```text
json_extract(tags_json, '$."bug_type"') = 'syntax'
json_extract(tags_json, '$."bug_type"') = 'logic'
json_extract(tags_json, '$."bug_type"') = 'runtime'
json_extract(tags_json, '$."bug_type"') = 'assertion'
```

如果相关标签缺失或 bucket 数量不足，`recipe plan/export` 应报告 blocker，
继续采集或补标后再出湖。
