# WebCrawler Skill

## Purpose

WebCrawler Skill 用于在无 LangGraph（独立模式）下运行 LoopAI 的网页爬取与数据集构建流程。它支持：

- 读取任务级 `webcrawler` 运行态配置（来自 DB）
- 注入运行时参数（CLI / env / kwargs）
- 执行爬取 + 数据集抽取 + 结束汇总
- 断点续跑（SQLite checkpoint）
- 实时事件流持久化（pickle）
- 统一 success / error JSON 返回格式

## Python Implementation

```
loopai/skills/WebCrawler/
├── __init__.py            # run() / load_events()
├── runtime_config.py      # 运行时参数注入 + DB 配置加载 + 必填校验
└── runner.py              # 独立流水线（start -> crawl -> dataset -> finish）

loopai/agents/WebCrawler/nodes/
├── start_node.py
├── crawl_node.py
├── webcrawler_dataset_node.py
├── end_node.py
└── _event_utils.py        # 统一事件写入（LangGraph + 持久化事件）

examples/scripts/run_webcrawler_standalone.py
```

## Runtime Entry

```python
from loopai.skills.WebCrawler import run

result = run(
    state=state,
    thread_id="task_001",
    resume=False,
    from_step=None,
)
```

Direct runner:

```python
from loopai.skills.WebCrawler.runner import run_webcrawler_standalone
```

## CLI

```bash
python examples/scripts/run_webcrawler_standalone.py \
  --config-path /path/to/starter.yaml \
  --thread-id task_001 \
  --print-result
```

Supported options:

- `--config-path`
- `--thread-id`
- `--checkpoint-path`
- `--resume`
- `--from-step`
- `--user-query`
- `--print-result`

## Pipeline

Standalone pipeline steps:

```
start -> crawl -> dataset -> finish
```

`from_step` supports step aliases:

- `start_node` -> `start`
- `crawl_node` -> `crawl`
- `webcrawler_dataset_node` -> `dataset`
- `end_node` -> `finish`

## Runtime Config Injection

核心入口：

`loopai.skills.WebCrawler.runtime_config.resolve_webcrawler_runtime_config(...)`

该接口统一处理：

1. 运行时参数注入（kwargs / env / state）
2. task 级 DB 配置读取（通过 Configer）
3. 必填参数校验（缺参直接报错）

### Priority

通用优先级：

`kwargs > env > state > DB(task-state) > schema/default`

其中 task 级 DB 配置读取会在存在 task_id 时触发。

### Required Fields

当前必须字段：

- `webcrawler.deepseek_api_key`
- `webcrawler.tavily_api_key`

缺失时会抛 `ValueError`，由上层统一包装为 `CONFIG_ERROR`。

## Task-Scoped DB Config

当运行时存在 task_id（`thread_id` / `TASK_ID` / state.task_id）时，WebCrawler Skill 会读取 DB 中该任务的 `webcrawler` section：

```python
from loopai.skills.Configer import get_configer_task_state_config

cfg = get_configer_task_state_config(
    section_name="webcrawler",
    task_id="task_001",
)
```

要求：

- 必须有 `DB_PATH`
- 必须能解析到有效 task_id

如果 DB 读取失败，会终止运行并返回标准错误格式。

## Environment Variables

常用环境变量：

- `TASK_ID`
- `DB_PATH`
- `OUTPUT_DIR`
- `DEEPSEEK_API_KEY`
- `TAVILY_API_KEY`
- `WEBCRAWLER_MODEL`
- `WEBCRAWLER_DEEPSEEK_API_BASE`
- `WEBCRAWLER_TEMPERATURE`
- `WEBCRAWLER_CHECKPOINT_PATH`

## Checkpoint & Resume

默认 checkpoint：

`outputs/webcrawler_checkpoints.sqlite`

表结构：

- `thread_id`
- `state_json`
- `updated_at`

每步前后都会保存 state。`--resume` 时从 checkpoint 恢复并继续执行后续步骤。

## Event Stream

WebCrawler 节点事件会双写：

1. LangGraph custom stream（兼容 Starter 主图）
2. 持久化事件流（`loopai.common.event_tool`）

默认事件文件：

`<output_dir>/<task_id>/webcrawler.pkl`

读取方式：

```python
from loopai.skills.WebCrawler import load_events

events = load_events(task_id="task_001", output_dir="./outputs")
```

## Success / Error Payload

统一走 `loopai.common.exception`：

- 成功：`emit_success(...)`
- 失败：`emit_error(...)`

成功格式：

```json
{
  "ok": true,
  "status": "completed",
  "message": "WebCrawler pipeline completed.",
  "data": {},
  "error": null
}
```

失败格式：

```json
{
  "ok": false,
  "status": "failed",
  "message": "WebCrawler pipeline failed.",
  "data": null,
  "error": {
    "type": "RuntimeError",
    "code": "UNHANDLED_EXCEPTION",
    "detail": "...",
    "traceback": "...",
    "recoverable": true,
    "time": "2026-06-01T00:00:00Z"
  }
}
```

## Config Via Configer

WebCrawler 配置建议通过 Configer 维护：

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    get_configer_task_state_config,
    update_configer_task_state_config,
)

# 看字段说明
schema = get_configer_state_schema(section_name="webcrawler")

# 读取 task 实际配置
cfg = get_configer_task_state_config(
    section_name="webcrawler",
    task_id="task_001",
)

# 更新 task 实际配置
ret = update_configer_task_state_config(
    "webcrawler",
    {
        "model": "deepseek-chat",
        "temperature": 0.2,
    },
    task_id="task_001",
)
```

