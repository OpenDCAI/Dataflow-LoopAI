# DataMixer 数据质量分级重构施工清单

本文档用于单人顺序完成 DataMixer 数据质量分级重构。施工过程中按本文顺序推进，不并行拆分，不跳过底层约束直接修改 UI 或文档。

## 1. 目标

为所有新入湖数据增加必填核心字段 `quality_level`：

| 等级 | 含义 | 典型数据 |
| --- | --- | --- |
| `L1` | 原始来源数据 | 原始网页、原始抓取结果、未经处理的来源文件 |
| `L2` | 初步处理的原始数据 | 网页正文提取、格式解析、基础清洗后的数据 |
| `L3` | 标准处理数据 | SFT、DPO、标准训练样本 |
| `L4` | 内部精炼后的高质量数据 | 经数据湖内部精炼管线制作的数据 |

本次重构完成后：

- 所有新入湖操作必须显式提供 `quality_level`。
- 合法值严格为大写 `L1`、`L2`、`L3`、`L4`。
- 字段可以被查询、过滤、统计、监控和展示。
- Obtainer Skill、Agent policy、Codex prompt 和命令示例必须携带等级。
- `L4` 当前和其他等级一样直接传入，不增加二次确认。

## 2. 不在本次范围内

- 不迁移、复用或回填旧数据湖。
- 不把历史数据或缺少等级的数据默认标为 `L3`。
- 不根据 `stage`、`task_type`、`processing_level` 或 `quality_score` 自动推断等级。
- 不复用或重命名现有字段：
  - `stage` 仍表示训练阶段。
  - `processing_level` 仍表示操作处理状态。
  - `quality_score` 仍表示数值质量评分。
- 不实现外部 Agent 声明 `L4` 时的二次确认。
- 不实现精炼管线自动将 `L3` 晋升为 `L4` 的新协议。
- 不修改源码中使用 L1/L2/L3/L4 表示架构层次的既有注释。

## 3. 当前验收基线

验收测试已写入：

```text
tests/test_datamixer_quality_levels_acceptance.py
```

当前执行结果：

```text
12 failed, 1 passed
```

唯一通过的用例是 `L5` 当前会因为 CLI 不认识 `--quality-level` 而被拒绝。这不代表功能已经实现；成功入湖、schema、持久化等用例会防止这个用例假通过。

## 4. 施工顺序

### 阶段一：建立 Schema 契约

修改文件：

```text
loopai/agents/Obtainer/datamixer/schema.py
loopai/agents/Obtainer/datamixer/catalog.py
loopai/agents/Obtainer/datamixer/filterc.py
```

任务：

- [ ] 在 `schema.py` 中增加：

```python
QUALITY_LEVELS = ("L1", "L2", "L3", "L4")
```

- [ ] 将 `quality_level` 加入 `CORE_FIELDS`：
  - SQLite 类型为 `TEXT`。
  - dimension 为 `quality`。
  - `indexed=True`。
  - description 明确说明四个合法值。
- [ ] 更新 DataMixer schema version。
- [ ] 在 `schema.describe()` 中暴露合法值。推荐顶层返回：

```json
{
  "quality_levels": ["L1", "L2", "L3", "L4"]
}
```

- [ ] 确认 `Catalog` 新建 warehouse 时创建该列和索引。
- [ ] 不编写旧 warehouse 的迁移、回填或默认值逻辑。
- [ ] 确认 `filterc` 从核心字段列表获得 `quality_level`，允许：

```sql
quality_level = 'L3'
```

阶段验收：

```bash
loopai-obtainercli dm --root /tmp/quality-level-lake init --json
loopai-obtainercli dm --root /tmp/quality-level-lake schema --json
loopai-obtainercli dm --root /tmp/quality-level-lake columns --json
```

`schema` 和 `columns` 都必须出现 `quality_level`，schema 必须返回四个合法值。

### 阶段二：在 Store 建立不可绕过的约束

