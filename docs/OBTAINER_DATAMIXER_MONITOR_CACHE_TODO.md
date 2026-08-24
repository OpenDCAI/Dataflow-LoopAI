# Obtainer DataMixer Monitor Cache TODO

## 背景

当前 DataMixer lake 的 `load` 本身只是切换 `.loopai/lake.yaml` 指针；Workbench 的慢响应主要来自 `/obtainer/lake/monitor` 在 refresh 时全量物化 dashboard 数据。

现状问题：

- `load` 后进入 Workbench 会触发 monitor refresh。
- monitor refresh 会读取 `records`、`record_tags`、`quality_findings` 等虚拟表。
- 这些虚拟表当前会对 DataMixer `samples` 做全量查询和 Python 内存展开。
- 大 lake 下，refresh 变成全湖统计任务，而不是轻量状态读取。

目标：为每个 DataMixer warehouse 维护一个可增量更新的 monitor cache。默认 `load/refresh` 只读取 cache；写操作成功后同步维护轻量状态统计，并异步后台修正较重统计。cache 缺失、过期、字段不足、schema 升级或外部绕过 CLI 修改 warehouse 时，不阻塞前端或 CLI agent，可显示旧 cache/状态并显式触发后台 rebuild。

## 设计原则

- `.loopai/lake.yaml` 只保留当前 lake 指针，不承载 dashboard 统计。
- monitor cache 跟随 DataMixer warehouse，而不是跟随前端页面。
- 所有 skill 内涉及 DataMixer CLI/API 的 ingest、op、index、recipe、snapshot 操作，都应在提交后更新 monitor cache。
- 写操作后的 cache 维护以轻量、大致一致为目标；需要全量修正的统计交给后台异步 rebuild/update。
- 多 agent 并发操作下，cache 更新必须加锁并原子写入。
- 默认 refresh 不做全湖扫描；前端和 CLI agent 都不等待全量重建。
- embedding health 保持轻量实时探测，不纳入重型 monitor rebuild。

## 建议文件

新增当前 DataMixer warehouse 内部状态文件：

```text
<warehouse>/.loopai/monitor_state.json
```

建议字段：

```json
{
  "schema_version": 1,
  "warehouse": "/abs/path/to/warehouse",
  "updated_at": "2026-07-02T00:00:00Z",
  "catalog_db_mtime": 0,
  "catalog_db_size": 0,
  "catalog_db_wal_mtime": 0,
  "catalog_db_wal_size": 0,
  "audit_signature": {},
  "last_operation_id": "",
  "status": "fresh",
  "stale_reason": "",
  "rebuild": {
    "status": "idle",
    "job_id": "",
    "started_at": "",
    "finished_at": "",
    "message": ""
  },
  "summary": {},
  "charts": {},
  "latest": {},
  "warnings": []
}
```

## TODO

### 1. 新增 monitor cache 模块

- 新增 `monitor_state.py` 或同等模块。
- 提供：
  - `read_monitor_state(warehouse)`
  - `write_monitor_state(warehouse, state)`
  - `mark_monitor_stale(warehouse, reason)`
  - `update_monitor_delta(warehouse, delta)`
  - `is_monitor_state_fresh(warehouse, state)`
  - `rebuild_monitor_state(warehouse)`
- 写入时复用 DataMixer/Obtainer 的提交锁。
- 写入使用临时文件 + atomic rename，避免并发读到半截 JSON。
- 后台 rebuild/update 运行时写入 `rebuild.status = "running"` 或顶层 `status = "rebuilding"`，API 立即返回旧 cache + loading 状态。
- cache 缺失时可以写入最小状态文件，但不能阻塞执行全量统计。

### 2. 修改 `load`

`load` 只做：

- 校验 `warehouse/datamixer.toml`。
- 写 `.loopai/lake.yaml`。
- 读取 monitor cache 或最小状态。
- 如果 cache 不存在，返回 `cache_missing`，但不要自动全量重建。
- 如果 cache 过期，返回 `stale` 和具体原因。

