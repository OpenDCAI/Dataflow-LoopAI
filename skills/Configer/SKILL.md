# Configer Skill

用于处理 LoopAI 中两类请求：

- `state` 类：读取或修改任务 `states` 配置
- `runtime` 类：读取任务节点 runtime 状态

如果你只想快速判断该不该用这个 skill，看这三条：

- 用户在问“参数是什么、现在配成什么、要怎么改”时，用它
- 用户在问“节点现在跑到哪、历史状态是什么”时，也用它
- 用户在问全局 `system` 配置时，不要用它

## 什么时候使用

当用户要执行以下任一操作时，使用本 skill：

- 查看某个 section / agent 有哪些可配置字段
- 询问某个配置项是什么意思、允许填什么值
- 读取数据库里某个 task 的实际 `states` 值
- 读取全局默认 `states` 配置
- 修改某个 task 下的 `states` 配置
- 修改全局默认 `states` 配置
- 读取某个 task 下某个节点的最新 runtime
- 读取某个 task 下某个节点的历史 runtime
- 读取某个 task 下所有节点的最新 runtime

不要用它处理：

- 全局 `system` 配置读取或修改
- 模型服务地址、API key、workspace、runner、provider、系统路径等 `system.*` 请求
- 训练、评测、分析、爬取、数据构造本身

## 首先做分类

开始动作前，先把请求归到下面两类之一：

1. `state`：用户关心的是“任务参数是什么、现在怎么配、要不要改”
2. `runtime`：用户关心的是“任务现在跑到哪了、节点历史状态是什么”

然后再做第二层判断：

- 如果是全局 `system`：立即停止，不要调用本 skill 的任何 `state` / `runtime` 读写函数
- 如果是 `default_states` 或任务 `state`：走 `state` 工具
- 如果是节点运行态 / 节点历史：走 `runtime` 工具

## 快速决策表

- 想知道字段含义、可选值、schema 默认值：`get_configer_state_schema`
- 想读当前任务实际配置：`get_configer_task_state_config`
- 想读默认或自动作用域配置：`get_configer_state_config`
- 想改当前任务实际配置：`update_configer_task_state_config`
- 想改默认或自动作用域配置：`update_configer_state_config`
- 想看单节点最新状态：`get_runtime_task_node_latest`
- 想看单节点历史：`get_runtime_task_node_history`
- 想看当前任务全部节点最新状态：`get_runtime_task_latest_runtimes`

## 不要先做全仓搜索

当用户的意图明显是“读/改某个配置项”或“看某个节点 runtime”时，不要先在整个仓库里搜索字段名。

不要先执行这类全仓搜索：

```bash
rg -n "eval_task_type|judger|trainer|runtime" -S .
```

原因：

- 仓库较大，且包含 `ui/node_modules` 等目录
- 全仓搜索很容易拖慢首轮响应
- 对于明确的配置或 runtime 请求，这类搜索没有必要

如果确实需要搜索，也要限制范围，例如：

```bash
rg -n "eval_task_type|trainer" skills/Configer loopai/skills/Configer api/app -S
```

## 背景知识

这个 skill 对应两类底层数据：

1. `state`
   - 全局默认 states：`StarterConfig.config.default_states`
   - 任务实际 states：`TaskModel.state`
2. `runtime`
   - 任务节点运行态：`TaskRuntime`

重要：

- 全局 `system` 不属于这套 skill 的处理范围
- 任务级 states 应理解为 `TaskModel.state`
- 不要把任务级 states 修改理解成改 `TaskModel.config.default_states`
- runtime 只读当前任务运行态，不负责配置修改

## 环境变量与作用域

- `DB_PATH`：读取数据库实际值时必需
- `task_id` 或 `TASK_ID`：任务级读取时优先使用

规则：

- 显式传了 `task_id`，优先用显式参数
- 没显式传时，会回退到环境变量 `task_id` / `TASK_ID`
- `state` 工具里，没有任务 ID 时，部分接口会回退到默认配置
- `runtime` 工具只面向任务运行时，因此需要任务 ID

