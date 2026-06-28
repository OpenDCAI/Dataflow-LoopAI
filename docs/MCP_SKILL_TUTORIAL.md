# LoopAI MCP 教程

本文档介绍如何把 `loopai/skills` 里的 Python 方法，定义成 Codex 可调用的 MCP tool，并最终通过 `config.toml` 暴露出来。

本文以当前仓库里的 `Configer` 实现为基准，文件入口如下：

- skill 实现：[loopai/skills/Configer](/home/lpc/repos/Dataflow-LoopAI/loopai/skills/Configer)
- MCP 基础层：[loopai/mcp](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp)
- 现有 MCP tool 示例：[loopai/mcp/tools/configer.py](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/tools/configer.py)
- 示例配置：[examples/codex_home_example/config.toml](/home/lpc/repos/Dataflow-LoopAI/examples/codex_home_example/config.toml)

## Sub-Node调用结构

在开始之前，先把这三层区分开：

- `loopai/skills/*`
  这里放真正的 Python 能力实现。
- `loopai/mcp/tools/*`
  这里放 MCP 包装层，把 Python 方法注册成 `@mcp.tool(...)`。
- `config.toml`
  这里只决定启动哪个 MCP server，以及当前允许暴露哪些 tool。

之前的版本：

- 只在 `loopai/skills` 里写了一个 `run()`，它就会自动出现在 Codex 工具列表里。

这会导致Codex调用失败率大增.

只有经过 `loopai/mcp/tools/*.py` 注册，并且被 `loopai/mcp/base.py` 中的统一注册逻辑加载，再被 `config.toml` 的 `enabled_tools` 打开，Codex 才会严格执行。

## 当前仓库里的 MCP 入口

当前统一的 MCP 定义入口是：

- server 名称定义：[loopai/mcp/base.py](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/base.py)
- tool 注册入口：[loopai/mcp/base.py](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/base.py)

现在的模式是：

- 只维护一个统一的 `loopai_mcp` server
- 不同 skill 通过不同 tool 名挂到这个 server 下面

这也是后续最推荐的方式。通常不要为每个 skill 再单独维护新的 MCP 入口。

## 第一步：先把 skill 的 Python 入口做稳定

一个 skill 想暴露成 MCP tool，首先要有稳定、清晰的 Python 入口函数。

例如当前这些 skill 的入口就比较适合直接包装：

- [loopai/skills/Analyzer/__init__.py](/home/lpc/repos/Dataflow-LoopAI/loopai/skills/Analyzer/__init__.py)
- [loopai/skills/Judger/__init__.py](/home/lpc/repos/Dataflow-LoopAI/loopai/skills/Judger/__init__.py)
- [loopai/skills/Trainer/__init__.py](/home/lpc/repos/Dataflow-LoopAI/loopai/skills/Trainer/__init__.py)
- [loopai/skills/WebCrawler/__init__.py](/home/lpc/repos/Dataflow-LoopAI/loopai/skills/WebCrawler/__init__.py)

它们大多已经提供了：

- `run(...)`
- `load_events(...)`

这类函数很适合直接暴露成：

- `<skill>_run`
- `<skill>_load_events`

如果你在开发一个新 skill，建议优先先把 `loopai/skills/<SkillName>/__init__.py` 整理成这种形态。

推荐准则：

- 函数参数尽量简单、明确
- 返回值尽量是 `dict`、`list`、`str`、`bool` 这类可序列化结构
- 尽量不要把复杂内部对象直接返回给 MCP

## 第二步：在 `loopai/mcp/tools` 下新增一个包装文件

假设你有一个 skill：

- `loopai/skills/Foo/__init__.py`

并且里面已经有：

```python
def run(task_id: str, mode: str = "default") -> dict:
    ...

def load_events(task_id: str, output_dir: str = "./outputs") -> list[dict]:
    ...
```

那么下一步是在：

- [loopai/mcp/tools](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/tools)

下新增一个文件：

- `loopai/mcp/tools/foo.py`

这里`foo`表示`judger`/`trainer`等类似的子节点名, 一个最小包装层可以写成这样：

```python
from __future__ import annotations

from typing import Any

from ..base import mcp
from loopai.skills.Foo import load_events, run


@mcp.tool(
    name="foo_run",
    description="Run the Foo skill for one task.",
)
def foo_run(task_id: str, mode: str = "default") -> dict[str, Any]:
    return run(task_id=task_id, mode=mode)


@mcp.tool(
    name="foo_load_events",
    description="Load persisted Foo stream events for one task.",
)
def foo_load_events(task_id: str, output_dir: str = "./outputs") -> list[dict[str, Any]]:
    return load_events(task_id=task_id, output_dir=output_dir)
```

这里最关键的是：

- 用 `from ..base import mcp`
- 用 `@mcp.tool(...)` 注册函数
- `name=` 是最终暴露到 Codex 的基础 tool 名, 一定要以`<node_name>_`开头, 比如`judger_`
- `description=` 是 tool 的描述, 用于在 Codex 侧显示, 其详细度也要对齐Skill.

## 第三步：给 tool 起一个稳定名字

tool 命名建议遵守下面的模式：

- `configer_get`
- `configer_update`
- `analyzer_run`
- `analyzer_load_events`
- `judger_run`
- `trainer_run`
- `webcrawler_run`

推荐规则：

- 用 skill 名作前缀
- 动词放在后面
- 尽量避免太泛的名字，比如 `run`、`get`、`update`

因为在 Codex 侧最终看到的名字会变成：

- `analyzer_run`

如果基础名本身太短或太泛，后面会很难分辨。

## 第四步：把新 tool 模块接到统一 MCP 入口上

仅仅创建了 `loopai/mcp/tools/foo.py` 还不够。

你还需要确保它会被统一注册入口加载。

当前 `base.py` 的模式是：

```python
def ensure_mcp_tools_registered() -> None:
    from .tools import configer
```

如果你新增了 `foo.py`，就要改成类似：

```python
def ensure_mcp_tools_registered() -> None:
    from .tools import configer
    from .tools import foo
```

这里即使导入结果没被直接使用，也必须保留。因为导入本身会触发 `@mcp.tool(...)` 注册。

如果没 import，tool 就不会出现在 MCP app 里。

## 第五步：在 `config.toml` 中启用这个 tool

MCP tool 注册完成后，还要在 Codex 的 `config.toml` 中把它打开。

当前推荐配置模式是：

```toml
[mcp_servers.loopai_mcp]
url = "http://127.0.0.1:8855/mcp/"
enabled = true
enabled_tools = [
  "configer_get_schema",
  "configer_get",
  "foo_run",
  "foo_load_events",
]
```

要点如下：

- `loopai_mcp` 是 server key，不是 tool 名
- `url = "http://127.0.0.1:8855/mcp/"` 表示 Codex 通过 FastAPI 暴露的 HTTP 地址连接统一的 LoopAI MCP server
- `enabled_tools` 里列出的才会真正暴露给 Codex

最终在 Codex 侧，这些工具名会显示为：

- `foo_run`
- `foo_load_events`
