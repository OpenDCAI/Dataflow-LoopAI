import os
import sys
import logging
import contextvars
import colorlog
from contextlib import contextmanager
from typing import Iterator, Optional

# ---------- 1) 自定义 SUCCESS 等级 ----------
SUCCESS_LEVEL_NUM = 25
logging.addLevelName(SUCCESS_LEVEL_NUM, "SUCCESS")

def success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS_LEVEL_NUM):
        self._log(SUCCESS_LEVEL_NUM, message, args, **kwargs)

logging.Logger.success = success

_ACTIVE_LOG_CONTEXT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_log_context", default=""
)

# ---------- 2) stdout / stderr 分流的过滤器 ----------
class MaxLevelFilter(logging.Filter):
    """仅放行 level < max_level 的日志（用于 stdout）。"""
    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.max_level

class MinLevelFilter(logging.Filter):
    """仅放行 level >= min_level 的日志（用于 stderr）。"""
    def __init__(self, min_level):
        super().__init__()
        self.min_level = min_level
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self.min_level


class InjectContextFilter(logging.Filter):
    """Inject context id into each LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.log_context = _ACTIVE_LOG_CONTEXT.get("")
        return True


class NoContextFilter(logging.Filter):
    """Only allow records emitted outside dataset context."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _ACTIVE_LOG_CONTEXT.get("")


class MatchContextFilter(logging.Filter):
    """Only allow records matching a specific context id."""

    def __init__(self, context_id: str):
        super().__init__()
        self.context_id = context_id

    def filter(self, record: logging.LogRecord) -> bool:
        return _ACTIVE_LOG_CONTEXT.get("") == self.context_id