## 可调用函数

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    get_configer_state_config,
    get_configer_task_state_config,
    update_configer_state_config,
    update_configer_task_state_config,
    get_runtime_task_node_latest,
    get_runtime_task_node_history,
    get_runtime_task_latest_runtimes,
)
```

## State 工具工作流

在 `state` 请求里，优先顺序必须是：

1. 先判断用户要改的是 `system`、`default_states` 还是任务 `state`
2. 如果是 `system`：停止，不要调用本 skill 的任何 `state` 函数
3. 如果用户在问字段含义或取值范围，先调 `get_configer_state_schema(section_name=...)`
4. 如果用户在问当前实际值：
   默认/自动作用域用 `get_configer_state_config(...)`
   指定任务实际值用 `get_configer_task_state_config(...)`
5. 如果用户要修改：
   默认/自动作用域用 `update_configer_state_config(...)`
   指定任务实际值用 `update_configer_task_state_config(...)`

### State 常用函数

获取 schema：

```python
get_configer_state_schema()
get_configer_state_schema(section_name="judger")
```

读取默认/自动作用域配置：

```python
get_configer_state_config(section_name="judger")
get_configer_state_config(section_name="judger", field_name="eval_task_type")
```

显式读取某个任务的实际配置：

```python
get_configer_task_state_config(
    section_name="judger",
    field_name="eval_task_type",
    task_id="your-task-id",
)
```

更新默认/自动作用域配置：

```python
update_configer_state_config(
    "judger",
    {
        "eval_temperature": 0.2,
        "eval_batch_size": 8,
    },
)
```

显式更新某个任务的实际配置：

```python
update_configer_task_state_config(
    "judger",
    {
        "eval_temperature": 0.2,
        "eval_batch_size": 8,
    },
    task_id="your-task-id",
)
```

### State 结果解释

当你调用 `get_configer_state_schema(...)` 或 `get_configer_state_config(...)` 时，常见字段含义如下：

- `allowed_values`：允许填写的枚举值列表
- `default`：schema 层定义的默认值，不一定等于数据库当前值
- `value`：数据库里当前实际生效的值
- `default_value`：当前实现里通常和 `value` 一样，更像“当前值镜像”，不要把它当成 schema 默认值

重要区分：

- 看“可填什么、schema 默认是什么”，重点看 `allowed_values`、`default`
- 看“数据库里现在实际是什么”，重点看 `value`
- 前端如果要做“重置为 schema 默认值”，优先参考 `default`

## Runtime 工具工作流

在 `runtime` 请求里，优先顺序必须是：

1. 先确认用户要看的是单节点还是全任务
2. 先确认是否能拿到任务 ID：显式 `task_id` 优先，否则看环境变量 `task_id` / `TASK_ID`
3. 如果用户要看单节点当前状态，用 `get_runtime_task_node_latest(...)`
4. 如果用户要看单节点历史，用 `get_runtime_task_node_history(...)`
5. 如果用户要看整条任务链路当前状态，用 `get_runtime_task_latest_runtimes(...)`

### Runtime 常用函数

看单节点最新状态：

```python
get_runtime_task_node_latest(node_name="trainer", task_id="your-task-id")
```

看单节点历史：

```python
get_runtime_task_node_history(node_name="trainer", task_id="your-task-id")
```

看当前任务全部节点最新状态：

```python
get_runtime_task_latest_runtimes(task_id="your-task-id")
```

说明：

- `get_runtime_task_node_latest(...)` 返回 `data.runtime`
- `get_runtime_task_node_history(...)` 返回 `data.runtimes`
- `get_runtime_task_latest_runtimes(...)` 的语义对齐 `GET /task/runtime/{task_id}/latest`

## 执行稳定性要求

当通过 codex-sdk / shell 子进程调用这些函数时，要特别注意：

- 在 `python -c`、heredoc、脚本子进程里，函数 `return` 不会自动显示，必须显式 `print(...)`
- 建议使用 `timeout 20 python3 -u`
- 输出时建议 `print(json.dumps(result, ensure_ascii=False, default=str), flush=True)`

推荐模板：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import get_configer_task_state_config

result = get_configer_task_state_config("judger", "eval_task_type", task_id="your-task-id")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

runtime 模板：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import get_runtime_task_node_latest

result = get_runtime_task_node_latest("trainer", task_id="your-task-id")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

补充说明：

- 这组接口底层是同步 `sqlite3` 路径
- 正常情况下不应再因为 `aiosqlite` / `Tortoise` 链路而卡住
- 仍然建议保留 `timeout`，因为 shell / sdk 子进程本身仍可能挂起

## 返回格式

执行完后，一般都应该按统一 success / error payload 来理解和回复用户。

成功：

```json
{
  "ok": true,
  "status": "completed",
  "message": "...",
  "data": {...},
  "error": null
}
```

失败：

```json
{
  "ok": false,
  "status": "failed",
  "message": "...",
  "data": null,
  "error": {
    "type": "...",
    "code": "...",
    "detail": "...",
    "recoverable": true
  }
}
```

回复用户时，至少说清楚：

1. 本次操作类型：schema / 配置读取 / 配置更新 / runtime 读取
2. 作用范围：`task` 还是 `default`
3. 目标对象：`section_name`、`field_name`、`node_name`、`task_id`
4. 核心结果：当前值、允许值、更新是否成功、runtime 当前状态
5. 如失败，直接带上 `message` 和 `error.detail`

## 禁止绕过接口

处理这类请求时，必须优先使用本 skill 提供的函数，不要默认直接读写 SQLite。

不要直接：

- 手写 `sqlite3 ... SELECT ...`
- 手写 `sqlite3.connect(...)` 去改 `starterconfig` / `taskmodel` / `taskruntime`
- 绕过 skill 直接改数据库 JSON

原因：

- skill 已经封装了默认配置 / 任务配置 / runtime 的作用域判断
- skill 已经做了 section 和字段合法性校验
- 直接写库容易写错表结构、写错层级、绕过保护字段

唯一例外：

- 只有在接口调用已经明确超时或报错，且当前任务明确是排障分析而不是代替用户写配置时，才允许做只读排障
- 即使是排障，也优先只读检查，不要直接写库

## 约束

- 不允许修改全局 `system`
- 不允许修改不存在的 section
- 不允许修改不存在的字段
- 不允许通过该 skill 修改 `default.task_id`
- 不要把 runtime 请求错误映射成 `state` 配置请求

## 推荐工作流

1. 先判断用户请求属于 `state` 还是 `runtime`
2. 如果涉及全局 `system`：停止，不要调用本 skill
3. 如果是 `state`：先决定是 schema、读取还是更新
4. 如果是 `runtime`：先决定是单节点最新、单节点历史还是全任务最新
5. 优先使用 skill 函数，不要先做全仓搜索
6. shell 调用时显式 `print(json.dumps(...))`，建议带 `timeout`
7. 按统一 success / error payload 回复用户，并明确作用范围和目标对象

如果你只记一句话：

- `state` 工具负责“任务参数是什么”
- `runtime` 工具负责“任务现在跑到哪了”
