# ObtainerCLI 数据湖重构设计稿

日期：2026-05-31
状态：设计稿，等待确认
目标分支：`dev/v2`

## 1. 目标

把现有 `ObtainerAgent` 从 LangGraph 状态图重构为可由 CLI 驱动的数据湖工具链 `obtainercli`。新实现不再把“搜索/下载/后处理/状态流转”绑在图节点里，而是把核心能力拆成可测试、可复用、可由 Codex 自动调用的命令：

1. 初始化标准化数据湖。
2. 将数据稳定、可审计、幂等地写入数据湖。
3. 按每条数据的标签进行过滤、分层采样和出湖导出。

## 2. 现状判断

当前 `loopai/agents/Obtainer` 的主要问题不是“没有 CLI”，而是数据生命周期边界不清：

- `obtainer_agent.py` 负责配置、任务拆解、RAG 清理、路由、状态回填和摘要生成，职责过宽。
- `nodes/websearch_node.py`、`nodes/download_node.py`、`nodes/postprocess_node.py` 直接依赖 `langgraph.config.get_stream_writer` 和 `LoopAIState`。
- 下载结果主要落在 `output_dir/downloads` 和若干 state 字段里，没有统一 catalog、schema、snapshot、provenance 和 tag index。
- 下游 Constructor 目前更像读取临时中间文件，而不是从可版本化数据资产中取数。

所以重构重点应是：先建立数据湖内核，再把搜索/下载能力变成写湖前的 source adapter。

## 3. 方案选择

### 方案 A：自研 manifest lake

用 JSON manifest + JSONL/Parquet 文件维护数据湖版本。实现最快，依赖少，但会重复造 ACID、快照、schema evolution、并发写入和元数据裁剪能力，不适合“产业级标准化”。

### 方案 B：Apache Iceberg + Parquet + PyIceberg，推荐

以 Apache Iceberg 作为开放表格式，Parquet 作为列式数据文件，PyIceberg 作为 Python-native table API。CLI 在本地开发时使用 SQL catalog + local warehouse，在生产或集群环境切换到 REST/Glue/Nessie catalog 和对象存储。这个方案兼顾标准化、可迁移性、Python 生态和 CLI 可控性。

### 方案 C：Delta Lake + Spark

Delta Lake 在 lakehouse 能力上成熟，但 Python CLI 场景通常会把 Spark/Java 引入到简单数据入湖路径里，和“去 LangGraph、降低臃肿”的目标冲突。除非团队已经决定全面 Spark 化，否则不作为第一版。

结论：第一版采用方案 B。保留 `manifest lake` 的概念只作为 Iceberg 的可读操作报告，不作为主存储协议。

## 4. 数据湖范式

采用 Lakehouse + Medallion 分层：

- `bronze`：原始入湖层。保留下载文件、网页抓取结果、HF/Kaggle 原始样本、用户直接提供的 JSONL/Parquet。
- `silver`：标准记录层。统一为 LoopAI record schema，完成基础清洗、schema 归一、去重、标签标准化。
- `gold`：任务数据集层。面向训练、评测或 Constructor 的稳定导出视图，如 SFT、PT、Code、Text2SQL 子集。

物理上使用一个 warehouse，逻辑上使用 Iceberg namespace：

```text
# repo: .loopai/lake.yaml only stores a pointer to this external root
<lake_root>/
  lake.yaml
  warehouse/
    loopai.db/
      datasets/
      assets/
      records/
      record_tags/
      record_lineage/
      embeddings/
      ingest_runs/
      exports/
      quality_findings/
  staging/
  quarantine/
  reports/
  locks/
```


## 4.1 分区、标签和处理程度

这里必须把物理分区和业务标签分开：

- 物理分区用于减少扫描量，只放低基数、稳定、经常用于过滤的字段。
- 标签用于多维检索、采样和治理，可以承载高基数或多值语义。
- 核心列用于下游几乎每次都会用到的过滤条件，既可以参与分区，也可以同步写入标签表。

