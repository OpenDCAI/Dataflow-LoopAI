#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict

from langgraph.checkpoint.sqlite import SqliteSaver
from omegaconf import OmegaConf


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from loopai.agents.Analyzer import (
    ANALYZER_NODE_NAMES,
    get_analyzer_checkpoint_state,
    run_analyzer_standalone,
)


_DEFAULT_CHECKPOINT_PATH = "outputs/analyzer_checkpoints.sqlite"


def _load_state(config_path: str) -> Dict[str, Any]:
    if config_path.endswith(".json"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("default_states", cfg)

    cfg = OmegaConf.load(config_path)
    state_cfg = cfg.default_states if "default_states" in cfg else cfg
    return OmegaConf.to_container(state_cfg, resolve=True)


def _redact_key_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            key_name = str(key).lower()
            if key_name in {"api_key", "analyze_api_key"} or key_name.endswith("_key"):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_key_fields(child)
        return redacted
    if isinstance(value, list):
        return [_redact_key_fields(item) for item in value]
    return value


def _build_sqlite_checkpointer(checkpoint_path: str) -> SqliteSaver:
    checkpoint_dir = os.path.dirname(checkpoint_path)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)
    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    setup = getattr(checkpointer, "setup", None)
    if setup is not None:
        setup()
    return checkpointer


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run AnalyzerAgent directly with LangGraph checkpoint resume support."
    )
    parser.add_argument(
        "--config-path",
        required=True,
        help="Path to a YAML/JSON state config. If it contains default_states, that mapping is used as state.",
    )
    parser.add_argument(
        "--thread-id",
        default="analyzer-default",
        help="LangGraph checkpoint thread_id.",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=_DEFAULT_CHECKPOINT_PATH,
        help="SQLite checkpoint file path for cross-process resume.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume with the same thread_id from the latest checkpoint.",
    )
    parser.add_argument(
        "--from-node",
        default=None,
        help=(
            "Resume from a specific Analyzer node using checkpoint history when available. "
            f"Available nodes: {', '.join(ANALYZER_NODE_NAMES)}. "
            "Legacy names like analyze_result_node are also accepted."
        ),
    )
    parser.add_argument(
        "--print-result",
        action="store_true",
        help="Print the final state/result as JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    checkpointer = _build_sqlite_checkpointer(args.checkpoint_path)
    state = None if args.resume else _load_state(args.config_path)

    if args.resume:
        try:
            checkpoint_state = get_analyzer_checkpoint_state(
                args.thread_id,
                checkpointer=checkpointer,
                checkpoint_path=args.checkpoint_path,
            )
            print(
                f"[AnalyzerStandalone] resume checkpoint found: "
                f"thread_id={args.thread_id}, "
                f"current={checkpoint_state.get('current') or checkpoint_state.get('next_to') or 'unknown'}",
                flush=True,
            )
        except RuntimeError as exc:
            print(
                f"[AnalyzerStandalone] {exc}. "
                f"Please run once without --resume using thread_id={args.thread_id} "
                f"and checkpoint_path={args.checkpoint_path} first.",
                flush=True,
            )
            raise SystemExit(1)

    try:
        result = run_analyzer_standalone(
            state=state,
            thread_id=args.thread_id,
            resume=args.resume,
            from_node=args.from_node,
            checkpointer=checkpointer,
            checkpoint_path=args.checkpoint_path,
        )
    except RuntimeError as exc:
        print(f"[AnalyzerStandalone] {exc}", flush=True)
        raise SystemExit(1)

    if args.print_result:
        print(json.dumps(_redact_key_fields(result), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
