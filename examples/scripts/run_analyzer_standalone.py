#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

from omegaconf import OmegaConf


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from loopai.agents.Analyzer import ANALYZER_NODE_NAMES, run_analyzer_standalone


def _load_state(config_path: str) -> Dict[str, Any]:
    if config_path.endswith(".json"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("default_states", cfg)

    cfg = OmegaConf.load(config_path)
    state_cfg = cfg.default_states if "default_states" in cfg else cfg
    return OmegaConf.to_container(state_cfg, resolve=True)


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
        "--resume",
        action="store_true",
        help="Resume with the same thread_id from the latest checkpoint.",
    )
    parser.add_argument(
        "--from-node",
        choices=ANALYZER_NODE_NAMES,
        default=None,
        help="Resume from a specific Analyzer node using checkpoint history when available.",
    )
    parser.add_argument(
        "--print-result",
        action="store_true",
        help="Print the final state/result as JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    state = None if args.resume else _load_state(args.config_path)

    result = run_analyzer_standalone(
        state=state,
        thread_id=args.thread_id,
        resume=args.resume,
        from_node=args.from_node,
    )

    if args.print_result:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
