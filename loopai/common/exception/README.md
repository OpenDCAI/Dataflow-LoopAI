# `loopai.common.exception`

用于给子 Agent、worker、独立脚本提供统一的 JSON 成功/异常返回格式。

## 目标

- 运行成功时输出统一的 success JSON
- 运行失败时输出统一的 error JSON
- 提供错误码常量，便于后续扩展
- 尽量少写样板代码

## 返回格式

成功：

```json
{
  "ok": true,
  "status": "completed",
  "message": "Sub-agent completed.",
  "data": {},
  "error": null
}
```

失败：

```json
{
  "ok": false,
  "status": "failed",
  "message": "Sub-agent crashed with an unhandled exception.",
  "data": null,
  "error": {
    "type": "RuntimeError",
    "code": "UNHANDLED_EXCEPTION",
    "detail": "vector index not found",
    "traceback": "...",
    "recoverable": true,
    "time": "2026-06-01T00:00:00Z"
  }
}
```

## 推荐用法

同步入口：

```python
from loopai.common.exception import run_with_exception_guard


def main():
    return {"result": "ok"}


if __name__ == "__main__":
    run_with_exception_guard(main)
```

异步入口：

```python
import asyncio

from loopai.common.exception import run_async_with_exception_guard


async def main():
    return {"result": "ok"}


if __name__ == "__main__":
    asyncio.run(run_async_with_exception_guard(main))
```

## 手动控制

如果你想自己控制成功/失败时机，也可以直接调用：

```python
from loopai.common.exception import emit_success, emit_error, ErrorCode

emit_success(data={"a": 1})
emit_error(RuntimeError("boom"), code=ErrorCode.UNHANDLED_EXCEPTION)
```

### 在 `except Exception as e` 里使用

最常见的写法就是直接把 `e` 传给 `emit_error(...)`：

```python
from loopai.common.exception import emit_error, ErrorCode

try:
    raise ValueError("missing codex_api_key")
except Exception as e:
    emit_error(
        e,
        code=ErrorCode.CONFIG_ERROR,
        recoverable=True,
        message="Codex runtime config is incomplete.",
    )
```

### `message` 和异常内容的区别

- `message`：顶层业务提示，给调用方直接展示
- `str(exc)`：会自动写入 `error.detail`
- `traceback`：会自动写入 `error.traceback`

例如上面的代码会返回类似：

```json
{
  "ok": false,
  "status": "failed",
  "message": "Codex runtime config is incomplete.",
  "data": null,
  "error": {
    "type": "ValueError",
    "code": "CONFIG_ERROR",
    "detail": "missing codex_api_key",
    "traceback": "...",
    "recoverable": true,
    "time": "2026-06-01T00:00:00Z"
  }
}
```

### 手动构造异常对象

如果不是在 `except` 里，也可以直接构造一个异常传进去：

```python
from loopai.common.exception import emit_error, ErrorCode

emit_error(
    RuntimeError("vector index not found"),
    code=ErrorCode.NOT_FOUND,
    recoverable=False,
    message="Vector index file is missing.",
)
```

## 错误码

内置错误码定义在 `ErrorCode`：

- `UNHANDLED_EXCEPTION`
- `INTERRUPTED`
- `TIMEOUT`
- `INVALID_INPUT`
- `NOT_FOUND`
- `CONFIG_ERROR`
- `AUTH_ERROR`
- `PERMISSION_DENIED`
- `DEPENDENCY_ERROR`
- `EXTERNAL_SERVICE_ERROR`

后续新增错误码时，直接在 `ErrorCode` 和 `DEFAULT_ERROR_MESSAGES` 中追加即可。

## 可用函数

- `build_success_payload(data=None, message="Sub-agent completed.")`
- `build_error_payload(exc, code=ErrorCode.UNHANDLED_EXCEPTION, recoverable=True, message=None)`
- `emit_success(data=None, message="Sub-agent completed.")`
- `emit_error(exc, code=ErrorCode.UNHANDLED_EXCEPTION, recoverable=True, message=None)`
- `run_with_exception_guard(main, success_message="Sub-agent completed.")`
- `run_async_with_exception_guard(main, success_message="Sub-agent completed.")`
