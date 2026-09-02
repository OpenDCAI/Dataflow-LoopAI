# Analyzer 问题记录与解决方案

## 文档目的

本文档记录 Analyzer 在独立运行、状态管理、断点恢复、事件流、配置安全、报告生成和数据分析过程中遇到的主要工程问题，以及当前采用的解决方式。

本文档是问题排查摘要，不替代接口说明。对外调用方式和完整参数请参考同目录下的 `SKILL.md`。

## 当前架构概览

Analyzer 的 Python 能力实现位于：

```text
loopai/skills/Analyzer/
```

Codex、WebUI 和 standalone CLI 均通过 Skill 层调用 Analyzer。核心流水线按任务类型选择：

```text
Code / Text2SQL:
eval_model -> analyze_result -> draw_conclusion -> finish

General Text:
metric_recommend -> metric_score -> analyze_metric_report -> finish
```

运行产物、事件和 checkpoint 按任务及运行版本隔离：

```text
<output_dir>/<task_id>/analyzer/<version_id>/
```

## 问题总览

| 问题 | 典型现象 | 主要原因 | 当前解决方式 |
| --- | --- | --- | --- |
| Analyzer 依赖 Starter/LangGraph | 无法单独调用，standalone 在 LangGraph runtime 外报错 | 运行入口和业务节点绑定过紧 | 将对外运行能力集中到 `loopai/skills/Analyzer`，提供 Python 与 CLI 入口 |
| 导入速度慢 | `--list-nodes` 卡住，导入时初始化 PromptLoader、OneEval 或模型 | 顶层 eager import 过重 | 使用 lazy import；`--list-nodes` 直接读取静态节点列表 |
| standalone stream 报错 | `Called get_config outside of a runnable context` | 节点直接调用 LangGraph 的 `get_stream_writer()` | standalone 使用公共事件 writer；缺少 LangGraph runtime 时采用安全 fallback |
| `--resume` 出现 EmptyInputError | `graph.invoke(None)` 无法恢复 `__start__` | 当前 LangGraph 版本不会自动把 `None` 替换为 checkpoint state | 改用 function-level pipeline 和持久化 state 恢复，不再通过 `invoke(None)` 续跑 |
| 跨进程无法恢复 | 第一次运行有状态，第二次 CLI 找不到 checkpoint | `MemorySaver` 只保存在当前进程内存中 | 使用 SQLite checkpoint 持久化运行状态 |
| resume 重跑已完成节点 | 中断在 `draw_conclusion`，恢复后又执行前序节点 | 把完整 state 重新作为图输入会从 START 执行 | 根据 `current`、`last_completed` 和 node checkpoint 选择恢复入口 |
| 同一 task 的不同轮次冲突 | version2 被误认为已经跑完，或恢复到旧版本 | checkpoint 只按 `task_id` 区分 | checkpoint 主键和目录同时使用 `(task_id, version_id)` |
| version_id 变成 default | 产物落在 `analyzer/default`，WebUI 无法对应运行版本 | runner、writer 和调用参数各自生成或覆盖版本号 | 统一复用 writer/runtime 的 `version_id`，显式新运行才创建新版本 |
| 事件存在但 WebUI 不显示 | `analyzer.pkl` 有数据，右侧 Info 面板为空 | 事件读取端和写入端的 version 目录协议不一致 | 对齐版本化事件路径，并由公共 `get_event_writer` 写入 |
| 结束状态未更新 | 进度到 100%，但节点仍显示 running/failed | `emit_success`、`emit_error` 未传入 writer | 所有终态返回传入 `stream_writer=writer`，显式标记 completed/failed |
| 进度条跳跃或恢复倒退 | 进度从 45% 跳到 100%，resume 后又从较小值开始 | 只按节点设置进度，LLM 等待期间缺少事件，恢复时未读取节点内进度 | 增加节点内进度事件、等待心跳和 `node_progress` checkpoint；恢复进度不低于已保存值 |
| API Key 出现在 State/WebUI | Analyzer 面板直接显示密钥，模型可能读取密钥 | 密钥被写入 `state["analyzer"]` 或打印结果 | 优先从 system runtime/环境变量注入；事件和 `print-result` 对 `api_key`、`token`、`*_key` 脱敏 |
| 输出文件不便查询 | 报告和 `analyzer.pkl` 分散在 default 或多层错误目录 | task/version 路径构造不统一 | 统一保存到 `<output_dir>/<task_id>/analyzer/<version_id>/` |
| 历史报告无法比较 | Analyzer 只能分析单轮结果 | 没有 baseline 输入及样本匹配逻辑 | 支持 `baseline_result_path`，输出 `historical_comparison` 和报告小节 |
| 大数据量分析超时 | `analyze_result` 或结论阶段长时间等待，代理出现 524/timeout | Prompt 证据过长，代理网关时间限制较短 | 默认延长请求 timeout；首次失败后压缩证据重试，并按阶段保存 checkpoint |
| 多 benchmark 输入不明确 | HumanEval、MBPP 等结果只能分开分析或路径被覆盖 | 仅支持单个 `eval_result_path` | 同任务类型 benchmark 可合并分析，同时保留每个 benchmark 的独立统计 |
| `other` 占比过高 | 数据分桶把大量预算给不可操作的 `other` | 只按 Judger 原始错误标签和比例分配 | 根据执行证据重分类；无法归因的样本进入诊断池，不占训练预算 |
| 错误比例不等于训练收益 | 高频错误被分配大量数据，但实际学习效率可能较低 | 原始策略把观察频率近似当成数据需求 | 同时考虑置信度、严重性、迁移价值、学习效率先验和成本，并预留小规模试训后的动态更新 |

