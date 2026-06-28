from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette


# MCP is served through the parent FastAPI app and mounted at `/mcp`.
mcp = FastMCP(
    "loopai-mcp",
    streamable_http_path="/mcp",
)


def ensure_mcp_tools_registered() -> None:
    """Import tool modules so their @mcp.tool decorators run exactly once."""
    from .tools import configer  # noqa: F401
    from .tools import judger  # noqa: F401
    from .tools import trainer  # noqa: F401


def build_embedded_mcp_app() -> Starlette:
    """Build a Streamable HTTP MCP app intended to be mounted at `/mcp`.

    The outer FastAPI app owns the real HTTP host/port, so the inner MCP app
    only needs to expose `/` before it is mounted at `/mcp`.
    """
    ensure_mcp_tools_registered()
    original_path = mcp.settings.streamable_http_path
    mcp.settings.streamable_http_path = "/"
    try:
        return mcp.streamable_http_app()
    finally:
        mcp.settings.streamable_http_path = original_path
