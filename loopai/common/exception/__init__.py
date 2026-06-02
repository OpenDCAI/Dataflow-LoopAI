from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable


class ErrorCode(StrEnum):
    UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"
    INTERRUPTED = "INTERRUPTED"
    TIMEOUT = "TIMEOUT"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    CONFIG_ERROR = "CONFIG_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"


DEFAULT_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.UNHANDLED_EXCEPTION: "Sub-agent crashed with an unhandled exception.",
    ErrorCode.INTERRUPTED: "Sub-agent interrupted by user.",
    ErrorCode.TIMEOUT: "Sub-agent timed out.",
    ErrorCode.INVALID_INPUT: "Sub-agent received invalid input.",
    ErrorCode.NOT_FOUND: "Required resource was not found.",
    ErrorCode.CONFIG_ERROR: "Sub-agent configuration is invalid.",
    ErrorCode.AUTH_ERROR: "Sub-agent authentication failed.",
    ErrorCode.PERMISSION_DENIED: "Sub-agent permission denied.",
    ErrorCode.DEPENDENCY_ERROR: "Required dependency is unavailable.",
    ErrorCode.EXTERNAL_SERVICE_ERROR: "External service request failed.",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_success_payload(data: Any = None, message: str = "Sub-agent completed.") -> dict[str, Any]:
    return {
        "ok": True,
        "status": "completed",
        "message": message,
        "data": data,
        "error": None,
    }


def build_error_payload(
    exc: BaseException,
    code: str | ErrorCode = ErrorCode.UNHANDLED_EXCEPTION,
    recoverable: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    normalized_code = ErrorCode(code) if code in ErrorCode._value2member_map_ else str(code)
    default_message = DEFAULT_ERROR_MESSAGES.get(
        normalized_code,
        "Sub-agent crashed with an unhandled exception.",
    ) if isinstance(normalized_code, ErrorCode) else "Sub-agent crashed with an unhandled exception."
    return {
        "ok": False,
        "status": "failed",
        "message": message or default_message,
        "data": None,
        "error": {
            "type": type(exc).__name__,
            "code": str(normalized_code),
            "detail": str(exc),
            "traceback": traceback.format_exc(),
            "recoverable": recoverable,
            "time": utc_now_iso(),
        },
    }


def emit_success(data: Any = None, message: str = "Sub-agent completed.") -> None:
    print(json.dumps(build_success_payload(data=data, message=message), ensure_ascii=False))
    sys.exit(0)


def emit_error(
    exc: BaseException,
    code: str | ErrorCode = ErrorCode.UNHANDLED_EXCEPTION,
    recoverable: bool = True,
    message: str | None = None,
) -> None:
    print(
        json.dumps(
            build_error_payload(
                exc=exc,
                code=code,
                recoverable=recoverable,
                message=message,
            ),
            ensure_ascii=False,
        )
    )
    sys.exit(1)


def run_with_exception_guard(
    main: Callable[[], Any],
    *,
    success_message: str = "Sub-agent completed.",
) -> None:
    try:
        result = main()
        emit_success(data=result, message=success_message)
    except KeyboardInterrupt as exc:
        emit_error(exc, code=ErrorCode.INTERRUPTED, recoverable=False)
    except TimeoutError as exc:
        emit_error(exc, code=ErrorCode.TIMEOUT, recoverable=True)
    except Exception as exc:
        emit_error(exc, code=ErrorCode.UNHANDLED_EXCEPTION, recoverable=True)


async def run_async_with_exception_guard(
    main: Callable[[], Any],
    *,
    success_message: str = "Sub-agent completed.",
) -> None:
    try:
        result = await main()
        emit_success(data=result, message=success_message)
    except KeyboardInterrupt as exc:
        emit_error(exc, code=ErrorCode.INTERRUPTED, recoverable=False)
    except TimeoutError as exc:
        emit_error(exc, code=ErrorCode.TIMEOUT, recoverable=True)
    except Exception as exc:
        emit_error(exc, code=ErrorCode.UNHANDLED_EXCEPTION, recoverable=True)