## 1. 独立运行与 Skill 接入

### 原始问题

早期 Analyzer 主要通过 Starter 和 LangGraph 调起。Codex 或人工脚本直接调用时，会加载较重的 Agent、节点和模型依赖，部分节点还假设自己处于 LangGraph runnable context。

### 当前方案

- 对外入口统一放在 `loopai/skills/Analyzer`。
- `from loopai.skills.Analyzer import run` 供 Codex/Sub-Agent 调用。
- `run_analyzer_standalone(...)` 供 Python 进程内调用。
- `loopai-analyzer` 和 standalone 脚本供人工调试。
- 核心节点仍保留原业务职责，没有因为接入 Codex 被复制成第二套实现。

### 注意事项

`examples/scripts/run_analyzer_standalone.py` 是人工调试入口，系统或 Codex 应优先调用 Skill 的 Python 入口，不应通过拼接脚本命令来模拟能力调用。

## 2. State、Configer 与 Checkpoint 的职责

Analyzer 当前同时面对两类状态需求：

1. **任务运行态**：当 `DB_PATH` 和 `TASK_ID` 可用时，通过 Configer 读取和更新，供系统、WebUI 和其他 Sub-Agent 查询。
2. **断点执行态**：通过 version-scoped SQLite checkpoint 保存节点、批次和进度，供 standalone/Codex 在进程中断后恢复。

二者用途不同：Configer 是系统任务状态接口，checkpoint 是 Analyzer 长任务的恢复记录。checkpoint 不能替代数据库任务状态，数据库状态也不一定包含足够细的节点内恢复信息。

### 关键字段

```text
state["current"]
state["last_completed"]
state["version_id"]
state["analyzer"]["version_id"]
state["analyzer"]["checkpoint_path"]
```

### 恢复规则

- 默认先检查同一 `task_id` 下是否存在未完成版本。
- 未完成版本存在时，继续该版本，不自动创建新 `version_id`。
- `--new-version` 或 `new_version=True` 才明确开启新运行。
- 已完成版本不会阻止同一任务创建下一版本。
- `--from-node` 用于人工强制指定恢复节点。
- 节点内已经保存批次进度时，优先从该批次继续；无法细粒度恢复的 LLM 单次请求，只能从该请求开始重试。

## 3. StreamEvent 与 WebUI 状态

Analyzer 直接使用公共事件接口：

```python
from loopai.common.event_tool import StreamEvent, get_event_writer
```

不同功能阶段使用不同 `current`，例如：

```text
analyzer.initializing
analyzer.pipeline
analyzer.eval_model
analyzer.analyze_result
analyzer.draw_conclusion
analyzer.completed
analyzer.failed
```

这样 WebUI 可以分别展示各阶段进度，避免不同节点互相覆盖。

成功和失败必须传入同一个 writer：

```python
emit_success(data=result, stream_writer=writer)
emit_error(error, stream_writer=writer)
```

事件文件保存在当前版本目录下，读取端也必须使用同一个 `task_id` 和 `version_id`。

## 4. 配置与敏感信息

重要运行参数不应写死在普通 Analyzer state 中。推荐来源包括：

