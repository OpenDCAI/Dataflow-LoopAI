# Configer Skill

用于处理 LoopAI 中的配置类请求。

## 什么时候使用

当用户要执行以下任一操作时，使用本 skill：

- 查看某个 agent 的可配置字段
- 询问某个配置项是什么意思
- 修改某个 task 下的 states 配置
- 修改默认用户配置中的 states 配置

不要用它处理：

- `system` 配置修改
- 训练、评测、分析、爬取、数据构造本身

## 背景知识

LoopAI 的配置分两层：

1. 默认配置：`StarterConfig.config.default_states`
2. 任务配置：`TaskModel.config.default_states`

是否修改任务级配置，由环境变量决定：

- `DB_PATH`：必填
- `task_id` 或 `TASK_ID`：选填

规则：

- 有 `task_id` / `TASK_ID`：修改该任务的 `default_states`
- 没有 `task_id` / `TASK_ID`：修改全局默认的 `default_states`

## 可调用函数

```python
from loopai.skills.Configer import (
    get_configer_state_schema,
    update_configer_state_config,
)
```

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
- 返回统一 success / error payload

### 2. 更新配置

示例：

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

也支持：

```python
update_configer_state_config({
    "states": {
        "judger": {
            "eval_batch_size": {"value": 8, "type": "int"}
        }
    }
})
```

## 约束

- 不允许修改 `system`
- 不允许修改不存在的 section
- 不允许修改不存在的字段
- 不允许修改 `default.task_id`

## 返回格式

成功：

```json
{
  "ok": true,
  "status": "completed",
  "message": "Non-system state config updated.",
  "data": {},
  "error": null
}
```

失败：

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

## 推荐工作流

1. 先根据用户目标确定要看哪个 section
2. 调 `get_configer_state_schema(section_name=...)`
3. 再根据用户输入整理成嵌套 `states` 更新对象
4. 调 `update_configer_state_config(...)`
5. 根据统一 success / error payload 回复用户
