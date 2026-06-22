#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_state(config_path: str) -> Dict[str, Any]:
    if config_path.endswith(".json"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("default_states", cfg)

    from omegaconf import OmegaConf

    cfg = OmegaConf.load(config_path)
    state_cfg = cfg.default_states if "default_states" in cfg else cfg
    return OmegaConf.to_container(state_cfg, resolve=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run WebCrawler standalone with runtime injection and checkpoints."
    )
    parser.add_argument("--config-path", required=False, help="YAML/JSON state config path.")
    parser.add_argument("--thread-id", default=None, help="Task/thread id. Priority: CLI > env > state.")
    parser.add_argument("--checkpoint-path", default=None, help="SQLite checkpoint path.")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint.")
    parser.add_argument("--from-step", default=None, help="Force start from a specific step.")
    parser.add_argument("--user-query", default=None, help="Override user query.")
    parser.add_argument("--print-result", action="store_true", help="Print final state JSON.")
    return parser.parse_args()


def _build_result_data(final_state: Dict[str, Any]) -> Dict[str, Any]:
    wc = final_state.get("webcrawler", {}) or {}
    return {
        "task_id": final_state.get("task_id"),
        "output_dir": final_state.get("output_dir"),
        "run_id": wc.get("output_run_id"),
        "crawl_output_dir": wc.get("output_dir"),
        "total_pages": (wc.get("output_result") or {}).get("total_pages", 0),
        "dataset_sft_count": wc.get("dataset_sft_count", 0),
        "dataset_pt_count": wc.get("dataset_pt_count", 0),
        "dataset_sft_path": wc.get("dataset_sft_path", ""),
        "dataset_pt_path": wc.get("dataset_pt_path", ""),
        "dataset_sft_mapped_path": wc.get("dataset_sft_mapped_path", ""),
        "dataset_pt_mapped_path": wc.get("dataset_pt_mapped_path", ""),
    }


def main() -> None:
    args = _parse_args()

    if not args.config_path and not args.resume:
        raise SystemExit("--config-path is required unless --resume is used.")

    from loopai.common.exception import ErrorCode, emit_error, emit_success

    try:
        from loopai.skills.WebCrawler.runner import run_webcrawler_standalone

        state: Optional[Dict[str, Any]] = None if args.resume else _load_state(args.config_path)

        if state is not None and args.user_query:
            state["automated_query"] = args.user_query

        result = run_webcrawler_standalone(
            state=state,
            thread_id=args.thread_id,
            resume=args.resume,
            from_step=args.from_step,
            checkpoint_path=args.checkpoint_path,
            user_query=args.user_query,
        )

        if args.print_result:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str), file=sys.stderr)

        emit_success(
            data=_build_result_data(result),
            message="WebCrawler pipeline completed.",
        )
    except Exception as exc:
        code = ErrorCode.UNHANDLED_EXCEPTION
        if isinstance(exc, ValueError):
            code = ErrorCode.CONFIG_ERROR
        elif isinstance(exc, FileNotFoundError):
            code = ErrorCode.NOT_FOUND
        emit_error(exc, code=code, recoverable=True, message="WebCrawler pipeline failed.")


if __name__ == "__main__":
    main()