```text
system.analyzer_api_key
system.analyzer_model
system.analyzer_base_url
ANALYZER_API_KEY
ANALYZER_MODEL
ANALYZER_BASE_URL
TASK_ID
DB_PATH
ANALYZER_CHECKPOINT_PATH
```

旧配置中的 `analyzer.analyze_api_key` 只作为兼容 fallback。任何 stdout、事件、报告和 `--print-result` 输出都必须对以下字段脱敏：

```text
api_key
analyze_api_key
*_api_key
token
*_key
```

## 5. 历史对比报告

设置 `baseline_result_path` 后，Analyzer 会比较当前评测记录和历史评测记录，至少包括：

- 样本总数；
- passed、failed 和 pass rate；
- 分数差异；
- 错误类型分布变化；
- improved cases；
- regressed cases。

样本优先按 `sample_id`、`task_id`、`id` 匹配，缺少标识时按行号匹配。baseline 缺失或不可读时只写 warning，不中断主分析流程。

## 6. 大数据量与超时处理

大规模 benchmark 的主要耗时来自：

- 样本级判因循环；
- 大量失败样本进入 Prompt；
- LLM 生成 summary、分析结论和数据建议；
- 第三方代理网关的固定超时。

当前处理方式：

1. 分批处理样本，并在批次完成后保存进度。
2. 默认模型请求 timeout 为 300 秒，可通过运行配置调整。
3. 首次请求保留完整证据。
4. 如果出现 timeout/524，第二次请求压缩代表样本和冗余上下文。
5. 每个阶段完成后立即保存 state，避免重新执行已完成阶段。

需要注意：一个尚未返回的单次外部 LLM 请求无法从 token 中间恢复。细粒度恢复依赖“把大任务拆成可提交的小批次”，而不是仅依靠进度条模拟连续进度。

## 7. 数据分桶策略

当前 Analyzer 不再直接把错误占比当成训练数据占比。第一轮分配综合考虑：

```text
观察到的能力缺陷
归因置信度
错误严重性
能力迁移价值
学习效率先验
数据获取与训练成本
```

Code、Text2SQL 和 General Text 使用独立能力桶。原始 `other` 会先根据执行结果、解析信息和错误证据重新分类；仍无法解释的样本进入诊断池，训练预算为 0。

当前 `learnability` 仍以先验为主。更理想的闭环是：每轮先进行小规模试训，再根据“目标指标增量/新增样本数”更新下一轮分配比例。

## 8. 常见排查方法

### 快速确认入口是否可用

```bash
python examples/scripts/run_analyzer_standalone.py --list-nodes
```

### 快速确认 Skill 是否可导入

```bash
python - <<'PY'
from loopai.skills.Analyzer import run
print(callable(run))
PY
```

### 检查事件和产物目录

```text
<output_dir>/<task_id>/analyzer/<version_id>/
```

重点查看：

```text
analyzer.pkl
state_checkpoint.sqlite
summary_*.json / .txt
report_*.json / .txt
final_report_*.json / .txt
```

### resume 前检查

- `task_id` 是否与中断运行一致；
- `version_id` 是否与中断运行一致；
- checkpoint 是否位于对应 version 目录；
- state 中的 `current`、`last_completed` 和节点进度是否合理；
- 是否误用了 `--new-version`。

## 9. 当前边界与后续方向

当前仍需关注以下边界：

- 外部 LLM 单次请求本身不能从 token 中间恢复；
- 数据库中不存在相应任务时，standalone 可能正常产出文件，但 Configer 同步会给出 task-not-found 提示；
- benchmark bad case 可以用于能力诊断，但不应直接生成或检索测试题近邻训练数据，否则会产生 benchmark 泄露；
- 分桶中的学习效率需要通过真实小规模训练收益持续校准；
- 多 benchmark 只能合并相同任务类型，不应把 Code、Text2SQL 和 General Text 混入同一分析路由。

后续可以在 Analyzer 中加入独立能力 Proxy Bank、候选数据质量评估和历史边际收益模型，使流程从“根据错误比例补数据”逐步升级为“根据能力需求、数据效用和真实训练收益动态分配数据”。

## 相关文档

- `skills/Analyzer/SKILL.md`：Analyzer Skill 接口与能力说明。
- `skills/Analyzer/BUCKET_STRATEGY.md`：Code、Text2SQL、General Text 分桶策略。
- `loopai/skills/Analyzer/`：Analyzer Python 实现。

