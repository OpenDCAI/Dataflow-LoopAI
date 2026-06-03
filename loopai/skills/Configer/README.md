# `loopai.skills.Configer`

给 codex / function skill 用的配置工具，专门修改非 `system` 的 `states` 参数。

## 能力

- 读取全部非 `system` 的状态参数说明
- 读取非 `system` 的字段说明和 schema 默认值
- 根据传入 JSON 修改对应 `states` 字段
- 自动根据环境变量决定修改任务配置还是默认用户配置

## 环境变量

- `DB_PATH`: 更新配置时必填，SQLite 数据库路径
- `task_id` 或 `TASK_ID`: 选填

行为规则：

- 有 `task_id` / `TASK_ID`：修改对应任务的 `states`
- 没有 `task_id` / `TASK_ID`：修改全局默认配置里的 `default_states`

## 导入

```python
from loopai.skills.Configer import (
    get_configer_state_config,
    get_configer_state_schema,
    update_configer_state_config,
)
```

## 读取 schema

```python
result = get_configer_state_schema()
```

也可以只取某个 section：

```python
result = get_configer_state_schema(section_name="judger")
result = get_configer_state_schema(section_name="configer")
result = get_configer_state_schema(section_name="default")
```

返回的是统一 success/error payload。

成功时 `data.states` 里包含：

- 字段说明
- 字段类型
- schema 默认值

并且不会暴露 `system` 配置，也不区分 task_id。

如果传了 `section_name`，则只返回对应分组的 schema。

## 读取实际配置

读取数据库中的实际配置值，需要 `DB_PATH`，并且会根据是否有 `task_id` / `TASK_ID` 自动读取任务配置或默认配置。

读取整个 section：

```python
get_configer_state_config(section_name="judger")
```

读取 section 下的单个字段：

```python
get_configer_state_config(section_name="judger", field_name="eval_temperature")
```

## 更新配置

现在更新接口只针对单个 section：

```python
update_configer_state_config(
    "default",
    {
        "language": {"value": "en", "type": "str"}
    }
)
```

或者：

```python
update_configer_state_config(
    "judger",
    {
        "eval_batch_size": {"value": 8, "type": "int"}
    }
)
```

也支持传 JSON 字符串。

## Shell 调用建议

如果是在 `python -c`、heredoc、codex-sdk 子进程里调用，要注意：

- 函数 `return` 不会自动显示
- 需要显式 `print(...)`
- 建议配合 `timeout` 和 `python3 -u`

示例：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import get_configer_state_config

result = get_configer_state_config("judger", "eval_task_type")
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

更新示例：

```bash
timeout 20 python3 -u <<'PY'
import json
from loopai.skills.Configer import update_configer_state_config

result = update_configer_state_config("judger", {"eval_task_type": "code"})
print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
PY
```

## 排障约束

- 正常配置读写应优先使用 skill 接口，不要直接写 SQLite
- 如果接口调用超时或报错，可以只读检查数据库结构和当前值用于排障
- 排障时也不建议绕过 skill 直接修改 `starterconfig` / `taskmodel`

## 限制

- 不允许修改 `system`
- 不允许修改不存在的 section
- 不允许修改不存在的字段
- 不允许通过该 skill 修改 `default.task_id`