`load` 不应该：

- 读取全量 records。
- 展开 tags。
- 统计 quality findings。
- 计算 embedding coverage。
- 排序 latest records。
- 重建 top tags。

### 3. 修改 `/obtainer/lake/monitor`

默认行为：

- 读取当前 `.loopai/lake.yaml`。
- 找到对应 DataMixer warehouse。
- 读取 monitor cache。
- 做轻量 freshness check：
  - `catalog.db` mtime。
  - `catalog.db` size。
  - `catalog.db-wal` mtime。
  - `catalog.db-wal` size。
  - `catalog.db-shm` mtime/size（存在时）。
  - audit 文件 mtime。
  - audit 文件 size。
  - cache `schema_version`。
- fresh 时直接返回 cache。
- stale 时返回 cache + `stale: true` + `stale_reason`，不自动全量重建。

新增显式重建入口：

```text
POST /obtainer/lake/monitor/rebuild
```

或 CLI：

```text
loopai-obtainercli dm lake monitor rebuild
```

该入口默认异步触发后台 rebuild/update，立即返回 job/loading 状态；前端和 CLI agent 不等待全量统计完成。

### 4. 操作后增量维护 cache

以下操作执行成功后应更新 monitor cache：

- `ingest`
- `agent-ingest`
- `dataset-acquisition-agent`
- `op run`
- `pipeline run`
- `dataflow agent-run`
- `index build`
- `recipe export`
- `snapshot create`
- `lineage` 相关写操作

第一版要求同步维护轻量状态统计，不要求与深度全量统计严格一致。每个操作应尽量返回或记录 delta，例如：

- 新增 records 数。
- 新增/更新 datasets 数。
- 新增 tags counter。
- 新增 quality findings counter。
- embedding indexed 数。
- latest records。
- latest ingest runs。
- latest exports。
- warning 变化。

当无法可靠计算 delta 时，可以：

- 保留旧 cache。
- 更新 `updated_at`、operation id、catalog/audit signature。
- 标记 `status = "stale"` 或 `status = "rebuilding"`。
- 后台异步 rebuild/update 修正 summary/charts/latest。

### 5. 前端 Workbench 状态显示

Workbench monitor 区分状态：

- `fresh`：cache 可直接使用。
- `stale`：cache 可显示，但需要提示原因。
- `cache_missing`：没有 cache，提示需要 rebuild。
- `rebuilding`：显式重建中，显示进度条。
- `error`：cache 读取或重建失败。

默认进入 Workbench 不触发全湖 rebuild。

## 可静态 cache / 增量维护的内容

这些字段不应每次 refresh 全量计算：

- `summary.datasets`
- `summary.assets`
- `summary.records`
- `summary.record_tags`
- `summary.embeddings`
- `summary.quality_findings`
- `summary.ingest_runs`
- `summary.exports`
- `summary.embedding_coverage`
- `summary.warnings`
- `summary.health_score`

这些图表也可以通过 delta 维护：

- `charts.composition.domain`
- `charts.composition.processing_level`
- `charts.composition.source_kind`
- `charts.composition.task_type`
- `charts.top_tags`
- `charts.quality_findings`
- `charts.ingest_trend`

这些 latest 队列可以固定长度维护：

- `latest.records`
- `latest.ingest_runs`
- `latest.quality_findings`
- `latest.exports`

这些存储/索引信息可以缓存：

- `catalog_db_bytes`
- `sample_count`
- `dataset_count`
- `index_vector_count`
- `fulltext_docs`

## 需要全量或深度计算的内容

以下任务不能长期依赖简单 config 加减，必须按需走全量或专门优化算法：

- 全湖去重重算。
- 语义标签覆盖率重建。
- failure taxonomy 配比校验。
- recipe plan/preview/export 的约束满足检查。
- SFT alpaca schema 全量校验。
- embedding index 与 samples 的一致性修复。
- snapshot digest / lineage 完整性校验。
- 外部绕过 DataMixer CLI 修改 catalog 后的状态修复。
- cache 缺失或 schema 升级后的首次重建。
- tag schema 改变后的 top tags / composition 重算。
- quality detector 版本变化后的质量分布重算。