修改文件：

```text
loopai/agents/Obtainer/datamixer/store.py
```

任务：

- [ ] 在 `DataStore.ingest_records()` 中校验每条记录最终合并后的 `quality_level`。
- [ ] 缺少等级时抛出包含 `quality_level` 的 `ValueError`。
- [ ] 非法等级时抛出包含非法值的 `ValueError`。
- [ ] 对批次默认等级先进行校验，必须发生在 CAS blob 和 sample 写入之前。
- [ ] CLI 传入的批次等级不能被输入 JSONL 中的同名字段静默覆盖。
- [ ] 如果输入行和批次参数等级冲突，整次入湖失败并给出明确错误。
- [ ] 不允许出现已经写入部分样本后才发现等级非法的情况。

阶段验收：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py \
  -k "store_enforces_quality_level"
```

缺少等级和传入 `L5` 都必须零样本写入。

### 阶段三：修改 `dm ingest`

修改文件：

```text
loopai/agents/Obtainer/datamixer/cli.py
```

任务：

- [ ] 给 `dm ingest` 增加必填参数：

```python
sp.add_argument(
    "--quality-level",
    dest="quality_level",
    required=True,
    choices=schema.QUALITY_LEVELS,
)
```

- [ ] 参数校验必须在打开 warehouse、创建 dataset 或写 lineage 之前完成。
- [ ] 将等级写入 `defaults["quality_level"]`。
- [ ] ingest JSON 结果增加 `quality_level`，便于上层调用方记录。
- [ ] ingest lineage 的 `defaults` 中必须包含等级。
- [ ] 缺少参数时返回非零状态。
- [ ] `L5` 必须因为非法枚举被拒绝，而不是因为 CLI 不认识参数。

阶段验收：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py \
  -k "ingest_requires or ingest_rejects or all_quality_levels or schema_and_columns"
```

还需要手工确认：

```bash
loopai-obtainercli dm --root /tmp/quality-level-lake ingest example \
  --file /tmp/example.jsonl \
  --quality-level L3 \
  --json

loopai-obtainercli dm --root /tmp/quality-level-lake query \
  --filter "quality_level = 'L3'" \
  --columns sample_id,dataset_id,quality_level \
  --json
```

### 阶段四：修改 `agent-ingest`

修改文件：

```text
loopai/agents/Obtainer/datamixer/cli.py
loopai/agents/Obtainer/datamixer/harness.py
loopai/agents/Obtainer/datamixer/codex.py
```

任务：

- [ ] 给 `dm agent-ingest` 增加相同的必填 `--quality-level` 参数。
- [ ] builtin 路径将等级传给 `harness.agent_ingest()`。
- [ ] harness 写入 Store 时携带批次等级。
- [ ] codex 路径将等级传给 `codex.codex_ingest()`。
- [ ] `codex.build_prompt()` 增加：

```text
QUALITY_LEVEL=L3
```

- [ ] Codex prompt 中要求最终入湖命令使用传入的 `QUALITY_LEVEL`，不能自行更换。
- [ ] agent-ingest JSON 结果建议返回 `quality_level`。
- [ ] 不增加 `--confirm-l4`、交互确认、环境确认或其他 L4 特殊流程。

阶段验收：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py \
  -k "agent_ingest"