第一版核心字段与分区维度：

| 维度 | 建议位置 | 示例 | 说明 |
| --- | --- | --- | --- |
| `domain` | `records` 核心列 + 可分区 + tag | `code`、`math`、`text2sql`、`finance` | 垂域导出的主条件。 |
| `processing_level` | `records` 核心列 + 分区 + tag | `raw_web`、`extracted_web_text`、`pretrain_ready`、`postprocessed_high_quality`、`synthetic_raw`、`synthetic_validated` | 区分原始网页、正文抽取、预训练整理、高质量后处理和合成数据阶段。 |
| `source_kind` | `assets`/`records` 核心列 + tag | `web`、`hf`、`kaggle`、`local`、`api`、`synthetic` | 来源类型；第一版作为普通列，不进物理分区。 |
| `task_type` | `records` 核心列 + tag | `PT`、`SFT`、`EVAL` | 任务形态；第一版作为普通列，依赖 Parquet column stats 裁剪。 |
| `ingest_date` | 分区 | `2026-05-31` | 时间裁剪、审计和增量处理。 |

不建议作为物理分区的字段：

- 原始 URL、完整来源路径、文件名、repo 全名等高基数字段。
- 细粒度质量原因、license 原文、抽取器版本、网页 host 下的路径。
- 临时实验标签。

这些字段应写入 `assets.provenance`、`records.payload` 或 `record_tags`。例如原始来源可这样表达：

```text
source_kind = web                 # 核心列/tag，不作为 records 物理分区
source_domain = example.com        # 核心列或 tag，低基数时可加索引
source_uri = https://.../page.html # assets 元数据，不作为分区
source_sha256 = ...                # asset 校验
```

处理程度建议使用枚举：

```text
raw_web                     # 原始网页、HTML、markdown dump
extracted_web_text           # 初步抽取正文、去导航和模板噪声
pretrain_ready               # 整理为可预训练 text/chunk 的数据
postprocessed_high_quality   # 经过质量过滤、去重、规范化后的高质量数据
sft_ready                    # 整理为 instruction/input/output 或 messages
synthetic_raw                # 原始合成样本，保留 prompt、generator 和 seed
synthetic_validated          # 经过规则/模型校验后的合成样本
synthetic_high_quality       # 经过多样性、去重和质量过滤后的高质量合成样本
```

同一条数据在不同处理程度之间不是覆盖关系，而是派生关系。必须保留 lineage，避免后处理数据丢失原始来源。

## 5. 表模型

### `loopai.datasets`

数据集级元信息。

字段：`dataset_id`、`name`、`stage`、`domain`、`task_type`、`description`、`owner`、`source_kind`、`created_at`、`updated_at`、`schema_version`、`default_tags`。

分区建议：`stage`、`domain`、`task_type`。

### `loopai.assets`

文件级或来源级资产表。一个 asset 可以对应 HF repo、Kaggle dataset、网页抓取文件、本地 JSONL、Parquet 文件或压缩包。

字段：`asset_id`、`dataset_id`、`source_uri`、`source_kind`、`local_uri`、`content_sha256`、`size_bytes`、`mime_type`、`license`、`provenance`、`ingest_run_id`、`created_at`。

分区建议：`source_kind`、`ingest_date`。

### `loopai.records`

标准样本表，是出湖采样的主表。

字段：

- `record_id`：稳定哈希 ID，默认由 canonical payload、source URI、dataset ID 共同计算。
- `dataset_id`、`asset_id`、`stage`、`domain`、`processing_level`、`source_kind`、`source_domain`、`task_type`。
- `payload`：结构化 JSON 字符串，保留原始字段和标准字段。
- `text`：PT 场景的主文本字段。
- `instruction`、`input`、`output`、`messages`：SFT/Chat 场景字段。
- `tag_names`：冗余数组，便于轻量预览。
- `quality_score`、`dedup_key`、`parent_record_ids`、`pipeline_run_id`、`split`、`created_at`、`schema_version`。