## 全量重建优化方向

全量 rebuild 也不能沿用当前全量 Python list 展开方式。应优先使用：

- DataMixer catalog SQL 聚合。
- `COUNT/GROUP BY/LIMIT`。
- 流式扫描 `iter_query`。
- 分批 tags 解析。
- 分批 quality findings 解析。
- latest 查询使用 `ORDER BY ... LIMIT`。
- embedding coverage 使用索引文件计数或专门统计表。

## 验收标准

- `dm lake load` 在大 lake 上不触发全量样本读取。
- Workbench 默认 refresh 返回时间与 records 数量弱相关，主要取决于 cache 文件大小。
- cache fresh 时不调用 `store.catalog.query()` 全量查询。
- stale 时前端能展示旧数据和明确 stale reason。
- 显式 rebuild 才执行全量/深度统计。
- 多 agent 并发写入时 cache 不损坏。
- DataMixer ingest/op/index/recipe/snapshot 成功后 cache 自动更新。

## 第一版交付条件

### 延迟要求

- `GET /obtainer/lake/monitor`：只读 cache 和少量文件 stat；fresh/stale/cache_missing 都不触发全湖扫描。目标 P95 < 300ms，且耗时主要与 cache 文件大小相关。
- `dm lake load` / `POST /datamixer/lake/load`：只校验 warehouse、写 lake 指针、读取 monitor state。目标 P95 < 500ms，不调用 `store.catalog.query()` 全量查询。
- 写操作后的 monitor 维护：同步部分只更新轻量 counters、latest、signature、status。目标额外开销 < 200ms；重型修正必须异步。
- `POST /obtainer/lake/monitor/rebuild` 和 `dm lake monitor rebuild`：默认只 enqueue/trigger 后台任务，目标 < 500ms 返回；不等待全量统计完成。
- embedding health：保持轻量实时 probe，使用独立 endpoint，不阻塞 monitor refresh。

### 第一版目标操作

这些 skill 内 DataMixer 写操作需要接入轻量 monitor 更新：

- `dm lake load`
- `dm lake monitor rebuild`
- `ingest`
- `agent-ingest`
- `dataset-acquisition-agent`
- `op run`
- `pipeline run`
- `dataflow agent-run`
- `index build`
- `recipe export`
- `snapshot create`
- `lineage` 相关写操作（如后续新增写命令）

### 第一版非目标

- 不要求写操作后的 charts/top tags/quality distribution 与全量 rebuild 严格一致。
- 不在 Workbench 默认 refresh 中自动执行全量 rebuild。
- 不把 recipe plan/preview/export 的深度约束检查改造成 cache 逻辑。
- 不在 monitor cache 中缓存 embedding endpoint probe 结果。

## 建议实施分期

### Phase 1：默认路径不扫全湖

- 新增 monitor state 读写模块。
- `/obtainer/lake/monitor` 默认只读 cache。
- `dm lake load` 只做展示状态读取，不 rebuild。
- freshness check 覆盖 `catalog.db`、`catalog.db-wal`、`catalog.db-shm` 和 audit JSONL。
- 新增异步 rebuild/update 入口；第一版后台任务可先复用现有 monitor 构建逻辑。

### Phase 2：skill CLI 写路径轻量维护

- 覆盖 skill 内涉及的 DataMixer CLI 指令。
- 写成功后同步更新 counts/latest/signature/status。
- 无法精确维护的字段标记 stale/rebuilding，并触发后台更新。

### Phase 3：优化后台 rebuild

- 将全量 rebuild 从 Python 全量 list 展开改为 SQL 聚合、流式扫描和分批解析。
- latest 查询改用 `ORDER BY ... LIMIT`。
- embedding coverage 优先使用索引文件计数或专门统计表。
