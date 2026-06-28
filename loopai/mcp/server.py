from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .base import build_embedded_mcp_app, mcp


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="LoopAI MCP Server", version="1.0.0", lifespan=lifespan)
    app.mount("/mcp", build_embedded_mcp_app())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    host = os.getenv("LOOPAI_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("LOOPAI_MCP_PORT", "8765"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
