# ObtainerCLI 使用文档

本文档对应 v2 分支当前的 `loopai.obtainercli` 第一版实现。它的目标是把 Obtainer 的数据入湖、索引和出湖流程做成可被 CLI/Codex 稳定调用的接口。

当前默认实现是 **local-parquet catalog**：表数据以 Parquet part 文件写在 lake root 下。旧的 `local-jsonl` catalog 仍保留为兼容路径，已有 JSONL lake 不会被强制迁移。

## 1. 环境准备

推荐使用已安装好的 conda 环境：

```bash
conda activate loopaiv2
```

确认 CLI 可用：

```bash
obtainercli --help
python -m loopai.obtainercli --help
```

当前主命令：

```text
obtainercli lake init
obtainercli lake status
obtainercli ingest path
obtainercli index embed
obtainercli tag list
obtainercli sample
```

所有命令都会输出 JSON，便于 Codex 或其他自动化进程解析。

## 2. 数据湖初始化

建议把大数据湖 root 放在 repo 外部，只在 repo 内保留 `.loopai/lake.yaml` 指针，避免 TB 级数据拖垮 IDE/Codex 文件索引。

```bash
obtainercli lake init \
  --root /mnt/paper2any/xbr/loopai0531/lakes/loopai-v2 \
  --link .loopai/lake.yaml \
  --if-not-exists
```

默认会开启自动 embedding：

```bash
obtainercli lake init \
  --root /mnt/paper2any/xbr/loopai0531/lakes/loopai-v2 \
  --link .loopai/lake.yaml \
  --embedding-provider openai-compatible \
  --embedding-base-url http://127.0.0.1:8000/v1 \
  --embedding-model BAAI/bge-small-zh-v1.5 \
  --auto-embed \
  --if-not-exists
```

如果暂时不希望入湖后自动 embedding：

```bash
obtainercli lake init \
  --root /mnt/paper2any/xbr/loopai0531/lakes/loopai-v2 \
  --link .loopai/lake.yaml \
  --no-auto-embed \
  --if-not-exists
```

初始化后会生成两份配置：

```text
/mnt/.../lakes/loopai-v2/lake.yaml
.loopai/lake.yaml
```

`.loopai/lake.yaml` 是指针配置，典型内容如下：

```yaml
root: /mnt/paper2any/xbr/loopai0531/lakes/loopai-v2
warehouse: /mnt/paper2any/xbr/loopai0531/lakes/loopai-v2/warehouse
catalog: local-parquet
namespace: loopai
auto_embed: true
embedding_provider: openai-compatible
embedding_base_url: http://127.0.0.1:8000/v1
embedding_api_key:
embedding_model: BAAI/bge-small-zh-v1.5
embedding_backend: local-jsonl
embedding_text_field: text
```

## 3. 数据湖目录结构

当前第一版目录如下：

```text
lake-root/
  lake.yaml
  warehouse/
    loopai.db/
      datasets/
        _schema.json
        data/*.parquet
      assets/
        _schema.json
        data/*.parquet
      records/
        _schema.json
        data/*.parquet
      record_tags/
        _schema.json
        data/*.parquet
      record_lineage/
        _schema.json
        data/*.parquet
      embeddings/
        _schema.json
        data/*.parquet
      quality_findings/
        _schema.json
        data/*.parquet
      ingest_runs/
        _schema.json
        data/*.parquet
      exports/
        _schema.json
        data/*.parquet
  staging/
  quarantine/
  reports/
  locks/
```

核心表含义：

| 表 | 用途 |
| --- | --- |
| `datasets` | 逻辑数据集元信息，例如 `code_seed`、`math_seed` |
| `assets` | 原始入湖资产，例如某个 JSONL 文件 |
| `records` | 标准化后的训练/清洗记录 |
| `record_tags` | 标签倒排表，用于高基数标签筛选和采样 |
| `record_lineage` | 预留的记录血缘表 |
| `embeddings` | 每条 record 的 embedding 结果 |
| `quality_findings` | 质量检查、合成多样性、污染检测等发现 |
| `ingest_runs` | 每次入湖任务审计记录 |
| `exports` | 每次出湖采样任务审计记录 |

## 4. 输入数据格式

当前 `ingest path` 支持 JSONL 文件，每行必须是 JSON object。

最小格式：

```jsonl
{"text":"def add(a, b): return a + b","source_uri":"file://repo/a.py"}
{"text":"def sub(a, b): return a - b","source_uri":"file://repo/b.py"}
```

