from __future__ import annotations

from .base import mcp
from .tools import configer  # noqa: F401
from .tools import analyzer  # noqa: F401


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
