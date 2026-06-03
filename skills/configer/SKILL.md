# Configer Skill

用于处理 LoopAI 中的配置类请求。

## 什么时候使用

当用户要执行以下任一操作时，使用本 skill：

- 查看某个 agent / section 有哪些可配置字段
- 询问某个配置项是什么意思、能填什么值
- 读取数据库里某个 task 或默认配置的实际值
- 修改某个 task 下的 states 配置
- 修改默认用户配置中的 states 配置

不要用它处理：

- `system` 配置修改
- 训练、评测、分析、爬取、数据构造本身

## 背景知识

LoopAI 的配置分两层：

1. 默认配置：`StarterConfig.config.default_states`
2. 任务配置：`TaskModel.config.default_states`

是否读取 / 修改任务级配置，由环境变量决定：

- `DB_PATH`：读取实际配置、更新配置时必填
- `task_id` 或 `TASK_ID`：选填

规则：

- 有 `task_id` / `TASK_ID`：读取或修改该任务的 `default_states`
- 没有 `task_id` / `TASK_ID`：读取或修改全局默认的 `default_states`

## 可调用函数

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    get_configer_state_schema_async,
    get_configer_state_config,
    get_configer_state_config_async,
    update_configer_state_config,
    update_configer_state_config_async,
)
```

一般优先用同步版本；只有在你当前上下文本身就是 async 时再用 async 版本。

### 1. 获取 schema

获取全部非 `system` schema：

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
- 有 `task_id` / `TASK_ID` 时读取任务配置，否则读取默认配置
- 如果只传 `section_name`，返回整个 section
- 如果再传 `field_name`，只返回该字段的配置对象

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

## 执行稳定性要求

当通过 codex-sdk / shell 子进程调用这些函数时，要特别注意下面几点：

- 在 `python -c`、heredoc、脚本子进程里，函数 `return` 不会自动显示，必须显式 `print(...)`
- 交互式 REPL 里的 `>>> some_func()` 会自动回显返回值，但 shell 子进程不会
- 如果命令一直处于运行中，Codex 侧通常看不到最终 `aggregated_output`，这时不能简单理解成“函数没有输出”，更准确是“命令没有结束”

推荐调用模板：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import get_configer_state_config

result = get_configer_state_config("judger", "eval_task_type")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

更新配置时同理：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import update_configer_state_config

result = update_configer_state_config("judger", {"eval_task_type": "code"})
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

说明：

- `timeout 20`：避免接口卡住时一直挂起
- `python3 -u`：关闭 stdout 缓冲，尽快把输出刷出来
- `print(..., flush=True)`：确保输出及时可见
- `json.dumps(..., default=str)`：避免对象序列化失败

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
2. 如果 15-20 秒内没有返回，记录为“接口调用卡住 / 超时”
3. 再只读检查 `.tables`、`.schema`、`SELECT ...`
4. 向用户说明“接口链路异常”，不要把直接改库当成正常执行路径

## 约束

- 不允许修改 `system`
- 不允许修改不存在的 section
- 不允许修改不存在的字段
- 不允许通过该 skill 修改 `default.task_id`

## 推荐工作流

1. 先根据用户目标确定要看哪个 section
2. 如果用户是在问“这个字段能填什么、默认是什么”，先调 `get_configer_state_schema(section_name=...)`
3. 如果用户是在问“现在数据库里实际配成什么了”，调 `get_configer_state_config(section_name=..., field_name=...)`
4. 如果用户要修改配置，把更新对象整理成“某个 section 下的字段 dict”
5. 调 `update_configer_state_config(section_name, updates)`
6. 如果 shell 调用，要显式 `print(json.dumps(...))`，并建议带 `timeout`
7. 如果接口调用超时，先报告接口异常，再决定是否进入只读排障
8. 按统一 success / error payload 回复用户，并明确 `scope`、`section_name`、`field_name`