常用字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | string | 首选主文本字段，embedding 默认使用它 |
| `instruction` | string | 指令数据字段 |
| `input` | string | 输入字段 |
| `output` | string | 输出字段 |
| `messages` | array | chat/SFT 格式 |
| `source_uri` | string | 原始来源 URI；缺省时使用 `input_path#line_no` |
| `source_domain` | string | 来源域名，可作为标签或后续过滤条件 |
| `split` | string | train/validation/test 等 |
| `quality_score` | number | 质量分 |
| `parent_record_ids` | array | 上游记录 ID |
| `quality_findings` | array | 质量发现，会写入 `quality_findings` 表 |

如果没有 `text`，系统会依次从 `output`、`content`、`instruction`、`messages` 或整行 JSON 中构造主文本。

合成数据示例：

```jsonl
{"text":"synthetic instruction response","source_uri":"synthetic://run/1","quality_findings":[{"finding_type":"low_diversity","severity":"warning","score":0.42,"metric_name":"distinct_3","metric_value":0.18,"detector":"diversity_check","detector_version":"v1","details":{"window":128}}]}
```

## 5. 入湖

基础入湖：

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/code_seed.jsonl \
  --dataset code_seed \
  --stage bronze \
  --domain code \
  --task-type PT \
  --processing-level raw_web \
  --source-kind web \
  --tags lang=python,quality=medium \
  --idempotency-key code-seed-20260602
```

常用维度建议：

| 维度 | 示例 | 说明 |
| --- | --- | --- |
| `stage` | `bronze` / `silver` / `gold` | 数据湖层级 |
| `domain` | `code` / `math` / `general` | 垂域 |
| `processing_level` | `raw_web` / `extracted_text` / `pretrain_ready` / `postprocessed_high_quality` / `synthetic_validated` | 处理程度 |
| `source_kind` | `web` / `local` / `api` / `synthetic` | 来源类型 |
| `task_type` | `PT` / `SFT` / `RL` / `EVAL` | 任务类型 |
| `tags` | `lang=python,quality=high` | 额外标签，逗号分隔 |

入湖时会写入：

```text
datasets
assets
records
record_tags
quality_findings
ingest_runs
```

### 去重语义

当前有两层 ID：

| 字段 | 语义 |
| --- | --- |
| `record_id` | 物理唯一 ID，由 `dataset_id + source_uri + processing_level + payload` 哈希得到 |
| `dedup_key` | 语义去重 key，由规范化主文本哈希得到 |

因此，同一内容进入两个 dataset 会得到不同 `record_id`，但可能共享同一个 `dedup_key`。第一版不会自动删除语义重复项，只把 `dedup_key` 写入 `records`，供后续 silver/gold 去重流程使用。

### 幂等入湖

建议每次入湖都传稳定的 `--idempotency-key`：

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/code_seed.jsonl \
  --dataset code_seed \
  --idempotency-key code-seed-v1
```

同一个 `idempotency_key` 已成功入湖后，再次执行会返回：

```json
{
  "ok": true,
  "status": "success_with_warnings",
  "rows_written": 0,
  "warnings": [
    {
      "code": "DUPLICATE_INGEST_SKIPPED"
    }
  ]
}
```

## 6. 自动 Embedding

`lake init` 默认写入：

```yaml
auto_embed: true
embedding_provider: openai-compatible
embedding_base_url: http://127.0.0.1:8000/v1
embedding_model: BAAI/bge-small-zh-v1.5
embedding_backend: local-jsonl
embedding_text_field: text
```

因此，当 `ingest path` 成功写入新 records 后，会自动调用 `index embed`。如果 embedding 服务不可用，入湖本身不会回滚，命令会返回 `success_with_warnings`，并带有：

```json
{
  "code": "POST_INDEX_EMBEDDING_FAILED"
}
```

单次入湖禁用自动索引：

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/code_seed.jsonl \
  --dataset code_seed \
  --no-post-index
```

强制指定本次 embedding 参数：

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/code_seed.jsonl \
  --dataset code_seed \
  --post-index embedding \
  --embedding-provider openai-compatible \
  --embedding-base-url http://127.0.0.1:8000/v1 \
  --embedding-model BAAI/bge-small-zh-v1.5 \
  --embedding-text-field text
```

## 7. 启动本地 Embedding Server

ObtainerCLI 的 embedding client 调用 OpenAI-compatible `/v1/embeddings` 接口。当前仓库提供两种启动方式。

### 7.1 Python/Transformers 方式

适合普通 CUDA/CPU 环境：

