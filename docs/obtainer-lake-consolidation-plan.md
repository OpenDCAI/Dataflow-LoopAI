# Obtainer 数据湖整合 + 导出/导入 + 数据导入 修改计划

日期：2026-08-13
状态：待评审

## 1. 目标与范围

1. **目录整合**：数据湖资产 + obtainer 资产只存在于两个目录
   - `.datamixer/`：数据湖唯一位置（指针 + warehouse）
   - `outputs/obtainer/`：obtainer 资产唯一位置（runs / dataflow_work / events / logs / agent codex 状态）
   - `codex_home/`（starter 主 agent 专用）留在仓库根，不动
2. **一键导出/导入二进制**：`tar.gz`，**仅数字资产**（运行时状态不导出）
3. **额外数据导入**：支持对**已有数据的湖增量导入**、对**空湖批量导入**
4. **不做**：运行时状态（thread/status/logs/events）迁移工具

测试要求：**先用现有真实数据湖写好测试用例，再改代码**；测试要足够真实（走真实 DataStore/Catalog/ContentStore，不 mock）。

---

## 2. 目标目录规范

```
项目根/
├─ codex_home/                 # starter 专用（不动）
├─ .datamixer/                 # = 数据湖
│  ├─ lake.yaml                # 湖指针（替代 .loopai/lake.yaml）
│  └─ warehouse/               # datamixer warehouse（全量数字资产）
│     ├─ datamixer.toml / catalog.db
│     ├─ blobs/  index/  exports/  snapshots/  lineage/
│     ├─ dataset_cards/  quality_reports/  obtainercli_audit/
│     └─ .loopai/              # 运行时缓存：monitor_state.json、campaign_logs（不导出）
└─ outputs/obtainer/           # = obtainer 资产
   ├─ runs/<run_id>/           # orchestrator/acquisition/dataflow/sft-export 的 run
   │    （含 final_report.json、recipe/、出库 JSONL 等数字产物；thread/status/logs 为运行时）
   ├─ dataflow_work/           # dataflow chunked runner
   ├─ events/                  # obtainercli 事件 pkl（运行时）
   ├─ logs/                    # 运行时
   └─ .codex/
      ├─ worker/               # 原 codex_home_worker（仅 acquisition 用 → 迁入）
      └─ dataflow/             # 原 codex_home_dataflow（仅 dataflow 用 → 迁入）
```

### 2.1 codex home 归属（已确认）
| 目录 | 使用方 | 是否仅 obtainer | 处置 |
|---|---|---|---|
| `codex_home/` | starter 主 agent + obtainer codex 模块 | 否 | 保留仓库根 |
| `codex_home_worker/` | dataset-acquisition-agent | 是 | 迁 `outputs/obtainer/.codex/worker` |
| `codex_home_dataflow/` | dataflow agent | 是 | 迁 `outputs/obtainer/.codex/dataflow` |

---

## 3. 目录整合改动清单

### 3.1 默认湖指针 → `.datamixer/lake.yaml`
- 涉及默认值（`.loopai/lake.yaml` → `.datamixer/lake.yaml`）：
  - `loopai/skills/ObtainerCLI/lake_manager.py`（`active_link` 默认、`scan_lake_candidates`）
  - `loopai/skills/ObtainerCLI/cli.py`（`dm --lake` 默认）
  - `loopai/skills/ObtainerCLI/orchestrator_agent.py`、`dataset_acquisition_agent.py`、`sft_export_agent.py`（`--lake` 默认）
  - `api/app/controllers/obtainer.py`（`_resolve_lake_path` 默认）
  - `ui/src/views/manage/obtainerLake/index.vue` 等（`lakePath` 默认）
- `warehouse_root()` 已按 lake.yaml 解析，新布局天然支持（root=`.datamixer`，warehouse=`.datamixer/warehouse`）

### 3.2 规范化 run 目录
- 新增助手 `obtainer_run_root()` → `outputs/obtainer/runs`；orchestrator/各 agent 默认 `--run outputs/obtainer/runs/<ts>_<name>`
- `skills/obtainer/SKILL.md`、AGENTS.md 示例路径同步

### 3.3 codex home 迁移（仅 obtainer 两个）
- `dataset_acquisition_agent._worker_codex_home()` → `outputs/obtainer/.codex/worker`
- dataflow 的 `codex_home_dataflow` → `outputs/obtainer/.codex/dataflow`
- `codex.py:codex_home()`（starter）不变

