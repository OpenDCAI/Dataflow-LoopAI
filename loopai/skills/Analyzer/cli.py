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
    parser.add_argument("command", nargs="?", choices=("start", "status", "resume", "list"), default=None)
    parser.add_argument("--config-path", help="YAML/JSON Analyzer state configuration.")
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--version-id", default=None)
    parser.add_argument("--checkpoint-path", default=None)
    parser.add_argument("--baseline-result-path", default=None)
    parser.add_argument("--analyze-batch-size", type=int, default=None)
    parser.add_argument(
        "--critique-samples-per-tag",
        default=None,
        help="Short critiques read per error tag; use a positive integer or 'full'. Default: 5.",
    )
    parser.add_argument("--request-timeout-seconds", type=float, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--new-version", action="store_true")
    parser.add_argument("--from-node", default=None)
    parser.add_argument("--list-nodes", action="store_true")
    parser.add_argument("--print-result", action="store_true")
    parser.add_argument("--list-benchmarks", action="store_true", help="List discovered benchmark skills.")
    parser.add_argument("--sdk-worker", action="store_true", help="Run the benchmark skill through Codex SDK.")
    parser.add_argument("--benchmark", default=None, help="Benchmark skill name or alias.")
    parser.add_argument("--judger-report", "--input", dest="judger_report", default=None)
    parser.add_argument("--lake", "--warehouse", dest="lake", default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--run", "--run-dir", dest="run_dir", default=None)
    parser.add_argument("--snapshot-id", default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list" or args.list_benchmarks:
        from loopai.skills.benchmarks import discover_benchmarks
        print(json.dumps(sorted(discover_benchmarks()), ensure_ascii=False))
        return 0
    if args.command == "status":
        from . import status
        print(json.dumps(status(args.run_dir or "outputs/analyzer"), ensure_ascii=False))
        return 0
    if args.list_nodes:
        print("eval_model\nanalyze_result\ndraw_conclusion\nmetric_recommend\nmetric_score\nmath_llmaj_label\nanalyze_metric_report\nfinish")
        return 0
    # ``start``/``resume`` without SDK-specific options retain the historical
    # deterministic Analyzer pipeline.  A config file may opt into the SDK
    # worker through analyzer.benchmark/lake/sdk_worker, so inspect it before
    # choosing the execution path.
    sdk_state = _load_state(args.config_path) if args.config_path else None
    analyzer_cfg = sdk_state.get("analyzer") if isinstance(sdk_state, dict) else {}
    sdk_requested = bool(
        args.sdk_worker or args.benchmark or args.judger_report or args.lake
        or args.dataset or args.model or args.run_dir or args.snapshot_id
        or (isinstance(analyzer_cfg, dict) and (
            analyzer_cfg.get("benchmark") or analyzer_cfg.get("lake") or analyzer_cfg.get("sdk_worker")
        ))
    )
    if sdk_requested:
        from . import run
        result = run(
            state=sdk_state,
            benchmark=args.benchmark,
            run_dir=args.run_dir or "outputs/analyzer",
            judger_report=args.judger_report,
            lake=args.lake,
            dataset=args.dataset,
            model=args.model,
            thread_id=args.thread_id,
            resume=args.resume or args.command == "resume",
            timeout=int(args.request_timeout_seconds or 900),
            snapshot_id=args.snapshot_id,
            sdk_worker=True,
        )
        print(json.dumps(_redact(result), ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") != "failed" else 1
    if not args.config_path and not args.resume:
        _parser().error("--config-path is required unless --resume is used")

    state = None if args.resume else (sdk_state if sdk_state is not None else _load_state(args.config_path))
    try:
        from .runner import run_analyzer_standalone
        result = run_analyzer_standalone(
            state=state,
            thread_id=args.thread_id,
            resume=args.resume,
            from_node=args.from_node,
            checkpoint_path=args.checkpoint_path,
            baseline_result_path=args.baseline_result_path,
            analyze_batch_size=args.analyze_batch_size,
            critique_samples_per_tag=args.critique_samples_per_tag,
            analyze_request_timeout_seconds=args.request_timeout_seconds,
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