```bash
python scripts/obtainercli_embedding_server.py \
  --model-dir /mnt/paper2any/xbr/loopai0531/models/BAAI/bge-small-zh-v1.5 \
  --model-name BAAI/bge-small-zh-v1.5 \
  --host 127.0.0.1 \
  --port 8000 \
  --device auto \
  --dtype auto \
  --max-length 512
```

环境变量等价写法：

```bash
export OBTAINERCLI_EMBED_MODEL_DIR=/mnt/paper2any/xbr/loopai0531/models/BAAI/bge-small-zh-v1.5
export OBTAINERCLI_EMBED_MODEL_NAME=BAAI/bge-small-zh-v1.5
export OBTAINERCLI_EMBED_PORT=8000
python scripts/obtainercli_embedding_server.py
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/models
```

手动请求：

```bash
curl http://127.0.0.1:8000/v1/embeddings \
  -H 'content-type: application/json' \
  -d '{"model":"BAAI/bge-small-zh-v1.5","input":["hello","world"]}'
```

### 7.2 Docker/vLLM 方式

适合使用定制 torch/vLLM 镜像的开发机，例如沐曦环境。脚本不会猜测你的镜像名，需要显式设置：

```bash
export OBTAINERCLI_VLLM_IMAGE=your-custom-vllm-image:tag
export OBTAINERCLI_EMBED_MODEL_DIR=/mnt/paper2any/xbr/loopai0531/models/BAAI/bge-small-zh-v1.5
export OBTAINERCLI_EMBED_MODEL_NAME=BAAI/bge-small-zh-v1.5
export OBTAINERCLI_EMBED_PORT=8000
export OBTAINERCLI_DOCKER_GPU_ARGS="--privileged"

bash scripts/obtainercli_vllm_embedding_server.sh
```

脚本会以 host network 暴露：

```text
http://127.0.0.1:8000/v1/embeddings
```

如果你的容器内部启动命令不是 `python -m vllm.entrypoints.openai.api_server`，可以覆盖：

```bash
export OBTAINERCLI_VLLM_CMD="python -m vllm.entrypoints.openai.api_server"
```

## 8. 手动 Embedding 索引

即使初始化时关闭了自动 embedding，也可以手动索引：

```bash
obtainercli index embed \
  --lake .loopai/lake.yaml \
  --dataset code_seed \
  --provider openai-compatible \
  --base-url http://127.0.0.1:8000/v1 \
  --model BAAI/bge-small-zh-v1.5 \
  --backend local-jsonl \
  --text-field text
```

如果只是测试流程，不想依赖真实模型，可以用 deterministic hash embedding：

```bash
obtainercli index embed \
  --lake .loopai/lake.yaml \
  --dataset code_seed \
  --provider local-hash \
  --model local-hash-v1 \
  --backend local-jsonl \
  --text-field text
```

`index embed` 会跳过已经存在的 `(record_id, embedding_model, text_field, index_backend)` 组合，因此重复执行通常不会重复写入。

## 9. 查看状态和标签

查看各表行数和路径：

```bash
obtainercli lake status --lake .loopai/lake.yaml
```

查看标签分布：

```bash
obtainercli tag list --lake .loopai/lake.yaml
```

标签来自两部分：

1. 核心列自动生成：`domain`、`processing_level`、`source_kind`、`task_type`
2. 入湖时 `--tags` 传入的键值对，例如 `lang=python`、`quality=high`

## 10. 出湖采样

按核心维度出湖：

```bash
obtainercli sample \
  --lake .loopai/lake.yaml \
  --output exports/code_pretrain.jsonl \
  --domain code \
  --processing-level pretrain_ready \
  --source-kind web \
  --task-type PT \
  --n 1000 \
  --seed 42
```

按标签过滤：

```bash
obtainercli sample \
  --lake .loopai/lake.yaml \
  --output exports/python_high_quality.jsonl \
  --domain code \
  --processing-level postprocessed_high_quality \
  --include-tag lang=python \
  --include-tag quality=high \
  --exclude-tag license=unknown \
  --n 500 \
  --seed 7
```

候选不足但允许导出较小集合：

```bash
obtainercli sample \
  --lake .loopai/lake.yaml \
  --output exports/small.jsonl \
  --domain code \
  --include-tag quality=high \
  --n 1000 \
  --allow-smaller
```

返回会包含 warning：

```json
{
  "status": "success_with_warnings",
  "warnings": [
    {
      "code": "ALLOW_SMALLER_TRIGGERED"
    }
  ]
}
```