### 3.4 兼容
- `dm lake scan` 仍能发现旧 `.loopai/lake.yaml` 与旧 `lake/`；新默认写 `.datamixer/`
- web_pipeline 的 status 发现逻辑补 `.datamixer` 布局（已有部分推断）

---

## 4. 导出 / 导入 bundle（tar.gz，仅数字资产）

### 4.1 导出 `dm lake export-bundle --out datamixer-bundle-<ts>.tar.gz [--include-runtime]`
打包固定布局：
```
bundle/
├─ manifest.json            # schema_version、created_at、source_root、datasets/records 数、文件 sha256
├─ .datamixer/lake.yaml
├─ .datamixer/warehouse/    # catalog.db、blobs、index、exports、snapshots、lineage、
│                           #   dataset_cards、quality_reports、obtainercli_audit、model_pool
└─ outputs/obtainer/        # runs/ 下数字产物（final_report.json、recipe/、出库 JSONL）
```
- **默认排除**（运行时/缓存）：codex home、`thread.json`/`status.json`、`logs/`、`events/`、`monitor_state.json`、`campaign_logs/`、`downloads/` 中间文件、`llm_cache/`、锁文件
- `--include-runtime` 时全量（除 codex home、锁）
- 流式 tar.gz（gzip，大湖不占双份内存）

### 4.2 导入 `dm lake import-bundle --file <bundle> --target <project-root>`
1. 解包 → 校验 manifest（schema、sha256、数据集/记录数）
2. 写 `.datamixer/lake.yaml`（新绝对 root）
3. **前缀重写绝对路径**：metadata 文本里 `old_source_root` → 新 root（lake.yaml、monitor_state、thread/final_report、export manifest、lineage、events）
4. 打开 catalog 校验记录数一致 → 设为 active lake

---

## 5. 额外数据导入（新功能，测试先行）

### 5.1 目标
`dm lake import-data`（或 `dm data import`）——把**数据包**导入湖：
- **增量**：对已有数据的湖，合并追加（dataset 级幂等：sample_id 去重、内容去重、新增 dataset 则建）
- **批量**：对空湖全量导入（多个数据集 + dataset card + 元数据）
- 输入形态：目录数据包（`manifest.json` + 各 `*.jsonl` + `dataset_cards/*.md` + 元数据）或单 JSONL
- 复用/增强现有 `ingest`（`add_sample` 已按 `(dataset,cid,core)` 幂等合并）

### 5.2 与全湖 bundle 的区别
- 全湖 bundle = 湖快照迁移（restore/替换）
- import-data = 向湖里**追加/批量注入数据**（不替换现有湖）

### 5.3 测试先行（用现有真实湖）
- **fixture**：用真实 ingest 流程构建一个"真实副本"warehouse（含 catalog/blobs/index/audit/exports），或从现有湖导出子集还原——保证与线上同构、非合成小样例
- **用例（先写，跑红，再实现）**：
  1. 空湖批量导入：多数据集 + dataset card + 元数据 → 校验 catalog 记录数、blobs、audit、monitor 一致
  2. 已有湖增量导入：同 dataset 追加（去重合并）、跨 dataset 新增、重复导入幂等
  3. 导入后一致性：index/embedding 计数、exports 审计、quality_levels 聚合
  4. 大文件/分片（如 10 万行级）性能与断点
- 现有湖上先跑一遍导出/导入 smoke，确认 fixture 与用例真实可用

---

## 6. 实施顺序（TDD）

| 阶段 | 内容 | 验收 |
|---|---|---|
| 0 | 用现有湖写真实测试用例（fixture + 用例），先跑红 | 用例在现有代码上失败/部分通过 |
| 1 | 数据导入实现（增量/批量） | 阶段 0 用例转绿 |
| 2 | 目录整合：默认指针 `.datamixer/lake.yaml`、run 规范化、codex home 迁移 | 新布局下全链路跑通 |
| 3 | 导出/导入 bundle（tar.gz、仅数字资产） | 真实湖导出→导入→校验一致 |
| 4 | UI/文档默认值同步、示例更新 | 前端湖页/任务卡片在新布局工作 |

---

## 7. 风险与注意
- 绝对路径散落：导入必须做前缀重写（只动文本元数据，不动 blob）
- 大湖性能：tar.gz 流式；import 分批
- 兼容：旧 `.loopai/lake.yaml` 仍可扫描/加载；新写入默认 `.datamixer`
- 不做运行时迁移：旧 `outputs/obtainer_run*` 遗留目录由新规范自然取代，不提供迁移脚本
