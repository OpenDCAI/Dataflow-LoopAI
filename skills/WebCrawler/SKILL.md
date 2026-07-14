# WebCrawler Skill

## Purpose

WebCrawler Skill 用于在无 LangGraph（独立模式）下运行 LoopAI 的网页爬取与数据集构建流程。它支持：

- 读取任务级 `webcrawler` 运行态配置（来自 DB）
- 注入运行时参数（CLI / env / kwargs）
- 执行爬取 + 数据集抽取 + 结束汇总
- 断点续跑（SQLite checkpoint）
- 实时事件流持久化（pickle）
- 子代理版本化输出目录：`{outputs}/{task_id}/webcrawler/{version_id}/`
- 核心产物回写 `state.webcrawler`（含版本号、路径、数据集统计）
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

### 预填写引导

1. `configer_get_task(schema="states", section="webcrawler", task_id="<task_id>")`
2. 基于 `required_fields` / `recommended_fields` 告知用户：
   - 必填缺失项（必须确认后写入）
   - 建议项（可使用默认值）
3. 用户确认后执行 `configer_update_task("webcrawler", {...}, task_id="<task_id>")`

WebCrawler 预填写模板（`task_type`）：

- `general`（默认）：
  - 必填：`deepseek_api_key`, `tavily_api_key`
  - 建议：`model`, `temperature`, `num_queries`, `max_pages`, `crawl_depth`
  - 可自动补：`deepseek_api_base`, `model`, `temperature`, `num_queries`, `max_pages`
- `code_collect`（代码语料收集）：
  - 必填：`deepseek_api_key`, `tavily_api_key`
  - 建议：`min_code_length`, `max_records_per_page`, `dataset_concurrent_limit`, `sft_mapping_format`
  - 可自动补：`min_code_length=80`, `max_records_per_page=20`, `dataset_concurrent_limit=8`

### Required Fields

当前必须字段：

- `webcrawler.deepseek_api_key`
- `webcrawler.tavily_api_key`

缺失时会抛 `ValueError`，由上层统一包装为 `CONFIG_ERROR`。

### Sub-Agent Input Contract（WebCrawler）

为保证任务可调度与可复现，WebCrawler 在子代理模式下应满足如下输入契约。

必填参数（required）：

- `task_id`：任务唯一标识（用于 trace / retry / logging）
- `input`：核心输入数据（字符串 / JSON / 结构化对象）
- `context`：上下文信息（可选但推荐，如历史状态 / external memory）
- `config`：运行配置（如 model、temperature、top_k、timeout）
- `callback`：结果写入方式（stream / webhook / queue）

可选参数（optional）：

- `trace_id`：链路追踪 ID（未传时建议自动生成并回传）
- `priority`：任务优先级（供 scheduler 使用）
- `resource_limit`：资源限制（CPU / GPU / time / tokens）

执行前校验（pre-check）：

- 参数完整性检查（required fields）
- schema 校验（JSON schema / pydantic）
- 依赖资源可用性（model / db / cache / index）
- 权限校验（是否允许调用 external tool）
- 关键密钥检查（`deepseek_api_key` / `tavily_api_key`）

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

## Version & State

WebCrawler 在独立模式下按“任务 + 版本”组织输出：

```text
{output_dir}/{task_id}/webcrawler/{version_id}/
```

- `version_id` 首次写事件时生成；`resume=True` 时会复用 `state.webcrawler.runtime_version_id`
- 关键运行态会回写到 `state.webcrawler`（并同步 Configer）：
  - `runtime_version_id`
  - `runtime_version_output_dir`
  - `runtime_current_step`
  - `runtime_last_completed_step`
  - `output_result` / `dataset_*`

这样可以在 TaskRuntimeItem 之外，从 state 直接定位“当前版本在跑什么、结果落在哪”。

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

## 结果解析与版本对比

建议把“核心结果”统一从 `state.webcrawler` 读取，而不是只看日志文本：

- 爬取规模：`output_result.total_pages`
- 数据集产出：`dataset_sft_count`, `dataset_pt_count`
- 关键文件：`dataset_sft_path`, `dataset_pt_path`, `dataset_*_mapped_path`

可以定义轻量解析函数做多版本对比（示例）：

```python
def pick_best_webcrawler_version(candidates: list[dict]) -> dict:
    def score(item: dict) -> tuple:
        return (
            int(item.get("dataset_sft_count", 0)),
            int(item.get("dataset_pt_count", 0)),
            int((item.get("output_result") or {}).get("total_pages", 0)),
        )
    return max(candidates, key=score)
```

说明：WebCrawler 可按 `SFT/PT 产出 + 页数` 做版本优选。

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

失败状态扩展约定：

- `status` 建议支持：`failed | partial_failed | timeout`
- `error.type` 建议归一为：`ValidationError | RuntimeError | ResourceError | ExternalServiceError | TimeoutError`
- 可恢复错误建议补充：`retry_after`（秒）

常见错误处理建议：

- `ValidationError`：参数缺失 / schema 不匹配 / 类型错误，返回 field-level detail，不建议自动重试
- `RuntimeError`：节点执行异常，允许降级流程并重试（不超过 2 次）
- `ResourceError`：模型或资源不可用，切换备选资源或进入排队重试
- `ExternalServiceError`：外部 API / DB 不可用，指数退避重试并优先回退缓存
- `TimeoutError`：长链路或 IO 卡住，优先 checkpoint 恢复并拆分任务

## Output Contract

为便于 orchestrator 消费，推荐在 `data` 中返回可结构化结果（除兼容 `emit_success` 的基础字段外）：

```json
{
  "ok": true,
  "status": "completed",
  "message": "WebCrawler pipeline completed.",
  "data": {
    "result": {
      "execution_result": "success",
      "output_result": {
        "total_pages": 12
      },
      "dataset_sft_count": 120,
      "dataset_pt_count": 340,
      "output_files": [
        "outputs/task_001/webcrawler/v1/sft.jsonl",
        "outputs/task_001/webcrawler/v1/pt.jsonl"
      ],
      "side_effects": [
        "state.webcrawler updated",
        "events persisted"
      ]
    },
    "metrics": {
      "latency_ms": 0,
      "token_usage": 0,
      "memory_peak": 0,
      "gpu_utilization": 0,
      "retry_count": 0
    },
    "artifacts": [
      "dataset jsonl",
      "event stream pickle",
      "version output directory"
    ],
    "logs": [],
    "trace_id": "",
    "time_cost_ms": 0
  },
  "error": null
}
```

其中 `result` 字段建议最少包含：

- `execution_result`
- `output_result`（含 `total_pages`）
- `dataset_sft_count` / `dataset_pt_count`
- `output_files`
- `side_effects`

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