分层采样：

```bash
obtainercli sample \
  --lake .loopai/lake.yaml \
  --output exports/balanced_by_domain.jsonl \
  --domain code \
  --processing-level postprocessed_high_quality \
  --include-tag quality=high \
  --strategy stratified \
  --balance-by tag:source_domain \
  --n 1000 \
  --seed 11
```

第一版 `--strategy stratified` 只支持 `--balance-by tag:<tag_name>`。

## 11. 出湖匹配策略

当前实现先用 records 的核心列做过滤：

```text
domain
processing_level
source_kind
task_type
```

再用 `record_tags` 对 `--include-tag` / `--exclude-tag` 做集合求交或排除。这样保留了未来迁移到 Iceberg/Parquet 后按核心列裁剪的空间，同时把高基数标签放在标签表中处理。

## 12. Exit Code 和错误

| exit code | 含义 |
| --- | --- |
| `0` | 命令执行完成；如果有 warning，会在 JSON 的 `warnings` 字段体现 |
| `1` | 未捕获异常 |
| `2` | 参数错误或通用 ObtainerCLI 错误 |
| `6` | 采样候选不足，错误码 `CANDIDATE_NOT_ENOUGH` |

注意：当前第一版没有单独的“成功但有警告” exit code。`success_with_warnings` 仍返回 `0`，调用方应读取 JSON 中的 `status` 和 `warnings`。

错误响应格式：

```json
{
  "ok": false,
  "error_code": "CANDIDATE_NOT_ENOUGH",
  "message": "Requested 100 records but only 3 matched.",
  "hint": "Relax filters or use --allow-smaller."
}
```

## 13. 常见工作流

### 13.1 原始网页入湖

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/raw_web.jsonl \
  --dataset raw_web_20260602 \
  --stage bronze \
  --domain general \
  --task-type PT \
  --processing-level raw_web \
  --source-kind web \
  --tags crawl_batch=20260602 \
  --idempotency-key raw-web-20260602
```

### 13.2 抽取正文后的网页入湖

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/extracted_text.jsonl \
  --dataset extracted_web_20260602 \
  --stage silver \
  --domain general \
  --task-type PT \
  --processing-level extracted_text \
  --source-kind web \
  --tags extractor=trafilatura \
  --idempotency-key extracted-web-20260602
```

### 13.3 预训练数据入湖

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/pretrain_ready_code.jsonl \
  --dataset code_pretrain_ready_v1 \
  --stage silver \
  --domain code \
  --task-type PT \
  --processing-level pretrain_ready \
  --source-kind web \
  --tags lang=python,quality=medium \
  --idempotency-key code-pretrain-ready-v1
```

### 13.4 高质量后处理数据入湖

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/code_hq.jsonl \
  --dataset code_hq_v1 \
  --stage gold \
  --domain code \
  --task-type SFT \
  --processing-level postprocessed_high_quality \
  --source-kind synthetic \
  --tags generator=qwen,quality=high \
  --idempotency-key code-hq-v1
```

### 13.5 导出垂域数据

```bash
obtainercli sample \
  --lake .loopai/lake.yaml \
  --output exports/code_hq_sft.jsonl \
  --domain code \
  --processing-level postprocessed_high_quality \
  --task-type SFT \
  --include-tag quality=high \
  --n 10000 \
  --allow-smaller \
  --seed 20260602
```

## 14. 并发和锁

当前第一版用 `locks/commit.lock` 做本地文件锁，保护写表阶段。建议仍按“单 lake 串行 commit”使用：

1. 下载、爬取、清洗、合成可以并行产出 JSONL。
2. 入湖写入同一个 lake 时尽量串行，或由外层调度器排队。
3. 不建议多个进程同时对同一个 lake 做高频入湖和索引。

## 15. 当前边界

当前 v2 第一版已经实现：

- 外部 lake root + repo 内指针配置
- JSONL 数据入湖
- 核心列和标签写入
- `record_id`/`dedup_key` 双层去重标识
- `quality_findings` 写入
- 自动 embedding 和手动 embedding
- OpenAI-compatible embedding server
- 标签统计
- 随机采样和按 tag 分层采样
- 出湖审计记录

尚未实现或后续可替换：

- Iceberg/PyIceberg catalog
- 跨 part 的 compaction 和 Parquet 分区布局优化
- Parquet column stats 驱动的查询裁剪
- 向量库 ANN 检索
- 自动语义去重删除
- 多 writer 高并发事务
- 更完整的数据质量规则引擎
