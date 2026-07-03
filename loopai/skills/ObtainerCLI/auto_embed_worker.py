from __future__ import annotations

import argparse
import json
import sys

from .datamixer_adapter import auto_embed_pending_embeddings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run asynchronous ObtainerCLI auto embedding.")
    parser.add_argument("--lake", required=True)
    parser.add_argument("--dataset", default="")
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--operation-id", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = auto_embed_pending_embeddings(
            lake=args.lake,
            dataset=args.dataset or None,
            batch_size=args.batch_size,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "command": "auto embed",
            "status": "error",
            "operation_id": args.operation_id,
            "error": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    result["operation_id"] = args.operation_id
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