```

必须同时验证 builtin 的 `L2` 和 `L4`。`L4` 只能使用 `--quality-level L4`，不得要求额外参数。

补充一个 Codex mock 测试，确认生成的 prompt 中包含传入等级。

### 阶段五：修改 Python Wrapper 和 Adapter

修改文件：

```text
loopai/skills/ObtainerCLI/ingest.py
loopai/skills/ObtainerCLI/datamixer_adapter.py
```

任务：

- [ ] 给 `ingest_path()` 增加无默认值的必填参数：

```python
quality_level: str
```

- [ ] 给 `ingest_datamixer_path()` 增加相同的必填参数。
- [ ] 非法值校验必须发生在 `ensure_tables()`、创建 dataset 和写 audit 之前。
- [ ] `_record_metadata()` 写入 `quality_level`。
- [ ] Store 接收到的记录包含等级。
- [ ] dataset metadata 可以记录本次入湖等级，但 record 字段仍是事实来源。
- [ ] ingest run/config snapshot 记录等级。
- [ ] audit records 包含等级。
- [ ] `_legacy_record_row()` 增加等级。
- [ ] `_legacy_tag_map()` 增加等级。
- [ ] 检查 quality finding preview 是否需要携带所属记录等级。
- [ ] idempotency duplicate 返回结果保留等级信息。

阶段验收：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py \
  -k "python_ingest_wrapper or monitor_exposes"
```

另外补充 wrapper 传入 `L5` 时零文件、零表、零样本写入的测试。

### 阶段六：修改监控数据

修改文件：

```text
loopai/skills/ObtainerCLI/monitor_state.py
api/app/utils/obtainer/monitor.py
```

任务：

- [ ] 空 monitor payload 的 composition 增加：

```json
"quality_level": {}
```

- [ ] lightweight monitor 直接按核心列 `quality_level` 分组统计。
- [ ] deep monitor 的 composition 增加质量等级。
- [ ] `_record_preview()` 返回 `quality_level`。
- [ ] latest records 返回等级。
- [ ] monitor cache schema version 升级，避免旧结构缓存被当成新结构。
- [ ] 保留现有 `processing_level` composition，不得覆盖或删除。
- [ ] 正常新湖的统计结果不应出现 `unknown` 等级。

接口目标：

```json
{
  "charts": {
    "composition": {
      "quality_level": {
        "L1": 1,
        "L2": 1,
        "L3": 1,
        "L4": 1
      }
    }
  }
}
```

阶段验收：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py \
  -k "monitor_exposes"
pytest -q tests/test_obtainer_monitor.py
```

### 阶段七：修改数据湖 UI

修改文件：

```text
ui/src/views/manage/obtainerLake/index.vue
```

任务：

- [ ] 默认 composition mode 从 `processing_level` 改为 `quality_level`。
- [ ] composition 选项增加 `Quality Level`。
- [ ] 原来的 `processing_level` 选项保留，并命名为 `Processing Level`。
- [ ] latest records 表增加 `quality_level` 列。
- [ ] 不再使用同一个 `Level` 文案同时表示两种概念。
- [ ] 中英文文案明确区分：
  - Quality Level / 质量等级
  - Processing Level / 处理阶段

阶段验收：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py \
  -k "monitor_dashboard"
```

启动 UI 后手工确认四级 composition 和最新记录列均能显示。

### 阶段八：修改 Obtainer Skill、Policy、Prompt 和文档 Demo

修改文件：

```text
skills/obtainer/SKILL.md
docs/OBTAINERCLI_USAGE.md
loopai/skills/ObtainerCLI/dataset_acquisition_agent.py
loopai/skills/ObtainerCLI/datamixer_ingest_prompt.md
```

任务：

- [ ] `SKILL.md` 中 code repair SFT ingest 示例增加：

```bash
--quality-level L3
```

- [ ] `SKILL.md` 中对应 agent-ingest 示例增加 `--quality-level L3`。
- [ ] `OBTAINERCLI_USAGE.md` 中两个对应示例同步增加 `L3`。
- [ ] acquisition worker 的完整 metadata 要求加入 `quality_level`。
- [ ] generic acquisition 命令增加：

```bash
--quality-level <L1|L2|L3|L4>
```

- [ ] worker policy 写明四级判定规则。
- [ ] worker 不确定时应选择较低等级并在报告中说明，不能省略参数。
- [ ] `final_report.json` 记录每个数据集选择的等级和理由。
- [ ] Codex ingest prompt 要求先确定等级，再执行入湖。
- [ ] Codex ingest 命令必须包含：

