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

## 更新配置

支持两种入参形式：

```python
update_configer_state_config({
    "default": {
        "language": {"value": "en", "type": "str"}
    },
    "judger": {
        "eval_temperature": 0.2
    }
})
```

或者：

```python
update_configer_state_config({
    "states": {
        "judger": {
            "eval_batch_size": {"value": 8, "type": "int"}
        }
    }
})
```

也支持传 JSON 字符串。

## 限制

- 不允许修改 `system`
- 不允许修改不存在的 section
- 不允许修改不存在的字段
- 不允许通过该 skill 修改 `default.task_id`
