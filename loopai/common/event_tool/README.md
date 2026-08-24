# `loopai.common.event_tool`

用于给指定 `context_id` 和 `agent_name` 生成一个基于 pickle 的事件 writer。

推荐通过子模块直接导入：

```python
from loopai.common.event_tool import StreamEvent, get_event_writer
```

## 目标

- 保持 `writer(StreamEvent(...))` 这种调用习惯
- 默认把事件持久化到 `./outputs/{context_id}/{agent_name}.pkl`
- 文件里存的是 `StreamEvent` 对象数组
- 每次调用 `writer(...)` 都会向数组末尾追加一个新事件
- 自动脱敏 `api_key` / `*_api_key` / `token` / `*_key`
- 可选输出 JSONL 到 stdout，方便 CLI/Codex/前端流式接入
- 支持通过 `append_stream_message(state, event)` 写入 `state["messages"]`

## 快速开始

```python
from loopai.common.event_tool import StreamEvent, get_event_writer

writer = get_event_writer(name="judger", context_id="task_001")

writer(StreamEvent(
    current="judger",
    progress=0.2,
    message="loading dataset",
    data={"rows": 128},
))

writer(StreamEvent(
    current="judger",
    progress=1.0,
    message="finished",
))
```

默认会写到：

```text
./outputs/task_001/judger.pkl
```

## 数据结构

```python
from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class StreamEvent:
    current: str
    progress: Optional[float] = None
    progress_num: Optional[int] = None
    total: Optional[int] = None
    message: Optional[str] = None
    data: Optional[Any] = None
    time: Optional[str] = None
    version_id: Optional[str] = None
    node: Optional[str] = None
    status: Optional[str] = None
    context_id: Optional[str] = None
    error: Optional[Any] = None
```

- 前 6 个字段兼容原有常用写法
- `time` 会在写入时自动补上
- `version_id` 为空时会在首次写入时自动生成，并同步到 task runtime
- `node` 会由 writer 的 `name` 自动填充，`status` 会统一写成 `running`

## API

```python
get_event_writer(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
    *,
    version_id: str | None = None,
)
```

返回值是一个可调用 writer，支持：

```python
writer(StreamEvent(...))
writer(StreamEvent(...).json())
```

如果 `stdout=True`，writer 会额外向 stdout 打印一行 JSON；如果
`stdout=None`，可通过环境变量打开：

```bash
export LOOPAI_STREAM_STDOUT=1
```

如果传入 `state=state`，writer 会把事件追加到 `state["messages"]`。

## 读取事件

```python
from loopai.common.event_tool import load_stream_events, dump_stream_events_json

events = load_stream_events(name="judger", context_id="task_001")
payload = dump_stream_events_json(name="judger", context_id="task_001")
```

## 追加到 state

```python
from loopai.common.event_tool import StreamEvent, append_stream_message

append_stream_message(
    state,
    StreamEvent(current="analyzer", progress=0.2, message="running"),
)
```

## 说明

- 持久化文件使用 pickle，不适合直接给前端读取
- 更适合后端轮询、SSE 转发、或执行完成后统一回放
- 为了降低并发写坏文件的风险，写入时带了文件锁
- stdout 输出是 JSON serializable 的 dict，适合被上层 SSE/前端转发