第一版分区建议：`domain`、`processing_level`、`ingest_date`。`source_kind`、`task_type`、`record_id` 保持普通列，依赖 Parquet column stats 和 Iceberg manifest 裁剪。

`record_id` 不默认做 bucket 分区，避免与多维分区组合后制造小文件。只有当单表规模和 join/point lookup 压力证明需要时，才在后续版本加入 Iceberg hidden partition `bucket(N, record_id)`，并配套 compaction 策略。

去重语义：

- `record_id` 管物理唯一性，用于行级身份、血缘和回表读取。默认包含 `dataset_id`、`source_uri`、`processing_level` 和 canonical payload hash，因此同一内容进入两个数据集会产生两个物理记录。
- `dedup_key` 管语义去重，默认由规范化文本或结构化内容计算，不包含 `dataset_id` 和 `source_uri`。
- bronze 层保留重复；silver/gold 层根据 `dedup_key`、质量分和 lineage 选择保留项，并把重复证据写入 `quality_findings`。


### `loopai.record_tags`

标签索引表，是“按每条数据标签采样出湖”的关键表。

字段：`record_id`、`dataset_id`、`tag_name`、`tag_value`、`tag_source`、`confidence`、`created_at`。

分区建议：`tag_name`，受控枚举类 `tag_value` 可以进入分区；自由文本类 `tag_value` 只建索引表字段，避免高基数分区。出湖时它与 `records` 的分区候选集求交，不作为唯一入口。


### `loopai.record_lineage`

记录不同处理程度之间的派生关系。例如一个 `raw_web` 记录可以派生出 `extracted_web_text`，再派生出 `pretrain_ready` 和 `postprocessed_high_quality`。

字段：`child_record_id`、`parent_record_id`、`dataset_id`、`relation_type`、`pipeline_name`、`pipeline_version`、`pipeline_run_id`、`created_at`。

分区建议：`relation_type`、`pipeline_name`、`pipeline_version`。

### `loopai.embeddings`

向量不是入湖前置条件，而是入湖后的派生索引。`records` 和 `record_tags` 是数据湖的 source of truth；embedding 依赖模型版本、切分策略和索引后端，必须可重建、可删除、可替换。

字段：`record_id`、`dataset_id`、`source_snapshot_id`、`text_field`、`chunk_id`、`chunk_text_sha256`、`embedding_model`、`embedding_dim`、`vector_uri`、`index_backend`、`index_status`、`created_at`。

分区建议：`embedding_model`、`dataset_id`、`index_status`。


### `loopai.quality_findings`

记录质量检测、合成数据多样性、模式坍缩、去重和安全治理结果。它不决定样本是否存在，而是为过滤、采样和审计提供证据。

字段：`finding_id`、`record_id`、`dataset_id`、`processing_level`、`source_kind`、`finding_type`、`severity`、`score`、`metric_name`、`metric_value`、`detector`、`detector_version`、`details`、`pipeline_run_id`、`created_at`。

典型 `finding_type`：`low_quality`、`duplicate`、`mode_collapse`、`low_diversity`、`prompt_leakage`、`unsafe_content`、`license_risk`、`extract_failed`。

分区建议：`finding_type`、`severity`、`created_date`。合成数据的多样性监控必须写入这里，不能混在普通标签里。

### `loopai.ingest_runs`

一次入湖动作的审计表。

字段：`ingest_run_id`、`command`、`input_uri`、`dataset_id`、`status`、`started_at`、`finished_at`、`rows_seen`、`rows_written`、`rows_quarantined`、`error_summary`、`config_snapshot`。

### `loopai.exports`

一次出湖动作的审计表。

