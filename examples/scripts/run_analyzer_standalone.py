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

_DEFAULT_CHECKPOINT_PATH = "outputs/analyzer_checkpoints.sqlite"
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
            if key_name == "api_key" or key_name.endswith("_api_key") or key_name == "token" or key_name.endswith("_key"):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_keys(child)
        return redacted
    if isinstance(value, list):
        return [_redact_keys(item) for item in value]
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run AnalyzerAgent directly with LangGraph checkpoint resume support."
    )
    parser.add_argument(
        "--config-path",
        required=False,
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
        help="SQLite checkpoint file path for standalone Analyzer resume.",
    )
    parser.add_argument(
        "--baseline-result-path",
        default=None,
        help="Optional previous jsonl result path for Historical Comparison.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume with the same thread_id from the latest checkpoint.",
    )
    parser.add_argument(
        "--from-node",
        default=None,
        help="Force standalone Analyzer to resume from a specific step.",
    )
    parser.add_argument(
        "--list-nodes",
        action="store_true",
        help="List standalone Analyzer resume steps and exit.",
    )
    parser.add_argument(
        "--print-result",
        action="store_true",
        help="Print the final state/result as JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.list_nodes:
        for node_name in _ANALYZER_NODE_NAMES:
            print(node_name)
        return

    if not args.config_path:
        raise SystemExit("--config-path is required unless --list-nodes is used.")

    from loopai.agents.Analyzer import run_analyzer_standalone

    state = None if args.resume else _load_state(args.config_path)
    if state is not None and args.baseline_result_path:
        state.setdefault("analyzer", {})["baseline_result_path"] = args.baseline_result_path

    result = run_analyzer_standalone(
        state=state,
        thread_id=args.thread_id,
        resume=args.resume,
        from_node=args.from_node,
        checkpoint_path=args.checkpoint_path,
        baseline_result_path=args.baseline_result_path,
    )

    if args.print_result:
        print(json.dumps(_redact_keys(result), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
