# -*- coding: utf-8 -*-
"""CLI for the pluggable Codex-backed Judger worker."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Optional, Sequence

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the LoopAI Judger benchmark skill via Codex SDK")
    p.add_argument("--list-benchmarks", action="store_true", help="List discovered benchmark skills")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("list", help="list discovered benchmark skills")
    start = sub.add_parser("start", help="start a benchmark worker")
    start.add_argument("--list-benchmarks", dest="list_benchmarks_cmd", action="store_true", help=argparse.SUPPRESS)
    for target in (p, start):
        target.add_argument("--run", "--run-dir", dest="run_dir", default="outputs/judger")
        target.add_argument("--benchmark", default=None)
        target.add_argument("--predictions", "--input", dest="predictions", default=None)
        target.add_argument("--references", dest="references", default=None)
        target.add_argument("--lake", "--warehouse", dest="lake", default=None)
        target.add_argument("--dataset", dest="dataset", default=None)
        target.add_argument("--model", default=None)
        target.add_argument("--thread-id", default=None)
        target.add_argument("--resume", action="store_true")
        target.add_argument("--from-step", default=None, help=argparse.SUPPRESS)
        target.add_argument(
            "--list-steps",
            dest="list_steps" if target is p else "list_steps_cmd",
            action="store_true",
            help=argparse.SUPPRESS,
        )
        target.add_argument("--snapshot-id", default=None)
        target.add_argument("--config-path", default=None)
    sub.add_parser("status").add_argument("--run", "--run-dir", dest="run_dir", default="outputs/judger")
    resume = sub.add_parser("resume")
    resume.add_argument("--list-benchmarks", dest="list_benchmarks_cmd", action="store_true", help=argparse.SUPPRESS)
    resume.add_argument("--run", "--run-dir", dest="run_dir", default="outputs/judger")
    resume.add_argument("--benchmark", default=None); resume.add_argument("--model", default=None)
    resume.add_argument("--lake", "--warehouse", dest="lake", default=None)
    resume.add_argument("--dataset", default=None); resume.add_argument("--snapshot-id", default=None)
    resume.add_argument("--config-path", default=None)
    resume.add_argument("--from-step", default=None, help=argparse.SUPPRESS)
    resume.add_argument("--list-steps", dest="list_steps_cmd", action="store_true", help=argparse.SUPPRESS)
    return p

def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "list_steps", False) or getattr(args, "list_steps_cmd", False):
        print("\n".join((
            "validate", "kill_vllm", "start_vllm", "format_data", "generate",
            "evaluate", "kill_vllm_cleanup", "eval_general_text", "finish",
        ))); return 0
    if (
        getattr(args, "command", None) == "list"
        or getattr(args, "list_benchmarks", False)
        or getattr(args, "list_benchmarks_cmd", False)
    ):
        from loopai.skills.benchmarks import discover_benchmarks
        print(json.dumps(sorted(discover_benchmarks()), ensure_ascii=False)); return 0
    from . import run, status
    if args.command == "status":
        print(json.dumps(status(args.run_dir), ensure_ascii=False)); return 0
    if args.command == "resume":
        state = None
        if args.config_path:
            path = Path(args.config_path)
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                import yaml
                state = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(state, dict) and isinstance(state.get("default_states"), dict):
                state = state["default_states"]
        result = run(benchmark=args.benchmark, run_dir=args.run_dir, model=args.model,
                     lake=args.lake, dataset=args.dataset, snapshot_id=args.snapshot_id,
                     resume=True, from_step=args.from_step, state=state)
    else:
        state = None
        if args.config_path:
            path = Path(args.config_path)
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                import yaml
                state = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(state, dict) and isinstance(state.get("default_states"), dict):
                state = state["default_states"]
        result = run(benchmark=args.benchmark, run_dir=args.run_dir, predictions=args.predictions,
                     references=args.references, lake=args.lake, dataset=args.dataset,
                     model=args.model, thread_id=args.thread_id,
                     snapshot_id=args.snapshot_id, state=state, resume=args.resume,
                     from_step=args.from_step)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") != "failed" else 1

if __name__ == "__main__":
    raise SystemExit(main())
