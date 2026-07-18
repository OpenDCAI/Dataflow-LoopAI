# -*- coding: utf-8 -*-
"""Judger CLI entry point — ``loopai-judger`` command."""

from __future__ import annotations

import argparse


def main():
    parser = argparse.ArgumentParser(
        description="Run LoopAI Judger evaluation pipeline (standalone, no LangGraph)",
    )
    parser.add_argument(
        "--resume", action="store_true", default=False,
        help="Resume from last checkpoint",
    )
    parser.add_argument(
        "--from-step", type=str, default=None,
        help="Force start from a specific pipeline step",
    )

    args = parser.parse_args()

    from loopai.skills.Judger import run
    run(resume=args.resume, from_step=args.from_step)


if __name__ == "__main__":
    main()
