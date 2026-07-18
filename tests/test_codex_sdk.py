"""通过 codex-runner 子进程调用 Codex 的简单 demo。

运行示例：
    python tests/test_codex_sdk.py
    python tests/test_codex_sdk.py "请分析当前目录下项目结构"
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CODEX_RUNNER_DIR = PROJECT_ROOT / "codex-runner"
PROJECT_CODEX_HOME = PROJECT_ROOT / "codex_home"
OUTPUT_PATH = PROJECT_ROOT / "codex_output" / "toy_result.txt"
RUN_MODE = os.getenv("CODEX_DEMO_MODE", "auth")
CODEX_MODEL = os.getenv("CODEX_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
CODEX_BASE_URL = os.getenv(
    "CODEX_BASE_URL",
    os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEFAULT_PROMPT = (
    "Create a file at ./codex_output/toy_result.txt. "
    "The file content must be exactly:\n"
    "HELLO_FROM_CODEX\n"
)
SUBPROCESS_STREAM_LIMIT = 16 * 1024 * 1024


async def run_codex_task(
    prompt: str,
    workspace: str = str(PROJECT_ROOT),
    run_mode: str = RUN_MODE,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["CODEX_WORKSPACE"] = workspace
    env["CODEX_HOME"] = str(PROJECT_CODEX_HOME)

    if run_mode == "deepseek":
        env["CODEX_MODEL"] = CODEX_MODEL
        env["CODEX_BASE_URL"] = CODEX_BASE_URL
        if DEEPSEEK_API_KEY:
            env["DEEPSEEK_API_KEY"] = DEEPSEEK_API_KEY
    elif run_mode == "dashscope":
        env["CODEX_USE_PROJECT_CONFIG"] = "1"
        env["CODEX_MODEL"] = CODEX_MODEL
        dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", DEEPSEEK_API_KEY)
        if dashscope_api_key:
            env["DASHSCOPE_API_KEY"] = dashscope_api_key

    proc = await asyncio.create_subprocess_exec(
        "corepack",
        "yarn",
        "dev",
        prompt,
        cwd=str(CODEX_RUNNER_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=SUBPROCESS_STREAM_LIMIT,
    )

    assert proc.stdout is not None
    assert proc.stderr is not None

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    events: list[dict[str, Any]] = []
    final_result: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None

    async def consume_stdout() -> None:
        nonlocal final_result

        while True:
            line = await proc.stdout.readline()
            if not line:
                break

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            stdout_lines.append(text)

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                print(f"[stdout] {text}")
                continue

            payload_type = payload.get("type")
            if payload_type == "event":
                event = payload.get("event")
                if isinstance(event, dict):
                    events.append(event)
                    print(json.dumps(event, ensure_ascii=False))
            elif payload_type == "completed":
                result = payload.get("result")
                if isinstance(result, dict):
                    final_result = result
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(json.dumps(payload, ensure_ascii=False))

    async def consume_stderr() -> None:
        nonlocal error_payload

        while True:
            line = await proc.stderr.readline()
            if not line:
                break

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            stderr_lines.append(text)

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                print(f"[stderr] {text}", file=sys.stderr)
                continue

            if payload.get("type") == "error":
                error_payload = payload

            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)

    await asyncio.gather(consume_stdout(), consume_stderr())
    returncode = await proc.wait()

    stdout_text = "\n".join(stdout_lines)
    stderr_text = "\n".join(stderr_lines)

    if returncode != 0:
        err_json = error_payload or {
            "type": "error",
            "message": stderr_text or "Codex runner failed",
        }

        return {
            "ok": False,
            "returncode": returncode,
            "error": err_json,
            "events": events,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }

    return {
        "ok": True,
        "result": final_result
        or {
            "type": "raw",
            "text": stdout_text,
        },
        "events": events,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }


async def run_demo(prompt: str = DEFAULT_PROMPT) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT_PATH.exists():
        OUTPUT_PATH.unlink()

    print(f"workspace: {PROJECT_ROOT}")
    print(f"runner_dir: {CODEX_RUNNER_DIR}")
    print(f"codex_home: {PROJECT_CODEX_HOME}")
    print(f"run_mode: {RUN_MODE}")
    if RUN_MODE == "deepseek":
        print(f"model: {CODEX_MODEL}")
        print(f"base_url: {CODEX_BASE_URL}")
    elif RUN_MODE == "dashscope":
        print(f"model: {CODEX_MODEL}")
        print("provider_config: codex_home/config.toml -> dashscope_http")
    print(f"prompt: {prompt}")
    print("starting codex runner...")

    result = await run_codex_task(
        prompt=prompt,
        workspace=str(PROJECT_ROOT),
        run_mode=RUN_MODE,
    )

    print("codex runner finished")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"output_exists: {OUTPUT_PATH.exists()}")
    if OUTPUT_PATH.exists():
        print(f"output_path: {OUTPUT_PATH}")
        print(f"output_text: {OUTPUT_PATH.read_text(encoding='utf-8')!r}")


if __name__ == "__main__":
    cli_prompt = " ".join(sys.argv[1:]).strip()
    asyncio.run(run_demo(cli_prompt or DEFAULT_PROMPT))