字段：`export_id`、`query`、`strategy`、`seed`、`requested_size`、`actual_size`、`output_uri`、`format`、`record_ids_sha256`、`created_at`。

## 6. CLI 命令面

所有命令必须支持非交互执行、JSON 输出、稳定 exit code，便于 Codex 调用。

### 初始化

```bash
obtainercli lake init \
  --root /data/loopai/lakes/dataflow-loopai \
  --catalog sql \
  --warehouse file:///data/loopai/lakes/dataflow-loopai/warehouse \
  --namespace loopai \
  --link .loopai/lake.yaml \
  --if-not-exists \
  --json
```

效果：

- 在外部 lake root 创建 `lake.yaml`，并在 repo 内创建 `.loopai/lake.yaml` 指针配置。
- 初始化 Iceberg catalog。
- 创建 namespace 和核心表。
- 写入 `reports/init_<ts>.json`。
- 执行 `doctor` 检查：依赖、catalog 可写性、warehouse 权限、schema 版本。

### 入湖

本地文件入湖：

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/raw/my_data.jsonl \
  --dataset code_sft_seed \
  --stage bronze \
  --task-type SFT \
  --processing-level pretrain_ready \
  --source-kind local \
  --tags domain=code,source=manual,lang=python \
  --format jsonl \
  --idempotency-key code_sft_seed_20260531 \
  --json
```

下载型入湖：

```bash
obtainercli ingest hf --lake .loopai/lake.yaml --repo tatsu-lab/alpaca --dataset alpaca --task-type SFT --json
obtainercli ingest kaggle --lake .loopai/lake.yaml --dataset-ref owner/name --dataset kaggle_name --json
obtainercli ingest web --lake .loopai/lake.yaml --url https://example.com/data --dataset web_seed --json
```

入湖流程：

1. 创建 `ingest_run`，状态为 `running`。
2. 下载或读取输入到 `staging/<ingest_run_id>`。
3. 计算 asset checksum，写 `assets`。
4. 解析记录，生成 canonical record。
5. 应用默认标签、CLI 标签、自动标签和质量标签。
6. 写 `records` 和 `record_tags`。
7. 失败记录进入 `quarantine/<ingest_run_id>`。
8. 提交 Iceberg snapshot，更新 `ingest_runs` 为 `succeeded` 或 `failed`。

入湖默认不做 embedding。embedding 通过后置命令或显式参数触发，失败时不回滚已经成功入湖的数据。

幂等策略：

- `idempotency_key` 相同且输入 checksum 相同，重复执行返回已有结果。
- `record_id` 冲突时默认跳过，可用 `--on-duplicate skip|replace|error` 控制。
- 所有写入先落 staging，commit 成功后才记录为可见。

提交与并发约束：

- PyIceberg append 采用乐观并发；本地 SQL catalog/SQLite 不适合作为多 writer 并发 commit 后端。
- 第一版允许下载、抽取、清洗并行，但所有 Iceberg commit 必须通过 `locks/lake.commit.lock` 串行化。
- 获取锁后按固定顺序提交：`assets` -> `records` -> `record_tags` -> `record_lineage`/`quality_findings` -> `ingest_runs`。
- 任一表提交失败时，`ingest_runs.status` 不能标记为 `succeeded`；`doctor repair --ingest-run <id>` 负责补写、隔离或生成修复报告。
- 生产环境如果需要多 writer，应切换到支持并发控制的 catalog/对象存储组合，并保留重试与冲突处理。


### 标签维护

```bash
obtainercli tag add --lake .loopai/lake.yaml --record-id <id> --tag quality=high --source human --json
obtainercli tag bulk-add --lake .loopai/lake.yaml --input tags.jsonl --json
obtainercli tag list --lake .loopai/lake.yaml --dataset code_sft_seed --json
```

### 向量索引

显式创建 embedding 索引：

```bash
obtainercli index embed \
  --lake .loopai/lake.yaml \
  --dataset code_sft_seed \
  --text-field text \
  --model text-embedding-3-large \
  --backend local-parquet \
  --json
