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
    run_id: Optional[str] = None
```

- 前 6 个字段兼容原有常用写法
- `time` 会在写入时自动补上
- `run_id` 可选，适合同一个 task 多次运行时做区分

## API

```python
get_event_writer(
    name: str,
    context_id: str,
    log_file_path: str = "./outputs",
    *,
    run_id: str | None = None,
)
```

返回值是一个可调用 writer，支持：

```python
writer(StreamEvent(...))
writer(StreamEvent(...).json())
```

## 读取事件

```python
from loopai.common.event_tool import load_stream_events, dump_stream_events_json

events = load_stream_events(name="judger", context_id="task_001")
payload = dump_stream_events_json(name="judger", context_id="task_001")
```

## 说明

- 持久化文件使用 pickle，不适合直接给前端读取
- 更适合后端轮询、SSE 转发、或执行完成后统一回放
- 为了降低并发写坏文件的风险，写入时带了文件锁
