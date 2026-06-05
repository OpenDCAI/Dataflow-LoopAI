# Configer Skill

用于处理 LoopAI 中的配置类请求。

## 什么时候使用

当用户要执行以下任一操作时，使用本 skill：

- 查看某个 agent / section 有哪些可配置字段
- 询问某个配置项是什么意思、能填什么值
- 读取数据库里某个 task 的实际 states 值
- 读取全局默认 states 配置
- 修改某个 task 下的 states 配置
- 修改全局默认 states 配置

不要用它处理：

- 全局 `system` 配置读取或修改
- 训练、评测、分析、爬取、数据构造本身

如果用户提到的是全局配置，尤其是模型服务地址、API key、workspace、runner、codex provider、系统级路径等：

- 立即停止使用本 skill 的 states 读写函数
- 不要把请求错误地映射到某个 task 的 `state`
- 先明确告诉用户：当前 skill 只处理全局默认 `states` 和任务 `state`
- 如果用户点名的是 `system.*`，再额外说明：`system` 本身不允许通过本 skill 修改
- 然后引导到全局配置对应的处理链路

## 首选动作

当用户的意图明显是“读/改某个配置项”时，不要先在整个仓库里搜索字段名。

优先顺序必须是：

1. 先判断用户要改的是“全局配置”还是“任务 states”
2. 如果是全局配置中的 `system`：不要调用本 skill 的任何 states 读写函数
3. 如果是全局配置中的 `default_states`，或某个 task 的 `state`：直接使用本 skill 里列出的函数
4. 如需确认字段约束，先调用 `get_configer_state_schema(section_name=...)`
5. 如需确认当前值：
   默认/自动作用域用 `get_configer_state_config(section_name=..., field_name=...)`
   指定任务实际值用 `get_configer_task_state_config(section_name=..., field_name=..., task_id=...)`
6. 如需修改：
   默认/自动作用域用 `update_configer_state_config(section_name, updates)`
   指定任务实际值用 `update_configer_task_state_config(section_name, updates, task_id=...)`

不要先执行这类全仓搜索：

```bash
rg -n "eval_task_type|judger|configer" -S .
```

原因：

- 仓库较大，且包含 `ui/node_modules` 等目录
- 全仓搜索很容易拖慢首轮响应，甚至让调用看起来像“卡住”
- 对于明确的配置请求，这类搜索没有必要

如果确实需要搜索，也要限制范围，只查必要文件，例如：

```bash
rg -n "eval_task_type" skills/configer loopai/skills/Configer api/app -S
```

## 背景知识

LoopAI 里和本 skill 相关的配置，分两层：

1. 全局默认 states：`StarterConfig.config.default_states`
2. 任务运行态：`TaskModel.state`

重要：

- 全局 `system` 不属于这套 skill 的处理范围
- 任务级 states 现在应理解为 `TaskModel.state`
- 不要再把任务级 states 修改理解成改 `TaskModel.config.default_states`

是否读取 / 修改任务级配置，由环境变量决定：

- `DB_PATH`：读取实际配置、更新配置时必填
- `task_id` 或 `TASK_ID`：选填

规则：

- 有 `task_id` / `TASK_ID`：读取或修改该任务的实际 `state`
- 没有 `task_id` / `TASK_ID`：读取或修改全局默认的 `default_states`
- 如果你必须显式指定某个任务，而不是依赖环境变量，使用 `get_configer_task_state_config(...)` / `update_configer_task_state_config(...)`

