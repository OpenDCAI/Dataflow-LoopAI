# `loopai.skills.Configer`

给外部 Codex / function skill 用的任务配置与运行时工具集。

它主要分成两类能力：

- `config_tool` 为首的 `state` 工具：读取和修改任务 `states` 配置
- `runtime_tool` 为首的 `runtime` 工具：读取任务节点 runtime 状态

如果你只想快速上手，可以这样理解：

- 想看或改任务参数，用 `state` 工具
- 想看节点跑到了哪里、历史状态如何，用 `runtime` 工具
- 读数据库通常需要 `DB_PATH`
- 任务级读取通常会优先使用 `task_id` / `TASK_ID`

## 快速导航

- 想理解参数结构：先看 `get_configer_state_schema`
- 想读当前任务配置：用 `get_configer_task_state_config`
- 想改当前任务配置：用 `update_configer_task_state_config`
- 想看单节点当前状态：用 `get_runtime_task_node_latest`
- 想看单节点历史：用 `get_runtime_task_node_history`
- 想看当前任务全部节点最新状态：用 `get_runtime_task_latest_runtimes`

## 环境变量

- `DB_PATH`：SQLite 数据库路径；读取数据库实际值时必需
- `task_id` 或 `TASK_ID`：当前任务 ID；任务级读取或修改时优先使用

通用规则：

- 显式传了 `task_id`，优先用显式参数
- 没显式传时，会回退到环境变量 `task_id` / `TASK_ID`
- `state` 工具里，部分接口在没有任务 ID 时会读取默认配置
- `runtime` 工具只面向任务运行时，因此需要任务 ID

## 导入

```python
from loopai.skills.Configer import (
    get_configer_state_config,
    get_configer_state_schema,
    get_configer_task_state_config,
    get_runtime_task_latest_runtimes,
    get_runtime_task_node_history,
    get_runtime_task_node_latest,
    update_configer_state_config,
    update_configer_task_state_config,
)
```

## State 工具

这一组工具围绕任务 `states` 配置工作，核心入口是 `config_tool`。

适合的场景：

- 想知道某个 section 下有哪些字段
- 想读取当前任务某个参数的实际值
- 想修改当前任务或默认配置里的某个 `states` 字段

### 你应该先用哪个

- 先看字段说明：`get_configer_state_schema`
- 读取当前生效配置：`get_configer_state_config` 或 `get_configer_task_state_config`
- 修改配置：`update_configer_state_config` 或 `update_configer_task_state_config`

### 1. 看 schema

```python
result = get_configer_state_schema()
```

只看某个 section：

```python
result = get_configer_state_schema(section_name="judger")
result = get_configer_state_schema(section_name="configer")
result = get_configer_state_schema(section_name="default")
```

成功时 `data.states` 里通常包含：

- 字段说明
- 字段类型
- schema 默认值

说明：

- 这里只看 schema，不区分 task_id
- 不会暴露 `system` 配置

### 2. 读当前配置

自动根据环境变量决定读任务配置还是默认配置：

```python
get_configer_state_config(section_name="judger")
get_configer_state_config(section_name="judger", field_name="eval_temperature")
```

如果你必须显式指定任务：

```python
get_configer_task_state_config(
    section_name="judger",
    field_name="eval_temperature",
    task_id="your-task-id",
)
```

行为规则：

- 有 `task_id` / `TASK_ID`：读对应任务的 `states`
- 没有 `task_id` / `TASK_ID`：读默认配置里的 `default_states`

### 3. 改当前配置

更新接口按 section 工作：

```python
update_configer_state_config(
    "default",
    {
        "language": {"value": "en", "type": "str"}
    }
)
```

```python
update_configer_state_config(
    "judger",
    {
        "eval_batch_size": {"value": 8, "type": "int"}
    }
)
```

也支持直接传 JSON 字符串。

如果你必须显式指定任务：

```python
update_configer_task_state_config(
    "judger",
    {
        "eval_batch_size": 8,
        "eval_temperature": 0.2,
    },
    task_id="your-task-id",
)
```

### State 工具限制

- 不允许修改 `system`
- 不允许修改不存在的 section
- 不允许修改不存在的字段
- 不允许通过该 skill 修改 `default.task_id`

## Runtime 工具

这一组工具围绕任务节点 runtime 工作，核心入口是 `runtime_tool`。

适合的场景：

- 想看某个节点当前最新状态
- 想追溯某个节点的历史 runtime
- 想快速看到当前任务下所有节点的最新状态

### 你应该先用哪个

- 看单节点最新状态：`get_runtime_task_node_latest`
- 看单节点历史：`get_runtime_task_node_history`
- 看当前任务全部节点最新状态：`get_runtime_task_latest_runtimes`

### 1. 看某个节点的最新 runtime

```python
get_runtime_task_node_latest(node_name="trainer")
```

显式指定任务：

```python
get_runtime_task_node_latest(node_name="trainer", task_id="your-task-id")
```

返回重点：

- `data.task_id`
- `data.node_name`
- `data.runtime`

### 2. 看某个节点的历史 runtime

```python
get_runtime_task_node_history(node_name="trainer")
```

显式指定任务：

```python
get_runtime_task_node_history(node_name="trainer", task_id="your-task-id")
```

返回重点：

- `data.task_id`
- `data.node_name`
- `data.runtimes`

### 3. 看当前任务全部节点的最新 runtime

这个接口的语义对齐：`GET /task/runtime/{task_id}/latest`

```python
get_runtime_task_latest_runtimes()
```

显式指定任务：

```python
get_runtime_task_latest_runtimes(task_id="your-task-id")
```

返回重点：

- `data.task_id`
- `data.runtimes`

## 返回格式

这两类工具都返回统一 success/error payload。

成功示意：

```python
{
    "ok": True,
    "status": "completed",
    "message": "...",
    "data": {...},
    "error": None,
}
```

失败示意：

```python
{
    "ok": False,
    "status": "failed",
    "message": "...",
    "data": None,
    "error": {
        "type": "...",
        "code": "...",
        "detail": "...",
        "recoverable": True,
    },
}
```

## Shell 调用建议

如果是在 `python -c`、heredoc、codex-sdk 子进程里调用，要注意：

- 函数 `return` 不会自动显示
- 需要显式 `print(...)`
- 建议配合 `timeout` 和 `python3 -u`

state 示例：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import get_configer_task_state_config

result = get_configer_task_state_config("judger", "eval_task_type", task_id="your-task-id")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

runtime 示例：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import get_runtime_task_node_latest

result = get_runtime_task_node_latest("trainer", task_id="your-task-id")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

## 使用建议

- 想理解参数结构，先用 `get_configer_state_schema`
- 想读任务真实配置，优先用 `get_configer_task_state_config`
- 想判断节点当前进度，优先用 `get_runtime_task_node_latest`
- 想看整条链路当前状态，优先用 `get_runtime_task_latest_runtimes`
- 正常读写优先走 skill 接口，不要直接改 SQLite

如果你只记一句话：

- `state` 工具负责“任务参数是什么”
- `runtime` 工具负责“任务现在跑到哪了”
