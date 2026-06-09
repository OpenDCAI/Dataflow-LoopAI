__version__ = "0.0.1"
version_info = (0, 0, 1)

__all__ = [
    '__version__',
    'version_info',
    'get_logger',
]


def __getattr__(name):
    if name == "get_logger":
        from .logger import get_logger
        return get_logger
    if name in {"short_version", "parse_version_info"}:
        from .version import parse_version_info, short_version
        return {"parse_version_info": parse_version_info, "short_version": short_version}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
