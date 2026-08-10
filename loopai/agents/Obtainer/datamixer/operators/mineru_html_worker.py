"""Isolated MinerU-HTML worker used by the DataMixer webpage operator.

This module is executed by the dedicated ``mineruhtml`` Conda environment,
not imported by DataMixer's main Python process. One JSON request is read from
stdin per line and one JSON response is written to stdout per line.
"""
from __future__ import annotations

import argparse
import json
import sys


def _build_extractor(args):
    from mineru_html import MinerUHTMLConfig, MinerUHTMLGeneric
    from mineru_html.inference.factory import (
        create_transformers_backend,
        create_vllm_backend,
    )

    config = MinerUHTMLConfig(
        use_fall_back=args.fallback,
        early_load=True,
        prompt_version="short_compact",
        response_format="compact",
        output_format=args.output_format,
    )
    if args.backend == "transformers":
        backend = create_transformers_backend(
            model_path=args.model_path,
            response_format="compact",
            max_context_window=args.max_context_window,
            model_init_kwargs={"device_map": "auto", "dtype": "auto"},
            model_gen_kwargs={
                "max_new_tokens": args.max_tokens,
                "temperature": 0,
                "do_sample": False,
            },
        )
    else:
        backend = create_vllm_backend(
            model_path=args.model_path,
            response_format="compact",
            max_context_window=args.max_context_window,
            model_init_kwargs={
                "tensor_parallel_size": 1,
                "gpu_memory_utilization": args.gpu_memory_utilization,
                "enforce_eager": True,
            },
            model_gen_kwargs={
                "max_tokens": args.max_tokens,
                "temperature": 0,
            },
        )
    return MinerUHTMLGeneric(backend, config)


def _case_to_dict(case):
    output = getattr(case, "output_data", None)
    return {
        "main_html": getattr(case, "main_html", None),
        "main_content": getattr(output, "main_content", None),
        "error": str(getattr(case, "error", "")) if getattr(case, "error", None) else None,
    }




def _build_extractor_silently(args) -> None:
    """Build the extractor while keeping the JSON line protocol on stdout clean.

    Model frameworks (vllm, transformers) and their subprocesses log to the
    inherited stdout, which would corrupt the one-JSON-line-per-request
    protocol this worker speaks. Redirect fd 1 to stderr while the model loads
    and restore it before the request loop starts.
    """
    import os

    saved_stdout = os.dup(1)
    try:
        os.dup2(2, 1)
        return _build_extractor(args)
    finally:
        os.dup2(saved_stdout, 1)
        os.close(saved_stdout)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--backend", choices=("vllm", "transformers"),
                        default="transformers")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.25)
    parser.add_argument("--max-context-window", type=int, default=70000)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--fallback", choices=("trafilatura", "bypass", "empty"),
                        default="trafilatura")
    parser.add_argument("--output-format", default="mm_md")
    args = parser.parse_args()

    extractor = _build_extractor_silently(args)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            request = json.loads(line)
            request_id = request.get("id")
            htmls = request.get("htmls") or []
            try:
                cases = extractor.process(htmls)
                response = {
                    "id": request_id,
                    "results": [_case_to_dict(case) for case in cases],
                }
            except Exception as exc:  # per-request error is returned to parent
                response = {
                    "id": request_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "results": [],
                }
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        cleanup = getattr(extractor.llm, "cleanup", None)
        if cleanup:
            cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