```bash
$DM ingest "$DATASET" ... --quality-level "$QUALITY_LEVEL"
```

- [ ] prompt 最终结构化摘要增加 `quality_level`。
- [ ] 不加入 L4 二次确认描述。

Demo 选级规则：

- 原始网页下载示例使用 L1。
- 正文提取或初步清洗示例使用 L2。
- SFT、DPO、code repair 示例使用 L3。
- 只有明确写明经过内部精炼管线的示例才使用 L4。

阶段验收：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py \
  -k "skill_and_agent_ingest_demos"
```

检查所有示例：

```bash
rg -n "dm .*ingest|agent-ingest|\\$DM ingest" \
  skills docs loopai/skills
```

### 阶段九：更新既有测试和调用方

重点文件：

```text
tests/test_obtainercli_lake.py
tests/test_obtainer_monitor.py
```

任务：

- [ ] 搜索所有 `dm ingest` 测试命令并显式补等级。
- [ ] 搜索所有 `agent-ingest` 测试命令并显式补等级。
- [ ] 搜索所有 `ingest_path()` 调用并显式补等级。
- [ ] 搜索所有 `ingest_datamixer_path()`、`agent_ingest()`、`codex_ingest()` 调用。
- [ ] 不允许为了减少修改而在测试 helper 中统一默认成 L3。
- [ ] 根据 fixture 实际语义选择等级：
  - 原始数据：L1。
  - 提取或清洗数据：L2。
  - SFT/DPO/标准训练集：L3。
  - 明确模拟内部精炼产物：L4。
- [ ] 为 CLI 参数与输入行等级冲突补充测试。
- [ ] 为 Codex prompt 等级传播补充测试。
- [ ] 为 wrapper 非法等级零写入补充测试。

搜索命令：

```bash
rg -n '"ingest"|agent-ingest' tests
rg -n "ingest_path\\(|ingest_datamixer_path\\(|agent_ingest\\(|codex_ingest\\(" \
  loopai api tests
```

### 阶段十：最终验收

先运行质量等级专项测试：

```bash
pytest -q tests/test_datamixer_quality_levels_acceptance.py
```

目标：全部通过，不允许 xfail、skip 或通过删除断言规避。

再运行相关回归：

```bash
pytest -q tests/test_obtainercli_lake.py tests/test_obtainer_monitor.py
```

运行静态检查：

```bash
ruff check tests/test_datamixer_quality_levels_acceptance.py
git diff --check
```

最后执行手工 smoke test：

1. 缺少等级的普通 ingest 失败，dataset/sample 均不增加。
2. `L5` 入湖失败，错误明确指向合法枚举。
3. L1、L2、L3、L4 分别成功入湖。
4. query 可以按 `quality_level` 过滤。
5. lineage 中存在等级。
6. builtin agent-ingest 的 L2 和 L4 都成功。
7. L4 不要求二次确认。
8. Python wrapper 缺少等级时失败。
9. monitor composition 正确统计四个等级。
10. UI 默认展示质量等级，并保留 processing level。
11. Skill 和文档中的所有入湖命令都携带 `--quality-level`。

## 5. 完成定义

只有同时满足以下条件才算施工完成：

- [ ] `quality_level` 是可索引、可查询的核心字段。
- [ ] Store、CLI、agent-ingest 和 Python wrapper 都不能绕过必填校验。
- [ ] L1–L4 都能持久化到 catalog、lineage、audit 和 monitor。
- [ ] 非法或缺失等级不会造成任何样本写入。
- [ ] L4 没有额外确认逻辑。
- [ ] monitor API 和 UI 能区分质量等级与处理阶段。
- [ ] Obtainer Skill、Agent policy、Codex prompt 和使用文档全部更新。
- [ ] 专项验收测试全部通过。
- [ ] 相关既有回归测试全部通过。
- [ ] 没有增加旧湖迁移、默认 L3 或自动等级推断逻辑。
