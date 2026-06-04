# `loopai.common.db_tool`

用于在 FastAPI 之外访问当前项目的 SQLite 数据库与配置表。

## 适用场景

- 独立脚本
- worker / 子进程
- agent 工具函数
- 不依赖 `register_tortoise(...)` 的异步任务

## 主要能力

- 手动初始化和关闭 Tortoise ORM
- 读取默认 `StarterConfig` 的 `system` / `states`
- 读取指定 `task_id` 的 `system` / `states`
- 读取指定 states 分组，如 `default` / `judger` / `analyzer`
- 更新默认 `StarterConfig` 下某个 states 分组
- 更新指定 `task_id` 下某个 states 分组，同时：
  - 覆盖已存在字段
  - 保留未修改字段
  - 支持新增字段

## 基本用法

```python
import asyncio

from loopai.common.db_tool import (
    sqlite_db_session,
    get_default_system_config,
    update_default_state_section_config,
    get_task_state_section_config,
    update_task_state_section_config,
)

DB_PATH = "api/db/db.sqlite3"


async def main():
    async with sqlite_db_session(DB_PATH):
        system_cfg = await get_default_system_config()
        print(system_cfg)

        judger_cfg = await get_task_state_section_config(
            "your-task-id",
            "judger",
        )
        print(judger_cfg)

        await update_default_state_section_config(
            "judger",
            {
                "eval_temperature": {"value": 0.1, "type": "float"},
            },
        )

        await update_task_state_section_config(
            "your-task-id",
            "judger",
            {
                "eval_api_key": {"value": "xxx", "type": "str"},
                "eval_temperature": 0.2,
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
```

## 初始化接口

### `sqlite_db_url(db_path)`

返回 SQLite 的 Tortoise 连接串。

### `init_sqlite_db(db_path)`

手动初始化 Tortoise。

### `close_db()`

关闭数据库连接。

### `sqlite_db_session(db_path)`

推荐的上下文管理器封装，自动初始化和关闭连接。

## 默认配置读取

### `get_default_system_config(starter_yaml_path=None)`

读取 `StarterConfig` 里的 `system` 配置。

- 如果数据库里没有 `starter` 记录，且传入了 `starter_yaml_path`，会自动从 YAML 初始化一条默认记录。

### `get_default_states_config(starter_yaml_path=None, section_name=None)`

读取 `StarterConfig` 里的 `default_states` 配置，并按 schema 包装成和现有 API 类似的结构。

- `section_name=None`：返回完整 states
- `section_name="default"`：返回默认平铺分组
- `section_name="judger"` / `"analyzer"`：返回指定嵌套分组

## 任务配置读取

### `get_task_config(task_id)`

返回任务整份原始配置。

### `get_task_system_config(task_id)`

返回任务的 `system` 配置。

### `get_task_states_config(task_id, section_name=None)`

返回任务的 `default_states` 配置。

### `get_task_state_section_config(task_id, section_name)`

返回指定 states 分组配置，例如：

- `default`
- `judger`
- `analyzer`
- `trainer`

## 任务配置更新

### `update_task_state_section_config(task_id, section_name, updates)`

更新指定任务某个 states 分组配置。

更新规则：

- 已存在字段：覆盖
- 未传字段：保留
- 新字段：新增写入

支持两种更新格式：

```python
{
    "eval_temperature": {"value": 0.2, "type": "float"},
    "eval_api_key": {"value": "xxx", "type": "str"},
}
```

或者直接传原始值：

```python
{
    "eval_temperature": 0.2,
    "eval_api_key": "xxx",
}
```

## 默认配置更新

### `update_default_state_section_config(section_name, updates, starter_yaml_path=None)`

更新默认 `StarterConfig.default_states` 下某个分组。

支持的 `updates` 格式与 `update_task_state_section_config(...)` 相同。

## 返回结构说明

读取接口返回结构统一类似：

```python
{
    "id": 1,
    "task_id": "xxx",   # 默认配置时没有这个字段
    "name": "starter",
    "config": {...}
}
```

其中 `config` 会尽量对齐当前 API 中 `config.py` 的包装风格。
