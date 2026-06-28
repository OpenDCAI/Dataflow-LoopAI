#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call LoopAI MCP server via the Python MCP SDK.",
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("LOOPAI_MCP_SERVER_URL", "http://127.0.0.1:8855/mcp/"),
        help="HTTP MCP endpoint exposed by the FastAPI service, for example `http://127.0.0.1:8855/mcp/`.",
    )
    parser.add_argument(
        "--db-path",
        default=str(PROJECT_ROOT / "api" / "db" / "db.sqlite3"),
        help="Optional context shown in the script output.",
    )
    parser.add_argument(
        "--task-id",
        default=os.environ.get("TASK_ID", ""),
        help="Optional context shown in the script output.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List MCP tools exposed by the FastAPI-mounted LoopAI MCP endpoint.",
    )
    parser.add_argument(
        "--tool",
        default="",
        help="MCP tool name to call, e.g. judger_run.",
    )
    parser.add_argument(
        "--args-json",
        default="{}",
        help="JSON object passed as MCP tool arguments.",
    )
    return parser


def _normalize_content_item(item: Any) -> Any:
    if hasattr(item, "text"):
        text = getattr(item, "text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except Exception:
                return text
    if hasattr(item, "data"):
        return getattr(item, "data")
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return str(item)


@asynccontextmanager
async def _http_session(server_url: str):
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": "Python MCP SDK not installed or too old for HTTP transport.",
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from exc

    async with streamablehttp_client(server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            yield session


async def _run(args: argparse.Namespace) -> int:
    async with _http_session(args.server_url) as session:
        init_result = await session.initialize()

        if args.list_tools:
            tools_result = await session.list_tools()
            payload = {
                "ok": True,
                "server": getattr(init_result.serverInfo, "name", "loopai-mcp"),
                "server_url": args.server_url,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.inputSchema,
                    }
                    for tool in tools_result.tools
                ],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        if not args.tool:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "message": "Either --list-tools or --tool is required.",
                    },
                    ensure_ascii=False,
                )
            )
            return 2

        try:
            tool_args = json.loads(args.args_json)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "message": "args-json must be a JSON object string.",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
            return 2

        result = await session.call_tool(args.tool, arguments=tool_args)
        payload = {
            "ok": True,
            "server": getattr(init_result.serverInfo, "name", "loopai-mcp"),
            "server_url": args.server_url,
            "tool": args.tool,
            "arguments": tool_args,
            "task_id": args.task_id,
            "db_path": args.db_path,
            "content": [_normalize_content_item(item) for item in result.content],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
