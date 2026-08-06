"""Console entry point for the Analyzer skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


def _load_state(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("default_states", payload)

    from omegaconf import OmegaConf

    payload = OmegaConf.load(str(path))
    state = payload.default_states if "default_states" in payload else payload
    return OmegaConf.to_container(state, resolve=True)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            key_name = str(key).lower()
            if (
                key_name == "api_key"
                or key_name.endswith("_api_key")
                or key_name == "token"
                or key_name.endswith("_token")
                or key_name.endswith("_key")
            ):
                result[key] = "***REDACTED***"
            else:
                result[key] = _redact(child)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LoopAI Analyzer skill.")
    parser.add_argument("--config-path", help="YAML/JSON Analyzer state configuration.")
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--version-id", default=None)
    parser.add_argument("--checkpoint-path", default=None)
    parser.add_argument("--baseline-result-path", default=None)
    parser.add_argument("--analyze-batch-size", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--new-version", action="store_true")
    parser.add_argument("--from-node", default=None)
    parser.add_argument("--list-nodes", action="store_true")
    parser.add_argument("--print-result", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.list_nodes:
        print("eval_model\nanalyze_result\ndraw_conclusion\nfinish")
        return 0
    if not args.config_path and not args.resume:
        _parser().error("--config-path is required unless --resume is used")

    from .runner import run_analyzer_standalone

    state = None if args.resume else _load_state(args.config_path)
    try:
        result = run_analyzer_standalone(
            state=state,
            thread_id=args.thread_id,
            resume=args.resume,
            from_node=args.from_node,
            checkpoint_path=args.checkpoint_path,
            baseline_result_path=args.baseline_result_path,
            analyze_batch_size=args.analyze_batch_size,
            version_id=args.version_id,
            force_new_version=args.new_version,
            emit_status=False,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "message": str(exc)}, ensure_ascii=False))
        return 1

    if args.print_result:
        print(json.dumps(_redact(result), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