## 可调用函数

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    get_configer_state_config,
    get_configer_task_state_config,
    update_configer_state_config,
    update_configer_task_state_config,
)
```

当前实现说明：

- 所有接口现在都只保留同步版本
- `get_configer_state_config(...)` / `update_configer_state_config(...)` 走同步 `sqlite3` 读写
- `get_configer_task_state_config(...)` / `update_configer_task_state_config(...)` 专门处理任务级 `TaskModel.state` 实际值
- 没有任何一个函数可以用来处理全局 `system`

### 1. 获取 schema

获取全部可配置 states schema：

```python
get_configer_state_schema()
```

只获取某个 section：

```python
get_configer_state_schema(section_name="judger")
get_configer_state_schema(section_name="configer")
get_configer_state_schema(section_name="default")
```

说明：

- 该函数直接基于 `get_state_config_schema(...)`
- 不读取 `task_id` 下的实际值
- 不处理全局 `system`
- 适合回答“这个字段是什么”“允许填什么”“schema 默认值是什么”

### 2. 获取数据库里的实际配置

读取某个 section：

```python
get_configer_state_config(section_name="judger")
```

读取某个字段：

```python
get_configer_state_config(section_name="judger", field_name="eval_task_type")
```

说明：

- 该函数读取数据库里的真实配置值
- 需要 `DB_PATH`
- 有 `task_id` / `TASK_ID` 时读取任务实际 `state`，否则读取默认配置
- 如果只传 `section_name`，返回整个 section
- 如果再传 `field_name`，只返回该字段的配置对象

### 2.1 显式读取某个 task 的实际配置

读取某个 task 的整个 section：

```python
get_configer_task_state_config(
    section_name="judger",
    task_id="your-task-id",
)
```

读取某个 task 的单个字段：

```python
get_configer_task_state_config(
    section_name="judger",
    field_name="eval_task_type",
    task_id="your-task-id",
)
```

说明：

- 这组接口只读任务级实际值，也就是 `TaskModel.state`
- `task_id` 优先用显式参数
- 如果没传，则强制从环境变量 `task_id` / `TASK_ID` 获取
- 如果两者都没有，会直接报错，不会回退到默认配置

### 3. 更新配置

更新接口现在只针对单个 section：

```python
update_configer_state_config(
    "default",
    {
        "language": {"value": "en", "type": "str"}
    }
)
```

也支持直接传原始值：

```python
update_configer_state_config(
    "judger",
    {
        "eval_batch_size": 8,
        "eval_temperature": 0.2
    }
)
```

也支持传 JSON 字符串。

### 3.1 显式更新某个 task 的实际配置

```python
update_configer_task_state_config(
    "judger",
    {
        "eval_batch_size": 8,
        "eval_temperature": 0.2
    },
    task_id="your-task-id",
)
```

说明：

- 这组接口只更新任务级实际值，也就是 `TaskModel.state`
- 不会回退去改默认配置
- 如果没有显式 `task_id`，则必须从环境变量 `task_id` / `TASK_ID` 中取到

## 执行稳定性要求

当通过 codex-sdk / shell 子进程调用这些函数时，要特别注意下面几点：

- 在 `python -c`、heredoc、脚本子进程里，函数 `return` 不会自动显示，必须显式 `print(...)`
- 交互式 REPL 里的 `>>> some_func()` 会自动回显返回值，但 shell 子进程不会
- 如果命令一直处于运行中，Codex 侧通常看不到最终 `aggregated_output`，这时不能简单理解成“函数没有输出”，更准确是“命令没有结束”

推荐调用模板：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import get_configer_task_state_config

result = get_configer_task_state_config("judger", "eval_task_type", task_id="your-task-id")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

更新配置时同理：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import update_configer_task_state_config

result = update_configer_task_state_config("judger", {"eval_task_type": "code"}, task_id="your-task-id")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

说明：

- `timeout 20`：避免接口卡住时一直挂起
- `python3 -u`：关闭 stdout 缓冲，尽快把输出刷出来
- `print(..., flush=True)`：确保输出及时可见
- `json.dumps(..., default=str)`：避免对象序列化失败

补充说明：

- 现在这组配置接口的底层实现是同步 `sqlite3`，正常情况下不会再因为 `aiosqlite` / `Tortoise` 链路而卡住
- 这里仍然推荐保留 `timeout`，原因是 shell / sdk 子进程本身仍可能因为环境、锁竞争、解释器问题或调用链其他环节而挂起

## 字段含义说明

当你调用 `get_configer_state_schema(...)` 或 `get_configer_state_config(...)` 时，常见返回字段含义如下：

- `allowed_values`：允许填写的枚举值列表。一般出现在 `ui_type` 是 `list` / `select` 的字段上。
- `default`：schema 层定义的默认值。这个值来自状态 schema，不一定等于数据库当前值。
- `value`：当前实际值。对 `get_configer_state_config(...)` 来说，这是数据库里当前生效的值。
- `default_value`：当前实现里，它在数据库读取结果中通常和 `value` 一样，都是用当前值包装出来的，并不是 schema 默认值。

重要区分：

- 看“可填什么”和“schema 默认是什么”，重点看 `allowed_values`、`default`
- 看“当前库里实际配成了什么”，重点看 `value`
- 不要把 `default_value` 当成 schema 默认值来理解

对你给的例子，可以这样理解：

- `allowed_values`：对，表示允许填写的值
- `default`：对，表示 schema 里的默认值
- `value`：对，表示当前值
- `default_value`：当前代码实现里更像是“当前值的镜像字段”，不是严格意义上的“用于前端重置到 schema 默认值”

所以如果前端要做“重置为 schema 默认值”，应该优先参考 `default`，不要单纯依赖 `default_value`。

## 返回格式

执行完后，一般都应该按统一 success / error payload 来理解和回复用户。

### 成功返回

读取 schema 成功：

```json
{
  "ok": true,
  "status": "completed",
  "message": "Non-system state schema loaded.",
  "data": {
    "states": {
      "judger": {
        "eval_task_type": {
          "allowed_values": ["code", "text2sql", "general_text"],
          "default": "code",
          "description": "...",
          "title": "...",
          "type": "string",
          "ui_group": "评估模型",
          "ui_type": "list"
        }
      }
    }
  },
  "error": null
}
```

读取实际配置成功：

```json
{
  "ok": true,
  "status": "completed",
  "message": "Non-system state config loaded.",
  "data": {
    "scope": "default",
    "task_id": null,
    "section_name": "judger",
    "field_name": "eval_task_type",
    "config": {
      "allowed_values": ["code", "text2sql", "general_text"],
      "default": "code",
      "description": "...",
      "title": "...",
      "type": "str",
      "ui_group": "评估模型",
      "ui_type": "list",
      "value": "general_text",
      "default_value": "general_text"
    }
  },
  "error": null
}
```

更新成功：

```json
{
  "ok": true,
  "status": "completed",
  "message": "Non-system state config updated.",
  "data": {
    "scope": "task",
    "task_id": "xxx",
    "section_name": "judger",
    "config": {
      "eval_temperature": {
        "value": 0.2,
        "default_value": 0.2,
        "type": "float"
      }
    }
  },
  "error": null
}
```

### 失败返回

```json
{
  "ok": false,
  "status": "failed",
  "message": "Failed to update non-system state config.",
  "data": null,
  "error": {
    "type": "ValueError",
    "code": "CONFIG_ERROR",
    "detail": "...",
    "traceback": "...",
    "recoverable": true,
    "time": "2026-06-01T00:00:00Z"
  }
}
```

## 回复用户时的建议格式

一般执行完后，回复里至少要说清楚这几件事：

1. 本次操作类型：读取 schema / 读取实际配置 / 更新配置
2. 作用范围：`task` 还是 `default`
3. 操作目标：哪个 `section_name`，必要时带 `field_name`
4. 核心结果：当前值、允许值、是否更新成功
5. 如失败，直接带上 `message` 和 `error.detail`

推荐表达：

- 读取 schema 时：说明字段含义、允许值、schema 默认值
- 读取实际配置时：说明当前值是 `value`，不要混淆成 `default`
- 更新配置时：说明更新到了哪个 section，当前生效值是什么

## 禁止绕过接口

处理配置类请求时，必须优先使用：

- `get_configer_state_schema`
- `get_configer_state_config`
- `update_configer_state_config`

但前提是：

- 用户请求不是全局 `system`
- 用户请求目标确实属于全局 `default_states` 或任务 `state`

不要直接：

- 手写 `sqlite3 ... SELECT ...`
- 手写 `sqlite3` / `sqlite3.connect(...)` 去改 `starterconfig` / `taskmodel`
- 绕过 skill 直接改数据库 JSON

原因：

- skill 已经封装了默认配置 / 任务配置的作用域判断
- skill 已经做了 section 和字段合法性校验
- 直接写库容易绕过保护字段、写错表结构、写错层级

唯一例外：

- 只有在“接口调用已经明确超时或报错，且当前任务明确是排障分析而不是代替用户写配置”时，才允许只读方式查看 SQLite 结构或数据来定位问题
- 即使是排障，也应优先做只读检查，不要直接写库

排障顺序建议：

1. 先用 `get_configer_state_config(...)` / `update_configer_state_config(...)`
2. 如果 15-20 秒内没有返回，优先判断是否是 shell 子进程、解释器环境、数据库锁或外层调用链问题
3. 再只读检查 `.tables`、`.schema`、`SELECT ...`
4. 向用户说明“接口链路异常”，不要把直接改库当成正常执行路径

## 约束

- 不允许修改全局 `system`
- 不允许修改不存在的 section
- 不允许修改不存在的字段
- 不允许通过该 skill 修改 `default.task_id`

## 推荐工作流

1. 先判断目标是全局配置还是任务 states
2. 如果属于全局 `system`：停止，不要调用任何 states 接口，并明确告知“当前 skill 只支持全局 `default_states` 和任务 `state`”
3. 如果属于全局 `default_states` 或任务 `state`，再确定要看哪个 section
4. 如果用户是在问“这个字段能填什么、默认是什么”，先调 `get_configer_state_schema(section_name=...)`
5. 如果用户是在问“现在数据库里实际配成什么了”：
   默认/自动作用域调 `get_configer_state_config(section_name=..., field_name=...)`
   指定 task 调 `get_configer_task_state_config(section_name=..., field_name=..., task_id=...)`
6. 如果用户要修改配置，把更新对象整理成“某个 section 下的字段 dict”
7. 修改默认/自动作用域用 `update_configer_state_config(section_name, updates)`
8. 修改指定 task 的实际值用 `update_configer_task_state_config(section_name, updates, task_id=...)`
9. 如果 shell 调用，要显式 `print(json.dumps(...))`，并建议带 `timeout`
10. 如果接口调用超时，先报告接口异常，再决定是否进入只读排障
11. 按统一 success / error payload 回复用户，并明确 `scope`、`section_name`、`field_name`