# ---------- 3) 贴近 loguru 的 ColoredFormatter ----------
def _make_colored_formatter():
    # 颜色映射（loguru 的感觉：DEBUG/INFO 偏冷色，WARNING 黄，ERROR/CRITICAL 红，SUCCESS 绿）
    log_colors = {
        "DEBUG":    "blue",
        "INFO":     "white",
        "SUCCESS":  "green",
        "WARNING":  "yellow",
        "ERROR":    "red",
        "CRITICAL": "red,bg_white",
    }
    # 二级着色：让 levelname 与 message 按级别着色；name/func/line 用青色
    secondary = {
        "levelname": log_colors,
        "message":   {
            "DEBUG":    "blue",
            "INFO":     "white",
            "SUCCESS":  "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "red",
        },
        # 这些是固定青色，模拟 loguru 的 <cyan> 标签
        "name":      {k: "cyan" for k in log_colors},
        "funcName":  {k: "cyan" for k in log_colors},
        "lineno":    {k: "cyan" for k in log_colors},
        "asctime":   {k: "green" for k in log_colors},  # 时间绿色
    }


    return colorlog.ColoredFormatter(
        fmt=(
            "%(asctime_log_color)s%(asctime)s.%(msecs)03d%(reset)s"
            " | %(levelname_log_color)s%(levelname)-8s%(reset)s"
            " | %(message_log_color)sctx=%(log_context)s%(reset)s"
            " | %(name_log_color)s%(name)s%(reset)s"
            ":%(funcName_log_color)s%(filename)s%(reset)s"
            ":%(funcName_log_color)s%(funcName)s%(reset)s"
            ":%(lineno_log_color)s%(lineno)d%(reset)s"
            " - %(message_log_color)s%(message)s%(reset)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors=log_colors,
        secondary_log_colors=secondary,
        style="%",
    )

def _make_plain_formatter():
    """创建不带颜色的格式化器，用于文件输出"""
    return logging.Formatter(
        fmt=(
            "%(asctime)s.%(msecs)03d"
            " | %(levelname)-8s"
            " | ctx=%(log_context)s"
            " | %(name)s"
            ":%(filename)s"
            ":%(funcName)s"
            ":%(lineno)d"
            " - %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# ---------- 4) 实时刷新的文件 Handler ----------
class ImmediateFlushFileHandler(logging.FileHandler):
    """文件 Handler，每次写入后立即刷新，确保日志实时保存"""
    def emit(self, record):
        super().emit(record)
        self.flush()

# ---------- 5) 获取 logger（贴近 loguru 的 add 行为） ----------
def get_logger(level: str = None) -> logging.Logger:
    """返回一个名为 'DataFlow-LoopAI' 的 logger：
    - 控制台输出分流：<ERROR 到 stdout；>=ERROR 到 stderr
    - 颜色与格式尽量贴近 loguru 默认
    - 避免重复添加 handler
    - 如果设置了 DF_LOG_FILE_PATH 环境变量，会自动添加文件日志
    """
    if level is None:
        level = os.getenv("DF_LOGGING_LEVEL", "INFO")

    logger = logging.getLogger("DataFlow-LoopAI")
    logger.setLevel(level)
    logger.propagate = False  # 避免向 root 传播造成重复输出

    if logger.handlers:
        return logger

    colored_fmt = _make_colored_formatter()

    # stdout：DEBUG/INFO/SUCCESS/WARNING
    h_out = logging.StreamHandler(stream=sys.stdout)
    h_out.setLevel(level)
    h_out.addFilter(MaxLevelFilter(logging.ERROR))
    h_out.addFilter(NoContextFilter())
    h_out.setFormatter(colored_fmt)

    # stderr：ERROR/CRITICAL
    h_err = logging.StreamHandler(stream=sys.stderr)
    h_err.setLevel(level)
    h_err.addFilter(MinLevelFilter(logging.ERROR))
    h_err.addFilter(NoContextFilter())
    h_err.setFormatter(colored_fmt)

    logger.addHandler(h_out)
    logger.addHandler(h_err)
    logger.addFilter(InjectContextFilter())
    
    # 如果设置了日志文件路径，添加文件 handler
    log_file_path = os.getenv("DF_LOG_FILE_PATH")
    if log_file_path:
        add_file_handler(logger, log_file_path, level)
    
    return logger

def add_file_handler(
    logger: logging.Logger,
    log_file_path: str,
    level=None,
    *,
    no_context_only: bool = False,
) -> None:
    """为 logger 添加文件 handler，实时写入日志文件
    
    Args:
        logger: 要添加 handler 的 logger
        log_file_path: 日志文件路径（可以是相对路径或绝对路径）
        level: 日志级别（字符串如 "INFO" 或整数），如果为 None 则使用 logger 的当前级别
    """
    if level is None:
        level = logger.level
    elif isinstance(level, str):
        # 如果是字符串，转换为日志级别整数
        level = getattr(logging, level.upper(), logging.INFO)
    
    # 确保日志目录存在
    log_dir = os.path.dirname(os.path.abspath(log_file_path))
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    # 检查是否已经添加了相同路径的文件 handler
    abs_log_path = os.path.abspath(log_file_path)
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == abs_log_path:
            # 已经存在相同路径的文件 handler，不重复添加
            return
    
    # 创建文件 handler（使用实时刷新）
    plain_fmt = _make_plain_formatter()
    h_file = ImmediateFlushFileHandler(log_file_path, encoding='utf-8')
    h_file.setLevel(level)
    h_file.setFormatter(plain_fmt)
    if no_context_only:
        h_file.addFilter(NoContextFilter())
    
    logger.addHandler(h_file)
    logger.info(f"File logging enabled: {log_file_path}")


def add_context_file_handler(
    logger: logging.Logger,
    log_file_path: str,
    context_id: str,
    level=None,
) -> logging.Handler:
    """Attach a file handler that only receives logs in *context_id*."""
    if level is None:
        level = logger.level
    elif isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    log_dir = os.path.dirname(os.path.abspath(log_file_path))
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    plain_fmt = _make_plain_formatter()
    h_file = ImmediateFlushFileHandler(log_file_path, encoding="utf-8")
    h_file.setLevel(level)
    h_file.setFormatter(plain_fmt)
    h_file.addFilter(MatchContextFilter(context_id))
    logger.addHandler(h_file)
    return h_file


def remove_handler(logger: logging.Logger, handler: Optional[logging.Handler]) -> None:
    if handler is None:
        return
    try:
        logger.removeHandler(handler)
        handler.close()
    except Exception:
        pass


@contextmanager
def logging_context(context_id: str) -> Iterator[None]:
    token = _ACTIVE_LOG_CONTEXT.set(context_id or "")
    try:
        yield
    finally:
        _ACTIVE_LOG_CONTEXT.reset(token)