```

入湖后自动触发派生索引：

```bash
obtainercli ingest path \
  --lake .loopai/lake.yaml \
  --input data/raw/my_data.jsonl \
  --dataset code_sft_seed \
  --tags domain=code,task=sft \
  --post-index embedding \
  --json
```

约束：

- embedding job 只读取成功提交的 `records` snapshot。
- embedding 结果必须记录 `embedding_model`、`embedding_dim`、`source_snapshot_id` 和切分策略。
- 同一数据集允许多套 embedding 并存。
- RAG/search 使用 `embeddings` 或外部向量库；标签采样出湖仍以 `record_tags` 为准。
- embedding 失败只影响索引状态，不影响入湖结果。

### 出湖采样

```bash
obtainercli sample \
  --lake .loopai/lake.yaml \
  --include-tag domain=code \
  --include-tag task=sft \
  --exclude-tag quality=bad \
  --n 10000 \
  --strategy stratified \
  --balance-by tag:source \
  --seed 42 \
  --format jsonl \
  --output outputs/code_sft_sample.jsonl \
  --json
```


垂域与处理程度导出示例：

```bash
obtainercli sample \
  --lake .loopai/lake.yaml \
  --domain code \
  --processing-level postprocessed_high_quality \
  --source-kind web \
  --include-tag lang=python \
  --include-tag quality=high \
  --n 5000 \
  --strategy stratified \
  --balance-by tag:source_domain \
  --output outputs/code_web_hq.jsonl \
  --json
```

采样策略：

- `random`：全局随机，支持 seed，结果可复现。
- `stratified`：按 `balance-by` 分层均分预算，适合防止某个来源或标签垄断。
- `weighted`：按 `tag_weight` 或质量分加权。
- `quota`：用户提供每个标签组合的配额 JSON。

标签表达式第一版保持简单：

```text
include: 多个 include-tag 默认为 AND
exclude: 任意命中即排除
value: 支持 exact match；第二版再加 glob/range
```

出湖流程：

1. 将条件拆成核心列条件和标签条件。
2. 用 `records` 的 `domain`、`processing_level`、`ingest_date` 分区先裁剪候选数据文件。
3. 用 `record_tags` 处理高基数、多值标签条件。
4. 对 records 候选集和 tags 候选 `record_id` 求交集。
5. 按 strategy 生成确定性 sample plan。
6. 回表读取 `records`。
7. 写 JSONL/Parquet/Arrow IPC。
8. 写 `exports` 审计记录和 `reports/export_<ts>.json`。

## 7. 模块拆分

建议新增包：

```text
loopai/obtainercli/
  __init__.py
  cli.py
  config.py
  errors.py
  logging.py
  models.py
  catalog.py
  schemas.py
  lake_init.py
  ingest.py
  sample.py
  tags.py
  index.py
  adapters/
    path_adapter.py
    hf_adapter.py
    kaggle_adapter.py
    web_adapter.py
  io/
    jsonl.py
    parquet.py
    canonical.py
  quality/
    validators.py
    dedup.py
  reports.py
