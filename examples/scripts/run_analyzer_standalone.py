#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ANALYZER_NODE_NAMES = (
    "eval_model",
    "analyze_result",
    "draw_conclusion",
    "finish",
)


def _load_state(config_path: str) -> Dict[str, Any]:
    if config_path.endswith(".json"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("default_states", cfg)

    from omegaconf import OmegaConf

    cfg = OmegaConf.load(config_path)
    state_cfg = cfg.default_states if "default_states" in cfg else cfg
    return OmegaConf.to_container(state_cfg, resolve=True)


def _redact_keys(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            key_name = str(key).lower()
            if (
                key_name == "api_key"
                or key_name.endswith("_api_key")
                or key_name == "token"
                or key_name.endswith("_token")
                or key_name.endswith("_key")
            ):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_keys(child)
        return redacted
    if isinstance(value, list):
        return [_redact_keys(item) for item in value]
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Analyzer directly with standalone checkpoint resume support."
    )
    parser.add_argument(
        "--config-path",
        required=False,
        help="Path to a YAML/JSON state config. If it contains default_states, that mapping is used as state.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Analyzer task/thread id. Priority: CLI > TASK_ID env > state/default.",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=None,
        help="Legacy compatibility option. Analyzer state is managed by Configer/external runtime.",
    )
    parser.add_argument(
        "--version-id",
        default=None,
        help="Analyzer run version id. Use a new value for version2 to avoid reusing an old finished checkpoint.",
    )
    parser.add_argument(
        "--baseline-result-path",
        default=None,
        help="Optional previous jsonl result path for Historical Comparison.",
    )
    parser.add_argument("--resume", action="store_true", help="Resume using Configer/external runtime state.")
    parser.add_argument("--from-node", default=None, help="Force Analyzer to resume from a specific step.")
    parser.add_argument("--list-nodes", action="store_true", help="List standalone Analyzer steps and exit.")
    parser.add_argument("--print-result", action="store_true", help="Print final state/result as JSON.")
    parser.add_argument("--stream-stdout", action="store_true", help="Print StreamEvent JSON lines to stdout.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.list_nodes:
        for node_name in _ANALYZER_NODE_NAMES:
            print(node_name)
        return

    from loopai.common.exception import ErrorCode, emit_error, emit_success

    if not args.config_path and not args.resume:
        emit_error(
            ValueError("--config-path is required unless --list-nodes or --resume is used."),
            code=ErrorCode.INVALID_INPUT,
            recoverable=False,
            message="Analyzer CLI input contract validation failed.",
        )

    from loopai.skills.Analyzer.event_tool import get_analyzer_event_writer
    from loopai.skills.Analyzer.runner import run_analyzer_standalone
    from loopai.skills.Analyzer.runtime_config import resolve_analyzer_runtime_config

    writer = None
    try:
        state = None if args.resume else _load_state(args.config_path)
        if state is not None and args.baseline_result_path:
            state.setdefault("analyzer", {})["baseline_result_path"] = args.baseline_result_path

        runtime_state = state if isinstance(state, dict) else {}
        runtime = resolve_analyzer_runtime_config(
            runtime_state,
            thread_id=args.thread_id,
            checkpoint_path=args.checkpoint_path,
            version_id=args.version_id,
        )
        writer = get_analyzer_event_writer(
            context_id=runtime["thread_id"],
            log_file_path=runtime["output_dir"],
            stdout=args.stream_stdout,
            state=state if isinstance(state, dict) else None,
            version_id=runtime["version_id"],
        )
        result = run_analyzer_standalone(
            state=state,
            thread_id=args.thread_id,
            resume=args.resume,
            from_node=args.from_node,
            checkpoint_path=args.checkpoint_path,
            version_id=args.version_id,
            baseline_result_path=args.baseline_result_path,
            stream_stdout=args.stream_stdout,
            writer=writer,
            emit_status=False,
        )
    except (ValueError, TypeError) as exc:
        emit_error(
            exc,
            code=ErrorCode.INVALID_INPUT,
            recoverable=False,
            message="Analyzer CLI input contract validation failed.",
            stream_writer=writer,
        )
    except RuntimeError as exc:
        emit_error(
            exc,
            code=ErrorCode.CONFIG_ERROR,
            recoverable=True,
            message="Analyzer runtime configuration is incomplete.",
            stream_writer=writer,
        )
    except Exception as exc:
        emit_error(
            exc,
            code=ErrorCode.UNHANDLED_EXCEPTION,
            recoverable=True,
            message="Analyzer crashed with an unhandled exception.",
            stream_writer=writer,
        )

    if args.print_result:
        print(json.dumps(_redact_keys(result), ensure_ascii=False, indent=2, default=str))
    emit_success(
        data={
            "task_id": result.get("task_id") if isinstance(result, dict) else args.thread_id,
            "version_id": result.get("version_id") if isinstance(result, dict) else args.version_id,
            "current": result.get("current") if isinstance(result, dict) else None,
            "last_completed": result.get("last_completed") if isinstance(result, dict) else None,
        },
        message="Analyzer CLI completed.",
        stream_writer=writer,
    )


if __name__ == "__main__":
    main()
