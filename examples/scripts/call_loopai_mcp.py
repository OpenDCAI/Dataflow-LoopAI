#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
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
        "--server-python",
        default=sys.executable,
        help="Python executable used to launch `python -m loopai.mcp.server`.",
    )
    parser.add_argument(
        "--db-path",
        default=str(PROJECT_ROOT / "api" / "db" / "db.sqlite3"),
        help="DB_PATH passed to the MCP server.",
    )
    parser.add_argument(
        "--task-id",
        default=os.environ.get("TASK_ID", ""),
        help="TASK_ID passed to the MCP server.",
    )
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Project root used for cwd and PYTHONPATH.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List MCP tools exposed by loopai.mcp.server.",
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


async def _run(args: argparse.Namespace) -> int:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": "Python MCP SDK not installed. Install `mcp` in the chosen Python environment.",
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2

    env = os.environ.copy()
    env["DB_PATH"] = args.db_path
    env["PYTHONPATH"] = args.project_root
    if args.task_id:
        env["TASK_ID"] = args.task_id
    else:
        env.pop("TASK_ID", None)

    server_params = StdioServerParameters(
        command=args.server_python,
        args=["-m", "loopai.mcp.server"],
        env=env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()

            if args.list_tools:
                tools_result = await session.list_tools()
                payload = {
                    "ok": True,
                    "server": getattr(init_result.serverInfo, "name", "loopai-mcp"),
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
                "tool": args.tool,
                "arguments": tool_args,
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