```

旧代码复用策略：

- `HuggingFaceManager`、`KaggleManager`、`WebTools` 可以作为 adapter 底层能力复用。
- `DataConvertor` 中能复用的文件发现和格式转换逻辑要拆成无 LangGraph 依赖的纯函数。
- `ObtainerAgent`、`nodes/*` 不作为新 CLI 的运行时依赖；后续如果 WebUI 仍需 Obtainer，可让 WebUI 调 CLI 或调用 `loopai.obtainercli` 服务函数。

## 8. 依赖建议

核心依赖：

- `pyiceberg`：Iceberg catalog、schema、append、scan。
- `pyarrow`：Arrow/Parquet 数据内存格式和文件读写。
- `typer` 或 `click`：CLI。
- `pydantic`：配置和命令入参校验。

可选依赖：

- `duckdb`：本地大样本过滤、join、采样加速。
- `rich`：人类可读输出；`--json` 时禁用。

第一版不强制 Spark，不强制服务端 catalog。生产化时允许通过 `lake.yaml` 切换 catalog。

## 9. 错误和输出契约

Exit code：

- `0`：成功；如果成功但有警告，仍返回 `0`，并在 JSON 中写 `status=success_with_warnings` 和 `warnings`。
- `2`：参数或配置错误。
- `3`：数据校验失败，且没有启用 quarantine。
- `4`：外部源下载失败。
- `5`：catalog/commit 失败。
- `6`：采样候选不足。

JSON 输出统一格式：

```json
{
  "ok": true,
  "command": "sample",
  "lake_config": ".loopai/lake.yaml",
  "lake_root": "/data/loopai/lakes/dataflow-loopai",
  "status": "success",
  "warnings": [],
  "result": {},
  "report_path": "/data/loopai/lakes/dataflow-loopai/reports/export_20260531_120000.json"
}
```


成功但有警告时：

```json
{
  "ok": true,
  "status": "success_with_warnings",
  "warnings": [
    {
      "code": "ALLOW_SMALLER_TRIGGERED",
      "message": "Requested 10000 records but exported 742 because --allow-smaller was set."
    }
  ],
  "result": {
    "requested_size": 10000,
    "actual_size": 742
  }
}
```

错误时：

```json
{
  "ok": false,
  "error_code": "CANDIDATE_NOT_ENOUGH",
  "message": "Requested 10000 records but only 742 matched.",
  "hint": "Relax tags or use --allow-smaller.",
  "report_path": "/data/loopai/lakes/dataflow-loopai/reports/export_failed_20260531_120000.json"
}
```

## 10. 验收标准

第一版必须满足：

- `obtainercli lake init` 可在空目录稳定创建可用数据湖。
- `obtainercli ingest path` 可把 JSONL 写入 `records` 和 `record_tags`，重复执行不产生重复记录。
- `obtainercli sample` 可按标签组合导出 deterministic JSONL。
- 所有 CLI 命令支持 `--json`，Codex 可以只看 JSON 判断下一步。
- 单测覆盖 tag expression、幂等 ID、分层采样、quarantine、exit code。
- 集成测试覆盖 init -> ingest -> sample -> audit report。

## 11. 不在第一版范围

- 不重建 LangGraph Obtainer 工作流。
- 不做复杂 LLM 自动标注平台，只支持 adapter 写入基础自动标签。
- 不做在线服务或 WebUI 页面。
- 不做跨表事务之外的大规模 CDC/upsert；第一版以 append + duplicate policy 为主。
- 第一版不提供真正多 writer 并发 commit；本地模式通过文件锁串行化 commit。生产并发写入应使用合适 catalog 和对象存储。

## 12. 参考标准

- Apache Iceberg 使用 snapshot、manifest list、table metadata 管理大规模表版本、分区演进和扫描计划。
- Apache Parquet 是开放的列式数据文件格式，适合高效存储和检索。
- Delta Lake 文档也验证了 lakehouse 的关键能力：ACID、可扩展元数据、batch/stream 统一、schema enforcement、time travel。
- PyIceberg 提供 Python-native catalog、create table、append、scan 能力，适合作为 `obtainercli` 第一版的数据湖后端。

## 13. 待确认问题

默认 `lake_root` 不放在 repo 工作目录内，避免 TB 级数据拖垮 IDE/Codex 文件索引。建议数据默认落在项目外路径，例如 `/data/loopai/lakes/<project>` 或 `$LOOPAI_LAKE_ROOT/<project>`；repo 内只保留 `.loopai/lake.yaml` 指针配置。测试场景可以显式使用临时目录。
