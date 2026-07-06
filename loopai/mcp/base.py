from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette


# MCP tools are registered once here and can be exposed by any HTTP wrapper.
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
    """Build a Streamable HTTP MCP app intended to be mounted at `/mcp`."""
    ensure_mcp_tools_registered()
    original_path = mcp.settings.streamable_http_path
    mcp.settings.streamable_http_path = "/"
    try:
        return mcp.streamable_http_app()
    finally:
        mcp.settings.streamable_http_path = original_path
